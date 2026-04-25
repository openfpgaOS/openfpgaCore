//
// GPU Core — span + triangle rasterizer
//
// Asynchronous 2D/3D GPU for openfpgaOS.  Reads commands from a ring
// buffer in SDRAM, rasterises spans (textured, colormapped, depth-tested),
// and writes pixels to the framebuffer via AXI4.
//
// Two AXI4 master ports:
//   M_RD  — ring buffer fetch + texture cache fills (read only)
//   M_WR  — framebuffer writes + clear DMA (write only)
//
// One SRAM word port:
//   Z-buffer read / compare / write (shared with CPU via external mux)
//
// Colormap BRAM (16 KB): CPU uploads via MMIO, GPU reads during fragment.
//
// MMIO registers exposed to CPU for control, status, and fence sync.
//

`default_nettype none

`include "gpu_features.vh"

module gpu_core (
    input wire clk,
    input wire reset_n,

    // GPU enable (gated by MMIO GPU_CTRL)
    input wire gpu_enable,

    // ================================================================
    // AXI4 Read Master — ring fetch + texture cache fills
    // ================================================================
    output wire        m_rd_arvalid,
    input  wire        m_rd_arready,
    output wire [31:0] m_rd_araddr,
    output wire [7:0]  m_rd_arlen,
    input  wire        m_rd_rvalid,
    input  wire [31:0] m_rd_rdata,
    input  wire        m_rd_rlast,

    // ================================================================
    // AXI4 Write Master — framebuffer writes + clear DMA
    // ================================================================
    output reg         m_wr_awvalid,
    input  wire        m_wr_awready,
    output reg  [31:0] m_wr_awaddr,
    output reg  [7:0]  m_wr_awlen,
    output reg         m_wr_wvalid,
    input  wire        m_wr_wready,
    output reg  [31:0] m_wr_wdata,
    output reg  [3:0]  m_wr_wstrb,
    output reg         m_wr_wlast,
    input  wire        m_wr_bvalid,

    // ================================================================
    // SRAM word interface — Z-buffer
    // ================================================================
    output reg         sram_rd,
    output reg         sram_wr,
    output reg  [21:0] sram_addr,
    output reg  [31:0] sram_wdata,
    output reg  [3:0]  sram_wstrb,
    input  wire [31:0] sram_rdata,
    input  wire        sram_busy,
    input  wire        sram_rdata_valid,

    // ================================================================
    // MMIO Register Interface (from axi_periph_slave)
    // ================================================================
    input  wire        reg_wr,
    input  wire [3:0]  reg_addr,        // word offset 0-15
    input  wire [31:0] reg_wdata,
    output reg  [31:0] reg_rdata,

    // ================================================================
    // Status outputs
    // ================================================================
    output wire        busy,
    output reg  [31:0] fence_reached,
    output reg  [31:0] stat_pixels,
    output reg  [31:0] stat_spans,
    // Debug outputs (active during development, removed for synthesis)
    output wire [5:0]  dbg_state,
    output wire [5:0]  dbg_setup_step,
    output wire [31:0] dbg_tri_det
);

wire active = reset_n & gpu_enable;

assign dbg_state = state;
`ifdef GPU_FEAT_TRIANGLE
assign dbg_setup_step = setup_step;
assign dbg_tri_det = tri_det;
`else
assign dbg_setup_step = 6'd0;
assign dbg_tri_det = 32'd0;
`endif

// ================================================================
// MMIO Register Map
// ================================================================
// 0x00  GPU_CTRL        W   bit0=enable, bit1=soft_reset
// 0x04  GPU_RING_WRPTR  W   CPU write pointer (byte offset into ring BRAM)
// 0x08  GPU_RING_DATA   W   Write next word to ring BRAM (auto-increment)
// 0x0C  (reserved)
// 0x10  GPU_RING_RDPTR  R   GPU read pointer
// 0x14  GPU_STATUS      R   {30'b0, ring_empty, busy}
// 0x18  GPU_FENCE       R   Last completed fence token
// 0x1C  GPU_STAT_PIXELS R   Pixel counter
// 0x20  GPU_CMAP_ADDR   W   Colormap write address (14-bit, auto-inc)
// 0x24  GPU_CMAP_DATA   W   Colormap write data (word — 4 bytes stored,
//                             addr post-increments by 4)
// 0x28  GPU_TEX_FLUSH   W   Flush texture cache (write any value)
// 0x2C  GPU_STAT_SPANS  R   Span counter
// 0x30  GPU_DBG_BADWR   R   First FB-range-violating M_WR awaddr since reset
//                           (bit 0 = ever_violated flag; bits [31:2] = addr>>2)
// 0x34  GPU_DBG_BADCNT  R   Count of M_WR writes outside 0x10000000..0x10400000
// 0x38  GPU_DBG_RINGWR  R   Total count of GPU_RING_DATA writes accepted —
//                           compare against app's "words submitted" to detect
//                           lost MMIO writes on the ring-BRAM port.  GPU_DEBUG.

// Ring BRAM: 16 KB = 4096 words, dual-port M10K
// Port A: CPU writes via MMIO (GPU_RING_DATA)
// Port B: GPU reads during command fetch (1-cycle latency)
localparam RING_WORDS = 4096;  // 16 KB
localparam RING_ADDR_BITS = 12;

reg [31:0] ring_bram [0:RING_WORDS-1];
reg [RING_ADDR_BITS-1:0] ring_wr_addr;  // CPU write pointer (word index)
reg [15:0] ring_wrptr;                    // CPU write pointer (byte offset, for GPU comparison)
reg [15:0] ring_rdptr;                    // GPU read pointer (byte offset)
reg [31:0] ring_rd_data;                  // Port B read output (registered)

// CPU upload address for the colormap and transluc[] LUT.  Both targets
// share GPU_CMAP_ADDR / GPU_CMAP_DATA on the MMIO; a 1-bit target-select
// in bit 31 of the addr write picks colormap (0) or transluc (1).  The
// 15-bit byte field is wide enough for the larger 32 KB transluc table.
reg [14:0] cmap_wr_addr;       // Auto-increment byte address (cmap or transluc)
reg        cmap_wr_target;     // 0 = colormap (16 KB), 1 = transluc[] (32 KB)
reg        tex_flush_req;      // Pulse to flush texture cache
reg        soft_reset;         // Pulse: resets FSM state + ring pointers
reg        ring_reset;         // Pulse: reset ring_rdptr (from MMIO, consumed by FSM)
`ifdef GPU_DEBUG
// Monotonic counter of accepted GPU_RING_DATA writes.  Readable via
// GPU_DBG_RINGWR (MMIO 0x38).  If the CPU tracks its own submitted-word
// count, a mismatch proves the MMIO bus is dropping writes on the way
// to this slave.
reg [31:0] ring_wr_count;
`else
wire [31:0] ring_wr_count = 32'b0;
`endif

wire ring_empty = (ring_rdptr == ring_wrptr);
wire [15:0] ring_mask = (RING_WORDS * 4) - 1;  // 0x3FFF for 16 KB

assign busy = !ring_empty || (state != S_IDLE);

// Port B: GPU read (synchronous, 1-cycle latency)
always @(posedge clk)
    ring_rd_data <= ring_bram[ring_rdptr[RING_ADDR_BITS+1:2]];

// MMIO write handling + ring BRAM port A (CPU write)
always @(posedge clk) begin
    if (!reset_n) begin
        ring_wrptr   <= 0;
        ring_wr_addr <= 0;
`ifdef GPU_DEBUG
        ring_wr_count <= 32'b0;
`endif
        cmap_wr_addr <= 0;
        cmap_wr_target <= 0;
        tex_flush_req <= 0;
        soft_reset <= 0;
        ring_reset <= 0;
    end else begin
        tex_flush_req <= 0;
        soft_reset <= 0;
        ring_reset <= 0;
        if (reg_wr) begin
            case (reg_addr)
                4'd0: begin  // GPU_CTRL: bit1=soft_reset, bit2=ring_reset
                    if (reg_wdata[1]) soft_reset <= 1;
                    if (reg_wdata[2]) begin
                        ring_reset <= 1;
                        ring_wr_addr <= 0;
                        ring_wrptr <= 0;
                    end
                end
                4'd1: begin  // GPU_RING_WRPTR — kick GPU
                    ring_wrptr <= reg_wdata[15:0];
                end
                4'd2: begin  // GPU_RING_DATA — write word, auto-increment
                    ring_bram[ring_wr_addr] <= reg_wdata;
                    ring_wr_addr <= ring_wr_addr + 1;
`ifdef GPU_DEBUG
                    ring_wr_count <= ring_wr_count + 32'd1;
`endif
                end
                4'd8: begin  // GPU_CMAP_ADDR
                    // bit 31 selects target (0 = colormap, 1 = transluc[]);
                    // bits [14:0] are the byte offset within that target.
                    cmap_wr_addr   <= reg_wdata[14:0];
                    cmap_wr_target <= reg_wdata[31];
                end
                4'd9: begin  // GPU_CMAP_DATA — 32-bit write, addr += 4 bytes
                    cmap_wr_addr <= cmap_wr_addr + 15'd4;
                end
                4'd10: begin // GPU_TEX_FLUSH
                    tex_flush_req <= 1;
                end
                default: ;
            endcase
        end
    end
end

// MMIO read mux
//
// GPU_STATUS (offset 0x14, reg_addr 5) layout — extended for hardware
// debug. Original two LSBs preserved so legacy code keeps working.
//   [ 0]    busy           — !ring_empty || (state != S_IDLE)
//   [ 1]    ring_empty     — (rdptr == wrptr)
//   [ 7: 2] state          — main FSM state (S_IDLE..S_FRAG_PIPE)
//   [ 8]    m_wr_awvalid   — FB write address in flight on M1
//   [ 9]    tex_arvalid    — tex cache miss in flight (M0 read pending)
//   [10]    tex_arready    — arbiter has granted M0 this cycle
//   [11]    tex_rvalid     — tex cache receiving response
//   [12]    fp_pipe_stall  — pipelined frag processor stalled
//   [15:13] tex_state      — gpu_tex_cache FSM state (S_PIPE..S_FILL_OUT)
//   [16]    tex_pipe_valid — tex cache pipe stage 2 holds a valid req
//   [20:17] fbss           — FB sub-FSM state (FBSS_IDLE..FBSS_ZWRWAIT)
//   [21]    p1_valid       — pipelined frag processor stage 1 valid
//   [22]    p3_valid       — pipelined frag processor stage 3 valid
//   [23]    m_wr_wvalid    — W beat in flight on M1
//   [24]    m_wr_bvalid    — B response received this cycle
always @(*) begin
    case (reg_addr)
        4'd1:    reg_rdata = {16'b0, ring_wrptr};
        4'd4:    reg_rdata = {16'b0, ring_rdptr};
        4'd5:    reg_rdata = {7'b0,
                              m_wr_bvalid,
                              m_wr_wvalid,
`ifdef GPU_FEAT_FRAG_PIPELINE
                              p3_valid,
                              p1_valid,
                              fbss,
                              tex_dbg_pipe_valid,
                              tex_dbg_state,
                              fp_pipe_stall,
`else
                              1'b0, 1'b0, 4'b0, 1'b0, 3'b0,
                              1'b0,
`endif
                              tex_axi_rvalid,
                              tex_axi_arready,
                              tex_axi_arvalid,
                              m_wr_awvalid,
                              state,
                              ring_empty,
                              busy};
        4'd6:    reg_rdata = fence_reached;
        4'd7:    reg_rdata = stat_pixels;
        4'd11:   reg_rdata = stat_spans;
        // Debug: first FB-range-violating M_WR awaddr.  The GPU only ever
        // should write to the framebuffer band (0x10000000..0x103FFFFF);
        // if a stray address leaks out, latch it so the CPU can read it
        // back after a crash.  bad_waddr_hit sits in bit 0 of the latch
        // (addresses are word-aligned so bits[1:0] are always zero).
        4'd12:   reg_rdata = {bad_waddr_latch[31:1], bad_waddr_hit};
        4'd13:   reg_rdata = bad_waddr_count;
        4'd14:   reg_rdata = ring_wr_count;
        default: reg_rdata = 32'b0;
    endcase
end

// ================================================================
// Stray-write diagnostic (gated behind GPU_DEBUG)
// ================================================================
// Latches the FIRST M_WR AXI write whose awaddr is outside the 4 MB
// framebuffer band 0x10000000..0x103FFFFF.  That band covers the three
// 320x240 framebuffers (0x10000000, 0x10100000, 0x10200000), the
// terminal FB at 0x50300000 is never touched by the GPU, and app text
// begins at 0x10400000 so any write >= 0x10400000 is a genuine escape.
// The latch holds the first violator; bad_waddr_count ticks on every
// subsequent violation so the CPU can distinguish a one-shot glitch
// from a sustained stream.
//
// Off by default (no ALM cost in production synth); enable via
// +define+GPU_DEBUG when chasing DMA-corruption symptoms.
`ifdef GPU_DEBUG
reg [31:0] bad_waddr_latch;
reg        bad_waddr_hit;
reg [15:0] bad_waddr_count;
wire       waddr_in_fb_band = (m_wr_awaddr[31:22] == 10'b0001_0000_00);
reg        m_wr_awvalid_d;
always @(posedge clk) begin
    if (reset_n == 1'b0) begin
        bad_waddr_latch <= 32'b0;
        bad_waddr_hit   <= 1'b0;
        bad_waddr_count <= 16'b0;
        m_wr_awvalid_d  <= 1'b0;
    end else begin
        m_wr_awvalid_d <= m_wr_awvalid;
        if (m_wr_awvalid && !m_wr_awvalid_d && !waddr_in_fb_band) begin
            if (!bad_waddr_hit) begin
                bad_waddr_latch <= m_wr_awaddr;
                bad_waddr_hit   <= 1'b1;
            end
            if (bad_waddr_count != 16'hFFFF)
                bad_waddr_count <= bad_waddr_count + 1'b1;
        end
    end
end
`else
// GPU_DEBUG off: MMIO reads of 0x30 / 0x34 return 0.
wire [31:0] bad_waddr_latch = 32'b0;
wire        bad_waddr_hit   = 1'b0;
wire [15:0] bad_waddr_count = 16'b0;
`endif

// ================================================================
// Colormap BRAM — 16 KB dual-port
// ================================================================
// Port A: CPU writes (via MMIO cmap_data register)
// Port B: GPU reads (during fragment processing, 1-cycle latency)

// 16 KB colormap — 4096 × 32-bit. CPU writes a full word per MMIO access
// (was byte-at-a-time — dropped 3/4 of the upload on each write). The
// write address ignores the low 2 bits; the CPU's little-endian word
// byte-order lands directly in bit lanes [7:0]/[15:8]/[23:16]/[31:24].
//
// Word-only writes (no byte-enable or partial-word case statements) —
// Quartus needs a simple full-word `mem[a] <= d` pattern to infer M10K;
// adding per-lane byte writes collapses the BRAM to ~131k discrete FFs
// (observed: 16,547 → 145,565 total registers, fitter error 170011).
// The SDK's colormap uploads are always word-aligned (`size = 64*256 =
// 16384 = 4096 × 4`), so the byte-tail loop in of_gpu_colormap_upload
// never executes with the current hardware's 16 KB colormap.
reg [31:0] cmap_bram [0:4095];

// Port A: CPU write (full 32-bit) — colormap target only.
wire cmap_cpu_wr = reg_wr && (reg_addr == 4'd9) && !cmap_wr_target;
always @(posedge clk) begin
    if (cmap_cpu_wr)
        cmap_bram[cmap_wr_addr[13:2]] <= reg_wdata;
end

// ================================================================
// transluc[] LUT — 32 KB BUILD-style indexed-color blend table
// ================================================================
// Stored as 8192 × 32-bit words (32 M10K under the same Quartus
// inference pattern that gives the 16 KB colormap 16 M10K — keep the
// access pattern word-aligned, no byte-enables, or BRAM inference
// collapses to FFs).  CPU loads via the shared GPU_CMAP_ADDR/DATA port
// with the target-select bit set.
//
// Layout: 128-source × 256-destination quantised approximation of
// BUILD's transluc[256][256].  Source axis is dropped to 7 bits (low
// bit zeroed) — measured against Duke3D's table this is 79% byte-exact
// vs the full 64 KB table with sub-JND average RGB error (1.3 in the
// 6-bit-per-channel palette space).  Tradeoff documented in
// transluc.md.  Address layout:
//
//   key[14:8] = shaded_src[7:1]   (7 bits — low bit dropped)
//   key[7:0]  = fb_byte           (8 bits)
//   word_addr = key[14:2]         (13 bits → 8192 words)
//   byte_lane = key[1:0]
reg [31:0] transluc_bram [0:8191];

wire transluc_cpu_wr = reg_wr && (reg_addr == 4'd9) && cmap_wr_target;
always @(posedge clk) begin
    if (transluc_cpu_wr)
        transluc_bram[cmap_wr_addr[14:2]] <= reg_wdata;
end

// Port B: GPU read
// HOLD cmap_rd_data during pipeline stalls (fp_pipe_stall). Otherwise the
// BRAM keeps reading the LATEST cmap_rd_addr (= the next pixel in p2 after
// the one currently in p2b), and by the time the stall clears, cmap_rd_data
// has been clobbered with the wrong pixel's colormap entry. Gating the
// always block keeps cmap_rd_data stable for the pixel currently in p2b,
// which is what the post-stall p3 <- p2b shift captures. (Manifested as
// pixels right before each FB word boundary getting their successor's
// colormap value once the perspective span path's longer initial stall
// made the timing reproducible.)
reg [13:0] cmap_rd_addr;
reg [7:0]  cmap_rd_data;
// BRAM now 32-bit wide — registered read captures the word; a
// combinational byte mux selects the lane using the low 2 bits of the
// addr. Net latency from cmap_rd_addr update to cmap_rd_data is still
// one cycle (same contract the pipeline expects).
reg [31:0] cmap_rd_word;
reg [1:0]  cmap_rd_lane;
`ifdef GPU_FEAT_FRAG_PIPELINE
always @(posedge clk) begin
    if (!fp_pipe_stall) begin
        cmap_rd_word <= cmap_bram[cmap_rd_addr[13:2]];
        cmap_rd_lane <= cmap_rd_addr[1:0];
    end
end
`else
always @(posedge clk) begin
    cmap_rd_word <= cmap_bram[cmap_rd_addr[13:2]];
    cmap_rd_lane <= cmap_rd_addr[1:0];
end
`endif
always @(*) begin
    case (cmap_rd_lane)
        2'd0: cmap_rd_data = cmap_rd_word[7:0];
        2'd1: cmap_rd_data = cmap_rd_word[15:8];
        2'd2: cmap_rd_data = cmap_rd_word[23:16];
        2'd3: cmap_rd_data = cmap_rd_word[31:24];
    endcase
end

// ================================================================
// transluc[] LUT — Port B: GPU read
// ================================================================
// Same registered-read pattern as the colormap BRAM: a 13-bit word
// address selects an 8K-word entry, a 2-bit lane selects the byte.
// Held across fp_pipe_stall for the same reason cmap_rd_word is —
// otherwise a stall mid-pipeline would let the LUT read drift to a
// later pixel's index before the stalled stage captured this one.
//
// Address composition (computed by the BLEND stage when SPAN_TRANSLUC
// is set on a fragment):
//   transluc_rd_addr = { shaded_src[7:1], fb_byte[7:0] }   // 15 bits
// (= 128 × 256 quantised LUT, dropping low bit of the source axis;
// see transluc.md for the table-format rationale.)
reg [14:0] transluc_rd_addr;
reg [7:0]  transluc_rd_data;
reg [31:0] transluc_rd_word;
reg [1:0]  transluc_rd_lane;
// Unlike cmap_rd_word, this BRAM read is NOT gated on fp_pipe_stall.
// fbss != FBSS_IDLE asserts fp_pipe_stall during the entire BLEND
// sub-flow (REQ → AR_WAIT → R_WAIT → LUT_WAIT → APPLY), but the BLEND
// flow specifically REQUIRES the BRAM read to fire during LUT_WAIT —
// transluc_rd_addr is set in BLEND_R_WAIT and the data must be
// available by BLEND_APPLY two cycles later.  The cmap stall gate is
// to prevent the cmap address from drifting to the next pipeline
// pixel mid-stall; here transluc_rd_addr is only ever written by
// BLEND_R_WAIT and the reset block, so there's no drift to gate.
always @(posedge clk) begin
    transluc_rd_word <= transluc_bram[transluc_rd_addr[14:2]];
    transluc_rd_lane <= transluc_rd_addr[1:0];
end
always @(*) begin
    case (transluc_rd_lane)
        2'd0: transluc_rd_data = transluc_rd_word[7:0];
        2'd1: transluc_rd_data = transluc_rd_word[15:8];
        2'd2: transluc_rd_data = transluc_rd_word[23:16];
        2'd3: transluc_rd_data = transluc_rd_word[31:24];
    endcase
end

// ================================================================
// Shared DSP multiply + reciprocal LUT
// ================================================================
// Used by triangle setup AND perspective span setup — both are always
// present in this single-config build.  The `ifdef guards are kept so
// the bridging gpu_features.vh shim still resolves, but every flag it
// defines is permanently on.
`ifdef GPU_HAS_RECIP_LUT
// Registered DSP multiply (18×18 maps to one Cyclone V DSP block)
reg signed [31:0] dsp_a;
reg signed [31:0] dsp_b;
(* multstyle = "dsp" *) reg signed [63:0] dsp_p;
always @(posedge clk) dsp_p <= dsp_a * dsp_b;

// Second + third DSP slots — used for parallel multiply pipelines. dsp2 is
// shared between PSS (sZ×recip || tZ×recip) and triangle setup (parallel
// edge-function C values, parallel gradient cross-multiplies). dsp3 is
// triangle-setup-only: lets tri_C[0]/tri_C[1]/tri_C[2] compute concurrently
// on all three DSPs, cutting S_TRI_SETUP from ~20 cycles to ~10.
`ifdef GPU_HAS_RECIP_LUT
reg signed [31:0] dsp2_a;
reg signed [31:0] dsp2_b;
(* multstyle = "dsp" *) reg signed [63:0] dsp2_p;
always @(posedge clk) dsp2_p <= dsp2_a * dsp2_b;
`endif
`ifdef GPU_FEAT_TRIANGLE
reg signed [31:0] dsp3_a;
reg signed [31:0] dsp3_b;
(* multstyle = "dsp" *) reg signed [63:0] dsp3_p;
always @(posedge clk) dsp3_p <= dsp3_a * dsp3_b;
`endif

`ifdef GPU_FEAT_TRIANGLE
// Dedicated registered DSP multiply for the triangle row-base address.
// `tri_ymin * st_fb_stride` was the worst critical path in fabric adders
// (16.8 ns, -2.8 ns slack). A DSP costs one of the 37 free slots and
// delivers the product.
//
// DSP input registers: tri_ymin / st_fb_stride are buffered through a
// stand-alone always-clocked register near the DSP so Quartus can pack
// those FFs into the Cyclone 10 DSP's built-in input flip-flops. Source
// registers have state-gated writes (different clock-enables from the
// unconditional tri_ymin_x_stride output), which prevented input-FF
// packing and left -0.858 ns routing slack on the path from the source
// FFs to the DSP input pins.  Adds 1 cycle of latency (tri_ymin update
// → tri_ymin_x_stride valid), absorbed by an extra S_TRI_MUL_WAIT2 step.
//
// Guarded on GPU_FEAT_TRIANGLE — references tri_ymin / tri_A / tri_B /
// tri_xmin which live inside the same guard in the single-config
// build (they were triangle-only regs in the old variant matrix too).
reg signed [15:0] tri_ymin_dsp_in, st_fb_stride_dsp_in;
always @(posedge clk) begin
    tri_ymin_dsp_in     <= tri_ymin;
    st_fb_stride_dsp_in <= st_fb_stride;
end
// Drop the sign-extension: st_fb_stride is unsigned [15:0]; the
// `{{16{st_fb_stride[15]}}, st_fb_stride}` made the operand 32 bits wide,
// forcing Quartus to cascade two DSP18 blocks with a fabric hlmac
// combine + fabric output reg.  Casting to 17-bit signed keeps the
// multiply at 16×17 — fits in ONE DSP18 with all three FFs (two inputs
// and output) packed inside the block.  Matches the unreset input-buffer
// FFs so Quartus has a consistent control set.
(* multstyle = "dsp" *) reg signed [31:0] tri_ymin_x_stride;
always @(posedge clk) begin
    tri_ymin_x_stride <= tri_ymin_dsp_in * $signed({1'b0, st_fb_stride_dsp_in});
end

// Pre-registered A*px and B*py products for the S_TRI_ROW edge-init. The
// original S_TRI_ROW expression `tri_A[i]*px + tri_B[i]*py + tri_C[i]` was
// the worst critical path on the 100 MHz domain (DSP mult + two long carry
// chains in one cycle, -1.603 ns at slow 85°C). Computing the two products
// continuously into registers lets S_TRI_ROW do only a 3-way add — no DSP
// in the same cycle — shortening the cone to a single carry chain. The
// update timing matches tri_ymin_x_stride: valid one cycle after tri_xmin/
// tri_ymin latch in S_TRI_BBOX_CLAMP, i.e. during S_TRI_MUL_WAIT, so
// triangle start latency is unchanged.
(* multstyle = "dsp" *) reg signed [31:0] tri_e_init_Apx [0:2];
(* multstyle = "dsp" *) reg signed [31:0] tri_e_init_Bpy [0:2];
always @(posedge clk) begin
    if (!reset_n) begin
        tri_e_init_Apx[0] <= 0; tri_e_init_Apx[1] <= 0; tri_e_init_Apx[2] <= 0;
        tri_e_init_Bpy[0] <= 0; tri_e_init_Bpy[1] <= 0; tri_e_init_Bpy[2] <= 0;
    end else begin
        // tri_xmin/tri_ymin are non-negative after clamping (S_TRI_BBOX_CLAMP),
        // so the {xmin, 4'b0} / {ymin, 4'b0} 20-bit value zero-extends cleanly
        // to 32-bit signed and the signed×signed DSP product matches the
        // original px/py expression exactly.
        tri_e_init_Apx[0] <= tri_A[0] * $signed({12'b0, tri_xmin, 4'b0});
        tri_e_init_Apx[1] <= tri_A[1] * $signed({12'b0, tri_xmin, 4'b0});
        tri_e_init_Apx[2] <= tri_A[2] * $signed({12'b0, tri_xmin, 4'b0});
        tri_e_init_Bpy[0] <= tri_B[0] * $signed({12'b0, tri_ymin, 4'b0});
        tri_e_init_Bpy[1] <= tri_B[1] * $signed({12'b0, tri_ymin, 4'b0});
        tri_e_init_Bpy[2] <= tri_B[2] * $signed({12'b0, tri_ymin, 4'b0});
    end
end
`endif // GPU_FEAT_TRIANGLE

// Reciprocal LUT: 1024 × 16-bit in M10K (Phase 4b — widened from 256 to
// 1024 to give 10-bit input precision instead of 8-bit, the simpler of
// the two precision options the 2026-04-25 bug report called out).
// 1024×16 doesn't fit in a single M10K (10 Kbits), so this synthesises
// to 2 M10K blocks; FB/cmap/etc. unchanged.  Registered read port: set
// recip_rd_addr, result in recip_rd_data next cycle.
// Stored value: recip_lut[i] = 0x1000000 / (1024 + i) → 16-bit Q14
// (i.e. recip_lut[0] = 16384 = 1.0 in Q14, recip_lut[1023] ≈ 0.501).
// LUT output Q-format unchanged — the PSS_RECIP_SHIFT shift constant
// (5'd13) is independent of the input bit-width, so no shift change.
(* ramstyle = "M10K" *) reg [15:0] recip_lut [0:1023];
reg [9:0]  recip_rd_addr;
reg [15:0] recip_rd_data;
always @(posedge clk) recip_rd_data <= recip_lut[recip_rd_addr];
integer ri;
initial begin
    for (ri = 0; ri < 1024; ri = ri + 1)
        recip_lut[ri] = (16777216) / (1024 + ri);
end
`endif // GPU_HAS_RECIP_LUT

// ================================================================
// Texture Cache instance
// ================================================================
// tex_req_* are combinational from the pipelined fragment processor's
// issue logic (single configuration: pipelined fragment processor is
// always on).
`ifdef GPU_FEAT_FRAG_PIPELINE
wire        tex_req_valid;
wire [25:0] tex_req_addr;
wire        tex_req_wide;
`else
reg         tex_req_valid;
reg  [25:0] tex_req_addr;
reg         tex_req_wide;
`endif
wire        tex_req_ready;
wire        tex_resp_valid;
wire [15:0] tex_resp_data;

wire        tex_axi_arvalid;
wire        tex_axi_arready;
wire [31:0] tex_axi_araddr;
wire [7:0]  tex_axi_arlen;
wire        tex_axi_rvalid;
wire [31:0] tex_axi_rdata;
wire        tex_axi_rlast;

wire [2:0] tex_dbg_state;
wire       tex_dbg_pipe_valid;

gpu_tex_cache tex_cache (
    .clk(clk),
    .reset_n(reset_n),
    .flush(tex_flush_req),
    .req_valid(tex_req_valid),
    .req_ready(tex_req_ready),
    .req_addr(tex_req_addr),
    .req_wide(tex_req_wide),
    .resp_valid(tex_resp_valid),
    .resp_data(tex_resp_data),
    .axi_arvalid(tex_axi_arvalid),
    .axi_arready(tex_axi_arready),
    .axi_araddr(tex_axi_araddr),
    .axi_arlen(tex_axi_arlen),
    .axi_rvalid(tex_axi_rvalid),
    .axi_rdata(tex_axi_rdata),
    .axi_rlast(tex_axi_rlast),
    .dbg_state(tex_dbg_state),
    .dbg_pipe_valid(tex_dbg_pipe_valid)
);

// ================================================================
// AXI4 Read Master — texture cache + translucent-blend FB readback
// ================================================================
// Two consumers share M0: the texture cache (multi-beat line fills)
// and the translucent-blend FB readback (single-beat, only fires when
// SPAN_TRANSLUC is set on a fragment).  Arbitration is by FBSS state:
// when fbss is in BLEND_AR_WAIT or BLEND_R_WAIT, BLEND owns the bus;
// otherwise the texture cache does.  BLEND only enters its own AR_WAIT
// state via FBSS_BLEND_REQ, which waits for the texture cache to be
// fully idle (no AR pending, no R pending) — so the two never race for
// the AR phase.
wire blend_owns_m0 = (fbss == FBSS_BLEND_AR_WAIT)
                  || (fbss == FBSS_BLEND_R_WAIT);

assign m_rd_arvalid    = blend_owns_m0 ? blend_arvalid : tex_axi_arvalid;
assign m_rd_araddr     = blend_owns_m0 ? blend_araddr  : tex_axi_araddr;
assign m_rd_arlen      = blend_owns_m0 ? 8'd0          : tex_axi_arlen;
assign tex_axi_arready = blend_owns_m0 ? 1'b0          : m_rd_arready;
assign tex_axi_rvalid  = blend_owns_m0 ? 1'b0          : m_rd_rvalid;
assign tex_axi_rdata   = m_rd_rdata;
assign tex_axi_rlast   = blend_owns_m0 ? 1'b0          : m_rd_rlast;

wire blend_arready = blend_owns_m0 ? m_rd_arready : 1'b0;
wire blend_rvalid  = blend_owns_m0 ? m_rd_rvalid  : 1'b0;
wire [31:0] blend_rdata = m_rd_rdata;

// Track texture-cache read-in-flight from gpu_core's vantage so
// FBSS_BLEND_REQ knows when M0 is fully drained.  Set on the cycle
// tex's AR is accepted (its handshake on M0); cleared on rlast.
// Only counts cycles where blend_owns_m0=0 (= the bus is muxed to
// tex), so the BLEND-side AR/R handshakes don't accidentally toggle it.
always @(posedge clk) begin
    if (!reset_n) begin
        tex_m0_in_flight <= 1'b0;
    end else if (!blend_owns_m0) begin
        if (tex_axi_arvalid && m_rd_arready)
            tex_m0_in_flight <= 1'b1;
        else if (m_rd_rvalid && m_rd_rlast)
            tex_m0_in_flight <= 1'b0;
    end
end

// ================================================================
// Command Types
// ================================================================
localparam CMD_NOP            = 8'h01;
localparam CMD_FENCE          = 8'h02;
localparam CMD_CLEAR          = 8'h10;
localparam CMD_SET_TEXTURE    = 8'h20;
localparam CMD_SET_DEPTH_FUNC = 8'h21;
localparam CMD_SET_FB         = 8'h23;
localparam CMD_SET_ZB         = 8'h24;
localparam CMD_DRAW_TRIANGLES = 8'h30;
localparam CMD_DRAW_SPAN      = 8'h40;
// Removed commands (reserved opcodes, do not reuse):
//   0x22 CMD_SET_BLEND      — no combine path in the datapath
//   0x25 CMD_SET_SHADE      — Gouraud gradient dropped in the FMax push
//   0x26 CMD_SET_ALPHA_REF  — no alpha test in the datapath
//   0x31 CMD_DRAW_INDEXED   — ~400 ALMs of dynamic pay_buf mux fabric;
//                             expand indices CPU-side and emit per-tri
//   0x41 CMD_DRAW_SPANS     — batch machinery was half-implemented;
//                             emit N separate CMD_DRAW_SPAN commands
//   0x42 CMD_DRAW_SPRITE    — 2-triangle sprite is cheaper and rotates
localparam CMD_SET_SKIP_ZERO  = 8'h27;  // 1-word payload: global SKIP_ZERO enable

// ================================================================
// GPU State Registers (sticky, set by SET_* commands)
// ================================================================
// Removed: st_tex_height / st_tex_format / st_tex_wrap_s / st_tex_wrap_t
//          were parsed from CMD_SET_TEXTURE but never read by the
//          datapath (format is I8-only, no wrap/clamp logic).
//          st_blend_mode / st_alpha_ref / st_gouraud dropped with their
//          respective SET commands; the blend/alpha/Gouraud paths never
//          existed as functioning code.  Triangle light uses flat v0.r.
reg [31:0] st_tex_addr;
reg [15:0] st_tex_width;
reg [2:0]  st_depth_func;      // 0=none,1=always,2=less,3=lequal,4=equal,5=gequal,6=greater,7=notequal
reg [31:0] st_fb_addr;
reg [15:0] st_fb_stride;
reg [31:0] st_zb_addr;
reg [15:0] st_zb_stride;

// ================================================================
// Span Registers (loaded from command payload)
// ================================================================
reg [31:0] sp_fb_addr;
reg [31:0] sp_tex_addr;
reg signed [31:0] sp_s, sp_t;
reg signed [31:0] sp_sstep, sp_tstep;
reg [15:0] sp_count;
// Phase 4d — Gouraud-capable light.  sp_light_q is Q16.16; bits
// [23:16] are the 8-bit value the fragment pipe sees as p0_light.
// sp_light_step is signed Q16.16, the per-pixel x delta.  Direct
// CMD_DRAW_SPAN payloads set sp_light_step = 0 (flat lighting,
// bit-identical to the old 8-bit sp_light path); triangle span emit
// loads sp_light_step from grad_r_dx<<<4 so light walks per pixel.
reg signed [31:0] sp_light_q;
reg signed [31:0] sp_light_step;
wire [7:0]        sp_light = sp_light_q[23:16];
reg [7:0]  sp_flags;
reg signed [15:0] sp_fb_stride;
reg [15:0] sp_tex_width;
// POT wrap masks: sp_s_int & sp_tex_w_mask, sp_t_int & sp_tex_h_mask
// before the address math.  Default 16'hFFFF (no-op) so callers that
// don't set word 8 see the legacy multiply-mode behaviour.  The masks
// reproduce BUILD's hlineasm4 shift-mode wrap exactly when tex_w/tex_h
// are powers of two (always true for BUILD/Quake/Doom textures).
reg [15:0] sp_tex_w_mask;
reg [15:0] sp_tex_h_mask;
reg [31:0] sp_z_addr;
reg signed [31:0] sp_zi;
reg signed [31:0] sp_zistep;

// Span flags
localparam SPAN_COLORMAP    = 0;
localparam SPAN_COLUMN      = 1;
localparam SPAN_SKIP_ZERO   = 2;
localparam SPAN_DEPTH_TEST  = 3;
localparam SPAN_DEPTH_WRITE = 4;
localparam SPAN_PERSP       = 5;
localparam SPAN_TRANSLUC    = 6;  // route p3_color through transluc[] LUT
localparam SPAN_TRANSLUC_REV= 7;  // swap (src, fb) → (fb, src) in the key

// ================================================================
// Main FSM
// ================================================================
localparam S_IDLE           = 6'd0;
localparam S_RING_WAIT      = 6'd2;  // 1-cycle BRAM read latency
localparam S_DECODE         = 6'd3;
localparam S_PAY_DATA       = 6'd5;  // 1 word/cycle from BRAM
localparam S_EXECUTE        = 6'd6;
localparam S_SPAN_PIXEL     = 6'd7;
localparam S_SPAN_TEX_REQ   = 6'd8;
localparam S_SPAN_TEX_WAIT  = 6'd9;
localparam S_SPAN_CMAP      = 6'd10;
localparam S_SPAN_CMAP_WAIT = 6'd11;
localparam S_SPAN_ZREAD     = 6'd12;
localparam S_SPAN_ZWAIT     = 6'd13;
localparam S_SPAN_ZCMP      = 6'd14;
localparam S_SPAN_ZWWAIT    = 6'd15;
localparam S_SPAN_FB        = 6'd16;
localparam S_SPAN_STEP      = 6'd17;
localparam S_FB_FLUSH       = 6'd18;
localparam S_FB_FLUSH_WAIT  = 6'd19;
localparam S_CLEAR_INIT     = 6'd20;
localparam S_CLEAR_FB       = 6'd21;
localparam S_CLEAR_FB_WAIT  = 6'd22;
localparam S_CLEAR_ZB       = 6'd23;
localparam S_CLEAR_ZB_WAIT  = 6'd24;
localparam S_SPAN_TEX_CALC = 6'd25;  // Pipeline stage 2: finish tex addr

// Triangle rasterisation states
localparam S_TRI_LOAD      = 6'd26;  // Extract vertices from payload
localparam S_TRI_SETUP     = 6'd27;  // Sequential edge/gradient computation
localparam S_TRI_BBOX      = 6'd28;  // Compute bounding box, clip to screen
localparam S_TRI_ROW       = 6'd29;  // Initialise scanline row
localparam S_TRI_PIX       = 6'd30;  // Scan row for inside-extent → emit span
localparam S_TRI_ROW_NEXT  = 6'd31;  // Advance to next Y (after span drains)
localparam S_TRI_GRAD      = 6'd32;  // Rolled gradient computation loop
localparam S_FRAG_PIPE     = 6'd33;  // Unified pipelined fragment processor
localparam S_TRI_MUL_WAIT  = 6'd34;  // wait for DSP input-register stage (+1 cycle from BBOX_CLAMP)
localparam S_TRI_BBOX_CLAMP = 6'd35; // Clamp raw min/max to screen bounds
localparam S_TRI_MUL_WAIT2 = 6'd36;  // wait for DSP output register (tri_ymin_x_stride valid)
localparam S_TRI_INIT_ATTRIB = 6'd37; // Bbox-origin attribute init (z/s/t at xmin,ymin)
localparam S_TRI_PERSP_PREMUL = 6'd38; // (Phase 4c.2) Pre-multiply v_s × v_w, v_t × v_w

reg [5:0] state;

// Command decoding
reg [7:0]  cmd_type;
reg [23:0] cmd_payload_words;

// Pre-decoded one-hot dispatch flags. Set in S_DECODE based on the
// registered cmd_type and consumed in S_EXECUTE. Pre-decoding shortens
// the combinational path from cmd_type to the per-command state regs
// (notably sp_tstep, which had a -0.6 ns critical path through the
// 8-bit case decoder before this change).
reg cmd_is_nop;
reg cmd_is_fence;
reg cmd_is_clear;
reg cmd_is_set_texture;
reg cmd_is_set_depth_func;
reg cmd_is_set_fb;
reg cmd_is_set_zb;
reg cmd_is_draw_span;
// cmd_is_draw_spans removed with CMD_DRAW_SPANS — firmware emits N separate
// CMD_DRAW_SPAN commands for batch draws now.
reg cmd_is_set_skip_zero;
`ifdef GPU_FEAT_TRIANGLE
reg cmd_is_draw_triangles;
`endif
// Global SKIP_ZERO (color-key at texel 0xFF) state — set via CMD_SET_SKIP_ZERO,
// ORed into every triangle-emitted span's flags so color-keyed sprites
// (emitted as 2 triangles) get the transparency treatment.
reg        st_skip_zero;

// Payload streaming state — ring_rd_data is routed directly to each
// destination reg in S_PAY_DATA; no intermediate pay_buf array.
// pay_idx saturates at 19 (any payload word past that still drains the
// ring via pay_remaining but has nowhere to go).
reg [4:0]  pay_idx;
reg [23:0] pay_remaining;  // total payload words still to consume from the ring
                           // (matches cmd_payload_words width so multi-triangle
                           //  commands drain completely without desyncing the ring)

// Current pixel state
reg [7:0]  frag_texel;        // Texel value (I8)
reg [15:0] frag_color;        // Output color (after colormap / combine)
reg        frag_discard;      // Alpha test / skip-zero result

// ================================================================
// Pipelined Fragment Processor — stage registers
// ================================================================
// Single fragment processor for spans and triangle-emitted spans.
// Triangles are rasterised into per-row spans in S_TRI_PIX that feed
// the same S_FRAG_PIPE path used by CMD_DRAW_SPAN.
//
// 4 logical stages, with combinational tex_req drive (1-cycle cache latency):
//   S0 (Issue, comb):    drive tex_req_valid/addr from current sp_*. The
//                        cache sees the request the same cycle and accepts
//                        if req_ready=1 (combinational). On commitment
//                        (`tex_req_valid && tex_req_ready` in same cycle),
//                        latch p1 with sp_*'s metadata and advance source.
//   S1 (TexResp, p1):    1 cycle later, cache returns tex_resp_data
//                        (combinational hit response). Capture into p2 and
//                        issue cmap_rd_addr.
//   S2 (CmapResp, p2):   1 cycle later, cmap_rd_data is valid. Pipeline
//                        shift folds cmap result into p3.
//   S3 (FBwrite, p3):    Sub-FSM (fbss) consumes p3; multi-cycle pause on
//                        word-boundary flush.
//
// Stalls:
//   * (p1_valid && !tex_resp_valid) — cache miss in flight, hold p1
//   * (fbss != FBSS_IDLE)           — fb sub-FSM busy with a flush
//
// Source mode: 0 = SPAN (sp_*). Triangle source mode is reserved but the
// triangle refactor is deferred to a later phase.
`ifdef GPU_FEAT_FRAG_PIPELINE
// p0: pre-issue stage. Holds the pixel whose multiply (tx_mul_q) is being
// computed by the registered DSP this cycle. p0 → p1 transition is the
// "issue commit" event, gated on the cache asserting req_ready in the same
// cycle the consumer drives req_valid.
reg        p0_valid;
reg [7:0]  p0_light;
reg [7:0]  p0_flags;
reg [31:0] p0_fb_addr;
reg [31:0] p0_z_addr;
reg [31:0] p0_zi;
reg signed [15:0] p0_s_int;     // for the post-mul add
reg [31:0] p0_tex_base;         // sp_tex_addr at issue time
// p0_mode / p0_shift_addr removed — multiply-mode tex address is universal

// DSP-pipelined texture multiply. Registered output gives the path a clean
// register-to-register boundary that the fitter can pack into a DSP slice.
// Loaded conditionally (only on issue commit OR when p0 is being primed).
(* multstyle = "dsp" *) reg signed [31:0] tx_mul_q;

reg        p1_valid;
reg [7:0]  p1_light;
reg [7:0]  p1_flags;
reg [31:0] p1_fb_addr;
reg [31:0] p1_z_addr;
reg [31:0] p1_zi;             // 16.16 z for compare/write

reg        p2_valid;
reg [7:0]  p2_color;          // tex result
reg [7:0]  p2_light;
reg [7:0]  p2_flags;
reg [31:0] p2_fb_addr;
reg [31:0] p2_z_addr;
reg [31:0] p2_zi;
reg        p2_discard;        // skip-zero outcome

// p2b: 1-cycle delay between p2 (cmap addr issued) and p3 (cmap data captured).
// Cmap BRAM has 2-cycle effective latency from NB-set of cmap_rd_addr to
// cmap_rd_data being valid for that index, so we need a no-op shift stage.
reg        p2b_valid;
reg [7:0]  p2b_color;
reg [7:0]  p2b_flags;
reg [31:0] p2b_fb_addr;
reg [31:0] p2b_z_addr;
reg [31:0] p2b_zi;
reg        p2b_discard;

reg        p3_valid;
reg [7:0]  p3_color;          // final color (post-cmap if applicable)
reg [7:0]  p3_flags;
reg [31:0] p3_fb_addr;
reg [31:0] p3_z_addr;
reg [31:0] p3_zi;
reg        p3_discard;
reg        p3_z_resolved;     // 1 once FBSS has finished the depth detour for p3

// FB write sub-FSM (lives within S3, pauses pipeline when not IDLE)
localparam FBSS_IDLE        = 4'd0;
localparam FBSS_FLUSH_AW    = 4'd1;  // emit AW, then resume into accumulate
localparam FBSS_FLUSH_W_RSP = 4'd2;  // wait for W handshake + B response
localparam FBSS_ZREAD       = 4'd3;  // issue SRAM read for depth compare
localparam FBSS_ZWAIT       = 4'd4;  // receive read, compare, launch write if needed
localparam FBSS_ZWRWAIT     = 4'd5;  // wait for SRAM write to complete
// Translucent-blend sub-flow.  Fragments with SPAN_TRANSLUC pass
// through a read-modify-write path: read existing FB byte from SDRAM
// (or bypass from fb_acc if same-word), compose a 15-bit key from the
// shaded source and the FB byte, look up the blended byte in
// transluc[], then accumulate that into fb_acc as usual.
//   BLEND_REQ      — wait for M0 to be free of texture-cache traffic
//   BLEND_AR_WAIT  — issue AR; m_rd_* muxed to BLEND while in this/R_WAIT
//   BLEND_R_WAIT   — wait for R, capture rdata
//   BLEND_LUT_WAIT — BRAM 1-cycle read latency for transluc_rd_data
//   BLEND_APPLY    — write transluc_rd_data into fb_acc (cross-word
//                    flush handled identically to the IDLE fast path)
localparam FBSS_BLEND_REQ      = 4'd6;
localparam FBSS_BLEND_AR_WAIT  = 4'd7;
localparam FBSS_BLEND_R_WAIT   = 4'd8;
localparam FBSS_BLEND_LUT_WAIT = 4'd9;
localparam FBSS_BLEND_APPLY    = 4'd10;
reg [3:0] fbss;

// BLEND scratch state (latched at FBSS_IDLE → FBSS_BLEND_REQ).
reg [7:0]  blend_src_color;       // shaded p3_color captured at entry
reg [31:0] blend_word_addr;       // p3_word_addr captured at entry
reg [1:0]  blend_byte_lane;       // p3_byte_lane captured at entry
reg [7:0]  blend_p3_flags;        // captured for cross-word handling
reg [31:0] blend_fb_word;         // SDRAM read result (or fb_acc merge)
reg        blend_arvalid;
reg [31:0] blend_araddr;

// Tracks whether the texture cache currently has a read in flight on M0.
// Used by FBSS_BLEND_REQ to wait for M0 to become idle before grabbing it.
reg        tex_m0_in_flight;

// Pending pixel queued during a flush — applied after AXI write completes.
reg        fbss_pend_valid;
reg [7:0]  fbss_pend_color;
reg [31:0] fbss_pend_addr;

// (fbss_z_rdata removed — ZWAIT now launches the SRAM write in the same
// cycle it sees the read response, so sram_rdata is live when we need it.)

// Source mode: which input feeds S0 each cycle.
localparam SRC_SPAN     = 1'b0;
localparam SRC_TRIANGLE = 1'b1;
reg src_mode;
reg src_done;            // source has issued its last pixel; pipeline draining

// ----------------------------------------------------------------
// Combinational tex_req drive
// ----------------------------------------------------------------
// Drives tex_req from p0 (registered pre-issue metadata) and tx_mul_q
// (registered DSP multiply output for that p0). The p0 stage exists so
// that the multiply can be DSP-pipelined: while p0 holds the pixel about
// to issue, the DSP is producing tx_mul_q for it. When the cache accepts
// (combinational `tex_req_valid && tex_req_ready`), p0 → p1 commits and
// the next sp_* pixel is loaded into p0 (with its DSP mul kicked off).
//
// Critical-path benefit: the long combinational `sp_t * sp_tex_width` path
// is broken at the DSP output register, so the fitter can pack it into a
// DSP slice and the path from `tx_mul_q` register through the post-multiply
// adds to the cache RAM port is short.

// Lookahead: if we'd enter a depth detour THIS cycle, also stall the pipeline
// so p3 holds stable while FBSS walks ZREAD/ZWAIT/ZWRITE/ZWRWAIT.
wire fbss_depth_entry = (fbss == FBSS_IDLE) && p3_valid && !p3_discard
                     && (p3_flags[SPAN_DEPTH_TEST] || p3_flags[SPAN_DEPTH_WRITE])
                     && !p3_z_resolved;
wire fp_pipe_stall = (p1_valid && !tex_resp_valid) || (fbss != FBSS_IDLE) || fbss_depth_entry;

// Combinational tex address from p0 + DSP output.  Multiply-mode only
// (sp_tex_width is always non-zero in every real caller — tested in
// tb_gpu with tex_width ∈ {1, 16, 32, 64, 300} and in gpudemo with
// tex_width = 64).  The old shift-mode p0_shift_addr path was dead
// code; removing it saves the 32-bit 2:1 mux + the p0_shift_addr
// register and its variable-barrel-shift update logic.
wire [31:0] fp_tex_addr_full = p0_tex_base + tx_mul_q
                             + {{16{p0_s_int[15]}}, p0_s_int};

assign tex_req_valid = (state == S_FRAG_PIPE) && p0_valid
                    && !fp_pipe_stall && !persp_issue_stall;
assign tex_req_addr  = fp_tex_addr_full[25:0];
assign tex_req_wide  = 1'b0;

// ----------------------------------------------------------------
// Perspective span — projection-space state + segment setup
// ----------------------------------------------------------------
// 8-pixel affine subdivision (perspective-correct at segment ends,
// linear interpolation within each segment). The span command supplies
// (s/z)_start, (t/z)_start, (1/z)_start and their per-pixel deltas in
// projection space (sdivz, tdivz, zi_persp + their *_step). Per 8-pixel
// segment, the GPU computes:
//
//   z       = 1 / (1/z)               -- via M10K reciprocal LUT
//   s_anchor = (s/z) * z              -- via shared DSP
//   t_anchor = (t/z) * z
//
// at both ends of the segment, derives an affine slope, and feeds those
// slopes into the existing pipelined fragment processor as the per-pixel
// (sp_s, sp_sstep, sp_t, sp_tstep). Within a segment the path is identical
// to affine spans — the perspective math only runs in the segment-setup
// (PSS) sub-FSM described below.
//
// Slot model:
//   slot A (sp_s/sp_t/sp_sstep/sp_tstep, sp_seg_left)  — currently rendering
//   slot B (persp_pend_*)                              — next segment, pre-computed
//
// At span start the PSS runs three passes back-to-back:
//   1. ANCHOR_ONLY  — compute s_anchor at pos 0 (no projection-space advance)
//   2. SLOPE_TO_A   — advance proj state by 16, compute s_anchor at pos 16,
//                     derive slope, fill slot A
//   3. SLOPE_TO_B   — advance proj state by 16, compute s_anchor at pos 32,
//                     derive slope, fill slot B
//
// Passes 1+2 stall the issue stage (`persp_issue_stall = persp_active &&
// !persp_seg_a_ready`). Pass 3 runs in parallel with the issue stage rendering
// segment 0 — its 7-cycle latency is well inside the 16-pixel segment, so
// slot B is ready by the time the issue stage finishes segment 0 and needs
// to swap.
//
// On segment boundary: when load_p0 fires for the last pixel of segment N
// (sp_seg_left == 0), the issue stage swaps slot B into slot A (sp_s,
// sp_sstep, etc), clears persp_seg_b_ready, and the PSS scheduler picks up
// segment N+2 in slot B.
`ifdef GPU_PERSP_IMPL
// Projection-space accumulators (advance by 16 each PSS run).
// Loaded from pay_buf[12..14] / pay_buf[15..17] in CMD_DRAW_SPAN.
reg signed [31:0] sp_sZ;        // s/z, 16.16 signed
reg signed [31:0] sp_tZ;        // t/z, 16.16 signed
reg signed [31:0] sp_zinv;      // 1/z, 16.16 (always positive)
reg signed [31:0] sp_sZstep;    // d(s/z)/dx, per-pixel
reg signed [31:0] sp_tZstep;    // d(t/z)/dx, per-pixel
reg signed [31:0] sp_zinv_step; // d(1/z)/dx, per-pixel

// Active when the current span has SPAN_PERSP set. Latched at CMD_DRAW_SPAN.
reg        persp_active;

// Pixels remaining in the current 8-pixel affine sub-segment, AFTER the
// pixel currently being issued. Counts 7 → 0 within a segment. When
// load_p0 fires with sp_seg_left == 0, slot B is swapped into slot A.
reg [3:0]  sp_seg_left;

// Slot A readiness: sp_s/sp_t/sp_sstep/sp_tstep are valid (PSS pass 2 done).
// Until set, the issue stage stalls (persp_issue_stall=1).
reg        persp_seg_a_ready;

// Anchor sample at the START of the current segment (= persp_pos_0 after
// pass 1, becomes persp_pos_16 after pass 2, persp_pos_32 after pass 3, …).
// Used by the next PSS pass as the slope baseline.
reg signed [31:0] persp_anchor_s;
reg signed [31:0] persp_anchor_t;

// First-pass marker: pass 1 (ANCHOR_ONLY) has produced persp_anchor_s/t.
// Cleared at span start, set after PSS_FINAL of pass 1.
reg        persp_first_done;

// Pending slot (slot B): the next segment, pre-computed in parallel.
reg signed [31:0] persp_pend_s;
reg signed [31:0] persp_pend_t;
reg signed [31:0] persp_pend_sstep;
reg signed [31:0] persp_pend_tstep;
reg        persp_seg_b_ready;

// PSS — segment-setup sub-FSM. Runs alongside the issue stage (and fbss)
// inside S_FRAG_PIPE. Drives dsp_a/dsp_b and recip_rd_addr; reads
// dsp_p / recip_rd_data. ~9 cycles per pass on the regular path (8 on
// the no-advance first pass).
//
// Setup-side pipeline (PSS_ADV → PSS_CLZ → PSS_TOP8) is split into 3
// register-bounded stages because the original 1-cycle combinational
// chain (sp_zinv → +step<<4 → abs → 32-line CLZ casez → 32-bit barrel
// shift → top8 → recip_rd_addr) was the worst critical path in the
// design with -3.451 ns slack at 50 MHz. The split is:
//   PSS_ADV    : sp_zinv += step<<4; register persp_zinv_abs_r
//   PSS_CLZ    : compute CLZ from registered abs; register persp_clz
//   PSS_TOP8   : compute top8 from (abs << clz); write recip_rd_addr
// PSS_RECIP_NA shares the same PSS_CLZ → PSS_TOP8 tail by registering
// abs of un-advanced sp_zinv into persp_zinv_abs_r and falling through.
localparam PSS_IDLE      = 4'd0;
localparam PSS_ADV       = 4'd1;  // stage 1: advance proj coords; register abs
localparam PSS_CLZ       = 4'd2;  // stage 2: compute CLZ from registered abs
localparam PSS_TOP8      = 4'd3;  // stage 3: compute top8; write recip_rd_addr
localparam PSS_RECIP_W   = 4'd4;  // BRAM read latency
localparam PSS_MUL       = 4'd5;  // kick BOTH dsp + dsp2 multiplies (operands pre-registered)
localparam PSS_MUL_W     = 4'd6;  // DSP pipeline delay (shared, both multiplies)
localparam PSS_FINAL     = 4'd7;  // capture both products; commit per pass
localparam PSS_RECIP_NA  = 4'd8;  // ANCHOR_ONLY entry — register abs without advance
localparam PSS_RECIP_SHIFT = 4'd9;  // stage between RECIP_W and MUL: compute recip_q16
                                    // and register it, so PSS_MUL becomes a simple
                                    // reg-to-reg load into dsp_b/dsp2_b instead of
                                    // synthesizing a 32-bit variable barrel shift
                                    // into the dsp2_b update mux (-0.661 ns cone).
// Newton-Raphson refinement (bug-report 2026-04-25 part C).  The
// 1024-entry LUT gives ~10-bit precision in 1/normalised; for Quake's
// very-small 1/z values on oblique surfaces that loses ~3 bits of
// useful precision and texcoords drift visibly across spans.  One
// N-R iteration y1 = y0 * (2 - x*y0) doubles the precision to ~20
// bits — well past Q16.16 — at the cost of 6 cycles per recip.
// Reuses the existing dsp slot, no new DSPs.
localparam PSS_NR_MUL_X    = 4'd10;  // launch x * y0
localparam PSS_NR_MUL_X_W  = 4'd11;  // DSP pipeline delay
localparam PSS_NR_SUB      = 4'd12;  // capture xy, register 2 - xy
localparam PSS_NR_MUL_Y    = 4'd13;  // launch y0 * (2 - xy)
localparam PSS_NR_MUL_Y_W  = 4'd14;  // DSP pipeline delay
localparam PSS_NR_CAPTURE  = 4'd15;  // refined recip → recip_q16_r
reg [3:0] persp_pss;
reg signed [31:0] recip_q16_r;
reg signed [31:0] nr_two_minus_xy;

// PSS pass type — what PSS_FINAL should do with the computed (s_end, t_end).
localparam PSS_PASS_ANCHOR = 2'd0;  // pass 1: anchor only → persp_anchor_s/t
localparam PSS_PASS_TO_A   = 2'd1;  // pass 2: derive slope, fill slot A
localparam PSS_PASS_TO_B   = 2'd2;  // pass 3+: derive slope, fill slot B (pending)
reg [1:0] persp_pass;

// Latched values across PSS pipeline stages.
reg [31:0] persp_zinv_abs_r;   // |sp_zinv| latched after PSS_ADV / PSS_RECIP_NA
reg [4:0]  persp_clz;          // CLZ of persp_zinv_abs_r, latched after PSS_CLZ
// (persp_recip_q16, persp_s_end removed — both sZ and tZ multiplies now
// run concurrently on dsp/dsp2, so PSS_FINAL reads dsp_p and dsp2_p together.)

// Stall the issue stage while slot A isn't ready (passes 1+2 still running).
// Slot B not being ready is handled separately inside the load_p0 gate.
wire persp_issue_stall = persp_active && !persp_seg_a_ready;

// Combinational |sp_zinv| variants. PSS_ADV uses the post-advance value
// (sp_zinv + 8 pixels of step); PSS_RECIP_NA uses the un-advanced value
// (first pass only). Both are registered into persp_zinv_abs_r so the
// downstream CLZ/top8 stages don't include the 32-bit add in their
// timing path.
wire signed [31:0] sp_zinv_advanced = sp_zinv + (sp_zinv_step <<< 3);  // 8-pixel advance
wire        [31:0] persp_zinv_abs   = sp_zinv_advanced[31]
                                    ? -sp_zinv_advanced
                                    :  sp_zinv_advanced;
wire        [31:0] persp_zinv_abs_na = sp_zinv[31] ? -sp_zinv : sp_zinv;

// CLZ helper — combinational casez. Returns leading-zero count for 32-bit.
function [4:0] persp_clz_fn;
    input [31:0] v;
    begin
        casez (v)
            32'b1???????????????????????????????: persp_clz_fn = 5'd0;
            32'b01??????????????????????????????: persp_clz_fn = 5'd1;
            32'b001?????????????????????????????: persp_clz_fn = 5'd2;
            32'b0001????????????????????????????: persp_clz_fn = 5'd3;
            32'b00001???????????????????????????: persp_clz_fn = 5'd4;
            32'b000001??????????????????????????: persp_clz_fn = 5'd5;
            32'b0000001?????????????????????????: persp_clz_fn = 5'd6;
            32'b00000001????????????????????????: persp_clz_fn = 5'd7;
            32'b000000001???????????????????????: persp_clz_fn = 5'd8;
            32'b0000000001??????????????????????: persp_clz_fn = 5'd9;
            32'b00000000001?????????????????????: persp_clz_fn = 5'd10;
            32'b000000000001????????????????????: persp_clz_fn = 5'd11;
            32'b0000000000001???????????????????: persp_clz_fn = 5'd12;
            32'b00000000000001??????????????????: persp_clz_fn = 5'd13;
            32'b000000000000001?????????????????: persp_clz_fn = 5'd14;
            32'b0000000000000001????????????????: persp_clz_fn = 5'd15;
            32'b00000000000000001???????????????: persp_clz_fn = 5'd16;
            32'b000000000000000001??????????????: persp_clz_fn = 5'd17;
            32'b0000000000000000001?????????????: persp_clz_fn = 5'd18;
            32'b00000000000000000001????????????: persp_clz_fn = 5'd19;
            32'b000000000000000000001???????????: persp_clz_fn = 5'd20;
            32'b0000000000000000000001??????????: persp_clz_fn = 5'd21;
            32'b00000000000000000000001?????????: persp_clz_fn = 5'd22;
            32'b000000000000000000000001????????: persp_clz_fn = 5'd23;
            32'b0000000000000000000000001???????: persp_clz_fn = 5'd24;
            32'b00000000000000000000000001??????: persp_clz_fn = 5'd25;
            32'b000000000000000000000000001?????: persp_clz_fn = 5'd26;
            32'b0000000000000000000000000001????: persp_clz_fn = 5'd27;
            32'b00000000000000000000000000001???: persp_clz_fn = 5'd28;
            32'b000000000000000000000000000001??: persp_clz_fn = 5'd29;
            32'b0000000000000000000000000000001?: persp_clz_fn = 5'd30;
            default: persp_clz_fn = 5'd31;
        endcase
    end
endfunction

// CLZ wire computed from the REGISTERED abs value during PSS_CLZ.
wire [4:0] persp_clz_pipe = persp_clz_fn(persp_zinv_abs_r);
// top10 = bits[30:21] of (persp_zinv_abs_r << persp_clz). 10-bit input
// to the widened reciprocal LUT (Phase 4b).  Computed during PSS_TOP8
// from the REGISTERED abs and the REGISTERED clz, so the variable
// barrel shift sits between two register banks instead of in front of a
// 32-line CLZ casez (which was the old critical path).  Width grows by
// 2 bits but the casez chain is unchanged.
wire [31:0] persp_norm_pipe = persp_zinv_abs_r << persp_clz;
wire [9:0]  persp_top8_pipe = persp_norm_pipe[30:21];
`else
wire persp_issue_stall = 1'b0;
`endif // GPU_PERSP_IMPL
`endif // GPU_FEAT_FRAG_PIPELINE

// FB write accumulator
reg [31:0] fb_acc_data;
reg [3:0]  fb_acc_mask;       // Byte enables
reg [31:0] fb_acc_addr;       // Word-aligned SDRAM address
reg        fb_acc_valid;      // Has pending data

// Clear state
reg [1:0]  clear_flags;
reg [15:0] clear_color;
reg [15:0] clear_depth;
reg [31:0] clear_addr;
reg [17:0] clear_remaining;   // Words remaining to clear

// (AXI4 write handshakes managed per-state, no global tracking)

// ================================================================
// Triangle Registers
// ================================================================

// tri_active: 1 = fragment pipeline returns to triangle path. Read by span
// states unconditionally; synthesizer folds
// away `tri_active ? S_TRI_PIX : S_SPAN_STEP` to just `S_SPAN_STEP`.
`ifdef GPU_FEAT_TRIANGLE
reg        tri_active;
`else
wire       tri_active = 1'b0;
`endif

`ifdef GPU_FEAT_TRIANGLE
// Vertex data (extracted from pay_buf in S_TRI_LOAD)
reg signed [15:0] v_x [0:2], v_y [0:2];       // 12.4 screen coords
reg        [15:0] v_z [0:2];                    // 16-bit depth
reg signed [15:0] v_s [0:2], v_t [0:2];        // tex coord integer part
reg        [7:0]  v_r [0:2];                    // light / vertex color
// Phase 4c.1 — perspective: v_w[i] is 1/W per vertex, Q16.16 fixed-
// point.  0x00010000 == 1.0 == affine.  When all three vertices have
// w == 0x00010000 we'll bypass the divide; otherwise the fragment
// stage divides interpolated (s*w, t*w) by interpolated w.  In 4c.1
// these are loaded but not yet consumed — the affine path is still
// the only live one.
reg signed [31:0] v_w [0:2];

// Phase 4c.2 — pre-multiplied texture coords for perspective.
// v_sw[i] = v_s[i] × v_w[i] in Q16.16, computed in S_TRI_PERSP_PREMUL
// before S_TRI_SETUP runs.  Linear-interpolating these in screen
// space (instead of the raw v_s/v_t) is what makes texture sampling
// perspective-correct: the SPAN_PERSP path then divides them back by
// interpolated w in the fragment stage.  Affine triangles (all v_w ==
// 0x10000) skip premul entirely — these regs go untouched.  Consumers
// (gradient loop, span emit) light up in 4c.3.
reg signed [31:0] v_sw [0:2];
reg signed [31:0] v_tw [0:2];

// Combinational: any vertex with non-unit w trips perspective.
// 0x00010000 == 1.0 in Q16.16 == affine.
wire tri_persp_active = (v_w[0] != 32'h00010000)
                      || (v_w[1] != 32'h00010000)
                      || (v_w[2] != 32'h00010000);

// Pre-multiply round counter for S_TRI_PERSP_PREMUL.  3-bit because
// each round is launch + wait + capture (matches S_TRI_INIT_ATTRIB).
reg [2:0] premul_step;

// Edge equation coefficients: E_i(x,y) = A_i*x + B_i*y + C_i
reg signed [31:0] tri_A [0:2], tri_B [0:2], tri_C [0:2];

// Attribute gradients (per sub-pixel step, fixed-point)
reg signed [31:0] grad_z_dx, grad_z_dy;
// Phase 4c.3 — when tri_persp_active, grad_s_dx/dy and grad_t_dx/dy
// hold gradients of (s*w) and (t*w) respectively (the perspective-
// corrected linearly-interpolatable form).  When affine, they hold
// gradients of s and t as before.  Span emit checks tri_persp_active
// to know which interpretation applies.
reg signed [31:0] grad_s_dx, grad_s_dy;
reg signed [31:0] grad_t_dx, grad_t_dy;
// New pair for the perspective-divide reciprocal gradient.  Only
// meaningful when tri_persp_active; unused on affine triangles
// (gradient loop skips computing them by exiting at grad_idx 5).
reg signed [31:0] grad_w_dx, grad_w_dy;
// Phase 4d — Gouraud.  Per-vertex `light` (the existing v_r byte)
// becomes a gradient instead of a constant: span emit writes
// sp_light_step alongside sp_light, fragment pipe walks it per-pixel.
// Gradients are 32-bit Q16.16 to match the rest of the loop's DSP
// schedule even though the underlying value is 8-bit.
reg signed [31:0] grad_r_dx, grad_r_dy;
// R gradient hard-wired to zero — Quake uses flat per-triangle light
// (sp_light = v0.r). Keeping the 32-bit interpolator would cost ~200 ALMs.

// Bounding box (integer pixel coords)
reg signed [15:0] tri_xmin, tri_xmax, tri_ymin, tri_ymax;
// Raw (pre-clamp) bbox in 12.4 subpixel space — registered in S_TRI_BBOX,
// consumed by S_TRI_BBOX_CLAMP. Splitting the bbox compute in two halves
// keeps each combinational chain shallow enough to hit the 10 ns target.
reg signed [15:0] tri_xmin_raw, tri_xmax_raw, tri_ymin_raw, tri_ymax_raw;

// Rasteriser current state
reg signed [15:0] tri_cur_x, tri_cur_y;
reg signed [31:0] tri_e [0:2];                  // edge function at current pixel
reg signed [31:0] tri_row_e [0:2];              // edge function at row start
reg signed [31:0] tri_z, tri_s, tri_t;          // interpolated attribs
// Phase 4c.3 — per-pixel walker for w (1/W).  Walked alongside
// tri_z/s/t in S_TRI_PIX, snapshot at first inside pixel, fed into
// the span's sp_zinv when persp_active.
reg signed [31:0] tri_w;
reg signed [31:0] tri_row_z, tri_row_s, tri_row_t;
// Phase 4c.3 — tri_row_w holds 1/W at the bbox origin (perspective
// only).  Initialised in S_TRI_INIT_ATTRIB's round-3 just like the
// others; advances per row in S_TRI_ROW_NEXT alongside z/s/t.
reg signed [31:0] tri_row_w;
// Phase 4d — Gouraud row-anchor and per-pixel walker for `light`.
// Q16.16 fixed-point so the DSP gradient + 8-bit-per-pixel-step
// arithmetic is uniform with the other attributes.  The fragment
// pipeline takes bits [23:16] (the integer part) as the 8-bit light.
reg signed [31:0] tri_row_r;
reg signed [31:0] tri_r;
// Bbox-origin attribute init (S_TRI_INIT_ATTRIB).  Pre-registered subpixel
// deltas from v0 to (xmin, ymin) — fed into the rolled DSP schedule.
reg signed [20:0] delta_x_subpix;
reg signed [20:0] delta_y_subpix;
// 4-bit so the loop can extend to a 4th round (w) when persp_active —
// affine triangles still complete at step 6 and transition out.
reg [3:0]         init_step;

// Span-emit scan state: captures the inside-triangle extent for the current
// scanline, then builds one CMD_DRAW_SPAN-equivalent sp_* setup and hands it
// to S_FRAG_PIPE. Convex triangles ⇒ the inside pixels are contiguous per row.
reg [15:0]        tri_span_x_start;
reg [15:0]        tri_span_count;
reg signed [31:0] tri_span_s_start;
reg signed [31:0] tri_span_t_start;
reg signed [31:0] tri_span_z_start;
// Phase 4c.3 — snapshot of tri_w at first inside pixel.  Used only on
// perspective triangles (sp_zinv source for the SPAN_PERSP path).
reg signed [31:0] tri_span_w_start;
// Phase 4d — snapshot of tri_r at first inside pixel.  Routed into
// the new sp_light_q at span emit so per-pixel light walks correctly
// from this anchor.
reg signed [31:0] tri_span_r_start;

// Pre-registered "|tri_det| < 16 or tri_det == 0" flag for setup_step 7.
// Captures at end of step 6 (same cycle tri_det updates from dsp_p + dsp2_p),
// reads directly from dsp_p/dsp2_p so the compare starts from the same
// combinational value tri_det is about to latch. Step 7 then only needs
// the single registered bit instead of synthesizing a 32-bit signed
// compare whose output feeds the tri_det-update mux (-1.606 ns cone on
// the 100 MHz domain — every bit of tri_det fed back into every bit).
reg tri_det_is_small_r;

// (CMD_DRAW_SPRITE was deleted — sprites now render as 2 triangles.
// That path supports arbitrary rotation, subpixel corners, and floor
// sprites for free, using ~540 fewer ALMs than the dedicated primitive.)

// Setup state
reg [6:0]  setup_step;
// Rolled gradient loop state (replaces setup steps 20-56)
// 4-bit so the loop reaches r gradients (idx 8/9) on top of the
// optional w pair (idx 6/7).  Mapping: idx[3:1] selects attribute
// (z/s/t/w/r), idx[0] selects the dx vs dy axis.
reg [3:0]  grad_idx;
reg [2:0]  grad_sub;          // 0..6: sub-cycle within current gradient
// (grad_partial removed — both cross-products now run in parallel on dsp/dsp2)

// Pre-registered `dsp_p >>> (29 - tri_clz)` for the gradient writeback.
// The DSP MAC output (Mult0~add_lh_hlmac_pl) → 64-bit variable barrel
// shift → grad_*_dx[*] register was the worst critical path (-1.128 ns
// on 100 MHz). Capturing the shifted result in its own register adds one
// sub-cycle per gradient (30 → 36 cycles total for the 6-gradient loop).
reg signed [63:0] dsp_p_shifted;

// Pre-registered gradient cross-product subtraction. The old flow drove
// `dsp_a <= grad_idx[0] ? (dsp2_p - dsp_p) : (dsp_p - dsp2_p)` directly in
// grad_sub=3'd2 — Quartus synthesized the 32-bit subtract + sign mux as
// a fabric cone from the DSP1 output register into dsp_a's next-DSP
// input (Mult1~add_lh_hlmac_pl[0][0] → dsp_a[20], -0.885 ns). Moving
// the subtraction into its own registered stage turns the dsp_a load
// into a simple reg-to-reg mux.
reg signed [31:0] grad_sub_r;
reg [31:0] tri_fb_row_addr;    // precomputed: st_fb_addr + cur_y * stride
reg [31:0] tri_zb_row_addr;    // precomputed: st_zb_addr + cur_y * zb_stride
reg signed [31:0] tri_det;
reg signed [15:0] tri_recip;  // scaled 1/|det| (fixed-point)
reg [5:0]  tri_clz;           // leading zeros of |det|
reg        tri_det_sign;       // 1 if det was negative

// Precomputed differences (used across setup steps)
reg signed [15:0] dX10, dY10, dX20, dY20;

// Triangle stats
reg [31:0] stat_triangles;

// ----------------------------------------------------------------
// Gradient loop operand selectors (S_TRI_GRAD)
// grad_idx[2:1]: 00=Z, 01=S, 10=T  (selects vertex attribute)
// grad_idx[0]:   0=dx (cross with Y axes),
//                1=dy (cross with X axes, with sign flip)
// ----------------------------------------------------------------
// Depth (v_z) is an UNSIGNED 16-bit value — the z-buffer compares
// (S_SPAN_ZWAIT) treat old_z/new_z as `reg [15:0]` unsigned.  Gradients
// used sign-extend ({{16{v_z[i][15]}}, v_z[i]}), which flipped direction
// on triangles whose min-z or max-z crossed 0x8000 — the delta came out
// as a large negative 32-bit number and the interpolator walked away
// from the true value instead of toward it.  Zero-extend (v_z only) to
// keep the 16→32 widen consistent with the unsigned compare.
// v_s / v_t are already signed texture coords, keep sign-extend.
// Phase 4c.3 / 4d — grad_idx[3:1] selects which attribute pair the
// rolled loop is computing this iteration:
//   000 = z   (always)
//   001 = s (affine) or s*w (persp)
//   010 = t (affine) or t*w (persp)
//   011 = w (persp only — skipped on affine via the exit condition
//             below; reaches here as idx 6/7)
//   100 = r (always — 8-bit light value, sign-extended/zero-extended
//             into the Q16.16 datapath; idx 8/9)
//
// v_sw / v_tw are already Q16.16 (filled in S_TRI_PERSP_PREMUL); v_s /
// v_t are 16-bit integer texcoords sign-extended to 32-bit.  v_z stays
// 16-bit unsigned; v_r 8-bit zero-extended into Q16.16 (so a delta of
// 1 unit = 0x10000 ≈ a full per-pixel light step).
wire signed [31:0] grad_dV10 =
    (grad_idx[3:1] == 3'd0) ? ({16'b0, v_z[1]} - {16'b0, v_z[0]}) :
    (grad_idx[3:1] == 3'd1) ? (tri_persp_active
                                 ? (v_sw[1] - v_sw[0])
                                 : ({{16{v_s[1][15]}}, v_s[1]}
                                  - {{16{v_s[0][15]}}, v_s[0]})) :
    (grad_idx[3:1] == 3'd2) ? (tri_persp_active
                                 ? (v_tw[1] - v_tw[0])
                                 : ({{16{v_t[1][15]}}, v_t[1]}
                                  - {{16{v_t[0][15]}}, v_t[0]})) :
    (grad_idx[3:1] == 3'd3) ? (v_w[1] - v_w[0]) :
                              ({24'b0, v_r[1]} - {24'b0, v_r[0]});
wire signed [31:0] grad_dV20 =
    (grad_idx[3:1] == 3'd0) ? ({16'b0, v_z[2]} - {16'b0, v_z[0]}) :
    (grad_idx[3:1] == 3'd1) ? (tri_persp_active
                                 ? (v_sw[2] - v_sw[0])
                                 : ({{16{v_s[2][15]}}, v_s[2]}
                                  - {{16{v_s[0][15]}}, v_s[0]})) :
    (grad_idx[3:1] == 3'd2) ? (tri_persp_active
                                 ? (v_tw[2] - v_tw[0])
                                 : ({{16{v_t[2][15]}}, v_t[2]}
                                  - {{16{v_t[0][15]}}, v_t[0]})) :
    (grad_idx[3:1] == 3'd3) ? (v_w[2] - v_w[0]) :
                              ({24'b0, v_r[2]} - {24'b0, v_r[0]});
wire signed [31:0] grad_axis_b1 = grad_idx[0] ? {{16{dX20[15]}}, dX20}
                                              : {{16{dY20[15]}}, dY20};
wire signed [31:0] grad_axis_b2 = grad_idx[0] ? {{16{dX10[15]}}, dX10}
                                              : {{16{dY10[15]}}, dY10};
`endif // GPU_FEAT_TRIANGLE

// ================================================================
// Texture Address Computation — 2-stage pipeline (DSP-friendly)
// ================================================================
// Stage 1 (S_SPAN_PIXEL): register multiply INPUTS for DSP inference
// Stage 2 (S_SPAN_TEX_CALC): DSP multiply + add, submit to cache
//
// Multiply mode (tex_width > 0): addr = base + (t>>16)*width + (s>>16)
// Shift mode (tex_width == 0):   addr = base + ((t>>shift)<<bits) | (s>>(32-bits))

wire [15:0] sp_s_int = sp_s[31:16];
wire [15:0] sp_t_int = sp_t[31:16];
// tex_pipe_* / tex_mul_result / tex_addr_final removed along with the
// sequential S_SPAN_* path they drove.  The pipelined fragment
// processor uses tx_mul_q (the dedicated DSP-inferred register below)
// for the per-pixel tex-coord multiply.

// ================================================================
// Main FSM body
// ================================================================
always @(posedge clk) begin
    if (!reset_n) begin
        state <= S_IDLE;
        ring_rdptr <= 0;
        m_wr_awvalid <= 0;
        m_wr_wvalid <= 0;
        sram_rd <= 0;
        sram_wr <= 0;
        fb_acc_valid <= 0;
        fb_acc_mask <= 0;
        fence_reached <= 0;
        stat_pixels <= 0;
        stat_spans <= 0;
        cmd_type <= 0;
        cmd_payload_words <= 0;
        cmd_is_nop <= 0; cmd_is_fence <= 0; cmd_is_clear <= 0;
        cmd_is_set_texture <= 0; cmd_is_set_depth_func <= 0;
        cmd_is_set_fb <= 0; cmd_is_set_zb <= 0;
        cmd_is_draw_span <= 0;
        cmd_is_set_skip_zero <= 0;
        st_skip_zero <= 0;
`ifdef GPU_FEAT_TRIANGLE
        cmd_is_draw_triangles <= 0;
`endif
        pay_idx <= 0;
        pay_remaining <= 0;
        frag_discard <= 0;
        clear_flags <= 0;
`ifdef GPU_FEAT_FRAG_PIPELINE
        // Pipelined fragment processor reset
        p0_valid <= 0; p0_light <= 0; p0_flags <= 0;
        p0_fb_addr <= 0; p0_z_addr <= 0; p0_zi <= 0;
        p0_s_int <= 0; p0_tex_base <= 0;
        tx_mul_q <= 0;
        p1_valid <= 0; p1_light <= 0; p1_flags <= 0;
        p1_fb_addr <= 0; p1_z_addr <= 0; p1_zi <= 0;
        p2_valid <= 0; p2_color <= 0; p2_light <= 0; p2_flags <= 0;
        p2_fb_addr <= 0; p2_z_addr <= 0; p2_zi <= 0; p2_discard <= 0;
        p2b_valid <= 0; p2b_color <= 0; p2b_flags <= 0;
        p2b_fb_addr <= 0; p2b_z_addr <= 0; p2b_zi <= 0; p2b_discard <= 0;
        p3_valid <= 0; p3_color <= 0; p3_flags <= 0;
        p3_fb_addr <= 0; p3_z_addr <= 0; p3_zi <= 0; p3_discard <= 0;
        p3_z_resolved <= 0;
        transluc_rd_addr <= 15'b0;
        fbss <= FBSS_IDLE;
        fbss_pend_valid <= 0; fbss_pend_color <= 0; fbss_pend_addr <= 0;
        blend_arvalid    <= 0;
        blend_araddr     <= 0;
        blend_src_color  <= 0;
        blend_word_addr  <= 0;
        blend_byte_lane  <= 0;
        blend_p3_flags   <= 0;
        blend_fb_word    <= 0;
        src_mode <= SRC_SPAN;
        src_done <= 0;
`ifdef GPU_PERSP_IMPL
        sp_sZ <= 0; sp_tZ <= 0; sp_zinv <= 0;
        sp_sZstep <= 0; sp_tZstep <= 0; sp_zinv_step <= 0;
        persp_active <= 0;
        sp_seg_left <= 0;
        persp_seg_a_ready <= 0;
        persp_anchor_s <= 0; persp_anchor_t <= 0;
        persp_first_done <= 0;
        persp_pend_s <= 0; persp_pend_t <= 0;
        persp_pend_sstep <= 0; persp_pend_tstep <= 0;
        persp_seg_b_ready <= 0;
        persp_pss <= PSS_IDLE;
        persp_pass <= PSS_PASS_ANCHOR;
        persp_zinv_abs_r <= 0;
        persp_clz <= 0;
        nr_two_minus_xy <= 0;
`endif
`endif
`ifdef GPU_HAS_RECIP_LUT
        dsp_a <= 0; dsp_b <= 0;
        dsp2_a <= 0; dsp2_b <= 0;
        recip_rd_addr <= 0;
`endif
`ifdef GPU_FEAT_TRIANGLE
        dsp3_a <= 0; dsp3_b <= 0;
        tri_active <= 0;
        setup_step <= 0;
        grad_idx <= 0;
        grad_sub <= 0;
        init_step <= 0;
        delta_x_subpix <= 0;
        delta_y_subpix <= 0;
        premul_step <= 0;
        tri_det <= 0;
        tri_recip <= 0;
        tri_clz <= 0;
        tri_det_sign <= 0;
        stat_triangles <= 0;
        tri_span_x_start <= 0;
        tri_span_count <= 0;
        tri_span_s_start <= 0;
        tri_span_t_start <= 0;
        tri_span_z_start <= 0;
        tri_span_w_start <= 0;
        tri_span_r_start <= 0;
        // Phase 4c.3 — perspective-walk regs.  Don't-care on affine
        // triangles, but need a deterministic reset value so post-
        // reset assertions stay clean.
        tri_w <= 0;
        tri_row_w <= 0;
        grad_w_dx <= 0; grad_w_dy <= 0;
        // Phase 4d — Gouraud regs.
        tri_r <= 0;
        tri_row_r <= 0;
        grad_r_dx <= 0; grad_r_dy <= 0;
        tri_xmin_raw <= 0; tri_xmax_raw <= 0;
        tri_ymin_raw <= 0; tri_ymax_raw <= 0;
`endif
        // State registers
        st_tex_addr <= 0; st_tex_width <= 0;
        sp_tex_w_mask <= 16'hFFFF; sp_tex_h_mask <= 16'hFFFF;
        st_depth_func <= 0;
        st_fb_addr <= 0; st_fb_stride <= 320;
        st_zb_addr <= 0; st_zb_stride <= 640;
    end else begin
        // Default: deassert one-shot signals
        sram_rd <= 0;
        sram_wr <= 0;

        // Ring reset: set rdptr to 0 (from MMIO ring_size write)
        if (ring_reset)
            ring_rdptr <= 0;

        // Soft reset: return FSM to idle, deassert all bus signals
        if (soft_reset) begin
            state        <= S_IDLE;
            ring_rdptr   <= ring_wrptr;
            m_wr_awvalid <= 0;
            m_wr_wvalid  <= 0;
            fb_acc_valid <= 0;
            fb_acc_mask  <= 0;
        end else
        case (state)

        // ============================================================
        // IDLE — wait for commands in ring BRAM
        // ============================================================
        S_IDLE: begin
            if (active && !ring_empty) begin
                // BRAM read initiated by ring_rdptr (port B, 1-cycle latency)
                // Advance rdptr; data available next cycle in ring_rd_data
                ring_rdptr <= (ring_rdptr + 16'd4) & ring_mask;
                state      <= S_RING_WAIT;
            end
        end

        // ============================================================
        // Ring read — wait 1 cycle for BRAM read latency
        // ============================================================
        S_RING_WAIT: begin
            // ring_rd_data now contains the header word
            cmd_type          <= ring_rd_data[31:24];
            cmd_payload_words <= ring_rd_data[23:0];
            state             <= S_DECODE;
        end

        // ============================================================
        // Decode — start reading payload words from BRAM
        // ============================================================
        S_DECODE: begin
            // Pre-decode cmd_type into one-hot dispatch flags. The case
            // expression in S_EXECUTE used to compare cmd_type directly,
            // which built a long combinational chain into the per-command
            // state-reg writes (notably sp_tstep). Doing the decode here
            // shortens the S_EXECUTE path to a 1-bit flag check.
            cmd_is_nop            <= (cmd_type == CMD_NOP);
            cmd_is_fence          <= (cmd_type == CMD_FENCE);
            cmd_is_clear          <= (cmd_type == CMD_CLEAR);
            cmd_is_set_texture    <= (cmd_type == CMD_SET_TEXTURE);
            cmd_is_set_depth_func <= (cmd_type == CMD_SET_DEPTH_FUNC);
            cmd_is_set_fb         <= (cmd_type == CMD_SET_FB);
            cmd_is_set_zb         <= (cmd_type == CMD_SET_ZB);
            cmd_is_draw_span      <= (cmd_type == CMD_DRAW_SPAN);
            // CMD_DRAW_SPANS removed (was half-implemented dead code)
            cmd_is_set_skip_zero  <= (cmd_type == CMD_SET_SKIP_ZERO);
`ifdef GPU_FEAT_TRIANGLE
            cmd_is_draw_triangles <= (cmd_type == CMD_DRAW_TRIANGLES);
`endif

            if (cmd_payload_words == 0) begin
                state <= S_EXECUTE;
            end else begin
                pay_idx <= 0;
                // Track the FULL payload count so we drain every word out
                // of the ring — stores into pay_buf are separately capped
                // at index 19 in S_PAY_DATA.  This keeps ring_rdptr aligned
                // even when a command (e.g. a multi-triangle draw) exceeds
                // the 19-entry pay_buf capacity.
                pay_remaining <= cmd_payload_words;
                // Start first BRAM read (data arrives next cycle)
                ring_rdptr <= (ring_rdptr + 16'd4) & ring_mask;
                state <= S_PAY_DATA;
            end
        end

        // ============================================================
        // Payload — stream words directly to destination regs
        // ============================================================
        // No intermediate pay_buf.  Each cycle, ring_rd_data holds the
        // current payload word (advanced by the 1-cycle BRAM read); we
        // route it straight to the right state reg based on the
        // registered command type and the payload-word index pay_idx.
        // Destinations live in regs that already exist (sp_*, st_*,
        // v_*, etc.), so the only storage cost is the 5-bit pay_idx
        // counter and pay_remaining — we save the 19×32 pay_buf array.
        //
        // pay_idx saturates at 19: any payload word past index 19
        // (e.g. padding at the end of an oversized DRAW_TRIANGLES batch)
        // is still drained from the ring so ring_rdptr ends up at the
        // next command header, but has no destination reg to write.
        S_PAY_DATA: begin
            if (pay_idx != 5'd19)
                pay_idx <= pay_idx + 5'd1;
            pay_remaining <= pay_remaining - 24'd1;

            // Per-command dispatch.  The if-else chain mirrors the pre-
            // decoded cmd_is_* one-hot flags set in S_DECODE — keeps the
            // combinational cone to each destination reg short.
            if (cmd_is_fence) begin
                if (pay_idx == 5'd0) fence_reached <= ring_rd_data;
            end
            else if (cmd_is_clear) begin
                if (pay_idx == 5'd0) begin
                    clear_flags <= ring_rd_data[17:16];
                    clear_color <= ring_rd_data[15:0];
                end else if (pay_idx == 5'd1) begin
                    clear_depth <= ring_rd_data[15:0];
                end
            end
            else if (cmd_is_set_texture) begin
                if (pay_idx == 5'd0) st_tex_addr  <= ring_rd_data;
                else if (pay_idx == 5'd1) st_tex_width <= ring_rd_data[31:16];
                // tex_height / format / wrap_s / wrap_t payload fields
                // ignored — the datapath is I8-only, no wrap logic.  The
                // firmware still emits them so the on-ring layout is
                // stable across core revisions.
            end
            else if (cmd_is_set_depth_func) begin
                if (pay_idx == 5'd0) st_depth_func <= ring_rd_data[2:0];
            end
            else if (cmd_is_set_fb) begin
                if (pay_idx == 5'd0) st_fb_addr   <= ring_rd_data;
                else if (pay_idx == 5'd1) st_fb_stride <= ring_rd_data[15:0];
            end
            else if (cmd_is_set_zb) begin
                if (pay_idx == 5'd0) st_zb_addr   <= ring_rd_data;
                else if (pay_idx == 5'd1) st_zb_stride <= ring_rd_data[15:0];
            end
            else if (cmd_is_set_skip_zero) begin
                if (pay_idx == 5'd0) st_skip_zero <= ring_rd_data[0];
            end
            else if (cmd_is_draw_span) begin
                case (pay_idx)
                    5'd0: sp_fb_addr   <= ring_rd_data;
                    5'd1: sp_tex_addr  <= ring_rd_data;
                    5'd2: sp_s         <= ring_rd_data;
                    5'd3: sp_t         <= ring_rd_data;
                    5'd4: sp_sstep     <= ring_rd_data;
                    5'd5: sp_tstep     <= ring_rd_data;
                    5'd6: begin
                        sp_count <= ring_rd_data[31:16];
                        // 8-bit flat light → Q16.16 (high byte clear,
                        // low 16 bits zero; per-pixel step = 0).
                        sp_light_q    <= {8'b0, ring_rd_data[15:8], 16'b0};
                        sp_light_step <= 32'b0;
                        sp_flags <= ring_rd_data[7:0];
                    end
                    5'd7: begin
                        sp_fb_stride <= ring_rd_data[31:16];
                        sp_tex_width <= ring_rd_data[15:0];
                    end
                    5'd8: begin
                        // POT wrap masks: low 16 = tex_w_mask (S),
                        // high 16 = tex_h_mask (T).  Set to tex_w-1
                        // / tex_h-1 to enable wrap; mask=0 means "no
                        // wrap" (decoded to 0xFFFF — keeps backward
                        // compat with legacy callers that wrote 0 to
                        // the formerly-reserved word 8).
                        sp_tex_w_mask <= (ring_rd_data[15:0]  == 16'd0)
                                         ? 16'hFFFF : ring_rd_data[15:0];
                        sp_tex_h_mask <= (ring_rd_data[31:16] == 16'd0)
                                         ? 16'hFFFF : ring_rd_data[31:16];
                    end
                    5'd9:  sp_z_addr     <= ring_rd_data;
                    5'd10: sp_zi         <= ring_rd_data;
                    5'd11: sp_zistep     <= ring_rd_data;
`ifdef GPU_PERSP_IMPL
                    5'd12: sp_sZ         <= ring_rd_data;
                    5'd13: sp_tZ         <= ring_rd_data;
                    5'd14: sp_zinv       <= ring_rd_data;
                    5'd15: sp_sZstep     <= ring_rd_data;
                    5'd16: sp_tZstep     <= ring_rd_data;
                    5'd17: sp_zinv_step  <= ring_rd_data;
`endif
                    default: ;
                endcase
            end
`ifdef GPU_FEAT_TRIANGLE
            else if (cmd_is_draw_triangles) begin
                // pay_idx 0 = vertex count (ignored; must be 3).
                // Vertex layout: 6 words each, packed as
                //   word 0: {x, y} (12.4 subpixel each)
                //   word 1: {z,  --}  (16-bit unsigned depth in [31:16])
                //   word 2: {s,  --}  (sign-extended tex s)
                //   word 3: {t,  --}  (sign-extended tex t)
                //   word 4: w (1/W in Q16.16, 0x00010000 == affine)
                //   word 5: {--, --, --, r}  (flat light in low byte)
                case (pay_idx)
                    5'd1: begin v_x[0] <= ring_rd_data[31:16]; v_y[0] <= ring_rd_data[15:0]; end
                    5'd2: v_z[0] <= ring_rd_data[31:16];
                    5'd3: v_s[0] <= ring_rd_data[31:16];
                    5'd4: v_t[0] <= ring_rd_data[31:16];
                    5'd5: v_w[0] <= ring_rd_data;
                    5'd6: v_r[0] <= ring_rd_data[7:0];
                    5'd7: begin v_x[1] <= ring_rd_data[31:16]; v_y[1] <= ring_rd_data[15:0]; end
                    5'd8: v_z[1] <= ring_rd_data[31:16];
                    5'd9: v_s[1] <= ring_rd_data[31:16];
                    5'd10: v_t[1] <= ring_rd_data[31:16];
                    5'd11: v_w[1] <= ring_rd_data;
                    5'd12: v_r[1] <= ring_rd_data[7:0];
                    5'd13: begin v_x[2] <= ring_rd_data[31:16]; v_y[2] <= ring_rd_data[15:0]; end
                    5'd14: v_z[2] <= ring_rd_data[31:16];
                    5'd15: v_s[2] <= ring_rd_data[31:16];
                    5'd16: v_t[2] <= ring_rd_data[31:16];
                    5'd17: v_w[2] <= ring_rd_data;
                    5'd18: v_r[2] <= ring_rd_data[7:0];
                    default: ;
                endcase
            end
`endif

            if (pay_remaining <= 24'd1) begin
                state <= S_EXECUTE;
            end
`ifdef GPU_FEAT_TRIANGLE
            // Multi-triangle batch: end of this triangle's 18 vertex
            // words (pay_idx=18 captured v_r[2]) and pay_remaining > 1
            // means more triangles follow.  Kick this triangle now and
            // hold ring_rdptr at the next triangle's v0 word 0 — BRAM
            // re-reads that same address every cycle the FSM sits on
            // it, so the value is primed for the re-entry at pay_idx=1
            // (which is timed to read it via the standard 1-cycle BRAM
            // latency: each "triangle-done mid-batch" exit advances
            // ring_rdptr by 4 to consume v0_word_0 on the same posedge
            // as the state transition back to S_PAY_DATA).
            else if (cmd_is_draw_triangles && pay_idx == 5'd18) begin
                state <= S_EXECUTE;
            end
`endif
            else begin
                // Advance rdptr for next word (BRAM read, 1-cycle latency)
                ring_rdptr <= (ring_rdptr + 16'd4) & ring_mask;
            end
        end

        // ============================================================
        // Execute command — dispatch to action state
        // ============================================================
        // Payload is already in its destination state regs by the time
        // we land here, so S_EXECUTE just picks the right action state
        // (or returns to S_IDLE for fire-and-forget commands).  Any
        // per-command setup that depends on payload values (e.g. the
        // perspective sub-FSM bring-up from sp_flags[SPAN_PERSP]) runs
        // here, reading the regs S_PAY_DATA just wrote.
        S_EXECUTE: begin
            if (cmd_is_nop || cmd_is_fence
                || cmd_is_set_texture || cmd_is_set_depth_func
                || cmd_is_set_fb || cmd_is_set_zb
                || cmd_is_set_skip_zero) begin
                state <= S_IDLE;
            end
            else if (cmd_is_clear) begin
                state <= S_CLEAR_INIT;
            end
            else if (cmd_is_draw_span) begin
`ifdef GPU_STATS
                stat_spans   <= stat_spans + 32'd1;
`endif
`ifdef GPU_PERSP_IMPL
                // sp_flags holds the flag byte written at pay_idx=6; its
                // SPAN_PERSP bit arms the perspective sub-FSM.
                persp_active      <= sp_flags[SPAN_PERSP];
                persp_first_done  <= 0;
                persp_seg_a_ready <= 0;
                persp_seg_b_ready <= 0;
                persp_pss         <= PSS_IDLE;
                persp_pass        <= PSS_PASS_ANCHOR;
                sp_seg_left       <= 0;
`endif
`ifdef GPU_FEAT_FRAG_PIPELINE
                src_mode     <= SRC_SPAN;
                src_done     <= 0;
                state        <= S_FRAG_PIPE;
`else
                state        <= S_SPAN_PIXEL;
`endif
            end
`ifdef GPU_FEAT_TRIANGLE
            else if (cmd_is_draw_triangles) begin
                // Vertices already loaded into v_*[] in S_PAY_DATA;
                // S_TRI_LOAD used to do the load in a separate cycle
                // but is now a pass-through state kept only for
                // schedule compatibility (setup_step reset).
                state <= S_TRI_LOAD;
            end
`endif
            else state <= S_IDLE;
        end

        // ============================================================
        // SPAN pixel loop

`ifdef GPU_FEAT_FRAG_PIPELINE
        // ============================================================
        // Pipelined Fragment Processor
        // ============================================================
        // Replaces the sequential S_SPAN_PIXEL..S_SPAN_STEP chain with a
        // 3-stage pipeline + a tail FB write sub-FSM:
        //   S0  combinational issue from sp_* (tex_req_* are wires)
        //   p1  metadata in flight, awaiting tex_resp from cache
        //   p2  tex color captured, cmap_rd_addr issued (if cmap)
        //   p3  cmap result merged, ready for fb_acc write
        //
        // S0 issue commits when the cache accepts the request this cycle
        // (combinational `tex_req_valid && tex_req_ready`). On commitment
        // we latch p1 with sp_*'s metadata and advance the source state.
        S_FRAG_PIPE: begin : frag_pipe_blk
            reg span_last_issue;
            reg [31:0] p3_word_addr;
            reg [1:0]  p3_byte_lane;
            reg        p3_word_match;
            reg        issue_committed;
            reg        load_p0;

            // Was the (combinational) tex_req accepted by the cache this
            // same cycle? Both signals are visible NOW.
            issue_committed = tex_req_valid && tex_req_ready;
            // Load p0 with the next pixel when: we just committed (so the
            // current p0 is shifting out), OR p0 is empty (priming).
            // Persp gating:
            //   * !persp_issue_stall: slot A must be loaded (pass 2 done).
            //   * If sp_seg_left == 0 (last px of segment), slot B must be
            //     ready so the swap can fire in the same cycle.
            load_p0         = (issue_committed || !p0_valid)
                           && (sp_count != 16'd0) && !src_done
`ifdef GPU_PERSP_IMPL
                           && !persp_issue_stall
                           && (!persp_active
                               || sp_seg_left != 4'd0
                               || persp_seg_b_ready)
`endif
                           ;
            span_last_issue = (sp_count == 16'd1);

            // ----------------------------------------------------------
            // Pipeline shift — only when not stalled
            // ----------------------------------------------------------
            if (!fp_pipe_stall) begin
                // p3 <- p2b  (merges cmap result if cmap was used; cmap_rd_data
                // is now valid because p2b gave us 1 extra cycle of latency)
                p3_valid     <= p2b_valid;
                p3_color     <= p2b_flags[SPAN_COLORMAP] ? cmap_rd_data : p2b_color;
                p3_flags     <= p2b_flags;
                p3_fb_addr   <= p2b_fb_addr;
                p3_z_addr    <= p2b_z_addr;
                p3_zi        <= p2b_zi;
                p3_discard   <= p2b_discard;
                p3_z_resolved <= 0;  // fresh pixel; FBSS must resolve depth if flagged

                // p2b <- p2  (no-op shift, gives cmap BRAM time to read)
                p2b_valid   <= p2_valid;
                p2b_color   <= p2_color;
                p2b_flags   <= p2_flags;
                p2b_fb_addr <= p2_fb_addr;
                p2b_z_addr  <= p2_z_addr;
                p2b_zi      <= p2_zi;
                p2b_discard <= p2_discard;

                // p2 <- p1  (captures tex_resp; issues cmap_rd_addr if needed)
                p2_valid   <= p1_valid;
                if (p1_valid) begin
                    p2_color   <= tex_resp_data[7:0];
                    p2_light   <= p1_light;
                    p2_flags   <= p1_flags;
                    p2_fb_addr <= p1_fb_addr;
                    p2_z_addr  <= p1_z_addr;
                    p2_zi      <= p1_zi;
                    p2_discard <= p1_flags[SPAN_SKIP_ZERO]
                               && (tex_resp_data[7:0] == 8'hFF);
                    if (p1_flags[SPAN_COLORMAP])
                        cmap_rd_addr <= {p1_light[5:0], tex_resp_data[7:0]};
                end

                // p1 <- p0 (issue commit). Cache accepted our request this
                // cycle. The OLD p0 metadata becomes p1 (the in-cache pixel).
                if (issue_committed) begin
                    p1_valid   <= 1;
                    p1_light   <= p0_light;
                    p1_flags   <= p0_flags;
                    p1_fb_addr <= p0_fb_addr;
                    p1_z_addr  <= p0_z_addr;
                    p1_zi      <= p0_zi;
                end else begin
                    p1_valid <= 0;
                end

                // p0 <- sp_* (pre-issue load + DSP fire). Happens when we
                // just committed (p0 needs a new pixel) or p0 was empty.
                if (load_p0) begin
                    p0_valid     <= 1;
                    p0_light     <= sp_light;
                    p0_flags     <= sp_flags;
                    p0_fb_addr   <= sp_fb_addr;
                    p0_z_addr    <= sp_z_addr;
                    p0_zi        <= sp_zi;
                    p0_s_int     <= sp_s[31:16] & sp_tex_w_mask;
                    p0_tex_base  <= sp_tex_addr;
                    // p0_mode / p0_shift_addr removed — multiply-mode
                    // tex addressing (t * tex_width + s) is universal.
                    // POT wrap: mask sp_s_int / sp_t_int with sp_tex_*_mask.
                    // Default mask 16'hFFFF is a no-op (legacy callers).

                    // DSP-pipelined multiply: registered output. The DSP
                    // slice will be inferred via the (* multstyle = "dsp" *)
                    // attribute on tx_mul_q's declaration.
                    tx_mul_q <= $signed({1'b0, sp_t[31:16] & sp_tex_h_mask})
                              * $signed({1'b0, sp_tex_width});

                    // Advance span source. For perspective spans, the s/t
                    // path is segmented: within a segment we step by the
                    // affine sub-slope (sp_sstep), and at the segment
                    // boundary (sp_seg_left == 0) we swap in the pre-
                    // computed slot B (persp_pend_*).
                    sp_fb_addr <= sp_fb_addr + {{16{sp_fb_stride[15]}}, sp_fb_stride};
                    sp_zi      <= sp_zi + sp_zistep;
                    sp_z_addr  <= sp_z_addr + 32'd2;
                    sp_count   <= sp_count - 16'd1;
                    // Phase 4d — per-pixel light step.  Direct
                    // CMD_DRAW_SPAN payloads set sp_light_step = 0
                    // (flat lighting unchanged); triangle Gouraud
                    // spans walk through the gradient per pixel.
                    sp_light_q <= sp_light_q + sp_light_step;
                    if (span_last_issue) src_done <= 1;
`ifdef GPU_PERSP_IMPL
                    if (persp_active) begin
                        if (sp_seg_left == 4'd0) begin
                            // Segment boundary — swap pending into current.
                            sp_s              <= persp_pend_s;
                            sp_t              <= persp_pend_t;
                            sp_sstep          <= persp_pend_sstep;
                            sp_tstep          <= persp_pend_tstep;
                            sp_seg_left       <= 4'd7;  // 8-pixel segments
                            persp_seg_b_ready <= 0;  // PSS will refill
                        end else begin
                            sp_s        <= sp_s + sp_sstep;
                            sp_t        <= sp_t + sp_tstep;
                            sp_seg_left <= sp_seg_left - 4'd1;
                        end
                    end else begin
                        sp_s <= sp_s + sp_sstep;
                        sp_t <= sp_t + sp_tstep;
                    end
`else
                    sp_s <= sp_s + sp_sstep;
                    sp_t <= sp_t + sp_tstep;
`endif
                end else if (issue_committed) begin
                    // Committed but no more pixels to load — drain p0
                    p0_valid <= 0;
                end
            end

            // ----------------------------------------------------------
            // FB sub-FSM — consumes p3 (drains pipeline tail)
            // ----------------------------------------------------------
            case (fbss)
                FBSS_IDLE: begin
                    // Depth detour: if the pixel needs a z-test/write and we
                    // haven't resolved it yet, peel off into FBSS_ZREAD.
                    // fp_pipe_stall's `fbss_depth_entry` term holds p3 stable.
                    if (p3_valid && !p3_discard
                        && (p3_flags[SPAN_DEPTH_TEST] || p3_flags[SPAN_DEPTH_WRITE])
                        && !p3_z_resolved) begin
                        fbss <= FBSS_ZREAD;
                    end
                    // Translucent detour: if SPAN_TRANSLUC is set, capture p3
                    // state and run the read-modify-write blend flow.  The
                    // blend result is written to fb_acc by FBSS_BLEND_APPLY,
                    // so we deliberately do NOT touch fb_acc here.  fb_acc
                    // state is frozen for the duration of the blend (fbss !=
                    // IDLE keeps fp_pipe_stall asserted, so no new fragment
                    // can advance to p3 and clobber it).
                    else if (p3_valid && !p3_discard && p3_flags[SPAN_TRANSLUC]) begin
                        blend_src_color <= p3_color;
                        blend_word_addr <= p3_fb_addr & 32'hFFFFFFFC;
                        blend_byte_lane <= p3_fb_addr[1:0];
                        blend_p3_flags  <= p3_flags;
                        fbss            <= FBSS_BLEND_REQ;
                        if (fp_pipe_stall) p3_valid <= 0;
                    end
                    // Process p3 if it has a non-discard pixel (and no pending depth work)
                    else if (p3_valid && !p3_discard) begin : fb_acc_blk
                        p3_word_addr  = p3_fb_addr & 32'hFFFFFFFC;
                        p3_byte_lane  = p3_fb_addr[1:0];
                        p3_word_match = (fb_acc_valid && fb_acc_addr == p3_word_addr)
                                     || !fb_acc_valid;

                        if (!p3_word_match) begin
                            // Word boundary cross — flush old word, queue new pixel
                            m_wr_awvalid <= 1;
                            m_wr_awaddr  <= fb_acc_addr;
                            m_wr_awlen   <= 0;
                            m_wr_wvalid  <= 1;
                            m_wr_wdata   <= fb_acc_data;
                            m_wr_wstrb   <= fb_acc_mask;
                            m_wr_wlast   <= 1;

                            // Queue p3's pixel for re-application post-flush
                            fbss_pend_valid <= 1;
                            fbss_pend_color <= p3_color;
                            fbss_pend_addr  <= p3_fb_addr;

                            // Reset accumulator (will be re-armed in FLUSH_W_RSP)
                            fb_acc_valid <= 0;
                            fb_acc_mask  <= 4'b0;

                            fbss <= FBSS_FLUSH_W_RSP;
                        end else begin
                            // Fast path: accumulate into current word
`ifdef GPU_STATS
                            stat_pixels  <= stat_pixels + 32'd1;
`endif
                            fb_acc_valid <= 1;
                            fb_acc_addr  <= p3_word_addr;
                            case (p3_byte_lane)
                                2'd0: begin fb_acc_data[7:0]   <= p3_color; fb_acc_mask[0] <= 1; end
                                2'd1: begin fb_acc_data[15:8]  <= p3_color; fb_acc_mask[1] <= 1; end
                                2'd2: begin fb_acc_data[23:16] <= p3_color; fb_acc_mask[2] <= 1; end
                                2'd3: begin fb_acc_data[31:24] <= p3_color; fb_acc_mask[3] <= 1; end
                            endcase
                        end

                        // If the pipeline is stalled this cycle (cache miss
                        // upstream), the shift won't fire to overwrite p3.
                        // Mark p3 consumed so the next FBSS_IDLE doesn't
                        // re-process the same pixel.
                        if (fp_pipe_stall) p3_valid <= 0;
                    end
                end

                FBSS_FLUSH_W_RSP: begin
                    // Drive AW/W until accepted
                    if (m_wr_awvalid && m_wr_awready) m_wr_awvalid <= 0;
                    if (m_wr_wvalid  && m_wr_wready ) m_wr_wvalid  <= 0;

                    // B response — flush done, apply pending pixel
                    if (m_wr_bvalid) begin
                        m_wr_awvalid <= 0;
                        m_wr_wvalid  <= 0;

                        if (fbss_pend_valid) begin : pend_apply
                            reg [31:0] pw_addr;
                            reg [1:0]  pw_lane;
                            pw_addr = fbss_pend_addr & 32'hFFFFFFFC;
                            pw_lane = fbss_pend_addr[1:0];

`ifdef GPU_STATS
                            stat_pixels  <= stat_pixels + 32'd1;
`endif
                            fb_acc_valid <= 1;
                            fb_acc_addr  <= pw_addr;
                            fb_acc_data  <= 32'b0;
                            fb_acc_mask  <= 4'b0;
                            case (pw_lane)
                                2'd0: begin fb_acc_data[7:0]   <= fbss_pend_color; fb_acc_mask[0] <= 1; end
                                2'd1: begin fb_acc_data[15:8]  <= fbss_pend_color; fb_acc_mask[1] <= 1; end
                                2'd2: begin fb_acc_data[23:16] <= fbss_pend_color; fb_acc_mask[2] <= 1; end
                                2'd3: begin fb_acc_data[31:24] <= fbss_pend_color; fb_acc_mask[3] <= 1; end
                            endcase

                            fbss_pend_valid <= 0;
                        end

                        // Don't touch p3_valid: in the typical case the
                        // pipeline shifted to a NEW pixel in the same cycle
                        // we triggered the flush, and that new pixel is now
                        // sitting in p3 waiting to be processed. The
                        // FBSS_IDLE branch handles "did we already process
                        // this p3?" via the conditional clear below.
                        fbss <= FBSS_IDLE;
                    end
                end

                // --------------------------------------------------------
                // Depth test/write detour — serialised on the shared Z SRAM.
                // p3 is held stable via fbss != FBSS_IDLE stalling the pipe.
                // --------------------------------------------------------
                FBSS_ZREAD: begin
                    if (!sram_busy) begin
                        sram_rd   <= 1;
                        sram_addr <= p3_z_addr[23:2];  // byte → word
                        fbss      <= FBSS_ZWAIT;
                    end
                end

                FBSS_ZWAIT: begin
                    // Compare + z-write launch happen in the same cycle we see
                    // the read response. Saves one state (FBSS_ZWRITE merged
                    // here) and one cycle on depth-tested pixels.
                    if (sram_rdata_valid) begin : fbss_z_cmp
                        reg [15:0] old_z;
                        reg [15:0] new_z;
                        reg        pass;
                        old_z = p3_z_addr[1] ? sram_rdata[31:16] : sram_rdata[15:0];
                        new_z = p3_zi[31:16];
                        case (st_depth_func)
                            3'd1: pass = 1'b1;                  // ALWAYS
                            3'd2: pass = (new_z <  old_z);      // LESS
                            3'd3: pass = (new_z <= old_z);      // LEQUAL
                            3'd4: pass = (new_z == old_z);      // EQUAL
                            3'd5: pass = (new_z >= old_z);      // GEQUAL
                            3'd6: pass = (new_z >  old_z);      // GREATER
                            3'd7: pass = (new_z != old_z);      // NOTEQUAL
                            default: pass = 1'b1;               // NONE — shouldn't reach here
                        endcase

                        if (p3_flags[SPAN_DEPTH_TEST] && !pass) begin
                            // Fail: skip fb write + skip z write.
                            p3_valid      <= 0;
                            p3_z_resolved <= 1;
                            fbss          <= FBSS_IDLE;
                        end else if (p3_flags[SPAN_DEPTH_WRITE]) begin
                            // Pass + write: launch SRAM write now, wait for
                            // completion in FBSS_ZWRWAIT.
                            sram_wr    <= 1;
                            sram_addr  <= p3_z_addr[23:2];
                            sram_wdata <= p3_z_addr[1]
                                ? {p3_zi[31:16], sram_rdata[15:0]}
                                : {sram_rdata[31:16], p3_zi[31:16]};
                            sram_wstrb <= p3_z_addr[1] ? 4'b1100 : 4'b0011;
                            fbss <= FBSS_ZWRWAIT;
                        end else begin
                            // Pass, no z-write — proceed straight to accumulate.
                            p3_z_resolved <= 1;
                            fbss          <= FBSS_IDLE;
                        end
                    end
                end

                FBSS_ZWRWAIT: begin
                    if (!sram_busy) begin
                        p3_z_resolved <= 1;
                        fbss          <= FBSS_IDLE;
                    end
                end

                // --------------------------------------------------------
                // Translucent-blend sub-flow (5 states).  Entry from
                // FBSS_IDLE captured the pixel into blend_*; we now read
                // the existing FB byte (from SDRAM, with fb_acc bypass for
                // the same-word case), look up the blended byte in
                // transluc[], then accumulate it into fb_acc using the
                // same path as the IDLE fast path.
                // --------------------------------------------------------
                FBSS_BLEND_REQ: begin
                    // Wait for the texture cache to be fully drained from M0
                    // (no pending AR, no in-flight read) before grabbing the
                    // bus.  blend_owns_m0 is gated on fbss state, so we
                    // can't drive m_rd_arvalid until we transition.
                    if (!tex_axi_arvalid && !tex_m0_in_flight) begin
                        blend_arvalid <= 1;
                        blend_araddr  <= blend_word_addr;
                        fbss          <= FBSS_BLEND_AR_WAIT;
                    end
                end

                FBSS_BLEND_AR_WAIT: begin
                    if (blend_arready) begin
                        blend_arvalid <= 0;
                        fbss          <= FBSS_BLEND_R_WAIT;
                    end
                end

                FBSS_BLEND_R_WAIT: begin
                    if (blend_rvalid) begin : blend_r_capture
                        // Compose the FB byte for the blend: prefer fb_acc's
                        // pending lane data over the SDRAM read when fb_acc
                        // has dirty data for our word + lane (same-word
                        // bypass, handles back-to-back GPU translucent
                        // overdraw correctly).
                        reg [7:0] rdata_lane;
                        reg [7:0] acc_lane;
                        reg [7:0] fb_byte;
                        case (blend_byte_lane)
                            2'd0: begin rdata_lane = blend_rdata[7:0];   acc_lane = fb_acc_data[7:0];   end
                            2'd1: begin rdata_lane = blend_rdata[15:8];  acc_lane = fb_acc_data[15:8];  end
                            2'd2: begin rdata_lane = blend_rdata[23:16]; acc_lane = fb_acc_data[23:16]; end
                            2'd3: begin rdata_lane = blend_rdata[31:24]; acc_lane = fb_acc_data[31:24]; end
                        endcase
                        fb_byte = (fb_acc_valid && fb_acc_addr == blend_word_addr
                                   && fb_acc_mask[blend_byte_lane])
                                ? acc_lane : rdata_lane;
                        // Stash for diagnostics (not used downstream).
                        blend_fb_word <= blend_rdata;
                        // Drive transluc_rd_addr now so the BRAM read
                        // happens during BLEND_LUT_WAIT and the data is
                        // valid by the time BLEND_APPLY runs (one cycle
                        // address-set + one cycle BRAM-read = two cycles).
                        // Key layout (15 bits): { src[7:1], fb_byte }.  The
                        // SPAN_TRANSLUC_REV variant swaps which axis loses
                        // its low bit.  Source LSB drop is the 128×256
                        // quantisation chosen in transluc.md.
                        if (blend_p3_flags[SPAN_TRANSLUC_REV])
                            transluc_rd_addr <= { fb_byte[7:1], blend_src_color };
                        else
                            transluc_rd_addr <= { blend_src_color[7:1], fb_byte };
                        fbss <= FBSS_BLEND_LUT_WAIT;
                    end
                end

                FBSS_BLEND_LUT_WAIT: begin
                    // BRAM read latency: addr was set at end of BLEND_R_WAIT,
                    // BRAM samples it during this cycle, transluc_rd_data is
                    // valid by the start of BLEND_APPLY.
                    fbss <= FBSS_BLEND_APPLY;
                end

                FBSS_BLEND_APPLY: begin : fbss_blend_apply_blk
                    // transluc_rd_data is now valid.  Apply it to fb_acc
                    // exactly like the IDLE fast path applies p3_color —
                    // including the cross-word flush case.  Cannot just
                    // re-enter IDLE because p3 may already have shifted
                    // out (we cleared p3_valid on entry to BLEND_REQ).
                    reg p3_word_match_b;
                    p3_word_match_b = (fb_acc_valid && fb_acc_addr == blend_word_addr)
                                   || !fb_acc_valid;
                    if (!p3_word_match_b) begin
                        // Cross-word: flush old fb_acc, queue blended byte
                        // for re-application (same as FBSS_FLUSH_W_RSP path).
                        m_wr_awvalid <= 1;
                        m_wr_awaddr  <= fb_acc_addr;
                        m_wr_awlen   <= 0;
                        m_wr_wvalid  <= 1;
                        m_wr_wdata   <= fb_acc_data;
                        m_wr_wstrb   <= fb_acc_mask;
                        m_wr_wlast   <= 1;

                        fbss_pend_valid <= 1;
                        fbss_pend_color <= transluc_rd_data;
                        fbss_pend_addr  <= { blend_word_addr[31:2], blend_byte_lane };

                        fb_acc_valid <= 0;
                        fb_acc_mask  <= 4'b0;

                        fbss <= FBSS_FLUSH_W_RSP;
                    end else begin
`ifdef GPU_STATS
                        stat_pixels  <= stat_pixels + 32'd1;
`endif
                        fb_acc_valid <= 1;
                        fb_acc_addr  <= blend_word_addr;
                        case (blend_byte_lane)
                            2'd0: begin fb_acc_data[7:0]   <= transluc_rd_data; fb_acc_mask[0] <= 1; end
                            2'd1: begin fb_acc_data[15:8]  <= transluc_rd_data; fb_acc_mask[1] <= 1; end
                            2'd2: begin fb_acc_data[23:16] <= transluc_rd_data; fb_acc_mask[2] <= 1; end
                            2'd3: begin fb_acc_data[31:24] <= transluc_rd_data; fb_acc_mask[3] <= 1; end
                        endcase
                        fbss <= FBSS_IDLE;
                    end
                end

                default: fbss <= FBSS_IDLE;
            endcase

`ifdef GPU_PERSP_IMPL
            // ----------------------------------------------------------
            // PSS — perspective segment-setup sub-FSM
            // ----------------------------------------------------------
            // Runs alongside the issue stage and fbss. Uses the shared
            // dsp_a/dsp_b/dsp_p multiplier and recip_lut. Drives slots
            // A and B with affine sub-segment slopes derived from the
            // projection-space (s/z, t/z, 1/z) accumulators.
            //
            // Pass type (persp_pass):
            //   PSS_PASS_ANCHOR  pass 1, span start: anchor at pos 0
            //   PSS_PASS_TO_A    pass 2: derive slot A slopes from anchor → pos 16
            //   PSS_PASS_TO_B    pass 3+: derive slot B slopes (fills pending)
            case (persp_pss)
                PSS_IDLE: begin
                    // Schedule next pass when persp is active.
                    if (persp_active) begin
                        if (!persp_first_done) begin
                            persp_pass <= PSS_PASS_ANCHOR;
                            persp_pss  <= PSS_RECIP_NA;
                        end else if (!persp_seg_a_ready) begin
                            persp_pass <= PSS_PASS_TO_A;
                            persp_pss  <= PSS_ADV;
                        end else if (!persp_seg_b_ready
                                  && (sp_count > 16'd8 || sp_seg_left != 4'd0)) begin
                            // Only fill slot B if there's a future 8-px segment
                            // that will swap it in. (sp_count includes the
                            // current segment's remaining pixels.)
                            persp_pass <= PSS_PASS_TO_B;
                            persp_pss  <= PSS_ADV;
                        end
                    end
                end

                PSS_ADV: begin
                    // Stage 1 of pipelined setup: advance projection-space
                    // accumulators by 8 pixels and register |sp_zinv_new|.
                    // Splitting the old single-cycle (advance → CLZ → top8 →
                    // recip_rd_addr) chain into ADV / CLZ / TOP8 closes the
                    // 50 MHz timing path that was failing by -3.45 ns.
                    sp_sZ            <= sp_sZ   + (sp_sZstep   <<< 3);  // 8-pixel advance
                    sp_tZ            <= sp_tZ   + (sp_tZstep   <<< 3);
                    sp_zinv          <= sp_zinv_advanced;
                    persp_zinv_abs_r <= persp_zinv_abs;
                    persp_pss        <= PSS_CLZ;
                end

                PSS_RECIP_NA: begin
                    // First-pass entry: no advance, but register |sp_zinv|
                    // into the same pipeline reg so we can fall through the
                    // shared CLZ / TOP8 stages.
                    persp_zinv_abs_r <= persp_zinv_abs_na;
                    persp_pss        <= PSS_CLZ;
                end

                PSS_CLZ: begin
                    // Stage 2: compute leading-zero count of the registered
                    // abs value and register it. Inputs are FF outputs;
                    // output is a FF input — the casez sits between two
                    // register banks.
                    persp_clz <= persp_clz_pipe;
                    persp_pss <= PSS_TOP8;
                end

                PSS_TOP8: begin
                    // Stage 3: compute the top-8 normalized bits (the recip
                    // LUT index) from the registered abs and clz, and write
                    // recip_rd_addr. Variable barrel shift is the only
                    // combinational chain in this stage.
                    recip_rd_addr <= persp_top8_pipe;
                    persp_pss     <= PSS_RECIP_W;
                end

                PSS_RECIP_W: begin
                    // BRAM read latency — recip_rd_data valid next cycle.
                    persp_pss <= PSS_RECIP_SHIFT;
                end

                PSS_RECIP_SHIFT: begin
                    // Compute the Q16.16 reciprocal from LUT mantissa + clz
                    // shift and LATCH it into recip_q16_r. Splitting this
                    // out of PSS_MUL removes the variable 32-bit barrel
                    // shifter from the dsp2_b update mux cone.
                    if (persp_clz >= 5'd13)
                        recip_q16_r <= $signed({16'b0, recip_rd_data})
                                     <<< (persp_clz - 5'd13);
                    else
                        recip_q16_r <= $signed({16'b0, recip_rd_data})
                                     >>> (5'd13 - persp_clz);
                    persp_pss <= PSS_NR_MUL_X;
                end

                // ----- Newton-Raphson iteration (bug-report 2026-04-25 #C)
                // Goal: refine recip_q16_r (= y0, ~10-bit precision from
                // the 1024-entry LUT) into a ~20-bit-precision Q16.16
                // reciprocal via y1 = y0 * (2 - x * y0).  All math in
                // signed Q16.16; products taken as dsp_p[47:16].
                PSS_NR_MUL_X: begin
                    // Launch x * y0 on dsp.  dsp2 idle this round.
                    dsp_a <= $signed(persp_zinv_abs_r);
                    dsp_b <= recip_q16_r;
                    persp_pss <= PSS_NR_MUL_X_W;
                end
                PSS_NR_MUL_X_W: persp_pss <= PSS_NR_SUB;
                PSS_NR_SUB: begin
                    // dsp_p[47:16] = x * y0 in Q16.16 ≈ 1.0 (= 0x10000).
                    // For a perfect y0, exactly 1.0; LUT precision causes
                    // a small offset.  N-R uses (2 - x*y0) to "correct"
                    // the offset on the next multiply.
                    nr_two_minus_xy <= 32'h00020000 - $signed(dsp_p[47:16]);
                    persp_pss <= PSS_NR_MUL_Y;
                end
                PSS_NR_MUL_Y: begin
                    // Launch y0 * (2 - x * y0).
                    dsp_a <= recip_q16_r;
                    dsp_b <= nr_two_minus_xy;
                    persp_pss <= PSS_NR_MUL_Y_W;
                end
                PSS_NR_MUL_Y_W: persp_pss <= PSS_NR_CAPTURE;
                PSS_NR_CAPTURE: begin
                    // Refined Q16.16 reciprocal.
                    recip_q16_r <= dsp_p[47:16];
                    persp_pss <= PSS_MUL;
                end

                PSS_MUL: begin
                    // Kick BOTH multiplies (sZ×recip on dsp, tZ×recip on dsp2)
                    // in parallel using the pre-registered recip_q16_r.
                    // They land together at PSS_FINAL one DSP cycle later.
                    dsp_a     <= sp_sZ;
                    dsp_b     <= recip_q16_r;
                    dsp2_a    <= sp_tZ;
                    dsp2_b    <= recip_q16_r;
                    persp_pss <= PSS_MUL_W;
                end

                PSS_MUL_W: begin
                    // DSP pipeline delay — dsp_p and dsp2_p both update here.
                    persp_pss <= PSS_FINAL;
                end

                PSS_FINAL: begin : pss_final_blk
                    // dsp_p  = sZ × recip → s_end
                    // dsp2_p = tZ × recip → t_end
                    reg signed [31:0] s_end;
                    reg signed [31:0] t_end;
                    s_end = dsp_p[47:16];
                    t_end = dsp2_p[47:16];
                    // (debug $display removed)
                    case (persp_pass)
                        PSS_PASS_ANCHOR: begin
                            // Pass 1: just store the anchor at pos 0.
                            persp_anchor_s   <= s_end;
                            persp_anchor_t   <= t_end;
                            persp_first_done <= 1;
                        end
                        PSS_PASS_TO_A: begin
                            // Pass 2: derive slot A slopes from anchor → pos 8.
                            sp_s              <= persp_anchor_s;
                            sp_t              <= persp_anchor_t;
                            sp_sstep          <= ($signed(s_end)
                                                - $signed(persp_anchor_s)) >>> 3;
                            sp_tstep          <= ($signed(t_end)
                                                - $signed(persp_anchor_t)) >>> 3;
                            sp_seg_left       <= 4'd7;  // 8-pixel segments
                            persp_anchor_s    <= s_end;
                            persp_anchor_t    <= t_end;
                            persp_seg_a_ready <= 1;
                        end
                        PSS_PASS_TO_B: begin
                            // Pass 3+: derive slot B (pending) slopes.
                            persp_pend_s      <= persp_anchor_s;
                            persp_pend_t      <= persp_anchor_t;
                            persp_pend_sstep  <= ($signed(s_end)
                                                - $signed(persp_anchor_s)) >>> 3;
                            persp_pend_tstep  <= ($signed(t_end)
                                                - $signed(persp_anchor_t)) >>> 3;
                            persp_anchor_s    <= s_end;
                            persp_anchor_t    <= t_end;
                            persp_seg_b_ready <= 1;
                        end
                        default: ;
                    endcase
                    persp_pss <= PSS_IDLE;
                end

                default: persp_pss <= PSS_IDLE;
            endcase
`endif // GPU_PERSP_IMPL

            // ----------------------------------------------------------
            // Drain detection — when source done and pipe empty, flush.
            // If we're inside a triangle rasterisation, hand back to the
            // row walker instead of flushing fb_acc (more rows may follow
            // and fb_acc will keep coalescing writes).
            // ----------------------------------------------------------
            if (src_done && !p0_valid && !p1_valid && !p2_valid && !p2b_valid
                         && !p3_valid && fbss == FBSS_IDLE) begin
                src_done <= 0;
`ifdef GPU_PERSP_IMPL
                persp_active      <= 0;  // disarm so PSS doesn't keep running
                persp_seg_a_ready <= 0;
                persp_seg_b_ready <= 0;
                persp_first_done  <= 0;
`endif
`ifdef GPU_FEAT_TRIANGLE
                if (tri_active)
                    state <= S_TRI_ROW_NEXT;
                else
`endif
                    state    <= S_FB_FLUSH;
            end
        end
`endif // GPU_FEAT_FRAG_PIPELINE

        // ============================================================
        // FB flush — end-of-span or mid-span word boundary
        // ============================================================
        S_FB_FLUSH: begin
            if (fb_acc_valid && |fb_acc_mask) begin
                m_wr_awvalid <= 1;
                m_wr_awaddr  <= fb_acc_addr;
                m_wr_awlen   <= 0;
                m_wr_wvalid  <= 1;
                m_wr_wdata   <= fb_acc_data;
                m_wr_wstrb   <= fb_acc_mask;
                m_wr_wlast   <= 1;
                state        <= S_FB_FLUSH_WAIT;
            end else begin
                fb_acc_valid <= 0;
                fb_acc_mask  <= 0;
                // End-of-primitive flush: return to idle. In FULL, also clear
                // tri_active so we don't re-enter the triangle path.
`ifdef GPU_FEAT_TRIANGLE
                if (tri_active) tri_active <= 0;
`endif
                state <= S_IDLE;
            end
        end

        S_FB_FLUSH_WAIT: begin
            // AW handshake
            if (m_wr_awvalid && m_wr_awready)
                m_wr_awvalid <= 0;
            // W handshake
            if (m_wr_wvalid && m_wr_wready)
                m_wr_wvalid <= 0;
            // B response — write complete
            if (m_wr_bvalid) begin
                m_wr_awvalid <= 0;
                m_wr_wvalid  <= 0;
                if (tri_active) begin
                    // Mid-triangle flush: re-accumulate pending pixel
                    begin : reaccum_tri
                        reg [1:0] bl;
                        bl = sp_fb_addr[1:0];
                        fb_acc_valid <= 1;
                        fb_acc_addr  <= sp_fb_addr & 32'hFFFFFFFC;
                        fb_acc_data  <= 32'b0;
                        fb_acc_mask  <= 4'b0;
                        case (bl)
                            2'd0: begin fb_acc_data[7:0]   <= frag_color[7:0]; fb_acc_mask[0] <= 1; end
                            2'd1: begin fb_acc_data[15:8]  <= frag_color[7:0]; fb_acc_mask[1] <= 1; end
                            2'd2: begin fb_acc_data[23:16] <= frag_color[7:0]; fb_acc_mask[2] <= 1; end
                            2'd3: begin fb_acc_data[31:24] <= frag_color[7:0]; fb_acc_mask[3] <= 1; end
                        endcase
                    end
                    state <= S_TRI_PIX;
                end else if (sp_count > 0) begin
                    // Mid-span flush: re-accumulate pending pixel
                    begin : reaccum_span
                        reg [1:0] bl;
                        bl = sp_fb_addr[1:0];
                        fb_acc_valid <= 1;
                        fb_acc_addr  <= sp_fb_addr & 32'hFFFFFFFC;
                        fb_acc_data  <= 32'b0;
                        fb_acc_mask  <= 4'b0;
                        case (bl)
                            2'd0: begin fb_acc_data[7:0]   <= frag_color[7:0]; fb_acc_mask[0] <= 1; end
                            2'd1: begin fb_acc_data[15:8]  <= frag_color[7:0]; fb_acc_mask[1] <= 1; end
                            2'd2: begin fb_acc_data[23:16] <= frag_color[7:0]; fb_acc_mask[2] <= 1; end
                            2'd3: begin fb_acc_data[31:24] <= frag_color[7:0]; fb_acc_mask[3] <= 1; end
                        endcase
                    end
                    state <= S_SPAN_STEP;
                end else begin
                    fb_acc_valid <= 0;
                    fb_acc_mask  <= 0;
                    state <= S_IDLE;
                end
            end
        end

        // ============================================================
        // Clear — single-word writes to FB and/or Z-buffer
        // ============================================================
        // Clear extents are hardcoded to 320x200: 16000 32-bit words for
        // the FB (320*200 bytes / 4) and 32000 halfwords for the Z-buffer
        // (320*200 * 2 / 2).  This is a documented hardware/firmware
        // contract — see openfpgaOS-SDK/apps/gpudemo/main.c where the
        // comment about GPU_CLEAR_ROWS = 200 + LETTERBOX_ROWS = 40
        // explicitly accounts for it.  Changing this requires adding a
        // width/height payload to CMD_CLEAR and updating the SDK's
        // of_gpu_clear() helper in lock-step.
        S_CLEAR_INIT: begin
            if (clear_flags[0]) begin
                clear_addr      <= st_fb_addr;
                clear_remaining <= 18'd16000;  // 320*200/4 words (FB)
                state           <= S_CLEAR_FB;
            end else if (clear_flags[1]) begin
                clear_addr      <= st_zb_addr;
                clear_remaining <= 18'd32000;  // 320*200/2 halfwords (ZB)
                state           <= S_CLEAR_ZB;
            end else begin
                state <= S_IDLE;
            end
        end

        S_CLEAR_FB: begin
            if (clear_remaining == 0) begin
                if (clear_flags[1]) begin
                    clear_addr      <= st_zb_addr;
                    clear_remaining <= 18'd32000;
                    state           <= S_CLEAR_ZB;
                end else
                    state <= S_IDLE;
            end else begin
                // Single-word AXI4 write
                m_wr_awvalid <= 1;
                m_wr_awaddr  <= clear_addr;
                m_wr_awlen   <= 0;
                m_wr_wvalid  <= 1;
                m_wr_wdata   <= {clear_color[7:0], clear_color[7:0],
                                 clear_color[7:0], clear_color[7:0]};
                m_wr_wstrb   <= 4'b1111;
                m_wr_wlast   <= 1;
                state        <= S_CLEAR_FB_WAIT;
            end
        end

        S_CLEAR_FB_WAIT: begin
            if (m_wr_awvalid && m_wr_awready)
                m_wr_awvalid <= 0;
            if (m_wr_wvalid && m_wr_wready)
                m_wr_wvalid <= 0;
            if (m_wr_bvalid) begin
                m_wr_awvalid    <= 0;
                m_wr_wvalid     <= 0;
                clear_remaining <= clear_remaining - 18'd1;
                clear_addr      <= clear_addr + 32'd4;
                state           <= S_CLEAR_FB;
            end
        end

        S_CLEAR_ZB: begin
            if (clear_remaining == 0)
                state <= S_IDLE;
            else if (!sram_busy) begin
                sram_wr    <= 1;
                sram_addr  <= clear_addr[23:2];  // byte addr → word addr
                sram_wdata <= {clear_depth, clear_depth};
                sram_wstrb <= 4'b1111;
                state      <= S_CLEAR_ZB_WAIT;
            end
        end

        S_CLEAR_ZB_WAIT: begin
            if (!sram_busy) begin
                clear_remaining <= clear_remaining - 18'd1;
                clear_addr      <= clear_addr + 32'd4;
                state           <= S_CLEAR_ZB;
            end
        end

`ifdef GPU_FEAT_TRIANGLE
        // ============================================================
        // Triangle: Load vertices — now a pass-through
        // ============================================================
        // Vertices are streamed directly into v_*[] during S_PAY_DATA,
        // so S_TRI_LOAD no longer touches the vertex regs.  Kept as a
        // 1-cycle passthrough that resets setup_step and hands off to
        // S_TRI_SETUP; callers still dispatch here from S_EXECUTE.
        S_TRI_LOAD: begin
            setup_step <= 0;
            premul_step <= 0;
            // When any vertex has non-unit w, run the pre-multiply pass
            // first to populate v_sw/v_tw (consumed in 4c.3 by the
            // gradient loop).  Affine triangles skip directly to
            // S_TRI_SETUP — zero overhead on the common path.
            state <= tri_persp_active ? S_TRI_PERSP_PREMUL : S_TRI_SETUP;
        end

        // ============================================================
        // Triangle: Pre-multiply v_s × v_w, v_t × v_w  (Phase 4c.2)
        // ------------------------------------------------------------
        // 3 rounds × 3 cycles + 1 commit = 7 cycles.  dsp does s × w,
        // dsp2 does t × w in parallel, one vertex per round.  Sliced
        // as bits [47:16] of the 64-bit product so v_sw / v_tw are
        // Q16.16 — same fixed-point scale as v_w itself, ready for
        // the linear-gradient computation in S_TRI_GRAD (when 4c.3
        // wires the mux).
        //
        // Pattern matches S_TRI_INIT_ATTRIB: launch on even step,
        // wait on odd step, capture on the next even step (which also
        // launches the next round).  2-cycle DSP pipeline: load at
        // end of step N → valid at start of step N+2.
        // ============================================================
        S_TRI_PERSP_PREMUL: begin
            case (premul_step)
                3'd0: begin
                    // Launch v0
                    dsp_a  <= {{16{v_s[0][15]}}, v_s[0]};
                    dsp_b  <= v_w[0];
                    dsp2_a <= {{16{v_t[0][15]}}, v_t[0]};
                    dsp2_b <= v_w[0];
                    premul_step <= 3'd1;
                end
                3'd1: premul_step <= 3'd2;  // DSP pipeline delay
                3'd2: begin
                    // Capture v0; launch v1.  dsp_a is sign-extended
                    // v_s (Q16.0 signed 32-bit), dsp_b is v_w (Q16.16
                    // signed 32-bit).  Their product is Q32.16 stored
                    // as a 64-bit signed integer where the Q16.16
                    // representation of (v_s × v_w) sits at bits
                    // [31:0] — NOT [47:16] (the original commit had
                    // this off by a factor of 2^16, which made every
                    // perspective triangle's interpolated texcoords
                    // wrong by the same factor).  See bug-report
                    // 2026-04-25 part A for the symptom on Quake.
                    v_sw[0] <= dsp_p [31:0];
                    v_tw[0] <= dsp2_p[31:0];
                    dsp_a   <= {{16{v_s[1][15]}}, v_s[1]};
                    dsp_b   <= v_w[1];
                    dsp2_a  <= {{16{v_t[1][15]}}, v_t[1]};
                    dsp2_b  <= v_w[1];
                    premul_step <= 3'd3;
                end
                3'd3: premul_step <= 3'd4;  // DSP pipeline delay
                3'd4: begin
                    // Capture v1; launch v2
                    v_sw[1] <= dsp_p [31:0];
                    v_tw[1] <= dsp2_p[31:0];
                    dsp_a   <= {{16{v_s[2][15]}}, v_s[2]};
                    dsp_b   <= v_w[2];
                    dsp2_a  <= {{16{v_t[2][15]}}, v_t[2]};
                    dsp2_b  <= v_w[2];
                    premul_step <= 3'd5;
                end
                3'd5: premul_step <= 3'd6;  // DSP pipeline delay
                3'd6: begin
                    // Capture v2; hand off to setup
                    v_sw[2] <= dsp_p [31:0];
                    v_tw[2] <= dsp2_p[31:0];
                    premul_step <= 3'd0;
                    state <= S_TRI_SETUP;
                end
                default: ;
            endcase
        end

        // ============================================================
        // Triangle: Sequential setup (edges, determinant, gradients)
        // ============================================================
        // DSP multiply has 1-cycle registered latency:
        // Parallel setup using 3 DSP slots (dsp, dsp2, dsp3):
        //   Step 0: edges/diffs + launch C0/C1/C2 first-multiply in parallel
        //   Step 2: capture partials; launch C0/C1/C2 second-multiply in parallel
        //   Step 4: subtract → final C0/C1/C2; launch det parts (dsp + dsp2)
        //   Step 6: det = dsp_p + dsp2_p
        //   Step 7: degenerate check + CLZ of |det|
        //   Step 8: write recip_rd_addr
        //   Step 9: capture recip → transition to S_TRI_GRAD
        // 10 cycles end-to-end vs 20 before (2× setup speedup).
        S_TRI_SETUP: begin
            setup_step <= setup_step + 7'd1;
            case (setup_step)
            0: begin
                tri_A[0] <= v_y[0] - v_y[1]; tri_B[0] <= v_x[1] - v_x[0];
                tri_A[1] <= v_y[1] - v_y[2]; tri_B[1] <= v_x[2] - v_x[1];
                tri_A[2] <= v_y[2] - v_y[0]; tri_B[2] <= v_x[0] - v_x[2];
                dX10 <= v_x[1] - v_x[0]; dY10 <= v_y[1] - v_y[0];
                dX20 <= v_x[2] - v_x[0]; dY20 <= v_y[2] - v_y[0];
                // Parallel first-multiplies for C0, C1, C2
                dsp_a  <= {{16{v_x[0][15]}}, v_x[0]}; dsp_b  <= {{16{v_y[1][15]}}, v_y[1]};
                dsp2_a <= {{16{v_x[1][15]}}, v_x[1]}; dsp2_b <= {{16{v_y[2][15]}}, v_y[2]};
                dsp3_a <= {{16{v_x[2][15]}}, v_x[2]}; dsp3_b <= {{16{v_y[0][15]}}, v_y[0]};
            end
            1: begin end  // DSP pipeline delay
            2: begin
                // Capture 3 first-multiply partials
                tri_C[0] <= dsp_p [31:0];
                tri_C[1] <= dsp2_p[31:0];
                tri_C[2] <= dsp3_p[31:0];
                // Parallel second-multiplies
                dsp_a  <= {{16{v_x[1][15]}}, v_x[1]}; dsp_b  <= {{16{v_y[0][15]}}, v_y[0]};
                dsp2_a <= {{16{v_x[2][15]}}, v_x[2]}; dsp2_b <= {{16{v_y[1][15]}}, v_y[1]};
                dsp3_a <= {{16{v_x[0][15]}}, v_x[0]}; dsp3_b <= {{16{v_y[2][15]}}, v_y[2]};
            end
            3: begin end
            4: begin
                // Subtract → final C0/C1/C2; launch det parts in parallel
                tri_C[0] <= tri_C[0] - dsp_p [31:0];
                tri_C[1] <= tri_C[1] - dsp2_p[31:0];
                tri_C[2] <= tri_C[2] - dsp3_p[31:0];
                dsp_a  <= tri_A[0]; dsp_b  <= {{16{dX20[15]}}, dX20};
                dsp2_a <= tri_B[0]; dsp2_b <= {{16{dY20[15]}}, dY20};
            end
            5: begin end
            6: begin
                // det = A0*dX20 + B0*dY20  (both multiplies done in parallel)
                tri_det <= dsp_p[31:0] + dsp2_p[31:0];
                // Capture the small-det test on the same combinational
                // value tri_det is latching, so step 7 sees a single
                // registered bit instead of a 28-bit compare cone.
                tri_det_is_small_r <= ($signed(dsp_p[31:0] + dsp2_p[31:0]) == 32'sd0)
                                       || ($signed(dsp_p[31:0] + dsp2_p[31:0]) > -32'sd16
                                           && $signed(dsp_p[31:0] + dsp2_p[31:0]) < 32'sd16);
            end
            7: begin
                // Check determinant: skip degenerate (uses registered compare)
                if (tri_det_is_small_r) begin
                    setup_step <= 0;
                    if (pay_remaining != 24'd0) begin
                        // Mid-batch: advance ring_rdptr (consumes
                        // v0_word_0 of next triangle on this posedge,
                        // primes ring_rd_data for pay_idx=1) and jump
                        // back to vertex-load.
                        ring_rdptr <= (ring_rdptr + 16'd4) & ring_mask;
                        pay_idx    <= 5'd1;
                        state      <= S_PAY_DATA;
                    end else begin
                        // Last triangle in batch — flush any coalesced
                        // pixels accumulated by earlier triangles.
                        state <= S_FB_FLUSH;
                    end
                end else begin
                    tri_det_sign <= tri_det[31];
                    // If det < 0: negate all edges (ensure CCW winding)
                    if (tri_det[31]) begin
                        tri_A[0] <= -tri_A[0]; tri_B[0] <= -tri_B[0]; tri_C[0] <= -tri_C[0];
                        tri_A[1] <= -tri_A[1]; tri_B[1] <= -tri_B[1]; tri_C[1] <= -tri_C[1];
                        tri_A[2] <= -tri_A[2]; tri_B[2] <= -tri_B[2]; tri_C[2] <= -tri_C[2];
                        tri_det <= -tri_det;
                    end
                    // CLZ: normalise |det| for reciprocal LUT
                    begin : clz_compute
                        reg [31:0] abs_d;
                        reg [5:0] lz;
                        abs_d = tri_det[31] ? -tri_det : tri_det;
                        casez (abs_d)
                            32'b1???????????????????????????????: lz = 0;
                            32'b01??????????????????????????????: lz = 1;
                            32'b001?????????????????????????????: lz = 2;
                            32'b0001????????????????????????????: lz = 3;
                            32'b00001???????????????????????????: lz = 4;
                            32'b000001??????????????????????????: lz = 5;
                            32'b0000001?????????????????????????: lz = 6;
                            32'b00000001????????????????????????: lz = 7;
                            32'b000000001???????????????????????: lz = 8;
                            32'b0000000001??????????????????????: lz = 9;
                            32'b00000000001?????????????????????: lz = 10;
                            32'b000000000001????????????????????: lz = 11;
                            32'b0000000000001???????????????????: lz = 12;
                            32'b00000000000001??????????????????: lz = 13;
                            32'b000000000000001?????????????????: lz = 14;
                            32'b0000000000000001????????????????: lz = 15;
                            32'b00000000000000001???????????????: lz = 16;
                            32'b000000000000000001??????????????: lz = 17;
                            32'b0000000000000000001?????????????: lz = 18;
                            32'b00000000000000000001????????????: lz = 19;
                            32'b000000000000000000001???????????: lz = 20;
                            32'b0000000000000000000001??????????: lz = 21;
                            32'b00000000000000000000001?????????: lz = 22;
                            32'b000000000000000000000001????????: lz = 23;
                            32'b0000000000000000000000001???????: lz = 24;
                            32'b00000000000000000000000001??????: lz = 25;
                            32'b000000000000000000000000001?????: lz = 26;
                            32'b0000000000000000000000000001????: lz = 27;
                            32'b00000000000000000000000000001???: lz = 28;
                            32'b000000000000000000000000000001??: lz = 29;
                            32'b0000000000000000000000000000001?: lz = 30;
                            default: lz = 31;
                        endcase
                        tri_clz <= lz;
                    end
                end
            end
            8: begin
                // Set M10K LUT read address (registered, 1-cycle latency).
                // Phase 4b widened the LUT from 256 → 1024 entries (10-bit
                // input); index slice grew from [30:23] to [30:21].  This
                // line was missed in the original 4b commit — every
                // triangle's tri_recip was reading the 8-bit-aligned entry
                // and silently losing 2 bits of input precision (and worse,
                // the OLD 8-bit value ended up zero-extended into a sparse
                // index into the new LUT, returning unrelated entries).
                begin : recip_addr_set
                    reg [31:0] abs_d, norm;
                    abs_d = tri_det[31] ? -tri_det : tri_det;
                    norm = abs_d << tri_clz;
                    recip_rd_addr <= norm[30:21];
                end
            end
            9: begin
                // BRAM read latency wait — recip_rd_addr was set at end
                // of step 8.  recip_rd_data is registered, so the
                // updated lookup result lands at end of this cycle.
                // Without this wait, tri_recip captured stale data
                // (always LUT[0] = 0x4000 on the first triangle, since
                // recip_rd_addr resets to 0).  Latent bug from before
                // 4d landed; existing tests passed because they only
                // checked the span's start pixel, where gradients are
                // multiplied by zero.
            end
            10: begin
                // Capture M10K read result, then enter rolled gradient loop.
                tri_recip <= recip_rd_data;
                state <= S_TRI_GRAD;
                grad_idx <= 0;
                grad_sub <= 0;
                setup_step <= 0;
            end
            default: state <= S_IDLE;
            endcase
        end

        // ============================================================
        // Triangle: Rolled gradient computation loop
        // ============================================================
        // Computes 6 gradients (Zdx, Zdy, Sdx, Sdy, Tdx, Tdy) using a
        // single 7-sub-cycle template per gradient. Replaces 36 explicit
        // case arms in S_TRI_SETUP. Operand selection is by grad_idx
        // (see grad_dV10/dV20/axis_b1/axis_b2 wires above).
        //
        // Sub-cycle layout (DSP has 1-cycle registered latency):
        //   0: load dV10 * axis_b1
        //   1: pipeline delay
        //   2: capture partial; load dV20 * axis_b2
        //   3: pipeline delay
        //   4: load (partial ± dsp_p) * recip   (sign by grad_idx[0])
        //   5: pipeline delay
        //   6: writeback shifted dsp_p to selected gradient register;
        //      advance grad_idx or transition to S_TRI_BBOX
        // ============================================================
        // Parallel gradient loop: both cross-multiplies (dV10×axis_b1 and
        // dV20×axis_b2) run simultaneously on dsp + dsp2. 7 sub-cycles ×
        // 6 gradients = 42 cycles.
        //   3'd0 launch parallel mul
        //   3'd1 DSP pipeline delay
        //   3'd2 capture grad_sub_r = dsp_p - dsp2_p (or reversed)
        //   3'd3 load dsp_a <= grad_sub_r, launch recip mul
        //   3'd4 DSP pipeline delay for recip mul
        //   3'd5 capture dsp_p_shifted
        //   3'd6 writeback grad_*_dx/dy
        S_TRI_GRAD: begin
            grad_sub <= grad_sub + 3'd1;
            case (grad_sub)
                3'd0: begin
                    // Launch both cross-multiplies in parallel.
                    dsp_a  <= grad_dV10; dsp_b  <= grad_axis_b1;
                    dsp2_a <= grad_dV20; dsp2_b <= grad_axis_b2;
                end
                3'd1: begin end  // DSP pipeline delay
                3'd2: begin
                    // Both DSP results are valid. Register the subtract +
                    // sign mux into grad_sub_r so next cycle's dsp_a load
                    // is just a reg-to-reg copy, not a 32-bit adder cone
                    // feeding the DSP input pin.
                    //
                    // Sign correction for negative-det triangles: tri_A/B/C
                    // were negated in S_TRI_SETUP step 7 to canonicalise the
                    // edge equations to "inside iff e_i >= 0", but the dY/dX
                    // operands feeding this DSP and v_sw / v_tw / v_z / v_r
                    // are NOT negated.  The standard cross-product formula
                    // therefore yields a result of the *original* signed-det
                    // sign, which we then divide by the post-negation +|det|
                    // (via tri_recip).  Result: the gradient is sign-inverted
                    // for CW-winding triangles.  Symptom: half of Quake's
                    // alias-model triangles render with mirrored textures
                    // (and the corresponding row/column walks in the wrong
                    // direction), since back-facing tris cross-product
                    // negative.  Fix: XOR grad_idx[0] with tri_det_sign so
                    // the subtract direction reverses when det was negative.
                    grad_sub_r <= (grad_idx[0] ^ tri_det_sign)
                                ? ($signed(dsp2_p[31:0]) - $signed(dsp_p[31:0]))
                                : ($signed(dsp_p[31:0])  - $signed(dsp2_p[31:0]));
                end
                3'd3: begin
                    // Launch recip mul using the pre-registered subtract.
                    dsp_a <= grad_sub_r;
                    dsp_b <= {{16{1'b0}}, tri_recip};
                end
                3'd4: begin end  // DSP pipeline delay for recip mul
                3'd5: begin
                    // Capture the variable-shift result in its own register
                    // so the grad-register writeback in 3'd6 doesn't have to
                    // synthesize a 6-stage barrel shifter on the same cycle.
                    dsp_p_shifted <= dsp_p >>> (6'd29 - tri_clz);
                end
                3'd6: begin
                    // Perspective Q-format fix.  v_sw / v_tw are Q16.16
                    // (they include the Q16.16 v_w factor), whereas
                    // affine v_s / v_t are Q16.0 sign-extended.  The
                    // same shift constant therefore lands the perspective
                    // gradient 2^16 too big — sp_sZ then overflows the
                    // 32-bit register on the 8-pixel PSS_ADV and inverts
                    // sign.  Shift perspective gradients down 16 bits.
                    // w gradients are perspective-only by design.
                    case (grad_idx)
                        4'd0: grad_z_dx <= dsp_p_shifted;
                        4'd1: grad_z_dy <= dsp_p_shifted;
                        4'd2: grad_s_dx <= tri_persp_active ? (dsp_p_shifted >>> 16) : dsp_p_shifted;
                        4'd3: grad_s_dy <= tri_persp_active ? (dsp_p_shifted >>> 16) : dsp_p_shifted;
                        4'd4: grad_t_dx <= tri_persp_active ? (dsp_p_shifted >>> 16) : dsp_p_shifted;
                        4'd5: grad_t_dy <= tri_persp_active ? (dsp_p_shifted >>> 16) : dsp_p_shifted;
                        4'd6: grad_w_dx <= dsp_p_shifted >>> 16;
                        4'd7: grad_w_dy <= dsp_p_shifted >>> 16;
                        // Phase 4d — Gouraud r gradients (always
                        // computed; light walks per-pixel in the
                        // fragment pipe).
                        4'd8: grad_r_dx <= dsp_p_shifted;
                        4'd9: grad_r_dy <= dsp_p_shifted;
                        default: ;
                    endcase
                    grad_sub <= 0;
                    // Loop progression:
                    //   idx 5 → 6 (persp) or jump to 8 (affine, skip w)
                    //   idx 9 → exit
                    if (grad_idx == 4'd9) begin
                        state <= S_TRI_BBOX;
                    end else if (grad_idx == 4'd5 && !tri_persp_active) begin
                        // Affine: skip w (idx 6/7), straight to r (idx 8/9).
                        grad_idx <= 4'd8;
                    end else begin
                        grad_idx <= grad_idx + 4'd1;
                    end
                end
                default: ;
            endcase
        end

        // ============================================================
        // Triangle: Bounding box stage 1 — raw min/max of v_x[0..2], v_y[0..2]
        // ------------------------------------------------------------
        // Just the 3-way min/max, no clamp. Keeps the combinational depth
        // to ~2 compares so the ~10 ns budget is easy.
        // ============================================================
        S_TRI_BBOX: begin
            begin : bbox_raw
                reg signed [15:0] xmin, xmax, ymin, ymax;
                xmin = v_x[0]; xmax = v_x[0];
                ymin = v_y[0]; ymax = v_y[0];
                if (v_x[1] < xmin) xmin = v_x[1];
                if (v_x[1] > xmax) xmax = v_x[1];
                if (v_x[2] < xmin) xmin = v_x[2];
                if (v_x[2] > xmax) xmax = v_x[2];
                if (v_y[1] < ymin) ymin = v_y[1];
                if (v_y[1] > ymax) ymax = v_y[1];
                if (v_y[2] < ymin) ymin = v_y[2];
                if (v_y[2] > ymax) ymax = v_y[2];
                tri_xmin_raw <= xmin;
                tri_xmax_raw <= xmax;
                tri_ymin_raw <= ymin;
                tri_ymax_raw <= ymax;
            end
            state <= S_TRI_BBOX_CLAMP;
        end

        // ============================================================
        // Triangle: Bounding box stage 2 — clamp to screen + >>4 (12.4→px)
        // ------------------------------------------------------------
        // Reads registered tri_*_raw, produces registered tri_xmin/xmax/
        // ymin/ymax. Only a compare + mux + shift per axis — shallow.
        // ============================================================
        S_TRI_BBOX_CLAMP: begin
            tri_xmin <= (tri_xmin_raw < 0) ? 16'd0 : (tri_xmin_raw >>> 4);
            tri_xmax <= (tri_xmax_raw >>> 4 > 319) ? 16'd319 : (tri_xmax_raw >>> 4);
            tri_ymin <= (tri_ymin_raw < 0) ? 16'd0 : (tri_ymin_raw >>> 4);
            tri_ymax <= (tri_ymax_raw >>> 4 > 199) ? 16'd199 : (tri_ymax_raw >>> 4);
            state <= S_TRI_MUL_WAIT;
        end

        // ============================================================
        // Triangle: 2-cycle wait for tri_ymin_x_stride to be registered.
        // With the DSP input-FF stage, tri_ymin_dsp_in / st_fb_stride_dsp_in
        // capture on cycle N+1 (one cycle after S_TRI_BBOX_CLAMP), and
        // the DSP output (tri_ymin_x_stride) is valid on cycle N+2. So
        // S_TRI_ROW needs to fire on cycle N+3. Two wait states give it
        // that margin and also let tri_e_init_Apx/Bpy settle fully.
        // ============================================================
        S_TRI_MUL_WAIT: begin
            // Pre-register subpixel deltas (xmin*16 - v0.x, ymin*16 - v0.y)
            // for the bbox-origin attribute init that runs in
            // S_TRI_INIT_ATTRIB.  tri_xmin/tri_ymin are valid here (set at
            // end of S_TRI_BBOX_CLAMP one cycle ago).  Sign-extending
            // tri_xmin to 21 bits as positive (it's pixel-space, ≥ 0
            // post-clamp) and v_x[0] / v_y[0] (12.4 signed subpixel) to 21
            // bits.  Result range: roughly ±37 870 — well inside 21-bit
            // signed.
            delta_x_subpix <= $signed({1'b0, tri_xmin, 4'b0})
                            - $signed({{5{v_x[0][15]}}, v_x[0]});
            delta_y_subpix <= $signed({1'b0, tri_ymin, 4'b0})
                            - $signed({{5{v_y[0][15]}}, v_y[0]});
            state <= S_TRI_MUL_WAIT2;
        end

        S_TRI_MUL_WAIT2: begin
            state <= S_TRI_INIT_ATTRIB;
        end

        // ============================================================
        // Triangle: Bbox-origin attribute init.
        // ------------------------------------------------------------
        // Computes tri_row_{z,s,t} at (xmin*16, ymin*16) instead of at v0:
        //   tri_row_a = v_a[0] + grad_a_dx * delta_x_subpix
        //                      + grad_a_dy * delta_y_subpix
        //
        // Uses dsp / dsp2 in parallel, 3 rounds (z, s, t) × 3 cycles each.
        // Total 6 effective cycles + 1 commit = 7 cycles per triangle.
        // Setup-time only — no per-pixel cost.
        //
        // Without this stage the row walk starts at v0 and accumulates a
        // constant error of (xmin - v0.x) * grad_dx + (ymin - v0.y) *
        // grad_dy across every pixel of the triangle.  Sub-pixel for tight
        // bboxes; large for clipped/skinny triangles (the tilted-text /
        // rotated-UI case in SDL2's RenderCopyEx, and the main contributor
        // to the "warped at angles" report on 3D consumers).
        // ============================================================
        S_TRI_INIT_ATTRIB: begin
            case (init_step)
                4'd0: begin
                    // Round 0 — z gradient × deltas, in parallel.
                    dsp_a  <= grad_z_dx;
                    dsp_b  <= {{11{delta_x_subpix[20]}}, delta_x_subpix};
                    dsp2_a <= grad_z_dy;
                    dsp2_b <= {{11{delta_y_subpix[20]}}, delta_y_subpix};
                    init_step <= 4'd1;
                end
                4'd1: init_step <= 4'd2;  // DSP pipeline delay
                4'd2: begin
                    // Capture z + launch s (or s*w when perspective).
                    tri_row_z <= {v_z[0], 16'b0}
                               + $signed(dsp_p[31:0])
                               + $signed(dsp2_p[31:0]);
                    dsp_a  <= grad_s_dx;  // semantic: d(s*w)/dx when persp
                    dsp_b  <= {{11{delta_x_subpix[20]}}, delta_x_subpix};
                    dsp2_a <= grad_s_dy;
                    dsp2_b <= {{11{delta_y_subpix[20]}}, delta_y_subpix};
                    init_step <= 4'd3;
                end
                4'd3: init_step <= 4'd4;
                4'd4: begin
                    // Capture s + launch t.  Base value is v_sw[0]
                    // (already Q16.16) when persp_active, else v_s[0]
                    // sign-extended into Q16.16 (integer-only).
                    tri_row_s <= (tri_persp_active ? v_sw[0]
                                                   : {v_s[0], 16'b0})
                               + $signed(dsp_p[31:0])
                               + $signed(dsp2_p[31:0]);
                    dsp_a  <= grad_t_dx;
                    dsp_b  <= {{11{delta_x_subpix[20]}}, delta_x_subpix};
                    dsp2_a <= grad_t_dy;
                    dsp2_b <= {{11{delta_y_subpix[20]}}, delta_y_subpix};
                    init_step <= 4'd5;
                end
                4'd5: init_step <= 4'd6;
                4'd6: begin
                    // Capture t.  On perspective triangles continue to
                    // the w round; on affine triangles jump straight to
                    // the r (Gouraud) round.
                    tri_row_t <= (tri_persp_active ? v_tw[0]
                                                   : {v_t[0], 16'b0})
                               + $signed(dsp_p[31:0])
                               + $signed(dsp2_p[31:0]);
                    if (tri_persp_active) begin
                        // Round 3 (w) — perspective only.
                        dsp_a  <= grad_w_dx;
                        dsp_b  <= {{11{delta_x_subpix[20]}}, delta_x_subpix};
                        dsp2_a <= grad_w_dy;
                        dsp2_b <= {{11{delta_y_subpix[20]}}, delta_y_subpix};
                        init_step <= 4'd7;
                    end else begin
                        // Affine: skip w; launch r round directly.
                        dsp_a  <= grad_r_dx;
                        dsp_b  <= {{11{delta_x_subpix[20]}}, delta_x_subpix};
                        dsp2_a <= grad_r_dy;
                        dsp2_b <= {{11{delta_y_subpix[20]}}, delta_y_subpix};
                        init_step <= 4'd9;  // jump past w wait state
                    end
                end
                4'd7: init_step <= 4'd8;
                4'd8: begin
                    // Capture w; launch r round (perspective path).
                    tri_row_w <= v_w[0]
                               + $signed(dsp_p[31:0])
                               + $signed(dsp2_p[31:0]);
                    dsp_a  <= grad_r_dx;
                    dsp_b  <= {{11{delta_x_subpix[20]}}, delta_x_subpix};
                    dsp2_a <= grad_r_dy;
                    dsp2_b <= {{11{delta_y_subpix[20]}}, delta_y_subpix};
                    init_step <= 4'd9;
                end
                4'd9: init_step <= 4'd10;  // DSP pipeline delay
                4'd10: begin
                    // Capture r at bbox origin.  Q16.16: v_r[0] is 8-bit,
                    // shifted into bits [23:16]; gradient products land
                    // in bits [31:0].
                    tri_row_r <= {8'b0, v_r[0], 16'b0}
                               + $signed(dsp_p[31:0])
                               + $signed(dsp2_p[31:0]);
                    init_step <= 4'd0;
                    state     <= S_TRI_ROW;
                end
                default: ;
            endcase
        end

        // ============================================================
        // Triangle: Initialise first row
        // ============================================================
        S_TRI_ROW: begin
            // Check for empty bbox (using registered values from S_TRI_BBOX)
            if (tri_xmin > tri_xmax || tri_ymin > tri_ymax) begin
                if (pay_remaining != 24'd0) begin
                    // Mid-batch: advance ring_rdptr (primes v0_word_0
                    // for pay_idx=1) and jump back to vertex-load.
                    ring_rdptr <= (ring_rdptr + 16'd4) & ring_mask;
                    pay_idx    <= 5'd1;
                    state      <= S_PAY_DATA;
                end else begin
                    // Last triangle in batch — flush coalesced pixels.
                    state <= S_FB_FLUSH;
                end
            end else begin
`ifdef GPU_STATS
            stat_triangles <= stat_triangles + 32'd1;
`endif
            tri_active <= 1;
            tri_cur_x <= tri_xmin;
            tri_cur_y <= tri_ymin;
            // Evaluate edge functions at (xmin*16, ymin*16) in 12.4 space.
            // tri_e_init_Apx/Bpy hold the A*px and B*py products registered
            // during S_TRI_MUL_WAIT, so this is a pure 3-way add — one carry
            // chain, no DSP — keeping the cone well inside the 10 ns budget.
            tri_e[0]     <= tri_e_init_Apx[0] + tri_e_init_Bpy[0] + tri_C[0];
            tri_e[1]     <= tri_e_init_Apx[1] + tri_e_init_Bpy[1] + tri_C[1];
            tri_e[2]     <= tri_e_init_Apx[2] + tri_e_init_Bpy[2] + tri_C[2];
            tri_row_e[0] <= tri_e_init_Apx[0] + tri_e_init_Bpy[0] + tri_C[0];
            tri_row_e[1] <= tri_e_init_Apx[1] + tri_e_init_Bpy[1] + tri_C[1];
            tri_row_e[2] <= tri_e_init_Apx[2] + tri_e_init_Bpy[2] + tri_C[2];
            // tri_row_{z,s,t,w,r} were evaluated at the bbox origin
            // (xmin*16, ymin*16) by S_TRI_INIT_ATTRIB.  Copy into the
            // current-pixel walk regs; the row regs are preserved so
            // the per-row Y-step in S_TRI_ROW_NEXT keeps the bbox-
            // origin anchor as it advances down the triangle.
            tri_z <= tri_row_z;
            tri_s <= tri_row_s;
            tri_t <= tri_row_t;
            tri_w <= tri_row_w;
            tri_r <= tri_row_r;
            // tri_ymin_x_stride is the DSP-registered product from
            // S_TRI_MUL_WAIT (tri_ymin × st_fb_stride).  For the Z-buffer
            // we use the configured st_zb_stride (set via CMD_SET_ZB);
            // computing it as tri_ymin * st_zb_stride keeps the row base
            // correct for any ZB dimensions the firmware programs in,
            // not just the 320×200 hardcode that used to live here as
            // "<<<9 + <<<7" (= 640).  One 16×17 fabric multiply; the
            // triangle start already budgets S_TRI_MUL_WAIT cycles.
            tri_fb_row_addr <= st_fb_addr + tri_ymin_x_stride;
            tri_zb_row_addr <= st_zb_addr
                             + $signed(tri_ymin) * $signed({1'b0, st_zb_stride});
            tri_span_count <= 0;  // reset for first row scan
            state <= S_TRI_PIX;
            end // else (bbox not empty)
        end

        // ============================================================
        // Triangle: Row scan — walk xmin..xmax finding the inside extent
        // ------------------------------------------------------------
        // Convex triangles → inside pixels are contiguous per row. Snapshot
        // attribute values at x_start, count inside pixels, and at row end
        // emit a single span into S_FRAG_PIPE via the SRC_SPAN path.
        // ============================================================
        S_TRI_PIX: begin
            if (tri_cur_x > tri_xmax) begin
                // Row scan complete.
                if (tri_span_count != 16'd0) begin
                    // Emit one span for the inside extent of this row.
                    sp_fb_addr   <= tri_fb_row_addr + {{16{1'b0}}, tri_span_x_start};
                    sp_tex_addr  <= st_tex_addr;
                    sp_s         <= tri_span_s_start;
                    sp_t         <= tri_span_t_start;
                    // Gradients in this file are computed per-SUB-PIXEL
                    // (12.4 scaling). The fragment pipe steps sp_* per PIXEL.
                    // Shift-left-4 converts sub-pixel → pixel rate, matching
                    // how tri_s / tri_t / tri_z are advanced in S_TRI_PIX.
                    sp_sstep     <= grad_s_dx <<< 4;
                    sp_tstep     <= grad_t_dx <<< 4;
                    sp_count     <= tri_span_count;
                    // Phase 4d — Gouraud: anchor at first inside pixel,
                    // step per pixel.  grad_r_dx<<<4 converts subpixel-
                    // x rate to pixel-x rate (matches sp_sstep / sp_tstep).
                    sp_light_q    <= tri_span_r_start;
                    sp_light_step <= grad_r_dx <<< 4;
                    // PERSP flag (bit 5) folds in when tri_persp_active.
                    sp_flags     <= (st_depth_func != 0 ? 8'h18 : 8'h00) | 8'h01
                                   | (st_skip_zero ? 8'h04 : 8'h00)
                                   | (tri_persp_active ? 8'h20 : 8'h00);
                    sp_fb_stride <= 16'd1;
                    sp_tex_width <= st_tex_width;
                    // Triangles use the bound texture's full extent — they
                    // are NOT POT-wrap candidates the way BUILD-style spans
                    // are.  Reset masks to no-wrap (0xFFFF) so a previous
                    // CMD_DRAW_SPAN that set wrap masks doesn't bleed into
                    // a subsequent DRAW_TRIANGLES (e.g. world span POT
                    // mask = 63 inherited by an alias-skin triangle would
                    // clip the 200-wide skin to 64 columns and produce
                    // garbage).  Per-triangle masks would require a new
                    // SET_TEXTURE field; for now fix the bleed.
                    sp_tex_w_mask <= 16'hFFFF;
                    sp_tex_h_mask <= 16'hFFFF;
                    sp_z_addr    <= tri_zb_row_addr + {tri_span_x_start, 1'b0};
                    sp_zi        <= tri_span_z_start;
                    sp_zistep    <= grad_z_dx <<< 4;
`ifdef GPU_STATS
                    stat_spans   <= stat_spans + 32'd1;
`endif
`ifdef GPU_PERSP_IMPL
                    // Phase 4c.4 — when perspective is active, route the
                    // triangle's row-walked attributes into the
                    // SPAN_PERSP path's perspective-source regs and arm
                    // the PSS_* sub-FSM.  When affine, disarm exactly as
                    // before.
                    if (tri_persp_active) begin
                        // sp_sZ / sp_tZ / sp_zinv are linearly-interpolated
                        // (s*w, t*w, w) at the span's first inside pixel,
                        // captured in S_TRI_PIX above.  Their per-pixel
                        // x-deltas come from the same shifted-by-4 gradient
                        // values used for the affine sp_*step fields.
                        sp_sZ             <= tri_span_s_start;
                        sp_tZ             <= tri_span_t_start;
                        sp_zinv           <= tri_span_w_start;
                        sp_sZstep         <= grad_s_dx <<< 4;
                        sp_tZstep         <= grad_t_dx <<< 4;
                        sp_zinv_step      <= grad_w_dx <<< 4;
                        persp_active      <= 1;
                        // Arm PSS in its initial state — pass 1 (anchor)
                        // will fire on first fragment-issue, fill the slot
                        // A slope, then the issue stage runs at full
                        // throughput within each 8-pixel segment.
                        persp_seg_a_ready <= 0;
                        persp_seg_b_ready <= 0;
                        persp_first_done  <= 0;
                        // ANCHOR_ONLY entry: kick PSS_RECIP_NA pipeline.
                        // Must also reset persp_pass to PASS_ANCHOR — left
                        // stale at PASS_TO_B from the last iteration of the
                        // previous row's PSS, the first PSS_FINAL of the new
                        // row would otherwise execute the PASS_TO_B writeback
                        // and pre-arm persp_seg_b_ready=1 with garbage
                        // persp_pend_s (= the prior row's anchor).  Segment 2
                        // of every row after the first then swapped that stale
                        // value into sp_s — visible as a 50%+ pixel mismatch
                        // jump at the second 8-pixel boundary.
                        persp_pss         <= PSS_RECIP_NA;
                        persp_pass        <= PSS_PASS_ANCHOR;
                        sp_seg_left       <= 0;
                    end else begin
                        persp_active      <= 0;
                        persp_seg_a_ready <= 0;
                        persp_seg_b_ready <= 0;
                        persp_first_done  <= 0;
                        persp_pss         <= PSS_IDLE;
                        sp_seg_left       <= 0;
                    end
`endif
                    src_mode <= SRC_SPAN;
                    src_done <= 0;
                    state    <= S_FRAG_PIPE;
                    // tri_active stays 1; drain-detect routes back to S_TRI_ROW_NEXT
                end else begin
                    // No inside pixels this row — skip the FRAG_PIPE round-trip.
                    state <= S_TRI_ROW_NEXT;
                end
            end else if (!tri_e[0][31] && !tri_e[1][31] && !tri_e[2][31]) begin
                // Inside triangle — extend the span.
                if (tri_span_count == 16'd0) begin
                    tri_span_x_start <= tri_cur_x;
                    tri_span_s_start <= tri_s;
                    tri_span_t_start <= tri_t;
                    tri_span_z_start <= tri_z;
                    tri_span_w_start <= tri_w;
                    tri_span_r_start <= tri_r;
                end
                tri_span_count <= tri_span_count + 16'd1;
                tri_cur_x <= tri_cur_x + 16'd1;
                tri_e[0] <= tri_e[0] + (tri_A[0] <<< 4);
                tri_e[1] <= tri_e[1] + (tri_A[1] <<< 4);
                tri_e[2] <= tri_e[2] + (tri_A[2] <<< 4);
                tri_z <= tri_z + (grad_z_dx <<< 4);
                tri_s <= tri_s + (grad_s_dx <<< 4);
                tri_t <= tri_t + (grad_t_dx <<< 4);
                tri_w <= tri_w + (grad_w_dx <<< 4);
                tri_r <= tri_r + (grad_r_dx <<< 4);
            end else begin
                // Outside — step without recording.
                tri_cur_x <= tri_cur_x + 16'd1;
                tri_e[0] <= tri_e[0] + (tri_A[0] <<< 4);
                tri_e[1] <= tri_e[1] + (tri_A[1] <<< 4);
                tri_e[2] <= tri_e[2] + (tri_A[2] <<< 4);
                tri_z <= tri_z + (grad_z_dx <<< 4);
                tri_s <= tri_s + (grad_s_dx <<< 4);
                tri_t <= tri_t + (grad_t_dx <<< 4);
                tri_w <= tri_w + (grad_w_dx <<< 4);
                tri_r <= tri_r + (grad_r_dx <<< 4);
            end
        end

        // ============================================================
        // Triangle: Advance to next row (or end triangle)
        // ------------------------------------------------------------
        // Entered either directly from S_TRI_PIX on an empty row, or from
        // the FRAG_PIPE drain detection once the row's span has drained.
        // ============================================================
        S_TRI_ROW_NEXT: begin
            if (tri_cur_y >= tri_ymax) begin
                // Triangle done — clear tri_active.
                tri_active <= 0;
                if (pay_remaining != 24'd0) begin
                    // Mid-batch: more triangles follow.  Advance
                    // ring_rdptr (primes v0_word_0 for pay_idx=1) and
                    // jump back to vertex-load.  Skip the FB flush so
                    // fb_acc keeps coalescing across triangles that
                    // touch the same FB word; word-change writes flush
                    // mid-stream as before.
                    ring_rdptr <= (ring_rdptr + 16'd4) & ring_mask;
                    pay_idx    <= 5'd1;
                    state      <= S_PAY_DATA;
                end else begin
                    state <= S_FB_FLUSH;
                end
            end else begin
                tri_cur_y <= tri_cur_y + 16'd1;
                tri_cur_x <= tri_xmin;
                tri_row_e[0] <= tri_row_e[0] + (tri_B[0] <<< 4);
                tri_row_e[1] <= tri_row_e[1] + (tri_B[1] <<< 4);
                tri_row_e[2] <= tri_row_e[2] + (tri_B[2] <<< 4);
                tri_e[0] <= tri_row_e[0] + (tri_B[0] <<< 4);
                tri_e[1] <= tri_row_e[1] + (tri_B[1] <<< 4);
                tri_e[2] <= tri_row_e[2] + (tri_B[2] <<< 4);
                tri_fb_row_addr <= tri_fb_row_addr + {{16{st_fb_stride[15]}}, st_fb_stride};
                tri_zb_row_addr <= tri_zb_row_addr + {{16{st_zb_stride[15]}}, st_zb_stride};
                tri_row_z <= tri_row_z + (grad_z_dy <<< 4);
                tri_row_s <= tri_row_s + (grad_s_dy <<< 4);
                tri_row_t <= tri_row_t + (grad_t_dy <<< 4);
                tri_row_w <= tri_row_w + (grad_w_dy <<< 4);
                tri_row_r <= tri_row_r + (grad_r_dy <<< 4);
                tri_z <= tri_row_z + (grad_z_dy <<< 4);
                tri_s <= tri_row_s + (grad_s_dy <<< 4);
                tri_t <= tri_row_t + (grad_t_dy <<< 4);
                tri_w <= tri_row_w + (grad_w_dy <<< 4);
                tri_r <= tri_row_r + (grad_r_dy <<< 4);
                tri_span_count <= 16'd0;
                state <= S_TRI_PIX;
            end
        end
`endif // GPU_FEAT_TRIANGLE

        default: state <= S_IDLE;
        endcase
    end
end

// Colormap BRAM initialises to zero in Cyclone V M10K.
// CPU uploads data via MMIO before use.

endmodule
