//
// GPU Core — span rasterizer
//
// Asynchronous 2D/3D GPU for openfpgaOS.  The CPU builds command streams in
// SDRAM; a doorbell DMA copies them into an internal BRAM ring, then the GPU
// rasterises textured/colormapped spans and writes pixels to the framebuffer
// via AXI4.
//
// Two AXI4 master ports:
//   M_RD  — command-stream DMA + texture/cache fills (read only)
//   M_WR  — framebuffer writes + clear DMA (write only)
// SRAM:
//   GPU-private scratch storage for low-locality tables that are too large
//   for BRAM.  The current user is the translucency blend LUT.
//
// MMIO registers expose control, status, fence sync, texture flush and the
// remaining translucency LUT upload path.  Command data is not accepted over
// MMIO.
//

`default_nettype none

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
    // SRAM word port — private GPU scratch
    // ================================================================
    output reg         sram_rd,
    output reg         sram_wr,
    output reg         sram_rd_half,
    output reg         sram_rd_hi,
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
    // CMD_FLIP side-port — single-cycle pulse into axi_periph_slave's
    // fb_swap_pending block, signalling "queue swap to gpu_swap_idx".
    // Routed alongside the existing kernel sysreg path; the slave's
    // mux gives GPU priority on conflicting same-cycle writes.
    // ================================================================
    output reg         gpu_swap_req,
    output reg  [1:0]  gpu_swap_idx,

    input  wire        slave_swap_pending,    // CMD_FLIP backpressure from display slave

    // ================================================================
    // Status outputs
    // ================================================================
    output wire        busy,
    output reg  [31:0] fence_reached,
    // Verilator diagnostic outputs.  Production builds keep them
    // tied off so debug cones do not survive into timing/resource closure.
    output wire [5:0]  dbg_state,
    output wire [5:0]  dbg_setup_step,
    output wire [31:0] dbg_aux,
    output wire [31:0] dbg_frag
);

localparam GPU_ENABLE_PERSP     = 1'b1;

wire active = reset_n & gpu_enable;

assign dbg_state = 6'd0;
assign dbg_setup_step = 6'd0;
assign dbg_aux = 32'd0;
assign dbg_frag = 32'd0;

// ================================================================
// MMIO Register Map
// ================================================================
// 0x00  GPU_CTRL          W   bit0=enable, bit1=soft_reset, bit2=ring_reset
// 0x04  GPU_RING_WRPTR    R   Published ring write pointer
// 0x08  Reserved
// 0x0C  GPU_DMA_SRC       W   SDRAM byte address of command buffer to pull
// 0x10  GPU_RING_RDPTR    R   GPU read pointer
// 0x14  GPU_STATUS        R   bit0=busy, bit1=ring_empty, bit2=dma_busy,
//                              bit3=transluc_busy, [5:4]=dma_state,
//                              bit6=dma_desc_full
// 0x18  GPU_FENCE         R   Last completed fence token
// 0x1C  GPU_DMA_LEN       W   Word count to pull (max 4096)
// 0x20  GPU_TRANSLUC_ADDR W   byte address into transluc[] upload window
// 0x24  GPU_TRANSLUC_DATA W   word data into transluc[] upload window
// 0x28  GPU_TEX_FLUSH     W   Flush texture cache (write any value)
// 0x2C  GPU_DMA_KICK      W   Write 1 to fire DMA pull from (SRC, LEN)
// 0x30  Reserved
// 0x34  GPU_DBG_WR_INFLIGHT R Low 4 bits = outstanding FB write responses
// 0x38  Reserved
// 0x3C  Reserved
//
// Upload semantics:
//   * GPU_DMA_* pulls either homogeneous payloads or already-encoded mixed
//     command streams from SDRAM into the ordered ring BRAM with fabric-side
//     AXI INCR bursts.
//   * The DMA publishes ring_wrptr only after the final word lands, so the
//     decoder never observes a partial command.
// Poll GPU_STATUS:dma_busy before reusing an SDRAM batch/stream buffer.

// Ring BRAM: 16 KB = 4096 words, dual-port M10K
// Port A: doorbell-DMA writes command streams from SDRAM
// Port B: GPU reads during command fetch (1-cycle latency)
localparam RING_WORDS = 4096;  // 16 KB
localparam RING_ADDR_BITS = 12;

reg [31:0] ring_bram [0:RING_WORDS-1];
reg [RING_ADDR_BITS-1:0] ring_wr_addr;  // DMA write pointer (word index)
reg [RING_ADDR_BITS-1:0] ring_wrptr;     // Published write pointer (word index)
reg [RING_ADDR_BITS-1:0] ring_rdptr;     // GPU read pointer (word index)
reg [RING_ADDR_BITS-1:0] ring_rd_addr;   // Port-B read address
reg [31:0] ring_rd_data;                  // Ring BRAM port B read output (registered)

// CPU upload address for the transluc[] LUT (32 KB).  Palookups live in SDRAM
// and the GPU reads them through gpu_tex_cache port B; transluc[] is GPU-private
// SRAM with a tiny last-word cache for repeated blend lookups.
reg [14:0] transluc_wr_addr;   // Auto-increment byte address into transluc[]
reg        tex_flush_req;      // Pulse to flush texture cache
reg        soft_reset;         // Pulse: resets FSM state + ring pointers
reg        ring_reset;         // Pulse: reset ring_rdptr (from MMIO, consumed by FSM)

// ---- Doorbell-DMA pull from SDRAM into ring BRAM ----
// Latched on MMIO writes; consumed by the dedicated DMA FSM below.
// dma_words_left counts words remaining in the entire kick; the FSM
// re-issues an AR for each 16-beat sub-burst until the count drains.
reg [31:0] dma_src_latched;       // SDRAM byte addr (from GPU_DMA_SRC)
reg [12:0] dma_len_latched;       // Total words to pull (max 4096; 13-bit fits)
localparam DMA_S_IDLE    = 2'd0;
localparam DMA_S_AR      = 2'd1;
localparam DMA_S_R       = 2'd2;
localparam DMA_S_PUBLISH = 2'd3;  // 1-cycle pulse to publish ring_wrptr
reg [1:0]  dma_state;
reg        dma_publish_wrptr;     // 1-cycle pulse, latched by ring_wrptr
reg [31:0] dma_burst_addr;        // SDRAM byte addr of next sub-burst
reg [12:0] dma_words_left;        // Words remaining in the kick (across sub-bursts)
reg [8:0]  dma_burst_words;       // Words remaining in current sub-burst (1..256)

reg [31:0] dma_desc_src [0:1];
reg [12:0] dma_desc_len [0:1];
reg        dma_desc_rd;
reg        dma_desc_wr;
reg [1:0]  dma_desc_count;
wire       dma_desc_empty = (dma_desc_count == 2'd0);
wire       dma_desc_full  = (dma_desc_count == 2'd2);
wire       dma_desc_push_req = reg_wr && (reg_addr == 4'd11) && reg_wdata[0]
                            && (dma_len_latched != 13'd0) && !dma_desc_full;
wire       dma_desc_pop_now = (dma_state == DMA_S_IDLE) && !dma_desc_empty;
wire       dma_pull_busy = (dma_state != DMA_S_IDLE) || !dma_desc_empty;
wire       dma_busy = dma_pull_busy;

// Anti-starvation counter for DMA_S_AR: count cycles where DMA wants to
// assert AR but is held off by !dma_bus_idle (sustained tex/blend traffic).
// At STARVE_THRESHOLD (512 cycles ≈ 5 µs at 100 MHz), force AR
// regardless of the idle gate.  The downstream tex/blend AR mux already
// blocks new tex ARs while dma_owns_ar=1, so any in-flight tex
// transaction drains naturally and DMA's AR lands on the next free slot
// at the slave.  Real DMA path is ~25 µs/batch end-to-end; the bound is
// shorter than that, so it only triggers under genuine starvation —
// the wedge case observed in Duke3D's heavy textured-rendering frames.
localparam DMA_STARVE_THRESHOLD = 10'd512;
reg [9:0] dma_starve_count;

wire ring_empty = (ring_rdptr == ring_wrptr);
wire [15:0] ring_wrptr_bytes = {2'b0, ring_wrptr, 2'b0};
wire [15:0] ring_rdptr_bytes = {2'b0, ring_rdptr, 2'b0};
wire transluc_upload_busy;

assign busy = dma_busy || transluc_upload_busy || !ring_empty || (state != S_IDLE);

// Port B: GPU read (synchronous, 1-cycle latency)
always @(posedge clk)
    ring_rd_data <= ring_bram[ring_rd_addr];

// DMA word-write into ring BRAM port A: asserted by the DMA FSM
// when an R-beat lands (dma_state==DMA_S_R && m_rd_rvalid).  These
// "raw" wires are driven below where the DMA FSM lives.
wire        dma_ring_wr_raw;
wire [31:0] dma_ring_wdata_raw;

// Canonical altsyncram-inferable single-port write to ring_bram.  Commands are
// staged in SDRAM and copied here by the doorbell DMA only.  That leaves one
// writer for port A, avoiding a CPU/DMA collision mux and skid registers.
always @(posedge clk) begin
    if (dma_ring_wr_raw)
        ring_bram[ring_wr_addr] <= dma_ring_wdata_raw;
end

// MMIO write handling + ring_wr_addr management (BRAM index pointer).
// The actual ring_bram write lives in the dedicated always block above
// to keep its inference shape clean.
always @(posedge clk) begin
    if (!reset_n) begin
        ring_wrptr      <= 0;
        ring_wr_addr    <= 0;
        tex_flush_req   <= 0;
        soft_reset      <= 0;
        ring_reset      <= 0;
        dma_src_latched <= 32'd0;
        dma_len_latched <= 13'd0;
    end else begin
        tex_flush_req <= 0;
        soft_reset    <= 0;
        ring_reset    <= 0;

        // ring_wr_addr advances once per DMA beat.  Keeping it here
        // (not in the BRAM-write block) means the BRAM block stays canonical.
        if (dma_ring_wr_raw)
            ring_wr_addr <= ring_wr_addr + 1'b1;

        // Upload end publishes ring_wrptr atomically (covering every
        // payload word copied into ring BRAM).  Otherwise the decoder
        // doesn't see in-flight upload words (it gates only S_IDLE on
        // ring_empty).
        if (dma_publish_wrptr)
            ring_wrptr <= ring_wr_addr;

        if (reg_wr) begin
            case (reg_addr)
                4'd0: begin  // GPU_CTRL: bit1=soft_reset, bit2=ring_reset
                    if (reg_wdata[1]) soft_reset <= 1;
                    if (reg_wdata[2]) begin
                        ring_reset   <= 1;
                        ring_wr_addr <= 0;
                        ring_wrptr   <= 0;
                    end
                end
                4'd1: begin end // GPU_RING_WRPTR is read-only now
                4'd2: begin end
                4'd3: begin  // GPU_DMA_SRC
                    dma_src_latched <= reg_wdata;
                end
                4'd7: begin  // GPU_DMA_LEN — clamp to ring depth
                    dma_len_latched <= reg_wdata[12:0];
                end
                4'd8: begin end // GPU_TRANSLUC_ADDR handled by SRAM LUT upload FSM
                4'd9: begin end // GPU_TRANSLUC_DATA handled by SRAM LUT upload FSM
                4'd10: begin // GPU_TEX_FLUSH
                    tex_flush_req <= 1;
                end
                4'd11: begin // GPU_DMA_KICK — fire pull
                    // Handled by the DMA descriptor queue FSM below.
                end
                default: ;
            endcase
        end
    end
end

// MMIO read mux
always @(*) begin
    case (reg_addr)
        4'd1:    reg_rdata = {16'b0, ring_wrptr_bytes};
        4'd4:    reg_rdata = {16'b0, ring_rdptr_bytes};
        // Compact production status:
        //   bit 0     = busy
        //   bit 1     = ring_empty
        //   bit 2     = dma_busy (active or queued SDRAM DMA pull)
        //   bit 3     = translucency SRAM upload/lookup busy
        //   bits[5:4] = dma_state (0=IDLE, 1=AR, 2=R, 3=PUBLISH)
        //   bit 6     = DMA descriptor FIFO full
        4'd5:    reg_rdata = {25'b0, dma_desc_full, dma_state, transluc_upload_busy,
                              dma_busy, ring_empty, busy};
        4'd6:    reg_rdata = fence_reached;
        // Compact current-inflight readback for the write-balance test.
        4'd13:   reg_rdata = {28'b0, m_wr_inflight};
        default: reg_rdata = 32'b0;
    endcase
end

// ================================================================
// transluc[] LUT — 32 KB BUILD-style indexed-color blend table
// ================================================================
// The table lives in external SRAM, not FPGA BRAM.  That recovers the
// 32 M10K blocks previously inferred for transluc_bram while keeping the
// random-access blend table private to the GPU.
//
// SRAM upload is handshaked.  The CPU writes GPU_TRANSLUC_ADDR once and
// then writes GPU_TRANSLUC_DATA words, polling GPU_STATUS bit3 before each
// word.  The old BRAM path accepted one word/cycle; SRAM cannot, so writes
// are only accepted while the LUT SRAM state is idle.
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
localparam LUTSRAM_IDLE       = 2'd0;
localparam LUTSRAM_WRITE_WAIT = 2'd1;
localparam LUTSRAM_READ_WAIT  = 2'd2;

reg [1:0]  lutsram_state;
reg        lutsram_seen_busy;

reg [14:0] transluc_rd_addr;
reg        transluc_lookup_fire;
reg [3:0]  transluc_cache_valid;
reg [13:0] transluc_cache_addr [0:3];
reg [15:0] transluc_cache_data [0:3];
reg [1:0]  transluc_cache_replace;

wire transluc_sram_lookup_ready =
    (lutsram_state == LUTSRAM_IDLE) && !sram_busy;

assign transluc_upload_busy = (lutsram_state != LUTSRAM_IDLE);

always @(posedge clk) begin
    if (!reset_n) begin
        sram_rd                 <= 1'b0;
        sram_wr                 <= 1'b0;
        sram_rd_half            <= 1'b0;
        sram_rd_hi              <= 1'b0;
        sram_addr               <= 22'd0;
        sram_wdata              <= 32'd0;
        sram_wstrb              <= 4'd0;
        transluc_wr_addr        <= 15'd0;
        lutsram_state           <= LUTSRAM_IDLE;
        lutsram_seen_busy       <= 1'b0;
        transluc_cache_valid    <= 4'b0;
        transluc_cache_addr[0]  <= 14'd0;
        transluc_cache_addr[1]  <= 14'd0;
        transluc_cache_addr[2]  <= 14'd0;
        transluc_cache_addr[3]  <= 14'd0;
        transluc_cache_data[0]  <= 16'd0;
        transluc_cache_data[1]  <= 16'd0;
        transluc_cache_data[2]  <= 16'd0;
        transluc_cache_data[3]  <= 16'd0;
        transluc_cache_replace  <= 2'd0;
    end else begin
        sram_rd    <= 1'b0;
        sram_wr    <= 1'b0;
        sram_rd_half <= 1'b0;
        sram_rd_hi   <= 1'b0;
        sram_wstrb <= 4'd0;

        if (reg_wr && reg_addr == 4'd8) begin
            transluc_wr_addr <= reg_wdata[14:0];
            transluc_cache_valid <= 4'b0;
        end

        case (lutsram_state)
            LUTSRAM_IDLE: begin
                lutsram_seen_busy <= 1'b0;
                if (transluc_lookup_fire && !sram_busy) begin
                    sram_rd               <= 1'b1;
                    sram_rd_half          <= 1'b1;
                    sram_rd_hi            <= transluc_rd_addr[1];
                    sram_addr             <= {9'd0, transluc_rd_addr[14:2]};
                    lutsram_state         <= LUTSRAM_READ_WAIT;
                end else if (reg_wr && reg_addr == 4'd9 && !sram_busy) begin
                    sram_wr          <= 1'b1;
                    sram_addr        <= {9'd0, transluc_wr_addr[14:2]};
                    sram_wdata       <= reg_wdata;
                    sram_wstrb       <= 4'hF;
                    transluc_wr_addr <= transluc_wr_addr + 15'd4;
                    transluc_cache_valid <= 4'b0;
                    lutsram_state    <= LUTSRAM_WRITE_WAIT;
                end
            end

            LUTSRAM_WRITE_WAIT: begin
                if (sram_busy)
                    lutsram_seen_busy <= 1'b1;
                if (lutsram_seen_busy && !sram_busy)
                    lutsram_state <= LUTSRAM_IDLE;
            end

            LUTSRAM_READ_WAIT: begin
                if (sram_rdata_valid) begin
                    transluc_cache_valid[transluc_cache_replace] <= 1'b1;
                    transluc_cache_addr[transluc_cache_replace]  <= transluc_rd_addr[14:1];
                    transluc_cache_data[transluc_cache_replace]  <= transluc_rd_addr[1]
                                                                  ? sram_rdata[31:16]
                                                                  : sram_rdata[15:0];
                    transluc_cache_replace <= transluc_cache_replace + 2'd1;
                    lutsram_state         <= LUTSRAM_IDLE;
                end
            end

            default: begin
                lutsram_state <= LUTSRAM_IDLE;
            end
        endcase
    end
end

// Cmap read path through gpu_tex_cache port B.  At p1→p2 shift, latch
// the SDRAM byte address for the upcoming p2 fragment's cmap lookup;
// tex_cache port B accepts the request combinationally on p2's cycle,
// and the byte response is available one cycle later on p2b's cycle.
// Cache misses stall the pipeline via fp_pipe_stall's cmap_pipe_wait
// term until the fill completes.
//
// PALOOKUP_BASE + (colormap_id << 14) anchors the slot for fragments.
// Direct-affine records carry a per-lane slot; parametric spans carry a
// per-command slot.  The per-pixel term (light << 8 | texel) indexes
// within the slot.  Slot encoding matches the SDK's
// of_gpu_palookup_upload() on the host side.
reg [25:0] cmap_req_addr_reg;

// resp_data_b is wired from gpu_tex_cache port B below; the byte falls out
// of resp_data_b[7:0] (req_wide_b is tied to 0 so port B always returns
// byte-mode responses).  No held-byte register needed because resp_data_b
// is held by tex_cache itself across the consumer's stall window — same
// contract port A uses.
// Drive port B's req high whenever the fragment in p2 needs cmap AND
// the FB-write sub-FSM is idle.  Gating on `fbss == FBSS_IDLE` is
// load-bearing: while fbss is mid-flush (cross-word FB write) the
// pipeline shift is frozen but cmap_req_addr_reg / p2_valid look
// "current" to the cache.  Without the gate, the cache keeps accepting
// new req_addr_b values (driven by the still-stale cmap_req_addr_reg
// from the pre-stall shifts) and pipe_addr_b drifts past the pixel
// waiting in p2b; when the stall releases and p3 captures p2b,
// cmap_rd_data is computed from the WRONG pixel's lane.  Surfaces as
// off-by-one byte-lane reads (triCK_y1_x2, w300_r1_px6, Duke3D
// rendering artifacts).
//
// Also gate while p2b is waiting for a prior cmap response, and issue p2's
// cmap request only when the p2->p2b shift is otherwise allowed.  If port B
// cannot accept that request in the shift cycle, cmap_issue_wait stalls the
// pipe instead of letting p2b hold a fragment whose colormap lookup was never
// queued.  This request/shift handshake is what keeps row-major span-group
// cache thrash from producing localized wrong pixels.
wire        cmap_resp_valid_b;
wire        fp_pipe_shift_blocked;
wire        cmap_pipe_wait = p2b_valid && p2b_flags[SPAN_COLORMAP]
                          && !cmap_resp_valid_b;
wire        cmap_req_ready_b;
wire        cmap_issue_base = p2_valid && p2_flags[SPAN_COLORMAP]
                           && (fbss == FBSS_IDLE)
                           && !cmap_pipe_wait
                           && !fp_pipe_shift_blocked;
wire        cmap_req_valid_b = cmap_issue_base;
wire        cmap_issue_wait = cmap_issue_base && !cmap_req_ready_b;
wire [15:0] cmap_resp_data_b;
wire [7:0]  cmap_rd_data = cmap_resp_data_b[7:0];

// cmap_rd_data is a continuous-assign wire.  tex_cache port B returns
// the selected byte at req_addr_b[1:0] when req_wide_b == 0.

// ================================================================
// Shared DSP multiply + reciprocal LUT
// ================================================================
// Used by parametric span setup and perspective segment setup.
// Registered DSP multiply (18×18 maps to one Cyclone V DSP block)
reg signed [31:0] dsp_a;
reg signed [31:0] dsp_b;
(* multstyle = "dsp" *) reg signed [63:0] dsp_p;
always @(posedge clk) dsp_p <= dsp_a * dsp_b;

// Second DSP slot — used for parallel multiply pipelines.  PSS uses dsp/dsp2
// for sZ×recip and tZ×recip; span setup uses the pair for screen-space
// address/attribute products.
reg signed [31:0] dsp2_a;
reg signed [31:0] dsp2_b;
(* multstyle = "dsp" *) reg signed [63:0] dsp2_p;
always @(posedge clk) dsp2_p <= dsp2_a * dsp2_b;

function signed [31:0] q16_round_product;
    input signed [63:0] product;
    begin
        q16_round_product = (product + 64'sd32768) >>> 16;
    end
endfunction


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

// ================================================================
// Texture Cache instance
// ================================================================
// tex_req_* are combinational from the pipelined fragment processor's
// issue logic (single configuration: pipelined fragment processor is
// always on).
wire        tex_req_valid;
wire [25:0] tex_req_addr;
wire        tex_req_wide;
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

// Port B is wired to the cmap read path: cmap_req_addr_reg holds the
// per-pixel SDRAM byte address; the request fires whenever p2 has a
// SPAN_COLORMAP fragment.  Tex_cache returns the byte combinationally
// in resp_data_b[7:0] (req_wide_b = 0 → byte mode).  Misses route through
// the shared AXI fill machine; the consumer-side stall is enforced by
// fp_pipe_stall's cmap_pipe_wait term.
gpu_tex_cache tex_cache (
    .clk(clk),
    .reset_n(reset_n),
    .flush(tex_flush_req),
    // Port A — texture fetch (existing wiring, unchanged)
    .req_valid(tex_req_valid),
    .req_ready(tex_req_ready),
    .req_addr(tex_req_addr),
    .req_wide(tex_req_wide),
    .resp_valid(tex_resp_valid),
    .resp_data(tex_resp_data),
    // Port B — cmap reads (one byte per fragment, SDRAM-backed palookup)
    .req_valid_b(cmap_req_valid_b),
    .req_ready_b(cmap_req_ready_b),
    .req_addr_b(cmap_req_addr_reg),
    .req_wide_b(1'b0),
    .resp_valid_b(cmap_resp_valid_b),
    .resp_data_b(cmap_resp_data_b),
    .axi_arvalid(tex_axi_arvalid),
    .axi_arready(tex_axi_arready),
    .axi_araddr(tex_axi_araddr),
    .axi_arlen(tex_axi_arlen),
    .axi_rvalid(tex_axi_rvalid),
    .axi_rdata(tex_axi_rdata),
    .axi_rlast(tex_axi_rlast)
);

// ================================================================
// AXI4 Read Master — texture cache + fragment readback
//                    + doorbell-DMA command pull
// ================================================================
// Three consumers share M0: the texture cache (multi-beat line fills),
// the fragment readback path for depth tests / translucent FB blending
// (single-beat, only fires on those fragment types), and the doorbell-DMA puller
// that streams a CPU-prepared command buffer from SDRAM into the ring
// BRAM.  Arbitration is split between AR and R channels:
//   * AR channel: DMA reserves as soon as it's in S_AR/S_R, so no
//     new tex/blend AR is accepted while a DMA burst is being set up.
//   * R channel: DMA only masks tex/blend R-beats while it's actually
//     in S_R (the data phase).  While DMA waits in S_AR for
//     `dma_bus_idle`, an in-flight tex burst MUST keep receiving its
//     R-beats — masking them would leave the cache deadlocked in
//     S_FILL_DATA after DMA's burst eventually starts.
wire blend_owns_m0  = (fbss == FBSS_ZTEST_AR_WAIT)
                   || (fbss == FBSS_ZTEST_R_WAIT)
                   || (fbss == FBSS_BLEND_AR_WAIT)
                   || (fbss == FBSS_BLEND_R_WAIT);
wire dma_owns_ar    = (dma_state == DMA_S_AR)
                   || (dma_state == DMA_S_R);
wire dma_owns_r     = (dma_state == DMA_S_R);

reg         dma_arvalid;
reg  [31:0] dma_araddr;
reg  [7:0]  dma_arlen;

// AR channel: dma_owns_ar wins (DMA reserves AR from the moment it
// enters S_AR; new tex/blend ARs are rejected until DMA's burst has
// fully drained back to S_IDLE).  R channel: dma_owns_r wins only
// while DMA is actively in S_R — see comment block above for why.
assign m_rd_arvalid    = dma_owns_ar   ? dma_arvalid
                       : blend_owns_m0 ? blend_arvalid
                       :                 tex_axi_arvalid;
assign m_rd_araddr     = dma_owns_ar   ? dma_araddr
                       : blend_owns_m0 ? blend_araddr
                       :                 tex_axi_araddr;
assign m_rd_arlen      = dma_owns_ar   ? dma_arlen
                       : blend_owns_m0 ? 8'd0
                       :                 tex_axi_arlen;
assign tex_axi_arready = (dma_owns_ar || blend_owns_m0) ? 1'b0 : m_rd_arready;
assign tex_axi_rvalid  = (dma_owns_r  || blend_owns_m0) ? 1'b0 : m_rd_rvalid;
assign tex_axi_rdata   = m_rd_rdata;
assign tex_axi_rlast   = (dma_owns_r  || blend_owns_m0) ? 1'b0 : m_rd_rlast;

wire blend_arready = (blend_owns_m0 && !dma_owns_ar) ? m_rd_arready : 1'b0;
wire blend_rvalid  = (blend_owns_m0 && !dma_owns_r ) ? m_rd_rvalid  : 1'b0;
wire [31:0] blend_rdata = m_rd_rdata;

// ---- Doorbell-DMA FSM ----
// Streams queued DMA descriptors into ring BRAM port A, splitting each
// descriptor at 16-beat AXI4 burst boundaries.  Enters DMA_S_AR
// only when the M0 bus is idle (no blend in flight, no texture-cache
// AR/R outstanding) so no fight for the bus mid-burst.  Per accepted
// R-beat, drives dma_ring_wr_raw/dma_ring_wdata_raw into ring BRAM
// port A and auto-advances the unpublished write address.
wire dma_bus_idle = !blend_owns_m0 && !tex_m0_in_flight;
assign dma_ring_wr_raw    = (dma_state == DMA_S_R) && m_rd_rvalid;
assign dma_ring_wdata_raw = m_rd_rdata;

always @(posedge clk) begin
    if (!reset_n) begin
        dma_state         <= DMA_S_IDLE;
        dma_burst_addr    <= 32'd0;
        dma_words_left    <= 13'd0;
        dma_burst_words   <= 9'd0;
        dma_arvalid       <= 1'b0;
        dma_araddr        <= 32'd0;
        dma_arlen         <= 8'd0;
        dma_publish_wrptr <= 1'b0;
        dma_starve_count  <= 10'd0;
        dma_desc_src[0]   <= 32'd0;
        dma_desc_src[1]   <= 32'd0;
        dma_desc_len[0]   <= 13'd0;
        dma_desc_len[1]   <= 13'd0;
        dma_desc_rd       <= 1'b0;
        dma_desc_wr       <= 1'b0;
        dma_desc_count    <= 2'd0;
    end else begin
        dma_publish_wrptr <= 1'b0;     // one-cycle pulse default

        if (dma_desc_push_req) begin
            dma_desc_src[dma_desc_wr] <= dma_src_latched;
            dma_desc_len[dma_desc_wr] <= dma_len_latched;
            dma_desc_wr <= ~dma_desc_wr;
        end

        if (dma_desc_pop_now)
            dma_desc_rd <= ~dma_desc_rd;

        case ({dma_desc_push_req, dma_desc_pop_now})
            2'b10: dma_desc_count <= dma_desc_count + 2'd1;
            2'b01: dma_desc_count <= dma_desc_count - 2'd1;
            default: ;
        endcase

        // Starvation counter: increments while DMA is in S_AR with
        // arvalid not yet asserted AND the idle gate is blocking.
        // Resets the moment we leave S_AR (or assert arvalid).
        if (dma_state == DMA_S_AR && !dma_arvalid && !dma_bus_idle)
            dma_starve_count <= dma_starve_count + 10'd1;
        else
            dma_starve_count <= 10'd0;

        case (dma_state)
        DMA_S_IDLE: begin
            dma_arvalid <= 1'b0;
            if (!dma_desc_empty) begin
                dma_burst_addr <= dma_desc_src[dma_desc_rd];
                dma_words_left <= dma_desc_len[dma_desc_rd];
                dma_state      <= DMA_S_AR;
            end
        end

        DMA_S_AR: begin
            if (dma_arvalid) begin
                // AR asserted; wait for arready.  When the master accepts,
                // drop arvalid and advance to the R phase.
                if (m_rd_arready) begin
                    dma_arvalid <= 1'b0;
                    dma_state   <= DMA_S_R;
                end
            end else if (dma_bus_idle || dma_starve_count >= DMA_STARVE_THRESHOLD) begin
                // Either the bus is idle (normal path) OR we've been
                // starved for too long under sustained tex/blend traffic
                // (forced path).  Without the starvation override, dense
                // textured rendering in Duke3D could keep tex_m0_in_flight
                // continuously asserted and DMA would spin in S_AR
                // forever, manifesting as the silent wedge in the
                // doorbell-DMA path (no trap, no progress).
                //
                // Forcing arvalid is safe: tex_axi_arready is already
                // gated to 0 by dma_owns_ar (line ~700), so any in-flight
                // tex transaction completes naturally without a new tex
                // AR sneaking in.  DMA's AR lands on the next free arb
                // slot at the slave.
                //
                // Capped at 16 beats per sub-burst because axi_sdram_slave
                // truncates arlen to 4 bits.  16 beats × ~10 cycles each:
                // 1920 / 16 = 120 sub-bursts × ~30 cycles = ~36 µs
                // end-to-end DMA fetch.
                if (dma_words_left >= 13'd16) begin
                    dma_arlen       <= 8'd15;
                    dma_burst_words <= 9'd16;
                end else begin
                    dma_arlen       <= dma_words_left[7:0] - 8'd1;
                    dma_burst_words <= {1'b0, dma_words_left[7:0]};
                end
                dma_araddr  <= dma_burst_addr;
                dma_arvalid <= 1'b1;
            end
            // else: bus is busy and starvation bound not yet hit — wait
            // without asserting AR.
        end

        DMA_S_R: begin
            dma_arvalid <= 1'b0;
            if (m_rd_rvalid) begin
                dma_burst_words <= dma_burst_words - 9'd1;
                dma_words_left  <= dma_words_left  - 13'd1;
                dma_burst_addr  <= dma_burst_addr  + 32'd4;
                if (m_rd_rlast) begin
                    // Sub-burst complete.  Either we're done with the
                    // whole kick (all words landed → publish ring_wrptr
                    // so the decoder finally sees them), or there's
                    // another sub-burst to issue without publishing yet.
                    if (dma_words_left == 13'd1)
                        dma_state <= DMA_S_PUBLISH;
                    else
                        dma_state <= DMA_S_AR;
                end
            end
        end

        DMA_S_PUBLISH: begin
            // Every DMA beat has landed in ring_bram and ring_wr_addr has
            // advanced, so publish the new write pointer atomically.
            dma_publish_wrptr <= 1'b1;
            dma_state         <= DMA_S_IDLE;
        end

        default: dma_state <= DMA_S_IDLE;
        endcase
    end
end

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
localparam CMD_FENCE          = 8'h02;
// CMD_FLIP (0x42) — GPU-triggered display page swap.  2-word payload:
//   word 0: bits[1:0] = back-buffer index (0/1/2 → FB_ADDR_{0,1,2})
//   word 1: fence token (published to fence_reached after the swap fires)
// Semantics: drains all outstanding m_wr_* writes (same primitive as
// the upgraded CMD_FENCE), then pulses the gpu_swap_req side-port to
// axi_periph_slave for one cycle with gpu_swap_idx = payload[0][1:0],
// then publishes the fence token.  Lets apps queue a vsync swap in
// the GPU command stream — see docs/cr-gpu-triggered-flip.md.
localparam CMD_FLIP           = 8'h42;
localparam CMD_CLEAR_RECT     = 8'h11;  // 3-word payload:
                                          // word0 = start byte addr (CPU
                                          //   pre-computes fb_base + y*stride
                                          //   + x);
                                          // word1 = {w[31:16], h[15:0]};
                                          // word2 = {stride[31:16], pad[15:8],
                                          //   color[7:0]} — stride==0 falls
                                          //   back to st_fb_stride; color's
                                          //   low 8 bits are replicated 4×
                                          //   per word.
localparam CMD_SET_TEXTURE    = 8'h20;
localparam CMD_SET_FB         = 8'h23;
// Generic span command.  A full parametric header carries screen-space
// attribute planes once, followed by packed {u,v,count} records.  Compact
// direct-affine commands use the same opcode with the shorter 4+7N payload
// for already-prepared independent lanes.
localparam CMD_DRAW_PARAM_SPAN_LIST   = 8'h48;

localparam PARAM_RECORD_U16V16_COUNT16 = 4'd0;

// ================================================================
// GPU State Registers (sticky, set by SET_* commands)
// ================================================================
// Only datapath-visible texture state is stored here.  The texture pipe is
// I8-only and does not implement wrap/clamp mode registers.
reg [15:0] st_fb_stride;

// ================================================================
// Span Registers (loaded from command payload)
// ================================================================
reg [31:0] sp_fb_addr;
reg [31:0] sp_tex_addr;
reg signed [31:0] sp_s, sp_t;
reg signed [31:0] sp_sstep, sp_tstep;
reg [15:0] sp_count;
// Phase 4d — Gouraud-capable light.  Palookups have 64 shade rows, so
// light is carried as signed Q6.16 and the fragment pipe sees bits [21:16].
// sp_light_step is signed Q6.16, the per-pixel x delta.  Direct-affine
// records set sp_light_step = 0 for flat lighting.
reg signed [23:0] sp_light_q;
reg signed [23:0] sp_light_step;
wire [5:0]        sp_light = sp_light_q[21:16];
reg [3:0]  sp_flags;
// Per-span colormap_id for direct-affine and parametric dispatch.
reg [3:0]  sp_colormap_id;
reg signed [31:0] sp_fb_stride;
reg [15:0] sp_tex_width;
// POT wrap masks: (sp_s[31:16] & sp_tex_w_mask) and
// (sp_t[31:16] & sp_tex_h_mask)
// before the address math.  Default 16'hFFFF (no-op) so callers that
// don't set word 8 see the multiply-mode behaviour.  The masks
// reproduce BUILD's hlineasm4 shift-mode wrap exactly when tex_w/tex_h
// are powers of two (always true for BUILD/Quake/Doom textures).
reg [15:0] sp_tex_w_mask;
reg [15:0] sp_tex_h_mask;
reg [1:0]  sp_clamp_enable;
reg signed [31:0] sp_s_clamp_min;
reg signed [31:0] sp_s_clamp_max;
reg signed [31:0] sp_t_clamp_min;
reg signed [31:0] sp_t_clamp_max;
reg        sp_z_write_enable;
reg        sp_z_test_enable;
reg [31:0] sp_z_addr;
reg signed [31:0] sp_z_step;
reg signed [31:0] sp_z_value;
reg signed [31:0] sp_z_value_step;

// Unified span front-end.  Parametric records derive scalar spans from
// compact screen-space planes.  Direct-affine records carry already-computed
// lane state and use the same scalar span emitter.
reg        spanprod_active;
reg        spanprod_compact_direct;
reg        spanprod_direct_affine;
reg [1:0]  spanprod_idx;
reg [2:0]  spanprod_record_count;
reg [15:0] spanprod_records_left;
reg [2:0]  spanprod_calc_step;
reg [31:0] spanprod_fb_base;
reg signed [31:0] spanprod_fb_major_step;
reg signed [31:0] spanprod_fb_minor_step;
reg [31:0] spanprod_tex_addr;
reg [15:0] spanprod_tex_width;
reg [15:0] spanprod_tex_w_mask;
reg [15:0] spanprod_tex_h_mask;
reg [3:0]  spanprod_flags;
reg [3:0]  spanprod_colormap_id;
reg        spanprod_attr_persp;
reg        spanprod_attr_q29;
reg        spanprod_span_axis;
reg        spanprod_header_supported;
reg        spanprod_z_write;
reg        spanprod_z_test;
reg signed [31:0] spanprod_attr0_origin;
reg signed [31:0] spanprod_attr0_du;
reg signed [31:0] spanprod_attr0_dv;
reg signed [31:0] spanprod_attr1_origin;
reg signed [31:0] spanprod_attr1_du;
reg signed [31:0] spanprod_attr1_dv;
reg signed [31:0] spanprod_attr2_origin;
reg signed [31:0] spanprod_attr2_du;
reg signed [31:0] spanprod_attr2_dv;
reg signed [23:0] spanprod_light_origin;
reg signed [23:0] spanprod_light_du;
reg signed [23:0] spanprod_light_dv;
reg signed [31:0] spanprod_clamp0_min;
reg signed [31:0] spanprod_clamp0_max;
reg signed [31:0] spanprod_clamp1_min;
reg signed [31:0] spanprod_clamp1_max;
reg [31:0] spanprod_z_base;
reg signed [31:0] spanprod_z_major_step;
reg signed [31:0] spanprod_z_minor_step;
reg signed [15:0] spanprod_u [0:3];
reg signed [15:0] spanprod_v [0:3];
reg [15:0] spanprod_count [0:3];
reg [31:0] spanprod_fb_addr_r;
reg [31:0] spanprod_z_addr_r;
reg signed [31:0] spanprod_attr0_start_r;
reg signed [31:0] spanprod_attr1_start_r;
reg signed [31:0] spanprod_attr2_start_r;
reg signed [23:0] spanprod_light_start_r;
reg [31:0] spanprod_direct_fb_addr [0:3];
reg [31:0] spanprod_direct_tex_addr [0:3];
reg signed [31:0] spanprod_direct_s [0:3];
reg signed [31:0] spanprod_direct_t [0:3];
reg signed [31:0] spanprod_direct_sstep [0:3];
reg signed [31:0] spanprod_direct_tstep [0:3];
reg [3:0]  spanprod_direct_colormap_id [0:3];
reg [5:0]  spanprod_direct_light [0:3];

// Internal span flags.  The command wire format is still the public 8-bit
// layout; span_flags_from_wire packs only the live bits into these four bits.
//
// SPAN_COLORMAP gates the cmap LUT lookup in the p2b → p3 mux:
//   set:   p3_color = palookup[colormap_id][light][texel]
//   clear: p3_color = texel  (raw passthrough — UI text, untextured glyphs)
//
localparam SPAN_COLORMAP    = 0;
localparam SPAN_SKIP_ZERO   = 1;
localparam SPAN_PERSP       = 2;
localparam SPAN_TRANSLUC    = 3;  // route p3_color through transluc[] LUT

function [3:0] span_flags_from_wire;
    input [7:0] flags;
    begin
        span_flags_from_wire = 4'b0;
        span_flags_from_wire[SPAN_COLORMAP] = flags[0];
        span_flags_from_wire[SPAN_SKIP_ZERO] = flags[2];
        span_flags_from_wire[SPAN_PERSP] = GPU_ENABLE_PERSP && flags[5];
        span_flags_from_wire[SPAN_TRANSLUC] = flags[6];
    end
endfunction

wire [1:0] spanprod_last_idx =
      (spanprod_record_count >= 3'd4) ? 2'd3
    : (spanprod_record_count == 3'd3) ? 2'd2
    : (spanprod_record_count == 3'd2) ? 2'd1
    : 2'd0;

task load_param_span_list_payload_word;
    input [5:0]  idx;
    input [31:0] data;
    begin
        if (spanprod_compact_direct) begin
            case (idx)
                6'd0: begin
                    spanprod_direct_affine <= 1'b1;
                    spanprod_record_count <= {1'b0, (data[31:28] >= 4'd4) ? 2'd3
                                               : (data[31:28] == 4'd3) ? 2'd2
                                               : (data[31:28] == 4'd2) ? 2'd1
                                               : 2'd0} + 3'd1;
                    spanprod_records_left <= {14'd0, (data[31:28] >= 4'd4) ? 2'd3
                                              : (data[31:28] == 4'd3) ? 2'd2
                                              : (data[31:28] == 4'd2) ? 2'd1
                                              : 2'd0} + 16'd1;
                    spanprod_flags        <= span_flags_from_wire(data[27:20]);
                    spanprod_attr_persp   <= 1'b0;
                    spanprod_attr_q29     <= 1'b0;
                    spanprod_span_axis    <= 1'b0;
                    spanprod_header_supported <= 1'b1;
                    spanprod_z_write      <= 1'b0;
                    spanprod_z_test       <= 1'b0;
                    spanprod_clamp0_min   <= 32'sd0;
                    spanprod_clamp0_max   <= 32'sd0;
                    spanprod_clamp1_min   <= 32'sd0;
                    spanprod_clamp1_max   <= 32'sd0;
                    spanprod_count[0] <= 16'd0;
                    spanprod_count[1] <= 16'd0;
                    spanprod_count[2] <= 16'd0;
                    spanprod_count[3] <= 16'd0;
                    spanprod_direct_colormap_id[0] <= 4'd0;
                    spanprod_direct_colormap_id[1] <= 4'd0;
                    spanprod_direct_colormap_id[2] <= 4'd0;
                    spanprod_direct_colormap_id[3] <= 4'd0;
                end
                6'd1: spanprod_tex_width <= data[15:0];
                6'd2: begin
                    spanprod_tex_w_mask <= (data[15:0]  == 16'd0) ? 16'hFFFF : data[15:0];
                    spanprod_tex_h_mask <= (data[31:16] == 16'd0) ? 16'hFFFF : data[31:16];
                end
                6'd3: spanprod_fb_minor_step <= data;
                6'd4: spanprod_direct_fb_addr[0] <= data;
                6'd5: spanprod_direct_tex_addr[0] <= data;
                6'd6: begin spanprod_count[0] <= data[15:0]; spanprod_direct_light[0] <= data[21:16]; spanprod_direct_colormap_id[0] <= data[31:28]; end
                6'd7: spanprod_direct_s[0] <= data;
                6'd8: spanprod_direct_t[0] <= data;
                6'd9: spanprod_direct_sstep[0] <= data;
                6'd10: spanprod_direct_tstep[0] <= data;
                6'd11: spanprod_direct_fb_addr[1] <= data;
                6'd12: spanprod_direct_tex_addr[1] <= data;
                6'd13: begin spanprod_count[1] <= data[15:0]; spanprod_direct_light[1] <= data[21:16]; spanprod_direct_colormap_id[1] <= data[31:28]; end
                6'd14: spanprod_direct_s[1] <= data;
                6'd15: spanprod_direct_t[1] <= data;
                6'd16: spanprod_direct_sstep[1] <= data;
                6'd17: spanprod_direct_tstep[1] <= data;
                6'd18: spanprod_direct_fb_addr[2] <= data;
                6'd19: spanprod_direct_tex_addr[2] <= data;
                6'd20: begin spanprod_count[2] <= data[15:0]; spanprod_direct_light[2] <= data[21:16]; spanprod_direct_colormap_id[2] <= data[31:28]; end
                6'd21: spanprod_direct_s[2] <= data;
                6'd22: spanprod_direct_t[2] <= data;
                6'd23: spanprod_direct_sstep[2] <= data;
                6'd24: spanprod_direct_tstep[2] <= data;
                6'd25: spanprod_direct_fb_addr[3] <= data;
                6'd26: spanprod_direct_tex_addr[3] <= data;
                6'd27: begin spanprod_count[3] <= data[15:0]; spanprod_direct_light[3] <= data[21:16]; spanprod_direct_colormap_id[3] <= data[31:28]; end
                6'd28: spanprod_direct_s[3] <= data;
                6'd29: spanprod_direct_t[3] <= data;
                6'd30: spanprod_direct_sstep[3] <= data;
                6'd31: spanprod_direct_tstep[3] <= data;
                default: ;
            endcase
        end else begin
            case (idx)
                6'd0:  spanprod_fb_base <= data;
                6'd1:  spanprod_fb_major_step <= data;
                6'd2:  spanprod_fb_minor_step <= data;
                6'd3:  spanprod_tex_addr <= data;
                6'd4:  spanprod_tex_width <= data[15:0];
                6'd5:  spanprod_tex_w_mask <= (data[15:0] == 16'd0) ? 16'hFFFF : data[15:0];
                6'd6:  spanprod_tex_h_mask <= (data[15:0] == 16'd0) ? 16'hFFFF : data[15:0];
                6'd7: begin
                    spanprod_direct_affine <= 1'b0;
                    spanprod_flags         <= span_flags_from_wire(data[7:0]);
                    spanprod_colormap_id   <= data[11:8];
                    spanprod_attr_persp    <= data[12] && (data[23:20] == PARAM_RECORD_U16V16_COUNT16);
                    spanprod_attr_q29      <= data[13] && data[12] && (data[23:20] == PARAM_RECORD_U16V16_COUNT16);
                    spanprod_span_axis     <= data[16];
                    spanprod_z_write       <= data[24] && (data[23:20] == PARAM_RECORD_U16V16_COUNT16);
                    spanprod_z_test        <= data[25] && (data[23:20] == PARAM_RECORD_U16V16_COUNT16);
                    spanprod_header_supported <=
                           ((data[23:20] == PARAM_RECORD_U16V16_COUNT16)
                            && (data[19:17] == 3'd0)
                            && !data[15]
                            && !data[14]
                            && (!data[13] || data[12])
                            && (!data[12] || GPU_ENABLE_PERSP)
                            && !data[27]
                            && !data[26]
                            && (!(data[25] || data[24])
                                || (data[12] && !data[6]
                                    && (!data[24] || data[25] || !data[2]))));
                end
                6'd8:  spanprod_attr0_origin <= data;
                6'd9:  spanprod_attr0_du <= data;
                6'd10: spanprod_attr0_dv <= data;
                6'd11: spanprod_attr1_origin <= data;
                6'd12: spanprod_attr1_du <= data;
                6'd13: spanprod_attr1_dv <= data;
                6'd14: spanprod_attr2_origin <= data;
                6'd15: spanprod_attr2_du <= data;
                6'd16: spanprod_attr2_dv <= data;
                6'd17: spanprod_light_origin <= data[23:0];
                6'd18: spanprod_light_du <= data[23:0];
                6'd19: spanprod_light_dv <= data[23:0];
                6'd20: spanprod_clamp0_min <= data;
                6'd21: spanprod_clamp0_max <= data;
                6'd22: spanprod_clamp1_min <= data;
                6'd23: spanprod_clamp1_max <= data;
                6'd26: spanprod_z_base <= data;
                6'd27: spanprod_z_major_step <= data;
                6'd28: spanprod_z_minor_step <= data;
                6'd29: begin
                    if (data[15:0] == 16'd0) begin
                        spanprod_record_count <= 3'd0;
                        spanprod_records_left <= 16'd0;
                    end else if (data[15:0] >= 16'd4) begin
                        spanprod_record_count <= 3'd4;
                        spanprod_records_left <= data[15:0];
                    end else begin
                        spanprod_record_count <= {1'b0, data[1:0]};
                        spanprod_records_left <= data[15:0];
                    end
                    spanprod_count[0] <= 16'd0;
                    spanprod_count[1] <= 16'd0;
                    spanprod_count[2] <= 16'd0;
                    spanprod_count[3] <= 16'd0;
                    spanprod_direct_colormap_id[0] <= 4'd0;
                    spanprod_direct_colormap_id[1] <= 4'd0;
                    spanprod_direct_colormap_id[2] <= 4'd0;
                    spanprod_direct_colormap_id[3] <= 4'd0;
                end
                6'd31: begin
                    spanprod_u[0] <= data[15:0];
                    spanprod_v[0] <= data[31:16];
                end
                6'd32: begin
                    spanprod_count[0] <= data[15:0];
                    spanprod_u[1] <= data[31:16];
                end
                6'd33: begin
                    spanprod_v[1] <= data[15:0];
                    spanprod_count[1] <= data[31:16];
                end
                6'd34: begin
                    spanprod_u[2] <= data[15:0];
                    spanprod_v[2] <= data[31:16];
                end
                6'd35: begin
                    spanprod_count[2] <= data[15:0];
                    spanprod_u[3] <= data[31:16];
                end
                6'd36: begin
                    spanprod_v[3] <= data[15:0];
                    spanprod_count[3] <= data[31:16];
                end
                default: ;
            endcase
        end
    end
endtask

task spanprod_launch_fb_mul;
    begin
        if (spanprod_span_axis) begin
            dsp_a  <= $signed({{16{spanprod_u[spanprod_idx][15]}}, spanprod_u[spanprod_idx]});
            dsp_b  <= spanprod_fb_major_step;
            dsp2_a <= $signed({{16{spanprod_v[spanprod_idx][15]}}, spanprod_v[spanprod_idx]});
            dsp2_b <= spanprod_fb_minor_step;
        end else begin
            dsp_a  <= $signed({{16{spanprod_v[spanprod_idx][15]}}, spanprod_v[spanprod_idx]});
            dsp_b  <= spanprod_fb_major_step;
            dsp2_a <= $signed({{16{spanprod_u[spanprod_idx][15]}}, spanprod_u[spanprod_idx]});
            dsp2_b <= spanprod_fb_minor_step;
        end
    end
endtask

task spanprod_launch_z_mul;
    begin
        if (spanprod_span_axis) begin
            dsp_a  <= $signed({{16{spanprod_u[spanprod_idx][15]}}, spanprod_u[spanprod_idx]});
            dsp_b  <= spanprod_z_major_step;
            dsp2_a <= $signed({{16{spanprod_v[spanprod_idx][15]}}, spanprod_v[spanprod_idx]});
            dsp2_b <= spanprod_z_minor_step;
        end else begin
            dsp_a  <= $signed({{16{spanprod_v[spanprod_idx][15]}}, spanprod_v[spanprod_idx]});
            dsp_b  <= spanprod_z_major_step;
            dsp2_a <= $signed({{16{spanprod_u[spanprod_idx][15]}}, spanprod_u[spanprod_idx]});
            dsp2_b <= spanprod_z_minor_step;
        end
    end
endtask

task spanprod_launch_attr_mul;
    input signed [31:0] du;
    input signed [31:0] dv;
    begin
        dsp_a  <= $signed({{16{spanprod_u[spanprod_idx][15]}}, spanprod_u[spanprod_idx]});
        dsp_b  <= du;
        dsp2_a <= $signed({{16{spanprod_v[spanprod_idx][15]}}, spanprod_v[spanprod_idx]});
        dsp2_b <= dv;
    end
endtask

task spanprod_load_generated_span;
    begin
        sp_count       <= spanprod_count[spanprod_idx];
        sp_fb_stride   <= spanprod_fb_minor_step;
        sp_tex_width   <= (spanprod_tex_width == 16'd0) ? 16'd1 : spanprod_tex_width;
        sp_tex_w_mask  <= spanprod_tex_w_mask;
        sp_tex_h_mask  <= spanprod_tex_h_mask;

        if (spanprod_direct_affine) begin
            sp_fb_addr     <= spanprod_direct_fb_addr[spanprod_idx];
            sp_tex_addr    <= spanprod_direct_tex_addr[spanprod_idx];
            sp_colormap_id <= spanprod_direct_colormap_id[spanprod_idx];
            sp_light_q     <= {2'b00, spanprod_direct_light[spanprod_idx], 16'b0};
            sp_light_step  <= 24'sd0;
            sp_flags       <= spanprod_flags & ~(4'b0001 << SPAN_PERSP);
            sp_clamp_enable <= 2'b00;
            sp_z_write_enable <= 1'b0;
            sp_z_test_enable  <= 1'b0;
            sp_z_addr       <= 32'd0;
            sp_z_step       <= 32'sd0;
            sp_z_value      <= 32'sd0;
            sp_z_value_step <= 32'sd0;
            sp_s           <= spanprod_direct_s[spanprod_idx];
            sp_t           <= spanprod_direct_t[spanprod_idx];
            sp_sstep       <= spanprod_direct_sstep[spanprod_idx];
            sp_tstep       <= spanprod_direct_tstep[spanprod_idx];
            sp_sZ          <= 32'sd0;
            sp_tZ          <= 32'sd0;
            sp_zinv        <= 32'sd0;
            sp_sZstep      <= 32'sd0;
            sp_tZstep      <= 32'sd0;
            sp_zinv_step   <= 32'sd0;
            sp_persp_q29_mode <= 1'b0;
            persp_active      <= 1'b0;
            persp_first_done  <= 1'b0;
            persp_swap_pending <= 1'b0;
            persp_pss         <= PSS_IDLE;
            persp_pass        <= PSS_PASS_ANCHOR;
            sp_seg_left       <= 4'd0;
        end else begin
            sp_fb_addr     <= spanprod_fb_addr_r;
            sp_tex_addr    <= spanprod_tex_addr;
            sp_colormap_id <= spanprod_colormap_id;
            sp_light_q     <= spanprod_light_start_r;
            sp_light_step  <= spanprod_span_axis
                            ? spanprod_light_dv : spanprod_light_du;
            sp_flags       <= spanprod_attr_persp
                            ? (spanprod_flags | (4'b0001 << SPAN_PERSP))
                            : (spanprod_flags & ~(4'b0001 << SPAN_PERSP));
            sp_clamp_enable[0] <= (spanprod_clamp0_min != 32'sd0)
                               || (spanprod_clamp0_max != 32'sd0);
            sp_clamp_enable[1] <= (spanprod_clamp1_min != 32'sd0)
                               || (spanprod_clamp1_max != 32'sd0);
            sp_s_clamp_min <= spanprod_clamp0_min;
            sp_s_clamp_max <= spanprod_clamp0_max;
            sp_t_clamp_min <= spanprod_clamp1_min;
            sp_t_clamp_max <= spanprod_clamp1_max;
            sp_z_write_enable <= spanprod_z_write;
            sp_z_test_enable  <= spanprod_z_test;
            sp_z_addr       <= spanprod_z_addr_r;
            sp_z_step       <= spanprod_z_minor_step;
            sp_z_value      <= spanprod_attr2_start_r;
            sp_z_value_step <= spanprod_span_axis
                             ? spanprod_attr2_dv : spanprod_attr2_du;
            sp_sZstep      <= spanprod_span_axis
                            ? spanprod_attr0_dv : spanprod_attr0_du;
            sp_tZstep      <= spanprod_span_axis
                            ? spanprod_attr1_dv : spanprod_attr1_du;
            sp_zinv_step   <= spanprod_span_axis
                            ? spanprod_attr2_dv : spanprod_attr2_du;
            sp_light_step  <= spanprod_span_axis
                            ? spanprod_light_dv : spanprod_light_du;
            sp_persp_q29_mode <= spanprod_attr_q29;
            if (spanprod_attr_persp) begin
            sp_s           <= 32'sd0;
            sp_t           <= 32'sd0;
            sp_sstep       <= 32'sd0;
            sp_tstep       <= 32'sd0;
            sp_sZ          <= spanprod_attr0_start_r;
            sp_tZ          <= spanprod_attr1_start_r;
            sp_zinv        <= spanprod_attr2_start_r;
            persp_active      <= 1'b1;
            persp_first_done  <= 1'b0;
            persp_seg_a_ready <= 1'b0;
            persp_seg_b_ready <= 1'b0;
            persp_swap_pending <= 1'b0;
            persp_pss         <= PSS_IDLE;
            persp_pass        <= PSS_PASS_ANCHOR;
            sp_seg_left       <= 4'd0;
            end else begin
                sp_s           <= spanprod_attr0_start_r;
                sp_t           <= spanprod_attr1_start_r;
                sp_sstep       <= spanprod_span_axis
                                ? spanprod_attr0_dv : spanprod_attr0_du;
                sp_tstep       <= spanprod_span_axis
                                ? spanprod_attr1_dv : spanprod_attr1_du;
                sp_sZ          <= 32'sd0;
                sp_tZ          <= 32'sd0;
                sp_zinv        <= 32'sd0;
                persp_active      <= 1'b0;
                persp_first_done  <= 1'b0;
                persp_swap_pending <= 1'b0;
                persp_pss         <= PSS_IDLE;
                persp_pass        <= PSS_PASS_ANCHOR;
                sp_seg_left       <= 4'd0;
            end
        end
        src_done <= 1'b0;
    end
endtask

function [3:0] fb_lane_mask;
    input [1:0] lane;
    begin
        case (lane)
            2'd0: fb_lane_mask = 4'b0001;
            2'd1: fb_lane_mask = 4'b0010;
            2'd2: fb_lane_mask = 4'b0100;
            default: fb_lane_mask = 4'b1000;
        endcase
    end
endfunction

function [31:0] fb_lane_data;
    input [1:0] lane;
    input [7:0] byte_value;
    begin
        case (lane)
            2'd0: fb_lane_data = {24'b0, byte_value};
            2'd1: fb_lane_data = {16'b0, byte_value, 8'b0};
            2'd2: fb_lane_data = {8'b0, byte_value, 16'b0};
            default: fb_lane_data = {byte_value, 24'b0};
        endcase
    end
endfunction

function [31:0] fb_lane_data_mask;
    input [1:0] lane;
    begin
        case (lane)
            2'd0: fb_lane_data_mask = 32'h000000FF;
            2'd1: fb_lane_data_mask = 32'h0000FF00;
            2'd2: fb_lane_data_mask = 32'h00FF0000;
            default: fb_lane_data_mask = 32'hFF000000;
        endcase
    end
endfunction

function [15:0] fb_halfword_read;
    input [31:0] word_value;
    input lane_hi;
    begin
        fb_halfword_read = lane_hi ? word_value[31:16] : word_value[15:0];
    end
endfunction

function [7:0] fb_lane_read;
    input [31:0] word_value;
    input [1:0] lane;
    begin
        case (lane)
            2'd0: fb_lane_read = word_value[7:0];
            2'd1: fb_lane_read = word_value[15:8];
            2'd2: fb_lane_read = word_value[23:16];
            default: fb_lane_read = word_value[31:24];
        endcase
    end
endfunction

// ================================================================
// Main FSM
// ================================================================
localparam S_IDLE           = 6'd0;
localparam S_RING_WAIT      = 6'd1;  // 1-cycle BRAM read latency
localparam S_DECODE         = 6'd2;
localparam S_PAY_DATA       = 6'd3;  // 1 word/cycle from BRAM
localparam S_EXECUTE        = 6'd4;
localparam S_FB_FLUSH       = 6'd5;
localparam S_FRAG_PIPE      = 6'd6;  // Unified pipelined fragment processor

// Rect-clear states — for partial-rect FB clears (letterbox bars,
// status-bar wipes, menu pane backgrounds).  Issues word-by-word AXI
// writes through M_WR with byte-strobed partial-word edges and is also
// the full-frame clear primitive.
localparam S_CLEAR_RECT       = 6'd7;  // entry: per-row setup (no-op if h=0)
localparam S_CLEAR_RECT_WORD  = 6'd8;  // emit AXI write for current word
localparam S_SPANPROD_SETUP   = 6'd9;  // launch fb products for current record
localparam S_SPANPROD_MUL_WAIT = 6'd10; // DSP latency wait
localparam S_SPANPROD_CAPTURE = 6'd11; // capture product pair / launch next
localparam S_SPANPROD_EMIT    = 6'd12; // emit generated span into fragment pipe

reg [5:0] state;

// Command decoding
reg [7:0]  cmd_type;
reg [12:0] cmd_payload_words;

// Pre-decoded one-hot dispatch flags. Set in S_DECODE based on the
// registered cmd_type and consumed in S_EXECUTE. Pre-decoding shortens
// the combinational path from cmd_type to the per-command state regs
// (notably sp_tstep, which had a -0.6 ns critical path through the
// 8-bit case decoder before this change).
reg cmd_is_fence;
reg cmd_is_clear_rect;
reg cmd_is_set_fb;
reg cmd_is_draw_param_span_list;
reg cmd_is_flip;

// Outstanding-write tracker for CMD_FENCE / CMD_FLIP drain semantics.
// Increments on m_wr_* AW handshake.  Decrements on m_wr_bvalid (slave
// pulses it for one cycle since axi_sdram_slave's bready is hardwired to 1
// upstream).
// CMD_FENCE/CMD_FLIP stall in S_EXECUTE while non-zero, so fence_reached
// / gpu_swap_req only fire after pixel writes commit to SDRAM.  4 bits is
// plenty (typical inflight is 1-3).
reg [3:0] m_wr_inflight;

// Latched payload for CMD_FENCE / CMD_FLIP — published only after
// m_wr_inflight drains in S_EXECUTE.  Pre-CR the fence token was
// written to fence_reached directly in S_PAY_DATA, which raced with
// pending pixel writes (see cr-gpu-fence-write-completion.md).
reg [31:0] pending_fence_token;
reg [1:0]  pending_swap_idx;

// SDRAM address layout for palookups.  PALOOKUP_BASE is the byte offset
// of slot 0; slots are spaced 16 KB apart.  Each slot has 32 shade rows
// and 256 entries per row, padded to 16 KB so the slot index multiplier
// is a clean shift.  CPU uploads and GPU cmap reads share these constants
// without any per-slot register state.  Keep in sync with
// OF_GPU_PALOOKUP_AXI_OFFSET in firmware/api/of_gpu.h.
localparam [25:0] PALOOKUP_BASE   = 26'h3400000;  // 52 MB into SDRAM

// Payload streaming state — ring_rd_data is routed directly to each
// destination reg in S_PAY_DATA; no intermediate payload array.
// pay_idx saturates at 63 (any payload word past that still drains the
// ring via pay_remaining but has nowhere to go).
reg [5:0]  pay_idx;
reg [12:0] pay_remaining;  // total payload words still to consume from the ring
                           // (ring BRAM is 4096 words, so 13 bits covers the
                           // largest valid command payload)

task spanprod_prepare_next_record_chunk;
    reg [15:0] next_left;
    begin
        next_left = spanprod_records_left - {13'd0, spanprod_record_count};
        spanprod_records_left <= next_left;
        if (next_left >= 16'd4)
            spanprod_record_count <= 3'd4;
        else
            spanprod_record_count <= {1'b0, next_left[1:0]};

        spanprod_count[0] <= 16'd0;
        spanprod_count[1] <= 16'd0;
        spanprod_count[2] <= 16'd0;
        spanprod_count[3] <= 16'd0;
        spanprod_idx       <= 2'd0;
        spanprod_calc_step <= 3'd0;

        // Consume the first word of the next packed-record chunk and prime
        // ring_rd_data so S_PAY_DATA sees it with pay_idx=31.
        ring_rdptr   <= ring_rdptr + 1'b1;
        ring_rd_addr <= ring_rdptr + 1'b1;
        pay_idx      <= 6'd31;
        state        <= S_PAY_DATA;
    end
endtask

// ================================================================
// Pipelined Fragment Processor — stage registers
// ================================================================
// Single fragment processor for direct and generated spans.
//
// 5 logical stages, with combinational tex_req drive (1-cycle cache latency):
//   S0a (Source snap):   capture current sp_* into a small pre-issue register
//                        and advance the source state. This isolates the
//                        source muxes from the texture-row DSP.
//   S0b (Issue, comb):   drive tex_req_valid/addr from p0 + tx_mul_q. The
//                        cache sees the request the same cycle and accepts
//                        if req_ready=1 (combinational). On commitment
//                        (`tex_req_valid && tex_req_ready` in same cycle),
//                        latch p1 with p0's metadata.
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
//   * fb_write_buffer_stall          — p3 crossed words while the FB write
//                                      queue has no free entry
//
// Source mode: 0 = SPAN (sp_*).
// p0a: source snapshot / DSP-input stage.  This stage is intentionally small
// and local: it breaks the timing path from the wide sp_* source muxes into
// the texture-row multiply.
reg        p0a_valid;
reg [5:0]  p0a_light;
reg [3:0]  p0a_colormap_id;
reg [3:0]  p0a_flags;
reg [31:0] p0a_fb_addr;
reg signed [15:0] p0a_s_int;
reg [31:0] p0a_tex_base;
reg signed [15:0] p0a_t_y;
reg [15:0] p0a_tex_width;
reg        p0a_z_test;
reg        p0a_z_write;
reg [31:0] p0a_z_addr;
reg [15:0] p0a_z_value;

// p0: cache-issue stage. Holds the pixel whose texture-row multiply output is
// already available in tx_mul_q. p0 -> p1 transition is the "issue commit"
// event, gated on the cache asserting req_ready in the same cycle p0 drives
// req_valid.
reg        p0_valid;
reg [5:0]  p0_light;
reg [3:0]  p0_colormap_id;
reg [3:0]  p0_flags;
reg [31:0] p0_fb_addr;
reg signed [15:0] p0_s_int;     // for the post-mul add
reg [31:0] p0_tex_base;         // sp_tex_addr at issue time
reg        p0_z_test;
reg        p0_z_write;
reg [31:0] p0_z_addr;
reg [15:0] p0_z_value;

// DSP-pipelined texture multiply.  The p0a source snapshot gives this DSP a
// narrow, local input boundary; tx_mul_q is updated only when that snapshot
// promotes into p0, so the following cycle's cache request sees the matching
// product.
(* multstyle = "dsp" *) reg signed [31:0] tx_mul_q;

reg        p1_valid;
reg [5:0]  p1_light;
reg [3:0]  p1_colormap_id;
reg [3:0]  p1_flags;
reg [31:0] p1_fb_addr;
reg        p1_z_test;
reg        p1_z_write;
reg [31:0] p1_z_addr;
reg [15:0] p1_z_value;

reg        p2_valid;
reg [7:0]  p2_color;          // tex result
reg [3:0]  p2_flags;
reg [31:0] p2_fb_addr;
reg        p2_discard;        // skip-zero outcome
reg        p2_z_test;
reg        p2_z_write;
reg [31:0] p2_z_addr;
reg [15:0] p2_z_value;

// p2b: 1-cycle delay between p2 (cmap addr issued) and p3 (cmap data captured).
// Cmap BRAM has 2-cycle effective latency from NB-set of cmap_rd_addr to
// cmap_rd_data being valid for that index, so we need a no-op shift stage.
reg        p2b_valid;
reg [7:0]  p2b_color;
reg [3:0]  p2b_flags;
reg [31:0] p2b_fb_addr;
reg        p2b_discard;
reg        p2b_z_test;
reg        p2b_z_write;
reg [31:0] p2b_z_addr;
reg [15:0] p2b_z_value;

reg        p3_valid;
reg [7:0]  p3_color;          // final color (post-cmap if applicable)
reg [3:0]  p3_flags;
reg [31:0] p3_fb_addr;
reg        p3_discard;
reg        p3_z_test;
reg        p3_z_write;
reg [31:0] p3_z_addr;
reg [15:0] p3_z_value;

// FB write sub-FSM (lives within S3, pauses pipeline when not IDLE)
localparam FBSS_IDLE        = 4'd0;
localparam FBSS_FLUSH_W_RSP = 4'd2;  // wait for write-buffer AW/W acceptance
localparam FBSS_ZTEST_AR_WAIT = 4'd4;
localparam FBSS_ZTEST_R_WAIT  = 4'd5;
// Translucent-blend sub-flow.  SPAN_TRANSLUC fragments are first collected
// into a same-word lane group while the fragment pipe keeps running.  The
// blend unit then reads the destination FB word once, serialises the
// transluc[] lookups for the active lanes, and commits the modified word
// back into fb_acc.  One active lane is the old single-pixel path; adjacent
// horizontal / span-group lanes can amortise the expensive FB read.
//   BLEND_REQ       — wait for M0 to be free of texture-cache traffic
//   BLEND_AR_WAIT   — issue AR; m_rd_* muxed to BLEND while in this/R_WAIT
//   BLEND_R_WAIT    — wait for R, capture rdata + fb_acc same-word bypass
//   BLEND_SELECT    — find next active lane and issue transluc[] SRAM read
//   BLEND_LUT_WAIT  — wait for SRAM read response, merge byte, loop/finish
//   BLEND_APPLY     — write the grouped word-fragment into fb_acc
localparam FBSS_BLEND_REQ      = 4'd6;
localparam FBSS_BLEND_AR_WAIT  = 4'd7;
localparam FBSS_BLEND_R_WAIT   = 4'd8;
localparam FBSS_BLEND_LUT_WAIT = 4'd9;
localparam FBSS_BLEND_APPLY    = 4'd10;
localparam FBSS_BLEND_SELECT   = 4'd11;
reg [3:0] fbss;

// BLEND same-word group state.  Source bytes are stored per framebuffer byte
// lane; duplicate lanes flush the current group first so overdraw order is
// preserved exactly.
reg        blend_group_active;
reg [31:0] blend_group_word_addr;
reg [3:0]  blend_group_mask;
reg [31:0] blend_group_src_data;
reg [31:0] blend_result_word;
reg [1:0]  blend_lane_iter;
reg [1:0]  blend_lut_lane;
reg        blend_arvalid;
reg [31:0] blend_araddr;
wire [7:0]  blend_lut_src_byte = fb_lane_read(blend_group_src_data, blend_lane_iter);
wire [7:0]  blend_lut_fb_byte  = fb_lane_read(blend_result_word, blend_lane_iter);
wire [14:0] blend_lut_addr_w   = {blend_lut_src_byte[7:1], blend_lut_fb_byte};
wire [13:0] transluc_cache_lookup_addr = blend_lut_addr_w[14:1];
wire        transluc_cache_hit0 = transluc_cache_valid[0]
                               && (transluc_cache_addr[0] == transluc_cache_lookup_addr);
wire        transluc_cache_hit1 = transluc_cache_valid[1]
                               && (transluc_cache_addr[1] == transluc_cache_lookup_addr);
wire        transluc_cache_hit2 = transluc_cache_valid[2]
                               && (transluc_cache_addr[2] == transluc_cache_lookup_addr);
wire        transluc_cache_hit3 = transluc_cache_valid[3]
                               && (transluc_cache_addr[3] == transluc_cache_lookup_addr);
wire        transluc_cache_hit = transluc_cache_hit0
                              || transluc_cache_hit1
                              || transluc_cache_hit2
                              || transluc_cache_hit3;
wire [15:0] transluc_cache_half = transluc_cache_hit0 ? transluc_cache_data[0] :
                                  transluc_cache_hit1 ? transluc_cache_data[1] :
                                  transluc_cache_hit2 ? transluc_cache_data[2] :
                                  transluc_cache_data[3];
wire [7:0]  transluc_cache_byte = blend_lut_addr_w[0]
                                ? transluc_cache_half[15:8]
                                : transluc_cache_half[7:0];
// Pre-computed after the grouped FB read, consumed by FBSS_BLEND_APPLY.  Hoists
// the 32-bit equality compare `fb_acc_addr == blend_group_word_addr` out of
// the BLEND_APPLY cycle so the only logic between blend_group_word_addr (FF)
// and fb_acc_addr (FF) on the same-word merge path is a 1-bit selector
// rather than a 32-bit eq + state mux.  Closes the worst GPU-internal
// path (`blend_group_word_addr[2] → fb_acc_addr[2]` at -1.031 ns).  Both
// operands are stable across the grouped lookup loop: blend_group_word_addr is
// latched before entry to BLEND_REQ; fb_acc_addr is only written by FBSS, and
// the SELECT/LUT states do not write it.
reg        blend_p3_match_r;

// Tracks whether the texture cache currently has a read in flight on M0.
// Used by FBSS_BLEND_REQ to wait for M0 to become idle before grabbing it.
reg        tex_m0_in_flight;

// (fbss_z_rdata removed — ZWAIT now launches the SRAM write in the same
// cycle it sees the read response, so sram_rdata is live when we need it.)

reg src_done;            // source has issued its last pixel; pipeline draining

// ----------------------------------------------------------------
// Combinational tex_req drive
// ----------------------------------------------------------------
// Drives tex_req from p0 (registered issue metadata) and tx_mul_q
// (registered DSP multiply output for that p0). p0 is loaded from p0a one
// cycle after p0a snapshots the source pixel.  That extra boundary lets the
// fitter place the source muxes and the texture-row DSP independently.
//
// Critical-path benefit: the long combinational `sp_t * sp_tex_width` path
// is broken at the DSP output register, so the fitter can pack it into a
// DSP slice and the path from `tx_mul_q` register through the post-multiply
// adds to the cache RAM port is short.

// cmap_pipe_wait is declared with the cmap port-B request wires above because
// it also gates new cmap accepts while p2b is waiting for an older response.
// On hit this is always 0 because resp_valid_b is high combinationally the
// same cycle pipe_addr_b matches.  See gpu_tex_cache.v for the held-response
// semantics this relies on.
// Small write FIFO in front of the AXI write master. Producers enqueue
// 32-bit word writes and the drain path emits up to 8-beat AXI bursts when
// adjacent full-word writes are already buffered.
localparam FBWQ_DEPTH = 16;
reg [31:0] fbwq_addr [0:FBWQ_DEPTH-1];
reg [31:0] fbwq_data [0:FBWQ_DEPTH-1];
reg [3:0]  fbwq_strb [0:FBWQ_DEPTH-1];
reg [15:0] fbwq_link_next;
reg [3:0]  fbwq_rd_ptr;
reg [3:0]  fbwq_wr_ptr;
reg [4:0]  fbwq_count;
reg [3:0]  fbwq_burst_remaining;
reg        fbwq_stage_valid;
reg [31:0] fbwq_stage_addr;
reg [31:0] fbwq_stage_data;
reg [3:0]  fbwq_stage_strb;
reg        fbwq_stage_link_tail;
wire       fbwq_empty = (fbwq_count == 5'd0);
wire       fbwq_full  = (fbwq_count == 5'd16);

// Stage 2b: framebuffer/clear writes enqueue into a compact FIFO and the
// AXI write port drains it independently.  This lets the fragment pipe keep
// coalescing pixels while the SDRAM slave has a short AW/W hiccup.  Keep the
// outstanding-B cap at 14 to prevent the 4-bit counter from overflowing if
// SDRAM is slow to drain.
wire m_wr_inflight_near_full = (m_wr_inflight >= 4'd14);

wire m_wr_chan_busy = m_wr_awvalid || m_wr_wvalid;
wire fb_write_drain_complete = !fbwq_stage_valid && fbwq_empty
                             && (m_wr_inflight == 4'b0) && !m_wr_chan_busy;
wire fbwq_output_idle = !m_wr_awvalid && !m_wr_wvalid;
wire fbwq_drain_can_load = !m_wr_inflight_near_full && fbwq_output_idle;
wire [3:0] fbwq_rd_ptr_1 = fbwq_rd_ptr + 4'd1;
wire [3:0] fbwq_rd_ptr_2 = fbwq_rd_ptr + 4'd2;
wire [3:0] fbwq_rd_ptr_3 = fbwq_rd_ptr + 4'd3;
wire [3:0] fbwq_rd_ptr_4 = fbwq_rd_ptr + 4'd4;
wire [3:0] fbwq_rd_ptr_5 = fbwq_rd_ptr + 4'd5;
wire [3:0] fbwq_rd_ptr_6 = fbwq_rd_ptr + 4'd6;
wire fbwq_burst2_ok = (fbwq_count >= 5'd2)
                    && fbwq_link_next[fbwq_rd_ptr];
wire fbwq_burst3_ok = (fbwq_count >= 5'd3)
                    && fbwq_burst2_ok
                    && fbwq_link_next[fbwq_rd_ptr_1];
wire fbwq_burst4_ok = (fbwq_count >= 5'd4)
                    && fbwq_burst3_ok
                    && fbwq_link_next[fbwq_rd_ptr_2];
wire fbwq_burst5_ok = (fbwq_count >= 5'd5)
                    && fbwq_burst4_ok
                    && fbwq_link_next[fbwq_rd_ptr_3];
wire fbwq_burst6_ok = (fbwq_count >= 5'd6)
                    && fbwq_burst5_ok
                    && fbwq_link_next[fbwq_rd_ptr_4];
wire fbwq_burst7_ok = (fbwq_count >= 5'd7)
                    && fbwq_burst6_ok
                    && fbwq_link_next[fbwq_rd_ptr_5];
wire fbwq_burst8_ok = (fbwq_count >= 5'd8)
                    && fbwq_burst7_ok
                    && fbwq_link_next[fbwq_rd_ptr_6];
wire [3:0] fbwq_start_burst_words =
      fbwq_burst8_ok ? 4'd8
    : fbwq_burst7_ok ? 4'd7
    : fbwq_burst6_ok ? 4'd6
    : fbwq_burst5_ok ? 4'd5
    : fbwq_burst4_ok ? 4'd4
    : fbwq_burst3_ok ? 4'd3
    : fbwq_burst2_ok ? 4'd2
    : 4'd1;
wire fbwq_start_now = !fbwq_empty && fbwq_drain_can_load;
wire fbwq_continue_now = m_wr_wvalid && m_wr_wready && (fbwq_burst_remaining != 4'd0);
wire fbwq_pop_now = fbwq_start_now || fbwq_continue_now;
wire [4:0] fbwq_pop_count = fbwq_pop_now ? 5'd1 : 5'd0;
wire fbwq_can_enqueue = !fbwq_full || fbwq_pop_now;
wire fbwq_stage_drain_now = fbwq_stage_valid && fbwq_can_enqueue;
wire fbwq_can_push = !fbwq_stage_valid || fbwq_stage_drain_now;
wire fb_write_can_issue = fbwq_can_push;
wire [3:0] fbwq_prev_wr_ptr = fbwq_wr_ptr - 4'd1;
wire fbwq_has_tail_after_pop = (fbwq_count > fbwq_pop_count);
wire p3_needs_fb_flush = p3_valid && !p3_discard && !p3_flags[SPAN_TRANSLUC]
                       && fb_acc_valid
                       && (fb_acc_addr[31:2] != p3_fb_addr[31:2]);
wire fb_write_buffer_stall = p3_needs_fb_flush && !fb_write_can_issue;
wire [31:0] p3_fb_word_addr_w = p3_fb_addr & 32'hFFFFFFFC;
wire [3:0]  p3_fb_lane_mask_w = fb_lane_mask(p3_fb_addr[1:0]);
wire [31:0] fb_acc_match_addr = (fbss == FBSS_BLEND_R_WAIT)
                              ? blend_group_word_addr
                              : p3_fb_word_addr_w;
wire        fb_acc_word_match = !fb_acc_valid
                              || (fb_acc_addr == fb_acc_match_addr);
wire blend_group_pipe_block = (fbss == FBSS_IDLE)
                           && blend_group_active
                           && p3_valid
                           && !p3_discard
                           && (!p3_flags[SPAN_TRANSLUC]
                               || (blend_group_word_addr != p3_fb_word_addr_w)
                               || (|(blend_group_mask & p3_fb_lane_mask_w)));
assign fp_pipe_shift_blocked = (p1_valid && !tex_resp_valid)
                            || (fbss != FBSS_IDLE)
                            || (p3_valid && !p3_discard && p3_z_test)
                            || blend_group_pipe_block
                            || fb_write_buffer_stall
                            || m_wr_inflight_near_full;
wire fp_pipe_stall = fp_pipe_shift_blocked || cmap_pipe_wait || cmap_issue_wait;

// Combinational tex address from p0 + DSP output.  Multiply-mode only
// (sp_tex_width is always non-zero in every real caller — tested in
// tb_gpu with tex_width ∈ {1, 16, 32, 64, 300} and in gpudemo with
// tex_width = 64).  The old shift-mode p0_shift_addr path was dead
// code; removing it saves the 32-bit 2:1 mux + the p0_shift_addr
// register and its variable-barrel-shift update logic.
wire [31:0] fp_tex_addr_full = p0_tex_base + tx_mul_q
                             + {{16{p0_s_int[15]}}, p0_s_int};

assign tex_req_valid = (state == S_FRAG_PIPE) && p0_valid
                    && !fp_pipe_stall;
assign tex_req_addr  = fp_tex_addr_full[25:0];
assign tex_req_wide  = 1'b0;

// ----------------------------------------------------------------
// Perspective span — projection-space state + segment setup
// ----------------------------------------------------------------
// 16-pixel affine subdivision (perspective-correct at segment ends,
// linear interpolation within each segment). The span command supplies
// (s/z)_start, (t/z)_start, (1/z)_start and their per-pixel deltas in
// projection space (sdivz, tdivz, zi_persp + their *_step). For each
// PERSPECTIVE_SEG_LEN-pixel segment, the GPU computes:
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
// segment 0, so slot B is ready by the time the issue stage finishes segment 0
// and needs to swap.
//
// On segment boundary: when the source snapshot captures the last pixel of segment N
// (sp_seg_left == 0), the issue stage swaps slot B into slot A (sp_s,
// sp_sstep, etc), clears persp_seg_b_ready, and the PSS scheduler picks up
// segment N+2 in slot B.
// Projection-space accumulators (advance by 16 each PSS run).  Loaded by
// the scalar/param perspective span setup before entering S_FRAG_PIPE.
reg signed [31:0] sp_sZ;        // s/z, 16.16 signed
reg signed [31:0] sp_tZ;        // t/z, 16.16 signed
reg signed [31:0] sp_zinv;      // 1/z, 16.16 or high-precision param q28
reg signed [31:0] sp_sZstep;    // d(s/z)/dx, per-pixel
reg signed [31:0] sp_tZstep;    // d(t/z)/dx, per-pixel
reg signed [31:0] sp_zinv_step; // d(1/z)/dx, per-pixel
// Perspective correction runs as affine sub-segments.  The PSS setup path
// takes about one segment's worth of cycles, so 16 pixels keeps slot B ready
// without creating the structural 8-on/8-off issue-stage bubble.
localparam integer PERSPECTIVE_SEG_SHIFT = 4;
localparam [15:0]  PERSPECTIVE_SEG_LEN   = 16'd16;
localparam [3:0]   PERSPECTIVE_SEG_LAST  = 4'd15;

// Active when the current span has SPAN_PERSP set.
reg        persp_active;

// Pixels remaining in the current affine sub-segment, AFTER the
// pixel currently being issued. Counts 15 → 0 within a segment. When
// the source snapshot captures with sp_seg_left == 0, slot B is swapped into slot A.
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
reg        persp_swap_pending;

// PSS — segment-setup sub-FSM. Runs alongside the issue stage (and fbss)
// inside S_FRAG_PIPE. Drives dsp_a/dsp_b and recip_rd_addr; reads
// dsp_p / recip_rd_data. ~16 cycles per advanced pass (15 on the
// no-advance first pass).
//
// Setup-side pipeline (PSS_ADV → PSS_ADV_CLAMP → PSS_CLZ → PSS_TOP8)
// is split into four register-bounded stages because the original
// 1-cycle combinational
// chain (sp_zinv → +step<<4 → abs → 32-line CLZ casez → 32-bit barrel
// shift → top8 → recip_rd_addr) was the worst critical path in the
// design with -3.451 ns slack at 50 MHz. The split is:
//   PSS_ADV       : sp_zinv += step<<4; register old/new zinv
//   PSS_ADV_CLAMP : register |sp_zinv_new| and clamp decision
//   PSS_CLZ       : compute CLZ from registered abs; register persp_clz
//   PSS_TOP8      : compute top8 from (abs << clz); write recip_rd_addr
// PSS_RECIP_NA shares the same PSS_CLZ → PSS_TOP8 tail by registering
// abs of un-advanced sp_zinv into persp_zinv_abs_r and falling through.
localparam PSS_IDLE      = 5'd0;
localparam PSS_ADV       = 5'd1;   // stage 1: advance proj coords; register old/new zinv
localparam PSS_CLZ       = 5'd2;   // stage 3: compute CLZ from registered abs
localparam PSS_TOP8      = 5'd3;   // stage 4: compute top8; write recip_rd_addr
localparam PSS_RECIP_W   = 5'd4;   // BRAM read latency
localparam PSS_MUL       = 5'd5;   // kick BOTH dsp + dsp2 multiplies (operands pre-registered)
localparam PSS_MUL_W     = 5'd6;   // DSP pipeline delay (shared, both multiplies)
localparam PSS_FINAL     = 5'd7;   // capture both projected endpoints
localparam PSS_RECIP_NA  = 5'd8;   // ANCHOR_ONLY entry — register abs without advance
localparam PSS_RECIP_SHIFT = 5'd9; // stage between RECIP_W and MUL: compute recip_q16
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
localparam PSS_NR_MUL_X    = 5'd10;  // launch x * y0
localparam PSS_NR_MUL_X_W  = 5'd11;  // DSP pipeline delay
localparam PSS_NR_SUB      = 5'd12;  // capture xy, register 2 - xy
localparam PSS_NR_MUL_Y    = 5'd13;  // launch y0 * (2 - xy)
localparam PSS_NR_MUL_Y_W  = 5'd14;  // DSP pipeline delay
localparam PSS_NR_CAPTURE  = 5'd15;  // refined recip → recip_q16_r
localparam PSS_ADV_CLAMP   = 5'd16;  // stage 2: register abs/clamp from old/new zinv
localparam PSS_SLOPE       = 5'd17;  // commit anchor/slope from registered endpoints
localparam PSS_CONSTZ_STEP_W = 5'd18; // wait for constant-Z step multiplies
localparam PSS_CONSTZ_STEP_CAPTURE = 5'd19; // commit constant-Z affine step
localparam PSS_SLOPE_PREP = 5'd20; // derive slope class from registered deltas
localparam PSS_SLOPE_DIV_WAIT = 5'd21; // wait for reused DSP small-divisor slope multiplies
localparam PSS_SLOPE_DIV_COMMIT = 5'd22; // capture small-divisor quotients
localparam PSS_SLOPE_DIV_CORR_WAIT = 5'd23; // wait for DSP quotient*divisor correction products
localparam PSS_SLOPE_DIV_CORR_COMMIT = 5'd24; // capture correction flags from DSP products
localparam PSS_SLOPE_DIV_QUOT_COMMIT = 5'd25; // apply quotient correction
localparam PSS_SLOPE_DIV_STEP_COMMIT = 5'd26; // sign/commit small-divisor slopes
localparam PSS_ADV_TAIL_ST_WAIT = 5'd27; // wait for Q29 tail sZ/tZ advance products
localparam PSS_ADV_TAIL_ST_CAPTURE = 5'd28; // capture sZ/tZ products, launch zinv product
localparam PSS_ADV_TAIL_Z_WAIT = 5'd29; // wait for Q29 tail zinv advance product
localparam PSS_ADV_TAIL_COMMIT = 5'd30; // commit Q29 tail advance
localparam [5:0] PSS_Q29_RECIP_EXTRA = 6'd4;
localparam integer PSS_Q29_RECIP_EXTRA_INT = 4;
reg [4:0] persp_pss;
reg signed [31:0] recip_q16_r;       // Q16, or Q(16+PSS_Q29_RECIP_EXTRA) for Q29
reg signed [31:0] nr_two_minus_xy;
reg        sp_persp_q29_mode;

// PSS pass type — what PSS_FINAL should do with the computed (s_end, t_end).
localparam PSS_PASS_ANCHOR = 2'd0;  // pass 1: anchor only → persp_anchor_s/t
localparam PSS_PASS_TO_A   = 2'd1;  // pass 2: derive slope, fill slot A
localparam PSS_PASS_TO_B   = 2'd2;  // pass 3+: derive slope, fill slot B (pending)
reg [1:0] persp_pass;

// Latched values across PSS pipeline stages.
reg [31:0] persp_zinv_abs_r;   // |sp_zinv| latched after PSS_ADV_CLAMP / PSS_RECIP_NA
reg [4:0]  persp_clz;          // CLZ of persp_zinv_abs_r, latched after PSS_CLZ
wire [5:0] pss_q29_recip_shift = {1'b0, persp_clz} + PSS_Q29_RECIP_EXTRA;
reg [31:0] recip_norm_abs_r;    // Shared reciprocal-normalizer input for PSS
reg [4:0]  recip_norm_clz_r;    // Registered CLZ used by the shared normalizer shifter
reg signed [31:0] pss_s_end_r;
reg signed [31:0] pss_t_end_r;

// PSS slope clamp on perspective singularity.
//
// Root cause: PSS computes s_end = sp_sZ * (1/sp_zinv) at the end of
// each affine sub-segment, then derives a per-pixel slope as
// (s_end - s_anchor) >> PERSPECTIVE_SEG_SHIFT.  The 1/sp_zinv operation has a
// singularity at sp_zinv=0.  On steep projected spans where d(zinv)/dx
// is non-trivial, sp_zinv at the sub-segment end can become very small.
// s_end = (linearly-interpolated sp_sZ) × (huge recip) blows up,
// producing a wildly wrong slope that contaminates the *inside*
// pixels of the sub-segment.
//
// A known repro from b85f498 fuzz tri[44] had sub-segment 1's sp_zinv go
// 0.164 → 0.00383 across the advance.  Resulting s_end =
// -1349, slope = -173/pixel — making sp_s at pixel 51 = -138 (real)
// when CPU barycentric reference says 26.7.  Byte error 89.
//
// Fix for the legacy Q16 path: clamp the slope to 0 when the
// sub-segment's sp_zinv ratio is too extreme.  Trigger on either:
//   (a) sp_zinv_advanced sign-flipped (crossed zero entirely)
//   (b) |sp_zinv_advanced| < |sp_zinv| / 4 (advance ratio worse
//       than 4x — past the linear-interpolation comfort zone)
//
// The Q29 param-span path is used by Quake floors/surfaces, where a
// valid high-angle segment can legitimately shrink by more than 4x
// across 16 pixels.  For Q29, only clamp actual singularities: zero
// or sign-crossing.  Applying the 4x guard there flattens floors into
// regular 16-pixel bands.
//
// When triggered, PSS_FINAL substitutes persp_anchor_s/t for
// s_end/t_end → slope = 0 → sub-segment uses anchor's s/t for
// every pixel.  Bounded error (within the sub-segment's natural
// 1-2 pixels) versus the unbounded extrapolation it replaces.
//
// Constant-z spans dodge this naturally because the advance never shrinks
// the magnitude.
//
// The clamp uses registered old/new zinv values so the advance adder
// does not share a cycle with the magnitude compare.
reg pss_zinv_clamp_r;
reg signed [31:0] pss_zinv_adv_r;
reg signed [31:0] pss_zinv_prev_r;
reg        [31:0] pss_zinv_abs_na_r;
reg [4:0] pss_slope_divisor;
reg signed [31:0] pss_slope_s_delta;
reg signed [31:0] pss_slope_t_delta;
reg        pss_slope_s_neg;
reg        pss_slope_t_neg;
reg [31:0] pss_slope_s_mag;
reg [31:0] pss_slope_t_mag;
reg [31:0] pss_slope_s_quot;
reg [31:0] pss_slope_t_quot;
reg        pss_slope_s_corr;
reg        pss_slope_t_corr;
reg signed [31:0] pss_tail_s_delta;
reg signed [31:0] pss_tail_t_delta;
reg [4:0] pss_tail_advance;
// Cheap "<<2 magnitude shrink" check:
// |post-advance zinv| < |pre-advance zinv| >> 2.

// Stall the issue stage while slot A isn't ready (passes 1+2 still running).
// Slot B not being ready is handled separately inside the source-snapshot gate.
wire persp_issue_stall = persp_active && !persp_seg_a_ready;

// Combinational zinv helpers. PSS_ADV captures the post-advance value
// (sp_zinv + one segment of step) and the pre-advance abs; PSS_ADV_CLAMP
// registers the post-advance abs into persp_zinv_abs_r. PSS_RECIP_NA
// uses the un-advanced value directly on the first pass.
wire        [31:0] persp_zinv_abs_na = sp_zinv[31] ? -sp_zinv : sp_zinv;
wire        [31:0] pss_zinv_adv_abs_r = pss_zinv_adv_r[31]
                                      ? -pss_zinv_adv_r
                                      :  pss_zinv_adv_r;

function [31:0] pss_div_recip32;
    input [4:0] divisor;
    begin
        case (divisor)
            5'd3:  pss_div_recip32 = 32'd1431655766;
            5'd5:  pss_div_recip32 = 32'd858993460;
            5'd6:  pss_div_recip32 = 32'd715827883;
            5'd7:  pss_div_recip32 = 32'd613566757;
            5'd9:  pss_div_recip32 = 32'd477218589;
            5'd10: pss_div_recip32 = 32'd429496730;
            5'd11: pss_div_recip32 = 32'd390451573;
            5'd12: pss_div_recip32 = 32'd357913942;
            5'd13: pss_div_recip32 = 32'd330382100;
            5'd14: pss_div_recip32 = 32'd306783379;
            5'd15: pss_div_recip32 = 32'd286331154;
            default: pss_div_recip32 = 32'd0;
        endcase
    end
endfunction

function signed [31:0] pss_div_pow2_trunc;
    input signed [31:0] value;
    input [1:0] shift;
    reg neg;
    reg [31:0] mag;
    reg [31:0] quot;
    begin
        neg = value[31];
        mag = neg ? (32'd0 - value[31:0]) : value[31:0];
        case (shift)
            2'd0: quot = mag;
            2'd1: quot = mag >> 1;
            2'd2: quot = mag >> 2;
            default: quot = mag >> 3;
        endcase
        pss_div_pow2_trunc = neg ? -$signed(quot) : $signed(quot);
    end
endfunction

// CLZ helper — combinational casez. Returns leading-zero count for 32-bit.
function [4:0] clz32_fn;
    input [31:0] v;
    begin
        casez (v)
            32'b1???????????????????????????????: clz32_fn = 5'd0;
            32'b01??????????????????????????????: clz32_fn = 5'd1;
            32'b001?????????????????????????????: clz32_fn = 5'd2;
            32'b0001????????????????????????????: clz32_fn = 5'd3;
            32'b00001???????????????????????????: clz32_fn = 5'd4;
            32'b000001??????????????????????????: clz32_fn = 5'd5;
            32'b0000001?????????????????????????: clz32_fn = 5'd6;
            32'b00000001????????????????????????: clz32_fn = 5'd7;
            32'b000000001???????????????????????: clz32_fn = 5'd8;
            32'b0000000001??????????????????????: clz32_fn = 5'd9;
            32'b00000000001?????????????????????: clz32_fn = 5'd10;
            32'b000000000001????????????????????: clz32_fn = 5'd11;
            32'b0000000000001???????????????????: clz32_fn = 5'd12;
            32'b00000000000001??????????????????: clz32_fn = 5'd13;
            32'b000000000000001?????????????????: clz32_fn = 5'd14;
            32'b0000000000000001????????????????: clz32_fn = 5'd15;
            32'b00000000000000001???????????????: clz32_fn = 5'd16;
            32'b000000000000000001??????????????: clz32_fn = 5'd17;
            32'b0000000000000000001?????????????: clz32_fn = 5'd18;
            32'b00000000000000000001????????????: clz32_fn = 5'd19;
            32'b000000000000000000001???????????: clz32_fn = 5'd20;
            32'b0000000000000000000001??????????: clz32_fn = 5'd21;
            32'b00000000000000000000001?????????: clz32_fn = 5'd22;
            32'b000000000000000000000001????????: clz32_fn = 5'd23;
            32'b0000000000000000000000001???????: clz32_fn = 5'd24;
            32'b00000000000000000000000001??????: clz32_fn = 5'd25;
            32'b000000000000000000000000001?????: clz32_fn = 5'd26;
            32'b0000000000000000000000000001????: clz32_fn = 5'd27;
            32'b00000000000000000000000000001???: clz32_fn = 5'd28;
            32'b000000000000000000000000000001??: clz32_fn = 5'd29;
            32'b0000000000000000000000000000001?: clz32_fn = 5'd30;
            default: clz32_fn = 5'd31;
        endcase
    end
endfunction

// Shared reciprocal normalizer used by perspective spans.
// CLZ and normalization are split across cycles so the 32-line casez and
// variable barrel shift remain register-bounded and synthesis has only one
// copy of this fabric-heavy path to place.
wire [4:0]  recip_clz_pipe  = clz32_fn(recip_norm_abs_r);
wire [31:0] recip_norm_pipe = recip_norm_abs_r << recip_norm_clz_r;
wire [9:0]  recip_top10_pipe = recip_norm_pipe[30:21];

// FB write accumulator
reg [31:0] fb_acc_data;
reg [3:0]  fb_acc_mask;       // Byte enables
reg [31:0] fb_acc_addr;       // Word-aligned SDRAM address
reg        fb_acc_valid;      // Has pending data

// Param-span z-write accumulator.  Quake's z buffer is a CPU-visible short*
// in SDRAM, so z writes use the same ordered AXI write queue as framebuffer
// writes.  Adjacent horizontal pixels merge into full 32-bit words, letting the
// existing write-queue burst combiner handle long z runs.
reg [15:0] z_acc_lo;
reg [15:0] z_acc_hi;
reg [3:0]  z_acc_mask;
reg [31:0] z_acc_addr;
reg        z_acc_valid;
wire [31:0] z_acc_data = {z_acc_hi, z_acc_lo};

// Rect-clear state — for CMD_CLEAR_RECT.  cr_addr is the byte address
// of the next byte to clear in the current row; cr_w_remaining counts
// down per-word as we walk the row; cr_y_remaining is the row count.
// cr_row_addr is the byte address of the current row's first byte and
// is used to derive the next row's base via cr_stride.  cr_w_total
// holds the rect width so each row resets cleanly.  cr_stride is the
// per-command row advance — decoded from word 2 bits [31:16] of the
// CLEAR_RECT payload (the slot previously used for `pad`).  When the
// field is zero, we fall back to st_fb_stride (the SET_FB-resident
// global) so callers that don't fill the new field stay bit-identical
// to the prior behaviour.  Per-command stride fixes the BUILD
// `setviewtotile`/`setviewback` class of bug where the app renders to
// a buffer whose stride differs from the global FB —
// see openfpgaOS/docs/cr-gpu-clear-rect-stride.md.  The 8-bit cr_color
// is just the low byte of CMD_CLEAR_RECT's color word, replicated
// 4× per AXI write.
reg [31:0] cr_addr;
reg [31:0] cr_row_addr;
reg [15:0] cr_w_remaining;
reg [15:0] cr_w_total;
reg [15:0] cr_y_remaining;
reg [15:0] cr_stride;        // per-command row advance; 0 = use st_fb_stride
reg [7:0]  cr_color;

// (AXI4 write handshakes managed per-state, no global tracking)

// ================================================================
// Texture Address Computation — 2-stage pipeline (DSP-friendly)
// ================================================================
// Stage 1: register multiply inputs for DSP inference.
// Stage 2: DSP multiply + add, submit to cache.
//
// Multiply mode (tex_width > 0): addr = base + (t>>16)*width + (s>>16)
// Shift mode (tex_width == 0):   addr = base + ((t>>shift)<<bits) | (s>>(32-bits))

// The pipelined fragment processor uses tx_mul_q (the dedicated
// DSP-inferred register below) for the per-pixel tex-coord multiply.

task finish_fragment_stream_after_flush;
    begin
        fb_acc_valid <= 1'b0;
        fb_acc_mask  <= 4'b0;
        z_acc_valid  <= 1'b0;
        z_acc_mask   <= 4'b0;
        if (spanprod_active)
            spanprod_active <= 1'b0;
        state <= S_IDLE;
    end
endtask

// ================================================================
// Main FSM body
// ================================================================
always @(posedge clk) begin : main_fsm
    reg        fbwq_push_req;
    reg [31:0] fbwq_push_addr;
    reg [31:0] fbwq_push_data;
    reg [3:0]  fbwq_push_strb;
    reg        fbwq_push_links_fifo_tail;
    reg        fbwq_push_links_stage_tail;
    reg        fbwq_push_link_tail;

    fbwq_push_req  = 1'b0;
    fbwq_push_addr = 32'b0;
    fbwq_push_data = 32'b0;
    fbwq_push_strb = 4'b0;
    fbwq_push_links_fifo_tail = 1'b0;
    fbwq_push_links_stage_tail = 1'b0;
    fbwq_push_link_tail = 1'b0;

    if (!reset_n) begin
        state <= S_IDLE;
        ring_rdptr <= 0;
        ring_rd_addr <= 0;
        m_wr_awvalid <= 0;
        m_wr_wvalid <= 0;
        fbwq_rd_ptr <= 4'b0;
        fbwq_wr_ptr <= 4'b0;
        fbwq_count  <= 5'b0;
        fbwq_link_next <= 16'b0;
        fbwq_burst_remaining <= 4'b0;
        fbwq_stage_valid <= 1'b0;
        fbwq_stage_addr <= 32'd0;
        fbwq_stage_data <= 32'd0;
        fbwq_stage_strb <= 4'd0;
        fbwq_stage_link_tail <= 1'b0;
        fb_acc_valid <= 0;
        fb_acc_mask <= 0;
        z_acc_valid <= 0;
        z_acc_mask <= 0;
        z_acc_addr <= 0;
        z_acc_lo <= 16'd0;
        z_acc_hi <= 16'd0;
        fence_reached <= 0;
        cmd_type <= 0;
        cmd_payload_words <= 0;
        cmd_is_fence <= 0;
        cmd_is_clear_rect <= 0;
        cr_addr <= 0; cr_row_addr <= 0;
        cr_w_remaining <= 0; cr_w_total <= 0;
        cr_y_remaining <= 0; cr_stride <= 0; cr_color <= 0;
        cmd_is_set_fb <= 0;
        cmd_is_draw_param_span_list <= 0;
        cmd_is_flip <= 0;
        spanprod_compact_direct <= 1'b0;
        m_wr_inflight       <= 4'b0;
        gpu_swap_req        <= 1'b0;
        gpu_swap_idx        <= 2'b0;
        pay_idx <= 0;
        pay_remaining <= 0;
        // Pipelined fragment processor reset
        p0a_valid <= 0; p0a_light <= 0; p0a_colormap_id <= 0; p0a_flags <= 0;
        p0a_fb_addr <= 0;
        p0a_s_int <= 0; p0a_tex_base <= 0;
        p0a_t_y <= 0; p0a_tex_width <= 0;
        p0a_z_test <= 0; p0a_z_write <= 0; p0a_z_addr <= 0; p0a_z_value <= 0;
        p0_valid <= 0; p0_light <= 0; p0_colormap_id <= 0; p0_flags <= 0;
        p0_fb_addr <= 0;
        p0_s_int <= 0; p0_tex_base <= 0;
        p0_z_test <= 0; p0_z_write <= 0; p0_z_addr <= 0; p0_z_value <= 0;
        tx_mul_q <= 0;
        p1_valid <= 0; p1_light <= 0; p1_colormap_id <= 0; p1_flags <= 0;
        p1_fb_addr <= 0;
        p1_z_test <= 0; p1_z_write <= 0; p1_z_addr <= 0; p1_z_value <= 0;
        p2_valid <= 0; p2_color <= 0; p2_flags <= 0;
        p2_fb_addr <= 0; p2_discard <= 0;
        p2_z_test <= 0; p2_z_write <= 0; p2_z_addr <= 0; p2_z_value <= 0;
        p2b_valid <= 0; p2b_color <= 0; p2b_flags <= 0;
        p2b_fb_addr <= 0; p2b_discard <= 0;
        p2b_z_test <= 0; p2b_z_write <= 0; p2b_z_addr <= 0; p2b_z_value <= 0;
        p3_valid <= 0; p3_color <= 0; p3_flags <= 0;
        p3_fb_addr <= 0; p3_discard <= 0;
        p3_z_test <= 0; p3_z_write <= 0; p3_z_addr <= 0; p3_z_value <= 0;
        transluc_rd_addr <= 15'b0;
        transluc_lookup_fire <= 1'b0;
        cmap_req_addr_reg <= 26'b0;
        fbss <= FBSS_IDLE;
        blend_arvalid    <= 0;
        blend_araddr     <= 0;
        blend_group_active <= 0;
        blend_group_word_addr <= 0;
        blend_group_mask <= 0;
        blend_group_src_data <= 0;
        blend_result_word <= 0;
        blend_lane_iter <= 0;
        blend_lut_lane <= 0;
        blend_p3_match_r <= 0;
        src_done <= 0;
        sp_clamp_enable <= 2'b00;
        sp_z_write_enable <= 1'b0;
        sp_z_test_enable <= 1'b0;
        sp_persp_q29_mode <= 1'b0;
        spanprod_active <= 0;
        spanprod_compact_direct <= 1'b0;
        spanprod_direct_affine <= 1'b0;
        spanprod_idx <= 0;
        spanprod_record_count <= 0;
        spanprod_records_left <= 0;
        spanprod_calc_step <= 0;
        spanprod_header_supported <= 1'b0;
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
        persp_swap_pending <= 1'b0;
        persp_pss <= PSS_IDLE;
        persp_pass <= PSS_PASS_ANCHOR;
        persp_zinv_abs_r <= 0;
        pss_zinv_clamp_r <= 0;
        pss_zinv_adv_r <= 0;
        pss_zinv_prev_r <= 0;
        pss_zinv_abs_na_r <= 0;
        pss_slope_divisor <= 5'd16;
        pss_slope_s_delta <= 32'sd0;
        pss_slope_t_delta <= 32'sd0;
        pss_slope_s_neg <= 1'b0;
        pss_slope_t_neg <= 1'b0;
        pss_slope_s_mag <= 32'd0;
        pss_slope_t_mag <= 32'd0;
        pss_slope_s_quot <= 32'd0;
        pss_slope_t_quot <= 32'd0;
        pss_slope_s_corr <= 1'b0;
        pss_slope_t_corr <= 1'b0;
        pss_tail_s_delta <= 32'sd0;
        pss_tail_t_delta <= 32'sd0;
        pss_tail_advance <= 5'd0;
        pss_s_end_r <= 0;
        pss_t_end_r <= 0;
        persp_clz <= 0;
        recip_norm_abs_r <= 0;
        recip_norm_clz_r <= 0;
        nr_two_minus_xy <= 0;
        dsp_a <= 0; dsp_b <= 0;
        dsp2_a <= 0; dsp2_b <= 0;
        recip_rd_addr <= 0;
        // State registers
        sp_tex_w_mask <= 16'hFFFF; sp_tex_h_mask <= 16'hFFFF;
        st_fb_stride <= 320;
    end else begin
        // Ring reset: set the read pointer to the start of ring BRAM.
        if (ring_reset) begin
            ring_rdptr <= 0;
            ring_rd_addr <= 0;
        end

        // Soft reset: return FSM to idle, deassert all bus signals
        if (soft_reset) begin
            state        <= S_IDLE;
            ring_rdptr   <= ring_wrptr;
            ring_rd_addr <= ring_wrptr;
            m_wr_awvalid <= 0;
            m_wr_wvalid  <= 0;
            fbwq_rd_ptr  <= 4'b0;
            fbwq_wr_ptr  <= 4'b0;
            fbwq_count   <= 5'b0;
            fbwq_link_next <= 16'b0;
            fbwq_burst_remaining <= 4'b0;
            fbwq_stage_valid <= 1'b0;
            fbwq_stage_link_tail <= 1'b0;
            fb_acc_valid <= 0;
            fb_acc_mask  <= 0;
            z_acc_valid <= 1'b0;
            z_acc_mask  <= 4'b0;
            p0a_valid <= 1'b0;
            p0_valid <= 1'b0;
            p1_valid <= 1'b0;
            p2_valid <= 1'b0;
            p2b_valid <= 1'b0;
            p3_valid <= 1'b0;
            p3_z_test <= 1'b0;
            p3_z_write <= 1'b0;
            sp_z_write_enable <= 1'b0;
            sp_z_test_enable <= 1'b0;
            fbss         <= FBSS_IDLE;
            blend_arvalid <= 1'b0;
            blend_group_active <= 1'b0;
            blend_group_mask <= 4'b0;
            spanprod_active <= 1'b0;
            spanprod_compact_direct <= 1'b0;
            spanprod_direct_affine <= 1'b0;
            m_wr_inflight <= 4'b0;
            gpu_swap_req <= 1'b0;
            transluc_lookup_fire <= 1'b0;
        end else begin
            // ------------------------------------------------------------
            // Always-on housekeeping (runs every non-reset cycle).
            //
            // m_wr inflight counter for CMD_FENCE / CMD_FLIP drain.
            // Increment when an AW handshake fires; decrement when a B
            // beat lands (slave pulses bvalid for one cycle since
            // s_axi_bready is hardwired to 1 upstream of the arbiter).
            // Both events the same cycle ⇒ no change.
            //
            // gpu_swap_req default-low: the FLIP drain-done branch in
            // S_EXECUTE writes <=1 later in code order, which wins
            // under non-blocking semantics.  Every other cycle this
            // default keeps the side-port low, so the slave sees a
            // single-cycle pulse.
            // ------------------------------------------------------------
            case ({m_wr_awvalid && m_wr_awready, m_wr_bvalid})
                2'b10: m_wr_inflight <= m_wr_inflight + 4'd1;
                2'b01: m_wr_inflight <= m_wr_inflight - 4'd1;
                default: ;
            endcase
            // Clear accepted AW/W beats globally so the write channel drains
            // independently from the producer FSM.
            if (m_wr_awvalid && m_wr_awready)
                m_wr_awvalid <= 1'b0;
            if (m_wr_wvalid && m_wr_wready)
                m_wr_wvalid <= 1'b0;
            gpu_swap_req <= 1'b0;
            transluc_lookup_fire <= 1'b0;

        case (state)

        // ============================================================
        // IDLE — wait for commands in ring BRAM
        // ============================================================
        S_IDLE: begin
            if (active && !ring_empty) begin
                // BRAM read initiated by ring_rdptr (port B, 1-cycle latency)
                // Advance rdptr; data available next cycle in ring_rd_data
                ring_rdptr <= ring_rdptr + 1'b1;
                ring_rd_addr <= ring_rdptr + 1'b1;
                state      <= S_RING_WAIT;
            end
        end

        // ============================================================
        // Ring read — wait 1 cycle for BRAM read latency
        // ============================================================
        S_RING_WAIT: begin
            // ring_rd_data now contains the header word
            cmd_type          <= ring_rd_data[31:24];
            // Public header has a 24-bit payload count, but one ring upload is
            // capped at 4096 words; keeping only 13 bits is sufficient here.
            cmd_payload_words <= ring_rd_data[12:0];
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
            cmd_is_fence          <= (cmd_type == CMD_FENCE);
            cmd_is_clear_rect     <= (cmd_type == CMD_CLEAR_RECT);
            cmd_is_set_fb         <= (cmd_type == CMD_SET_FB);
            cmd_is_draw_param_span_list <= (cmd_type == CMD_DRAW_PARAM_SPAN_LIST &&
                                             cmd_payload_words >= 13'd11);
            spanprod_compact_direct <= (cmd_type == CMD_DRAW_PARAM_SPAN_LIST &&
                                        cmd_payload_words <= 13'd32);
            cmd_is_flip           <= (cmd_type == CMD_FLIP);

            if (cmd_payload_words == 0) begin
                state <= S_EXECUTE;
            end else begin
                pay_idx <= 0;
                // Track the full payload count so every word drains out of
                // the ring.  This keeps ring_rdptr aligned even when a
                // command has more words than the direct destination decode
                // accepts.
                pay_remaining <= cmd_payload_words;
                // Start first BRAM read (data arrives next cycle)
                ring_rdptr <= ring_rdptr + 1'b1;
                ring_rd_addr <= ring_rdptr + 1'b1;
                state <= S_PAY_DATA;
            end
        end

        // ============================================================
        // Payload — stream words directly to destination regs
        // ============================================================
        // No intermediate payload array.  Each cycle, ring_rd_data holds the
        // current payload word (advanced by the 1-cycle BRAM read); we
        // route it straight to the right state reg based on the
        // registered command type and the payload-word index pay_idx.
        // Destinations live in regs that already exist, so the only storage
        // cost is the 6-bit pay_idx counter and pay_remaining.
        //
        // pay_idx saturates at 63: any payload word past index 63 is still
        // drained from the ring so ring_rdptr ends up at the next command
        // header, but has no destination reg to write.
        S_PAY_DATA: begin
            if (pay_idx != 6'd63)
                pay_idx <= pay_idx + 6'd1;
            pay_remaining <= pay_remaining - 13'd1;

            // Per-command dispatch.  The if-else chain mirrors the pre-
            // decoded cmd_is_* one-hot flags set in S_DECODE — keeps the
            // combinational cone to each destination reg short.
            if (cmd_is_fence) begin
                // Publish the token only AFTER outstanding m_wr_* writes
                // drain in S_EXECUTE — fixes the flashing-pixel race
                // documented in cr-gpu-fence-write-completion.md.
                if (pay_idx == 6'd0) pending_fence_token <= ring_rd_data;
            end
            else if (cmd_is_flip) begin
                // CMD_FLIP payload: word 0 = idx, word 1 = fence token.
                if (pay_idx == 6'd0) pending_swap_idx    <= ring_rd_data[1:0];
                if (pay_idx == 6'd1) pending_fence_token <= ring_rd_data;
            end
            else if (cmd_is_clear_rect) begin
                if (pay_idx == 6'd0) begin
                    cr_addr     <= ring_rd_data;
                    cr_row_addr <= ring_rd_data;
                end else if (pay_idx == 6'd1) begin
                    cr_w_total     <= ring_rd_data[31:16];
                    cr_w_remaining <= ring_rd_data[31:16];
                    cr_y_remaining <= ring_rd_data[15:0];
                end else if (pay_idx == 6'd2) begin
                    // Word 2: {stride[31:16], pad[15:8], color[7:0]}.
                    // stride==0 uses st_fb_stride from SET_FB.
                    cr_stride <= ring_rd_data[31:16];
                    cr_color  <= ring_rd_data[7:0];
                end
            end
            else if (cmd_is_set_fb) begin
                if (pay_idx == 6'd1) st_fb_stride <= ring_rd_data[15:0];
            end
            else if (cmd_is_draw_param_span_list) begin
                load_param_span_list_payload_word(pay_idx, ring_rd_data);
            end

            if (pay_remaining <= 13'd1) begin
                state <= S_EXECUTE;
            end
            // For long packed-record param lists, execute the current 4-record
            // chunk and return here to consume the next record chunk without
            // requiring a new surface header.
            else if (cmd_is_draw_param_span_list
                  && !spanprod_direct_affine
                  && spanprod_header_supported
                  && (spanprod_records_left > {13'd0, spanprod_record_count})
                  && pay_idx == 6'd36) begin
                state <= S_EXECUTE;
            end
            else begin
                // Advance rdptr for next word (BRAM read, 1-cycle latency)
                ring_rdptr <= ring_rdptr + 1'b1;
                ring_rd_addr <= ring_rdptr + 1'b1;
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
            if (cmd_is_fence) begin
                // Stall until all outstanding m_wr_* writes commit; then
                // publish the fence token and retire to S_IDLE.
                if (fb_write_drain_complete) begin
                    fence_reached <= pending_fence_token;
                    state         <= S_IDLE;
                end
                // else: stay in S_EXECUTE — m_wr_inflight ticks down via
                // the global counter update below.
            end
            else if (cmd_is_flip) begin
                // Same drain wait as CMD_FENCE, plus display-queue
                // backpressure.  axi_periph_slave has one pending swap slot;
                // if we pulse while it is already full, the ready index is
                // overwritten.  Hold the command here until vsync consumes the
                // previous request, then publish fence with the swap pulse.
                if (fb_write_drain_complete && !slave_swap_pending) begin
                    gpu_swap_req  <= 1'b1;
                    gpu_swap_idx  <= pending_swap_idx;
                    fence_reached <= pending_fence_token;
                    state         <= S_IDLE;
                end
            end
            else if ((cmd_type == CMD_SET_TEXTURE)
                || cmd_is_set_fb
            ) begin
                state <= S_IDLE;
            end
            else if (cmd_is_clear_rect) begin
                state <= S_CLEAR_RECT;
            end
            else if (cmd_is_draw_param_span_list) begin
                spanprod_idx <= 2'd0;
                spanprod_calc_step <= 3'd0;
                src_done <= 1'b0;
                persp_active      <= 1'b0;
                persp_first_done  <= 1'b0;
                persp_swap_pending <= 1'b0;
                persp_pss         <= PSS_IDLE;
                persp_pass        <= PSS_PASS_ANCHOR;
                sp_seg_left       <= 4'd0;
                spanprod_active <= spanprod_header_supported
                             && (spanprod_record_count != 3'd0);
                state <= (spanprod_header_supported && (spanprod_record_count != 3'd0))
                       ? S_SPANPROD_SETUP : S_IDLE;
            end
            else state <= S_IDLE;
        end

        // ============================================================
        // Unified span producer — expand compact param records or direct
        // affine lane records into scalar spans for the fragment pipe.
        // ============================================================
        S_SPANPROD_SETUP: begin
            if (!spanprod_active) begin
                state <= S_IDLE;
            end else if (spanprod_count[spanprod_idx] == 16'd0) begin
                if (z_acc_valid && |z_acc_mask) begin
                    if (fb_write_can_issue) begin
                        fbwq_push_req  = 1'b1;
                        fbwq_push_addr = z_acc_addr;
                        fbwq_push_data = z_acc_data;
                        fbwq_push_strb = z_acc_mask;
                        z_acc_valid    <= 1'b0;
                        z_acc_mask     <= 4'b0;
                    end
                end else if (fb_acc_valid && |fb_acc_mask) begin
                    if (fb_write_can_issue) begin
                        fbwq_push_req  = 1'b1;
                        fbwq_push_addr = fb_acc_addr;
                        fbwq_push_data = fb_acc_data;
	                        fbwq_push_strb = fb_acc_mask;
	                        fb_acc_valid <= 1'b0;
	                        fb_acc_mask  <= 4'b0;
	                        if (spanprod_idx == spanprod_last_idx) begin
	                            if ((spanprod_records_left > {13'd0, spanprod_record_count})
	                                && (pay_remaining != 13'd0)) begin
	                                spanprod_prepare_next_record_chunk;
	                            end else begin
	                                spanprod_active <= 1'b0;
	                                state <= S_IDLE;
	                            end
	                        end else begin
	                            spanprod_idx <= spanprod_idx + 2'd1;
	                            state <= S_SPANPROD_SETUP;
	                        end
	                    end
	                end else if (spanprod_idx == spanprod_last_idx) begin
	                    if ((spanprod_records_left > {13'd0, spanprod_record_count})
	                        && (pay_remaining != 13'd0)) begin
	                        spanprod_prepare_next_record_chunk;
	                    end else begin
	                        spanprod_active <= 1'b0;
	                        state <= S_IDLE;
	                    end
	                end else begin
                    spanprod_idx <= spanprod_idx + 2'd1;
                    state <= S_SPANPROD_SETUP;
                end
            end else begin
                spanprod_calc_step <= 3'd0;
                if (spanprod_direct_affine) begin
                    state <= S_SPANPROD_EMIT;
                end else begin
                    spanprod_launch_fb_mul;
                    state <= S_SPANPROD_MUL_WAIT;
                end
            end
        end

        S_SPANPROD_MUL_WAIT: begin
            state <= S_SPANPROD_CAPTURE;
        end

        S_SPANPROD_CAPTURE: begin
            case (spanprod_calc_step)
                3'd0: begin
                    spanprod_fb_addr_r <= spanprod_fb_base + dsp_p[31:0] + dsp2_p[31:0];
                    if (spanprod_z_write || spanprod_z_test) begin
                        spanprod_launch_z_mul;
                        spanprod_calc_step <= 3'd5;
                    end else begin
                        spanprod_launch_attr_mul(spanprod_attr0_du, spanprod_attr0_dv);
                        spanprod_calc_step <= 3'd1;
                    end
                    state <= S_SPANPROD_MUL_WAIT;
                end
                3'd5: begin
                    spanprod_z_addr_r <= spanprod_z_base + dsp_p[31:0] + dsp2_p[31:0];
                    spanprod_launch_attr_mul(spanprod_attr0_du, spanprod_attr0_dv);
                    spanprod_calc_step <= 3'd1;
                    state <= S_SPANPROD_MUL_WAIT;
                end
                3'd1: begin
                    spanprod_attr0_start_r <= spanprod_attr0_origin
                                         + $signed(dsp_p[31:0])
                                         + $signed(dsp2_p[31:0]);
                    spanprod_launch_attr_mul(spanprod_attr1_du, spanprod_attr1_dv);
                    spanprod_calc_step <= 3'd2;
                    state <= S_SPANPROD_MUL_WAIT;
                end
                3'd2: begin
                    spanprod_attr1_start_r <= spanprod_attr1_origin
                                         + $signed(dsp_p[31:0])
                                         + $signed(dsp2_p[31:0]);
                    if (spanprod_attr_persp) begin
                        spanprod_launch_attr_mul(spanprod_attr2_du, spanprod_attr2_dv);
                        spanprod_calc_step <= 3'd3;
                        state <= S_SPANPROD_MUL_WAIT;
                    end else if (spanprod_flags[SPAN_COLORMAP]) begin
                        spanprod_attr2_start_r <= 32'sd0;
                        spanprod_launch_attr_mul({{8{spanprod_light_du[23]}}, spanprod_light_du},
                                              {{8{spanprod_light_dv[23]}}, spanprod_light_dv});
                        spanprod_calc_step <= 3'd4;
                        state <= S_SPANPROD_MUL_WAIT;
                    end else begin
                        spanprod_attr2_start_r <= 32'sd0;
                        spanprod_light_start_r <= 24'sd0;
                        state <= S_SPANPROD_EMIT;
                    end
                end
                3'd3: begin
                    spanprod_attr2_start_r <= spanprod_attr2_origin
                                         + $signed(dsp_p[31:0])
                                         + $signed(dsp2_p[31:0]);
                    if (spanprod_flags[SPAN_COLORMAP]) begin
                        spanprod_launch_attr_mul({{8{spanprod_light_du[23]}}, spanprod_light_du},
                                              {{8{spanprod_light_dv[23]}}, spanprod_light_dv});
                        spanprod_calc_step <= 3'd4;
                        state <= S_SPANPROD_MUL_WAIT;
                    end else begin
                        spanprod_light_start_r <= 24'sd0;
                        state <= S_SPANPROD_EMIT;
                    end
                end
                default: begin
                    spanprod_light_start_r <= spanprod_light_origin
                                         + $signed(dsp_p[23:0])
                                         + $signed(dsp2_p[23:0]);
                    state <= S_SPANPROD_EMIT;
                end
            endcase
        end

        S_SPANPROD_EMIT: begin
            spanprod_load_generated_span;
            state <= S_FRAG_PIPE;
        end

        // ============================================================
        // SPAN pixel loop

        // ============================================================
        // Pipelined Fragment Processor
        // ============================================================
        // Replaces the old sequential span-pixel chain with a source snapshot,
        // a cache-issue stage, and the texture/cmap/fb tail:
        //   p0a source snapshot and DSP inputs
        //   p0  cache issue using registered tx_mul_q
        //   p1  metadata in flight, awaiting tex_resp from cache
        //   p2  tex color captured, cmap_rd_addr issued (if cmap)
        //   p3  cmap result merged, ready for fb_acc write
        //
        // p0 issue commits when the cache accepts the request this cycle
        // (combinational `tex_req_valid && tex_req_ready`). Source state
        // advances when p0a snapshots a source pixel.
        S_FRAG_PIPE: begin : frag_pipe_blk
            reg scalar_span_last_issue;
            reg [31:0] p3_word_addr;
            reg [1:0]  p3_byte_lane;
            reg        p3_word_match;
            reg        issue_committed;
            reg        p0a_to_p0;
            reg        p0a_free_after;
            reg        source_pixel_available;
            reg        scalar_source_pixel_available;
            reg        load_p0a;
            reg        load_p0a_z;
            reg        p3_consumed;
            reg [31:0] source_fb_addr;
            reg [31:0] source_tex_base;
            reg signed [31:0] source_s;
            reg signed [31:0] source_t;
            reg signed [31:0] source_s_clamped;
            reg signed [31:0] source_t_clamped;
            reg [5:0] source_light;
            reg [3:0] source_colormap_id;
            reg [31:0] source_z_word_addr;
            reg        source_z_hi;
            reg [15:0] source_z_half;
            reg        source_z_can_flush;
            reg        source_z_ready;
            reg        source_z_write_only_active;
            reg        source_z_advance_active;
            reg [31:0] p3_z_word_addr;
            reg        p3_z_hi;
            reg [15:0] p3_z_old_half;
            reg [31:0] p3_z_word;
            reg        p3_z_pass;

            // Was the (combinational) tex_req accepted by the cache this
            // same cycle? Both signals are visible NOW.
            issue_committed = tex_req_valid && tex_req_ready;
            p3_consumed = 1'b0;
            scalar_span_last_issue = 1'b0;

            // Load/refresh p0a only if the one-entry source snapshot will be
            // free after this cycle.  p0 itself remains the cache-issue stage;
            // if it is busy, p0a can still prefetch one source pixel.
            // Persp gating:
            //   * !persp_issue_stall: slot A must be loaded (pass 2 done).
            //   * If sp_seg_left == 0 (last px of segment), slot B must be
            //     ready so the swap can fire in the same cycle, unless this
            //     pixel is also the last pixel of the whole span.
            p0a_to_p0 = p0a_valid && (issue_committed || !p0_valid);
            p0a_free_after = !p0a_valid || p0a_to_p0;
            scalar_source_pixel_available = 1'b0;
            source_pixel_available = 1'b0;
            scalar_span_last_issue = (sp_count == 16'd1);
            scalar_source_pixel_available =
                (sp_count != 16'd0)
                && !src_done
                && !persp_swap_pending
                && !persp_issue_stall
                && (!persp_active
                    || sp_seg_left != 4'd0
                    || persp_seg_b_ready
                    || scalar_span_last_issue);
            source_pixel_available = scalar_source_pixel_available;
            source_z_write_only_active =
                sp_z_write_enable && !sp_z_test_enable;
            source_z_advance_active =
                sp_z_write_enable || sp_z_test_enable;
            source_z_word_addr = sp_z_addr & 32'hFFFFFFFC;
            source_z_hi = sp_z_addr[1];
            source_z_half = sp_persp_q29_mode
                          ? sp_z_value[29:14]
                          : sp_z_value[16:1];
            source_z_can_flush = fb_write_can_issue && (fbss == FBSS_IDLE)
                               && !p3_needs_fb_flush;
            source_z_ready = !source_z_write_only_active
                           || !z_acc_valid
                           || (z_acc_addr == source_z_word_addr)
                           || source_z_can_flush;
            load_p0a = p0a_free_after && source_pixel_available && source_z_ready;
            load_p0a_z = p0a_free_after && scalar_source_pixel_available
                       && source_z_ready && source_z_write_only_active;
            source_fb_addr = sp_fb_addr;
            source_tex_base = sp_tex_addr;
            source_s = sp_s;
            source_t = sp_t;
            source_s_clamped = sp_s;
            if (sp_clamp_enable[0]) begin
                if (source_s < sp_s_clamp_min)
                    source_s_clamped = sp_s_clamp_min;
                else if (source_s > sp_s_clamp_max)
                    source_s_clamped = sp_s_clamp_max;
            end
            source_t_clamped = sp_t;
            if (sp_clamp_enable[1]) begin
                if (source_t < sp_t_clamp_min)
                    source_t_clamped = sp_t_clamp_min;
                else if (source_t > sp_t_clamp_max)
                    source_t_clamped = sp_t_clamp_max;
            end
            source_light = sp_light;
            source_colormap_id = sp_colormap_id;
            p3_z_word_addr = p3_z_addr & 32'hFFFFFFFC;
            p3_z_hi = p3_z_addr[1];
            p3_z_word = (z_acc_valid && z_acc_addr == p3_z_word_addr)
                      ? z_acc_data : blend_rdata;
            p3_z_old_half = fb_halfword_read(p3_z_word, p3_z_hi);
            p3_z_pass = (p3_z_value >= p3_z_old_half);

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
                p3_discard   <= p2b_discard;
                p3_z_test    <= p2b_z_test;
                p3_z_write   <= p2b_z_write;
                p3_z_addr    <= p2b_z_addr;
                p3_z_value   <= p2b_z_value;

	                // p2b <- p2  (no-op shift, gives cmap port B time to respond)
                p2b_valid   <= p2_valid;
                p2b_color   <= p2_color;
                p2b_flags   <= p2_flags;
                p2b_fb_addr <= p2_fb_addr;
                p2b_discard <= p2_discard;
                p2b_z_test  <= p2_z_test;
                p2b_z_write <= p2_z_write;
                p2b_z_addr  <= p2_z_addr;
                p2b_z_value <= p2_z_value;

                // p2 <- p1  (captures tex_resp; issues cmap_rd_addr if needed)
                p2_valid   <= p1_valid;
                if (p1_valid) begin
                    p2_color   <= tex_resp_data[7:0];
                    p2_flags   <= p1_flags;
                    p2_fb_addr <= p1_fb_addr;
                    p2_discard <= p1_flags[SPAN_SKIP_ZERO]
                               && (tex_resp_data[7:0] == 8'hFF);
                    p2_z_test  <= p1_z_test;
                    p2_z_write <= p1_z_write;
                    p2_z_addr  <= p1_z_addr;
                    p2_z_value <= p1_z_value;
                    if (p1_flags[SPAN_COLORMAP]) begin
                        // SDRAM byte address for the cmap lookup.  Slot
                        // base + per-pixel (shade × 256 + texel).  The
                        // 16 KB per colormap slot, expressed as a shift here
                        // to avoid a multiplier.
                        // Colormap id is captured per fragment so affine
                        // groups can interleave lanes with different slots.
                        cmap_req_addr_reg <= PALOOKUP_BASE
                                           | {8'b0, p1_colormap_id, 14'b0}
                                           | {12'b0, p1_light, tex_resp_data[7:0]};
                    end
                end

                // p1 <- p0 (issue commit). Cache accepted our request this
                // cycle. The OLD p0 metadata becomes p1 (the in-cache pixel).
                if (issue_committed) begin
                    p1_valid   <= 1;
                    p1_light   <= p0_light;
                    p1_colormap_id <= p0_colormap_id;
                    p1_flags   <= p0_flags;
                    p1_fb_addr <= p0_fb_addr;
                    p1_z_test  <= p0_z_test;
                    p1_z_write <= p0_z_write;
                    p1_z_addr  <= p0_z_addr;
                    p1_z_value <= p0_z_value;
                end else begin
                    p1_valid <= 0;
                end

                // p0 <- p0a.  The texture row multiply consumes the registered
                // p0a inputs, not the wide sp_* mux, which is the timing win.
                if (p0a_to_p0) begin
                    p0_valid     <= 1;
                    p0_light     <= p0a_light;
                    p0_colormap_id <= p0a_colormap_id;
                    p0_flags     <= p0a_flags;
                    p0_fb_addr   <= p0a_fb_addr;
                    p0_s_int     <= p0a_s_int;
                    p0_tex_base  <= p0a_tex_base;
                    p0_z_test    <= p0a_z_test;
                    p0_z_write   <= p0a_z_write;
                    p0_z_addr    <= p0a_z_addr;
                    p0_z_value   <= p0a_z_value;
                    tx_mul_q     <= $signed(p0a_t_y)
                                  * $signed({1'b0, p0a_tex_width});
                end else if (issue_committed) begin
                    p0_valid <= 0;
                end
                if (p0a_to_p0 && !load_p0a)
                    p0a_valid <= 0;
            end

            // Source snapshot and source-state advance are intentionally outside
            // the tail-pipe shift gate.  When p0a is empty, it can prefetch one
            // source fragment while p1/p2/p3 are stalled on a cache or FB event.
            // This removes a hot `p1_valid -> sp_t/sp_s enable` timing cone and
            // gives the fragment pipe one extra cycle of elasticity.
            if (load_p0a) begin
                p0a_valid     <= 1;
                p0a_light     <= source_light;
                p0a_colormap_id <= source_colormap_id;
                p0a_flags     <= sp_flags;
                p0a_fb_addr   <= source_fb_addr;
                p0a_tex_base  <= source_tex_base;
                p0a_tex_width <= sp_tex_width;
                p0a_z_test    <= sp_z_test_enable;
                p0a_z_write   <= sp_z_test_enable && sp_z_write_enable;
                p0a_z_addr    <= sp_z_addr;
                p0a_z_value   <= source_z_half;
                p0a_s_int <= source_s_clamped[31:16] & sp_tex_w_mask;
                p0a_t_y   <= source_t_clamped[31:16] & sp_tex_h_mask;

                if (load_p0a_z) begin
	                    if (z_acc_valid && z_acc_addr != source_z_word_addr) begin
	                        fbwq_push_req  = 1'b1;
	                        fbwq_push_addr = z_acc_addr;
	                        fbwq_push_data = z_acc_data;
	                        fbwq_push_strb = z_acc_mask;
	                        z_acc_addr     <= source_z_word_addr;
	                        z_acc_valid    <= 1'b1;
	                        if (source_z_hi) begin
                            z_acc_hi <= source_z_half;
                            z_acc_lo <= 16'b0;
	                            z_acc_mask <= 4'b1100;
	                        end else begin
                            z_acc_hi <= 16'b0;
                            z_acc_lo <= source_z_half;
	                            z_acc_mask <= 4'b0011;
	                        end
	                    end else begin
	                        z_acc_valid <= 1'b1;
	                        z_acc_addr  <= source_z_word_addr;
	                        if (source_z_hi) begin
		                            z_acc_hi <= source_z_half;
	                            z_acc_mask[3:2]   <= 2'b11;
	                            if (!z_acc_valid) begin
		                                z_acc_lo <= 16'b0;
	                                z_acc_mask[1:0]  <= 2'b00;
	                            end
	                        end else begin
		                            z_acc_lo <= source_z_half;
	                            z_acc_mask[1:0]  <= 2'b11;
	                            if (!z_acc_valid) begin
		                                z_acc_hi <= 16'b0;
	                                z_acc_mask[3:2]   <= 2'b00;
	                            end
	                        end
	                    end
                    sp_z_addr  <= sp_z_addr + sp_z_step;
                    sp_z_value <= sp_z_value + sp_z_value_step;
                end
                else if (source_z_advance_active) begin
                    sp_z_addr  <= sp_z_addr + sp_z_step;
                    sp_z_value <= sp_z_value + sp_z_value_step;
                end

                sp_fb_addr <= sp_fb_addr + sp_fb_stride;
                sp_count   <= sp_count - 16'd1;
                sp_light_q <= sp_light_q + sp_light_step;
                if (scalar_span_last_issue) src_done <= 1;

                if (persp_active) begin
                    if (sp_seg_left == 4'd0) begin
                        if (!scalar_span_last_issue) begin
                            persp_swap_pending <= 1'b1;
                            persp_seg_b_ready <= 0;
                        end
                    end else begin
                        sp_s        <= sp_s + sp_sstep;
                        sp_t        <= sp_t + sp_tstep;
                        sp_seg_left <= sp_seg_left - 4'd1;
                    end
                end else begin
                    sp_s <= sp_s + sp_sstep;
                    sp_t <= sp_t + sp_tstep;
                end

                // Non-constant-Z perspective spans keep the PSS running
                // ahead of the issue pipe.  Once the final source pixel
                // has been accepted there is no future segment to prepare;
                // disarm PSS so the tail-pipe drain can retire the command
                // and reach the following fence.  Constant-Z already
                // clears persp_active when it converts to affine.
                if (scalar_span_last_issue && persp_active) begin
                    persp_active       <= 1'b0;
                    persp_swap_pending <= 1'b0;
                    persp_first_done   <= 1'b0;
                    persp_pss          <= PSS_IDLE;
                end
            end

            // Apply a perspective segment swap one cycle after the boundary
            // pixel is accepted.  This keeps cache req_ready out of the
            // sp_sstep/sp_tstep update cone while only adding a bubble at the
            // 16-pixel perspective sub-segment boundary.
            if (persp_swap_pending) begin
                sp_s                 <= persp_pend_s;
                sp_t                 <= persp_pend_t;
                sp_sstep             <= persp_pend_sstep;
                sp_tstep             <= persp_pend_tstep;
                sp_seg_left          <= PERSPECTIVE_SEG_LAST;
                persp_swap_pending   <= 1'b0;
            end

            // ----------------------------------------------------------
            // FB sub-FSM — consumes p3 (drains pipeline tail)
            // ----------------------------------------------------------
            case (fbss)
                FBSS_IDLE: begin
                    // Translucent fragments are grouped by destination word.
                    // As long as consecutive fragments hit different byte
                    // lanes in the same word, keep collecting and let the
                    // pipe advance.  A word change, duplicate lane, or
                    // following opaque pixel flushes the group first so draw
                    // order stays byte-exact.
                    if (p3_valid && !p3_discard && p3_flags[SPAN_TRANSLUC]) begin
                        p3_word_addr = p3_fb_addr & 32'hFFFFFFFC;
                        p3_byte_lane = p3_fb_addr[1:0];
                        if (!blend_group_active) begin
                            blend_group_active    <= 1'b1;
                            blend_group_word_addr <= p3_word_addr;
                            blend_group_mask      <= fb_lane_mask(p3_byte_lane);
                            blend_group_src_data  <= fb_lane_data(p3_byte_lane, p3_color);
                            p3_consumed = 1'b1;
                        end else if (blend_group_word_addr == p3_word_addr
                                  && !(|(blend_group_mask & fb_lane_mask(p3_byte_lane)))) begin
                            blend_group_mask <= blend_group_mask | fb_lane_mask(p3_byte_lane);
                            blend_group_src_data <= blend_group_src_data
                                                  | fb_lane_data(p3_byte_lane, p3_color);
                            p3_consumed = 1'b1;
                        end else begin
                            fbss <= FBSS_BLEND_REQ;
                        end
                    end
                    else if (blend_group_active && p3_valid && !p3_discard) begin
                        // A following opaque pixel cannot pass the older
                        // translucent word group, even if it targets the same
                        // word.  Flush first, then retry p3 from IDLE.
                        fbss <= FBSS_BLEND_REQ;
                    end
                    else if (p3_valid && !p3_discard && p3_z_test) begin
                        if (z_acc_valid && z_acc_addr != p3_z_word_addr) begin
                            if (fb_write_can_issue) begin
                                fbwq_push_req  = 1'b1;
                                fbwq_push_addr = z_acc_addr;
                                fbwq_push_data = z_acc_data;
                                fbwq_push_strb = z_acc_mask;
                                z_acc_valid    <= 1'b0;
                                z_acc_mask     <= 4'b0;
                            end else begin
                                fbss <= FBSS_FLUSH_W_RSP;
                            end
                        end else if (z_acc_valid && z_acc_addr == p3_z_word_addr) begin
	                            if (p3_z_pass) begin
	                                if (p3_z_write) begin
	                                    if (p3_z_hi) begin
		                                        z_acc_hi <= p3_z_value;
	                                        z_acc_mask[3:2]   <= 2'b11;
	                                    end else begin
		                                        z_acc_lo <= p3_z_value;
	                                        z_acc_mask[1:0]  <= 2'b11;
	                                    end
	                                end
	                                p3_z_test <= 1'b0;
	                            end else begin
                                p3_valid <= 1'b0;
                                p3_consumed = 1'b1;
                            end
                        end else if (!tex_axi_arvalid && !tex_m0_in_flight
                                  && fb_write_drain_complete) begin
                            blend_arvalid <= 1'b1;
                            blend_araddr  <= p3_z_word_addr;
                            fbss          <= FBSS_ZTEST_AR_WAIT;
                        end
                    end
                    // Process p3 if it has a non-discard pixel (and no pending depth work)
                    else if (p3_valid && !p3_discard) begin : fb_acc_blk
                        p3_word_addr  = p3_fb_addr & 32'hFFFFFFFC;
                        p3_byte_lane  = p3_fb_addr[1:0];
                        p3_word_match = fb_acc_word_match;

                        if (!p3_word_match) begin
                            if (fb_write_can_issue) begin
                                fbwq_push_req  = 1'b1;
                                fbwq_push_addr = fb_acc_addr;
                                fbwq_push_data = fb_acc_data;
                                fbwq_push_strb = fb_acc_mask;

                                fb_acc_valid <= 1'b1;
                                fb_acc_addr  <= p3_word_addr;
                                fb_acc_data  <= fb_lane_data(p3_byte_lane, p3_color);
                                fb_acc_mask  <= fb_lane_mask(p3_byte_lane);
                                p3_consumed = 1'b1;
                            end else begin
                                // The write queue is full.  Park FBSS for one
                                // or more cycles; p3 remains valid because
                                // fbss != IDLE stalls the pipe.
                                fbss <= FBSS_FLUSH_W_RSP;
                            end
                        end else begin
                            fb_acc_valid <= 1'b1;
                            fb_acc_addr  <= p3_word_addr;
                            fb_acc_data  <= (fb_acc_data & ~fb_lane_data_mask(p3_byte_lane))
                                          | fb_lane_data(p3_byte_lane, p3_color);
                            fb_acc_mask  <= fb_acc_mask | fb_lane_mask(p3_byte_lane);
                            p3_consumed = 1'b1;
                        end

                    end
                    else if (blend_group_active
                          && src_done && !p0a_valid && !p0_valid && !p1_valid && !p2_valid
                          && !p2b_valid && !p3_valid) begin
                        // End of stream with a partially collected translucent
                        // word.  Drain it before the normal fragment-pipe
                        // exit path can hand control back to the command FSM.
                        fbss <= FBSS_BLEND_REQ;
                    end
                    // If the pipeline is stalled this cycle (cache miss
                    // upstream, write counter pressure, or a FBSS detour), the
                    // shift won't fire to overwrite p3.  Clear it only after a
                    // path above has actually consumed the pixel.
                    if (p3_consumed && fp_pipe_stall) p3_valid <= 0;
                end

                FBSS_FLUSH_W_RSP: begin
                    // Queue-full retry.  The boundary-crossing pixel is
                    // still in p3; return to IDLE once the queue has a slot.
                    if (fb_write_can_issue) begin
                        // Don't touch p3_valid: if FBSS parked because the
                        // write queue was full, p3 is still the unconsumed
                        // boundary-crossing pixel.  FBSS_IDLE will retry it.
                        fbss <= FBSS_IDLE;
                    end
                end

                FBSS_ZTEST_AR_WAIT: begin
                    if (blend_arready) begin
                        blend_arvalid <= 1'b0;
                        fbss          <= FBSS_ZTEST_R_WAIT;
                    end
                end

                FBSS_ZTEST_R_WAIT: begin
	                    if (blend_rvalid) begin
	                        if (p3_z_pass) begin
	                            if (p3_z_write) begin
	                                z_acc_valid <= 1'b1;
	                                z_acc_addr  <= p3_z_word_addr;
	                                if (p3_z_hi) begin
	                                    z_acc_hi <= p3_z_value;
	                                    z_acc_lo <= blend_rdata[15:0];
	                                end else begin
	                                    z_acc_hi <= blend_rdata[31:16];
	                                    z_acc_lo <= p3_z_value;
	                                end
	                                z_acc_mask  <= p3_z_hi ? 4'b1100 : 4'b0011;
	                            end
                            p3_z_test <= 1'b0;
                        end else begin
                            p3_valid <= 1'b0;
                        end
                        fbss <= FBSS_IDLE;
                    end
                end

                // --------------------------------------------------------
                // Translucent-blend sub-flow.  Entry from FBSS_IDLE has
                // collected one or more active lanes into blend_group_*.
                // Read the existing FB word once, apply fb_acc same-word
                // bypass, serialise active-lane LUT reads, then commit the
                // modified word into fb_acc using the same cross-word logic
                // as the opaque fast path.
                // --------------------------------------------------------
                FBSS_BLEND_REQ: begin
                    // Three gates before issuing the BLEND read on M0:
                    //   1. !tex_axi_arvalid && !tex_m0_in_flight — texture
                    //      cache must be fully drained from M0 (no pending
                    //      AR, no in-flight read) so blend_owns_m0 doesn't
                    //      collide with an in-flight tex fill on the R
                    //      channel.
                    //   2. write FIFO empty + m_wr channel idle +
                    //      m_wr_inflight == 0 — RAW barrier.  Cross-word
                    //      fb_acc flushes may be queued or already in
                    //      transit to SDRAM when we'd otherwise issue this
                    //      BLEND read.  If the arbiter grants m_rd before
                    //      the slave's pending write commits, the read
                    //      returns pre-flush data and the blend uses a stale
                    //      FB byte.  The same-word fb_acc bypass below
                    //      catches writes that haven't yet flushed; these
                    //      gates catch the ones that have.
                    if (!blend_group_active) begin
                        fbss <= FBSS_IDLE;
                    end else if (!tex_axi_arvalid && !tex_m0_in_flight
                              && fb_write_drain_complete) begin
                        blend_arvalid <= 1;
                        blend_araddr  <= blend_group_word_addr;
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
                        reg [31:0] read_word;
                        read_word = blend_rdata;
                        if (fb_acc_valid && fb_acc_addr == blend_group_word_addr) begin
                            if (fb_acc_mask[0]) read_word[7:0]   = fb_acc_data[7:0];
                            if (fb_acc_mask[1]) read_word[15:8]  = fb_acc_data[15:8];
                            if (fb_acc_mask[2]) read_word[23:16] = fb_acc_data[23:16];
                            if (fb_acc_mask[3]) read_word[31:24] = fb_acc_data[31:24];
                        end
                        blend_result_word <= read_word;
                        blend_p3_match_r <= fb_acc_word_match;
                        blend_lane_iter <= 2'd0;
                        fbss <= FBSS_BLEND_SELECT;
                    end
                end

                FBSS_BLEND_SELECT: begin : fbss_blend_select_blk
                    if (blend_group_mask[blend_lane_iter]) begin
                        if (transluc_cache_hit) begin
                            blend_result_word <= (blend_result_word & ~fb_lane_data_mask(blend_lane_iter))
                                               | fb_lane_data(blend_lane_iter, transluc_cache_byte);
                            if (blend_lane_iter == 2'd3) begin
                                fbss <= FBSS_BLEND_APPLY;
                            end else begin
                                blend_lane_iter <= blend_lane_iter + 2'd1;
                            end
                        end else if (transluc_sram_lookup_ready) begin
                            transluc_rd_addr      <= blend_lut_addr_w;
                            transluc_lookup_fire  <= 1'b1;
                            blend_lut_lane        <= blend_lane_iter;
                            fbss                  <= FBSS_BLEND_LUT_WAIT;
                        end
                    end else if (blend_lane_iter == 2'd3) begin
                        fbss <= FBSS_BLEND_APPLY;
                    end else begin
                        blend_lane_iter <= blend_lane_iter + 2'd1;
                    end
                end

                FBSS_BLEND_LUT_WAIT: begin
                    if (sram_rdata_valid) begin : fbss_blend_lut_capture
                        reg [7:0] lut_byte;
                        reg [31:0] lut_shifted;
                        lut_shifted = sram_rdata >> {transluc_rd_addr[1:0], 3'b0};
                        lut_byte = lut_shifted[7:0];
                        blend_result_word <= (blend_result_word & ~fb_lane_data_mask(blend_lut_lane))
                                           | fb_lane_data(blend_lut_lane, lut_byte);
                        if (blend_lut_lane == 2'd3) begin
                            fbss <= FBSS_BLEND_APPLY;
                        end else begin
                            blend_lane_iter <= blend_lut_lane + 2'd1;
                            fbss <= FBSS_BLEND_SELECT;
                        end
                    end
                end

                FBSS_BLEND_APPLY: begin : fbss_blend_apply_blk
                    // All active lanes have been blended into
                    // blend_result_word.  Apply the grouped word fragment to
                    // fb_acc, preserving same-word dirty lanes that were not
                    // part of this translucent group.
                    if (!blend_p3_match_r) begin
                        if (fb_write_can_issue) begin
                            fbwq_push_req  = 1'b1;
                            fbwq_push_addr = fb_acc_addr;
                            fbwq_push_data = fb_acc_data;
                            fbwq_push_strb = fb_acc_mask;

                            fb_acc_valid <= 1'b1;
                            fb_acc_addr  <= blend_group_word_addr;
                            fb_acc_data  <= blend_result_word;
                            fb_acc_mask  <= blend_group_mask;
                            blend_group_active <= 1'b0;
                            blend_group_mask   <= 4'b0;
                            fbss <= FBSS_IDLE;
                        end
                    end else begin
                        fb_acc_valid <= 1'b1;
                        fb_acc_addr  <= blend_group_word_addr;
                        fb_acc_data  <= blend_result_word;
                        fb_acc_mask  <= (fb_acc_valid && fb_acc_addr == blend_group_word_addr)
                                      ? (fb_acc_mask | blend_group_mask)
                                      : blend_group_mask;
                        blend_group_active <= 1'b0;
                        blend_group_mask   <= 4'b0;
                        fbss <= FBSS_IDLE;
                    end
                end

                default: fbss <= FBSS_IDLE;
            endcase

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
                                  && (sp_count > PERSPECTIVE_SEG_LEN
                                      || sp_seg_left != 4'd0)) begin
                            // Only fill slot B if there's a future segment
                            // that will swap it in. (sp_count includes the
                            // current segment's remaining pixels.)
                            persp_pass <= PSS_PASS_TO_B;
                            persp_pss  <= PSS_ADV;
                        end
                    end
                end

	                PSS_ADV: begin : pss_adv_blk
	                    reg [15:0] future_count;
	                    reg [15:0] current_segment_left;
	                    reg [4:0]  end_advance;
	                    reg [4:0]  slope_divisor;
	                    reg        tail_dynamic;
                    // Stage 1 of pipelined setup: advance projection-space
                    // accumulators by one segment and register old/new zinv.
                    // Splitting the old single-cycle (advance → CLZ → top8 →
                    // recip_rd_addr) chain into ADV / ADV_CLAMP / CLZ /
                    // TOP8 closes the timing path.
                    //
                    // Quake's software rasterizer uses 16-pixel affine
                    // subspans for non-final chunks, but the final chunk uses
                    // endpoint (count-1) and divides by (count-1).  The old
                    // GPU path always advanced by 16 and divided by 16, which
                    // over-projected every short/remainder floor span.
	                    if (!sp_persp_q29_mode) begin
	                        end_advance = PERSPECTIVE_SEG_LEN[4:0];
	                        slope_divisor = PERSPECTIVE_SEG_LEN[4:0];
	                        tail_dynamic = 1'b0;
	                    end else begin
                        if (persp_pass == PSS_PASS_TO_A) begin
                            future_count = sp_count;
                        end else begin
                            current_segment_left = {12'd0, sp_seg_left} + 16'd1;
                            future_count = (sp_count > current_segment_left)
                                         ? (sp_count - current_segment_left)
                                         : 16'd0;
                        end

	                        if (future_count > PERSPECTIVE_SEG_LEN) begin
	                            end_advance = PERSPECTIVE_SEG_LEN[4:0];
	                            slope_divisor = PERSPECTIVE_SEG_LEN[4:0];
	                            tail_dynamic = 1'b0;
	                        end else if (future_count > 16'd1) begin
	                            end_advance = future_count[4:0] - 5'd1;
	                            slope_divisor = future_count[4:0] - 5'd1;
	                            tail_dynamic = 1'b1;
	                        end else begin
	                            end_advance = 5'd0;
	                            slope_divisor = 5'd1;
	                            tail_dynamic = 1'b1;
	                        end
	                    end

	                    pss_slope_divisor <= slope_divisor;
	                    pss_zinv_prev_r  <= sp_zinv;
	                    pss_zinv_abs_na_r <= persp_zinv_abs_na;
	                    if (tail_dynamic) begin
	                        pss_tail_advance <= end_advance;
	                        dsp_a  <= sp_sZstep;
	                        dsp_b  <= $signed({27'd0, end_advance});
	                        dsp2_a <= sp_tZstep;
	                        dsp2_b <= $signed({27'd0, end_advance});
	                        persp_pss <= PSS_ADV_TAIL_ST_WAIT;
	                    end else begin
	                        sp_sZ          <= sp_sZ   + (sp_sZstep   <<< PERSPECTIVE_SEG_SHIFT);
	                        sp_tZ          <= sp_tZ   + (sp_tZstep   <<< PERSPECTIVE_SEG_SHIFT);
	                        sp_zinv        <= sp_zinv + (sp_zinv_step <<< PERSPECTIVE_SEG_SHIFT);
	                        pss_zinv_adv_r <= sp_zinv + (sp_zinv_step <<< PERSPECTIVE_SEG_SHIFT);
	                        persp_pss      <= PSS_ADV_CLAMP;
	                    end
	                end

	                PSS_ADV_TAIL_ST_WAIT: begin
	                    persp_pss <= PSS_ADV_TAIL_ST_CAPTURE;
	                end

	                PSS_ADV_TAIL_ST_CAPTURE: begin
	                    pss_tail_s_delta <= dsp_p[31:0];
	                    pss_tail_t_delta <= dsp2_p[31:0];
	                    dsp_a  <= sp_zinv_step;
	                    dsp_b  <= $signed({27'd0, pss_tail_advance});
	                    persp_pss <= PSS_ADV_TAIL_Z_WAIT;
	                end

	                PSS_ADV_TAIL_Z_WAIT: begin
	                    persp_pss <= PSS_ADV_TAIL_COMMIT;
	                end

	                PSS_ADV_TAIL_COMMIT: begin
	                    sp_sZ          <= sp_sZ + pss_tail_s_delta;
	                    sp_tZ          <= sp_tZ + pss_tail_t_delta;
	                    sp_zinv        <= sp_zinv + $signed(dsp_p[31:0]);
	                    pss_zinv_adv_r <= sp_zinv + $signed(dsp_p[31:0]);
	                    persp_pss      <= PSS_ADV_CLAMP;
	                end

	                PSS_ADV_CLAMP: begin
                    // Stage 2: register |sp_zinv_new| and the singularity
                    // clamp decision from the old/new values captured by
                    // PSS_ADV.  This removes the clamp compare from the
                    // advance-adder timing cone.
                    persp_zinv_abs_r <= pss_zinv_adv_abs_r;
                    recip_norm_abs_r <= pss_zinv_adv_abs_r;
                    // Singularity clamp.  Q29 param spans represent real
                    // Quake floor perspective, where a valid 16-pixel
                    // segment can shrink |1/z| by more than 4x.  Keep the
                    // ratio guard for the legacy Q16 path only.
                    if (sp_persp_q29_mode) begin
                        pss_zinv_clamp_r <=
                            (pss_zinv_adv_abs_r == 32'd0)
                         || ((pss_zinv_adv_r[31] ^ pss_zinv_prev_r[31])
                             && (pss_zinv_prev_r != 32'sd0)
                             && (pss_zinv_adv_r != 32'sd0));
                    end else begin
                        pss_zinv_clamp_r <=
                            ((pss_zinv_adv_r[31] ^ pss_zinv_prev_r[31])
                             && (pss_zinv_prev_r != 32'sd0)
                             && (pss_zinv_adv_r != 32'sd0))
                         || (pss_zinv_adv_abs_r < (pss_zinv_abs_na_r >> 2));
                    end
                    persp_pss <= PSS_CLZ;
                end

                PSS_RECIP_NA: begin
                    // First-pass entry: no advance, but register |sp_zinv|
                    // into the same pipeline reg so we can fall through the
                    // shared CLZ / TOP8 stages.
                    persp_zinv_abs_r <= persp_zinv_abs_na;
                    recip_norm_abs_r <= persp_zinv_abs_na;
                    // Anchor pass — no advance to clamp.
                    pss_zinv_clamp_r <= 1'b0;
                    persp_pss <= PSS_CLZ;
                end

                PSS_CLZ: begin
                    // Stage 3: compute leading-zero count of the registered
                    // abs value and register it. Inputs are FF outputs;
                    // output is a FF input — the casez sits between two
                    // register banks.
                    persp_clz        <= recip_clz_pipe;
                    recip_norm_clz_r <= recip_clz_pipe;
                    persp_pss        <= PSS_TOP8;
                end

                PSS_TOP8: begin
                    // Stage 4: compute the top-10 normalized bits (the recip
                    // LUT index) from the registered abs and clz, and write
                    // recip_rd_addr. Variable barrel shift is the only
                    // combinational chain in this stage.
                    recip_rd_addr <= recip_top10_pipe;
                    persp_pss     <= PSS_RECIP_W;
                end

                PSS_RECIP_W: begin
                    // BRAM read latency — recip_rd_data valid next cycle.
                    persp_pss <= PSS_RECIP_SHIFT;
                end

                PSS_RECIP_SHIFT: begin
                    // Compute the reciprocal from LUT mantissa + clz shift
                    // and latch it before the N-R refinement. Normal spans
                    // use Q16.16.  Quake param-span Q29 mode carries a much
                    // larger denominator, so keep four extra fractional bits
                    // in the reciprocal and compensate in the final multiply;
                    // otherwise the reciprocal often has only ~10 integer
                    // levels and texel boundaries drift.
                    if (sp_persp_q29_mode) begin
                        if (pss_q29_recip_shift >= 6'd13)
                            recip_q16_r <= $signed({16'b0, recip_rd_data})
                                         <<< (pss_q29_recip_shift - 6'd13);
                        else
                            recip_q16_r <= $signed({16'b0, recip_rd_data})
                                         >>> (6'd13 - pss_q29_recip_shift);
                    end else if (persp_clz >= 5'd13) begin
                        recip_q16_r <= $signed({16'b0, recip_rd_data})
                                     <<< (persp_clz - 5'd13);
                    end else begin
                        recip_q16_r <= $signed({16'b0, recip_rd_data})
                                     >>> (5'd13 - persp_clz);
                    end
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
                PSS_NR_SUB: begin : pss_nr_sub_blk
                    reg signed [31:0] xy_q16;
                    reg signed [63:0] xy_shifted;
                    // dsp_p[47:16] = x * y0 in Q16.16 ≈ 1.0 (= 0x10000).
                    // For a perfect y0, exactly 1.0; LUT precision causes
                    // a small offset.  N-R uses (2 - x*y0) to "correct"
                    // the offset on the next multiply.
                    xy_shifted = dsp_p >>> (16 + PSS_Q29_RECIP_EXTRA_INT);
                    xy_q16 = sp_persp_q29_mode ? xy_shifted[31:0]
                                                : $signed(dsp_p[47:16]);
                    nr_two_minus_xy <= 32'h00020000 - xy_q16;
                    persp_pss <= PSS_NR_MUL_Y;
                end
                PSS_NR_MUL_Y: begin
                    // Launch y0 * (2 - x * y0).
                    dsp_a <= recip_q16_r;
                    dsp_b <= nr_two_minus_xy;
                    persp_pss <= PSS_NR_MUL_Y_W;
                end
                PSS_NR_MUL_Y_W: persp_pss <= PSS_NR_CAPTURE;
                PSS_NR_CAPTURE: begin : pss_nr_capture_blk
                    reg signed [63:0] recip_shifted;
                    // Refined Q16.16 reciprocal.  Constant-Z spans convert
                    // projected endpoints into affine spans once, and Doom's
                    // wall projection often lands exactly on an integer
                    // texture column.  A floor reciprocal can under-project
                    // those values by a few Q16 LSBs and sample the previous
                    // column.  Bias constant-Z by one reciprocal LSB so exact
                    // boundaries land on/above the intended texel while full
                    // perspective spans keep the unbiased reciprocal.
                    if (sp_persp_q29_mode) begin
                        recip_shifted = dsp_p >>> 16;
                        recip_q16_r <= recip_shifted[31:0];
                    end else begin
                        recip_q16_r <= dsp_p[47:16]
                                     + ((sp_zinv_step == 32'sd0
                                      && sp_zinv != 32'sh00010000) ? 32'sd1
                                                                   : 32'sd0);
                    end
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
                    reg signed [31:0] s_projected;
                    reg signed [31:0] t_projected;
                    reg signed [63:0] s_projected64;
                    reg signed [63:0] t_projected64;
                    // dsp_p  = sZ × recip → s_end
                    // dsp2_p = tZ × recip → t_end
                    //
                    // Task #89 clamp: when the sub-segment advance
                    // brought sp_zinv too close to zero (or
                    // across it), 1/sp_zinv has blown up and the
                    // computed s_end / t_end are wildly wrong.
                    // Substitute persp_anchor_s/t — slope = 0, the
                    // sub-segment renders with constant s/t.  See
                    // pss_zinv_clamp_r declaration for the full
                    // analysis.
                    //
                    // Register endpoints before deriving slopes.  The previous
                    // single-cycle path ran DSP output -> 32-bit subtract ->
                    // sp_sstep/sp_tstep and was the post-fit 100 MHz limiter.
                    if (sp_persp_q29_mode) begin
                        s_projected64 = (dsp_p + (64'sd1 << (15 + PSS_Q29_RECIP_EXTRA_INT)))
                                      >>> (16 + PSS_Q29_RECIP_EXTRA_INT);
                        t_projected64 = (dsp2_p + (64'sd1 << (15 + PSS_Q29_RECIP_EXTRA_INT)))
                                      >>> (16 + PSS_Q29_RECIP_EXTRA_INT);
                        s_projected = s_projected64[31:0];
                        t_projected = t_projected64[31:0];
                    end else begin
                        s_projected = q16_round_product(dsp_p);
                        t_projected = q16_round_product(dsp2_p);
                    end
                    pss_s_end_r <= pss_zinv_clamp_r ? persp_anchor_s
                                                     : s_projected;
                    pss_t_end_r <= pss_zinv_clamp_r ? persp_anchor_t
                                                     : t_projected;
                    persp_pss   <= PSS_SLOPE;
                end

                PSS_SLOPE: begin
                    persp_pss <= PSS_IDLE;
                    case (persp_pass)
                        PSS_PASS_ANCHOR: begin
                            if (sp_zinv_step == 32'sd0) begin
                                // Constant-Z perspective spans (Doom walls):
                                // one reciprocal is enough for the whole
                                // generated span.  Convert to affine by
                                // projecting the start point and multiplying
                                // the projection-space minor steps by the same
                                // reciprocal.  This avoids the 16-pixel PSS
                                // segment loop entirely while preserving the
                                // perspective-correct result for constant 1/Z.
                                sp_s <= pss_s_end_r;
                                sp_t <= pss_t_end_r;
                                dsp_a  <= sp_sZstep;
                                dsp_b  <= recip_q16_r;
                                dsp2_a <= sp_tZstep;
                                dsp2_b <= recip_q16_r;
                                persp_first_done <= 1'b1;
                                persp_pss <= PSS_CONSTZ_STEP_W;
                            end else begin
                                // Pass 1: just store the anchor at pos 0.
                                persp_anchor_s   <= pss_s_end_r;
                                persp_anchor_t   <= pss_t_end_r;
                                persp_first_done <= 1;
                            end
                        end
                        PSS_PASS_TO_A: begin
                            // Pass 2: derive slot A slopes from anchor to the
                            // end of the first affine sub-segment.
                            pss_slope_s_delta <= $signed(pss_s_end_r) - $signed(persp_anchor_s);
                            pss_slope_t_delta <= $signed(pss_t_end_r) - $signed(persp_anchor_t);
                            sp_s              <= persp_anchor_s;
                            sp_t              <= persp_anchor_t;
                            persp_pss         <= PSS_SLOPE_PREP;
                        end
                        PSS_PASS_TO_B: begin
                            // Pass 3+: derive slot B (pending) slopes.
                            pss_slope_s_delta <= $signed(pss_s_end_r) - $signed(persp_anchor_s);
                            pss_slope_t_delta <= $signed(pss_t_end_r) - $signed(persp_anchor_t);
                            persp_pend_s      <= persp_anchor_s;
                            persp_pend_t      <= persp_anchor_t;
                            persp_pss         <= PSS_SLOPE_PREP;
                        end
                        default: ;
                    endcase
                end

                PSS_SLOPE_PREP: begin : pss_slope_prep_blk
                    reg [31:0] s_mag;
                    reg [31:0] t_mag;
                    reg [1:0]  pow2_shift;
                    pow2_shift = (pss_slope_divisor == 5'd1) ? 2'd0
                               : (pss_slope_divisor == 5'd2) ? 2'd1
                               : (pss_slope_divisor == 5'd4) ? 2'd2
                                                             : 2'd3;
                    persp_pss <= PSS_IDLE;
                    if (pss_slope_divisor == 5'd1
                     || pss_slope_divisor == 5'd2
                     || pss_slope_divisor == 5'd4
                     || pss_slope_divisor == 5'd8) begin
                        case (persp_pass)
                            PSS_PASS_TO_A: begin
                                sp_sstep          <= pss_div_pow2_trunc(pss_slope_s_delta, pow2_shift);
                                sp_tstep          <= pss_div_pow2_trunc(pss_slope_t_delta, pow2_shift);
                                sp_seg_left       <= PERSPECTIVE_SEG_LAST;
                                persp_anchor_s    <= pss_s_end_r;
                                persp_anchor_t    <= pss_t_end_r;
                                persp_seg_a_ready <= 1;
                            end
                            PSS_PASS_TO_B: begin
                                persp_pend_sstep  <= pss_div_pow2_trunc(pss_slope_s_delta, pow2_shift);
                                persp_pend_tstep  <= pss_div_pow2_trunc(pss_slope_t_delta, pow2_shift);
                                persp_anchor_s    <= pss_s_end_r;
                                persp_anchor_t    <= pss_t_end_r;
                                persp_seg_b_ready <= 1;
                            end
                            default: ;
                        endcase
                    end else if (pss_slope_divisor < 5'd16) begin
                        s_mag = pss_slope_s_delta[31] ? (32'd0 - pss_slope_s_delta[31:0])
                                                      : pss_slope_s_delta[31:0];
                        t_mag = pss_slope_t_delta[31] ? (32'd0 - pss_slope_t_delta[31:0])
                                                      : pss_slope_t_delta[31:0];
                        pss_slope_s_neg <= pss_slope_s_delta[31];
                        pss_slope_t_neg <= pss_slope_t_delta[31];
                        pss_slope_s_mag <= s_mag;
                        pss_slope_t_mag <= t_mag;
                        dsp_a  <= $signed(s_mag);
                        dsp_b  <= $signed(pss_div_recip32(pss_slope_divisor));
                        dsp2_a <= $signed(t_mag);
                        dsp2_b <= $signed(pss_div_recip32(pss_slope_divisor));
                        persp_pss <= PSS_SLOPE_DIV_WAIT;
                    end else begin
                        case (persp_pass)
                            PSS_PASS_TO_A: begin
                                sp_sstep          <= pss_slope_s_delta >>> PERSPECTIVE_SEG_SHIFT;
                                sp_tstep          <= pss_slope_t_delta >>> PERSPECTIVE_SEG_SHIFT;
                                sp_seg_left       <= PERSPECTIVE_SEG_LAST;
                                persp_anchor_s    <= pss_s_end_r;
                                persp_anchor_t    <= pss_t_end_r;
                                persp_seg_a_ready <= 1;
                            end
                            PSS_PASS_TO_B: begin
                                persp_pend_sstep  <= pss_slope_s_delta >>> PERSPECTIVE_SEG_SHIFT;
                                persp_pend_tstep  <= pss_slope_t_delta >>> PERSPECTIVE_SEG_SHIFT;
                                persp_anchor_s    <= pss_s_end_r;
                                persp_anchor_t    <= pss_t_end_r;
                                persp_seg_b_ready <= 1;
                            end
                            default: ;
                        endcase
                    end
                end

                PSS_SLOPE_DIV_WAIT: begin
                    persp_pss <= PSS_SLOPE_DIV_COMMIT;
                end

                PSS_SLOPE_DIV_COMMIT: begin
                    pss_slope_s_quot <= dsp_p[63:32];
                    pss_slope_t_quot <= dsp2_p[63:32];
                    dsp_a  <= $signed(dsp_p[63:32]);
                    dsp_b  <= $signed({27'd0, pss_slope_divisor});
                    dsp2_a <= $signed(dsp2_p[63:32]);
                    dsp2_b <= $signed({27'd0, pss_slope_divisor});
                    persp_pss <= PSS_SLOPE_DIV_CORR_WAIT;
                end

                PSS_SLOPE_DIV_CORR_WAIT: begin
                    persp_pss <= PSS_SLOPE_DIV_CORR_COMMIT;
                end

                PSS_SLOPE_DIV_CORR_COMMIT: begin
                    pss_slope_s_corr <= (dsp_p[36:0] > {5'd0, pss_slope_s_mag});
                    pss_slope_t_corr <= (dsp2_p[36:0] > {5'd0, pss_slope_t_mag});
                    persp_pss <= PSS_SLOPE_DIV_QUOT_COMMIT;
                end

                PSS_SLOPE_DIV_QUOT_COMMIT: begin
                    pss_slope_s_quot <= pss_slope_s_quot - {31'd0, pss_slope_s_corr};
                    pss_slope_t_quot <= pss_slope_t_quot - {31'd0, pss_slope_t_corr};
                    persp_pss <= PSS_SLOPE_DIV_STEP_COMMIT;
                end

                PSS_SLOPE_DIV_STEP_COMMIT: begin : pss_slope_div_step_commit_blk
                    reg signed [31:0] s_step;
                    reg signed [31:0] t_step;
                    s_step = pss_slope_s_neg ? -$signed(pss_slope_s_quot) : $signed(pss_slope_s_quot);
                    t_step = pss_slope_t_neg ? -$signed(pss_slope_t_quot) : $signed(pss_slope_t_quot);
                    persp_pss <= PSS_IDLE;
                    case (persp_pass)
                        PSS_PASS_TO_A: begin
                            sp_sstep          <= s_step;
                            sp_tstep          <= t_step;
                            sp_seg_left       <= PERSPECTIVE_SEG_LAST;
                            persp_anchor_s    <= pss_s_end_r;
                            persp_anchor_t    <= pss_t_end_r;
                            persp_seg_a_ready <= 1;
                        end
                        PSS_PASS_TO_B: begin
                            persp_pend_sstep  <= s_step;
                            persp_pend_tstep  <= t_step;
                            persp_anchor_s    <= pss_s_end_r;
                            persp_anchor_t    <= pss_t_end_r;
                            persp_seg_b_ready <= 1;
                        end
                        default: ;
                    endcase
                end

                PSS_CONSTZ_STEP_W: begin
                    persp_pss <= PSS_CONSTZ_STEP_CAPTURE;
                end

                PSS_CONSTZ_STEP_CAPTURE: begin : pss_constz_step_capture_blk
                    reg signed [63:0] s_step_projected;
                    reg signed [63:0] t_step_projected;
                    if (sp_persp_q29_mode) begin
                        s_step_projected =
                            (dsp_p + (64'sd1 << (15 + PSS_Q29_RECIP_EXTRA_INT)))
                         >>> (16 + PSS_Q29_RECIP_EXTRA_INT);
                        t_step_projected =
                            (dsp2_p + (64'sd1 << (15 + PSS_Q29_RECIP_EXTRA_INT)))
                         >>> (16 + PSS_Q29_RECIP_EXTRA_INT);
                        sp_sstep <= s_step_projected[31:0];
                        sp_tstep <= t_step_projected[31:0];
                    end else begin
                        sp_sstep <= q16_round_product(dsp_p);
                        sp_tstep <= q16_round_product(dsp2_p);
                    end
                    sp_flags          <= sp_flags & ~(4'b0001 << SPAN_PERSP);
                    sp_seg_left       <= 4'd0;
                    persp_active      <= 1'b0;
                    persp_seg_a_ready <= 1'b1;
                    persp_seg_b_ready <= 1'b0;
                    persp_swap_pending <= 1'b0;
                    persp_pss         <= PSS_IDLE;
                end

                default: persp_pss <= PSS_IDLE;
            endcase

            // ----------------------------------------------------------
            // Drain detection — when source done and pipe empty, flush the
            // framebuffer accumulators before accepting the next command.
            // ----------------------------------------------------------
		            if (src_done && !p0a_valid && !p0_valid && !p1_valid && !p2_valid && !p2b_valid
		                         && !p3_valid && fbss == FBSS_IDLE
		                         && !blend_group_active
		                         && !persp_active) begin
	                src_done <= 0;
	                persp_active      <= 0;  // disarm so PSS doesn't keep running
	                persp_swap_pending <= 1'b0;
	                persp_first_done  <= 0;
                if (spanprod_active) begin
	                    if (spanprod_idx == spanprod_last_idx) begin
	                        if ((spanprod_records_left > {13'd0, spanprod_record_count})
	                            && (pay_remaining != 13'd0)) begin
	                            spanprod_prepare_next_record_chunk;
	                        end else begin
	                            spanprod_active <= 1'b0;
	                            state <= S_FB_FLUSH;
	                        end
	                    end else begin
	                        spanprod_idx <= spanprod_idx + 2'd1;
	                        state <= S_SPANPROD_SETUP;
                    end
                end else
                    state    <= S_FB_FLUSH;
            end
        end

        // ============================================================
        // FB flush — end-of-span or mid-span word boundary
        // ============================================================
        S_FB_FLUSH: begin
            if (z_acc_valid && |z_acc_mask) begin
                if (fb_write_can_issue) begin
                    fbwq_push_req  = 1'b1;
                    fbwq_push_addr = z_acc_addr;
                    fbwq_push_data = z_acc_data;
                    fbwq_push_strb = z_acc_mask;
                    z_acc_valid    <= 1'b0;
                    z_acc_mask     <= 4'b0;
                end
            end else if (fb_acc_valid && |fb_acc_mask) begin
                if (fb_write_can_issue) begin
                    fbwq_push_req  = 1'b1;
                    fbwq_push_addr = fb_acc_addr;
                    fbwq_push_data = fb_acc_data;
                    fbwq_push_strb = fb_acc_mask;
                    finish_fragment_stream_after_flush();
                end
            end else begin
                finish_fragment_stream_after_flush();
            end
        end

        // ============================================================
        // CMD_CLEAR_RECT — partial-rect FB clear (letterbox bars,
        // status-bar wipes, menu pane backgrounds, splash underlay).
        // Walks h rows × w bytes through M_WR with byte-strobed
        // partial-word edges.  Word-aligned full-width row strips hit
        // the 4-byte fast path (cr_strobe = 4'b1111, 4 bytes per AXI
        // beat); arbitrary x/w paths use the general byte-strobe.
        //
        // Stride is the active st_fb_stride.  CPU pre-computes the
        // start byte address (fb_base + y*stride + x) so the GPU
        // doesn't need a runtime multiply for the row-base derivation.
        // ============================================================
        S_CLEAR_RECT: begin
            // Entry from S_EXECUTE.  cr_addr / cr_row_addr / cr_w_remaining
            // / cr_w_total / cr_y_remaining / cr_color have already been
            // loaded by S_PAY_DATA.  If the rect is empty (h=0 or w=0),
            // bail straight back to idle — no AXI traffic.
            if (cr_y_remaining == 16'd0 || cr_w_total == 16'd0)
                state <= S_IDLE;
            else
                state <= S_CLEAR_RECT_WORD;
        end

        S_CLEAR_RECT_WORD: begin : clear_rect_word_blk
            // Compute word-aligned addr + lane + bytes-this-word + strobe
            // combinationally for the current cr_addr / cr_w_remaining.
            // bytes_this_word = min(4 - lane, cr_w_remaining), capped at
            // 4 (when lane=0 and the row has ≥4 bytes left).  Strobe is
            // ((1 << bytes_this_word) - 1) << lane — the 4×4 lane×bytes
            // combinations resolve to a single AND/OR mask in fitting.
            reg  [1:0] cr_lane;
            reg  [2:0] cr_bytes_this;     // 1..4
            reg  [3:0] cr_strobe;
            cr_lane = cr_addr[1:0];
            cr_bytes_this = (cr_w_remaining >= 16'd4
                             && cr_lane == 2'd0)
                              ? 3'd4
                              : ((cr_w_remaining >= {13'b0, 3'd4 - {1'b0, cr_lane}})
                                  ? (3'd4 - {1'b0, cr_lane})
                                  : cr_w_remaining[2:0]);
            cr_strobe = ((4'b0001 << cr_bytes_this) - 4'b0001) << cr_lane;

            if (fbwq_can_push) begin
                fbwq_push_req  = 1'b1;
                fbwq_push_addr = cr_addr & 32'hFFFFFFFC;
                fbwq_push_data = {cr_color, cr_color, cr_color, cr_color};
                fbwq_push_strb = cr_strobe;

                if (cr_w_remaining <= {13'b0, cr_bytes_this}) begin
                    // Last word in this row.  Advance to next row.
                    if (cr_y_remaining == 16'd1) begin
                        // Last row finished — rect done.
                        state <= S_IDLE;
                    end else begin : cr_row_advance_queued
                        // Per-command stride if non-zero; otherwise the
                        // SET_FB global for callers that don't fill the stride field.
                        reg [15:0] cr_eff_stride;
                        cr_eff_stride = (cr_stride != 16'd0) ? cr_stride
                                                              : st_fb_stride;
                        cr_row_addr    <= cr_row_addr + {16'b0, cr_eff_stride};
                        cr_addr        <= cr_row_addr + {16'b0, cr_eff_stride};
                        cr_w_remaining <= cr_w_total;
                        cr_y_remaining <= cr_y_remaining - 16'd1;
                        state          <= S_CLEAR_RECT_WORD;
                    end
                end else begin
                    cr_addr        <= cr_addr + {29'b0, cr_bytes_this};
                    cr_w_remaining <= cr_w_remaining - {13'b0, cr_bytes_this};
                    state          <= S_CLEAR_RECT_WORD;
                end
            end
        end


        default: state <= S_IDLE;
        endcase

            fbwq_push_links_fifo_tail =
                   (fbwq_strb[fbwq_prev_wr_ptr] == 4'hF)
                && (fbwq_push_strb == 4'hF)
                && (fbwq_addr[fbwq_prev_wr_ptr][11:0] <= 12'hFF8)
                && (fbwq_push_addr == fbwq_addr[fbwq_prev_wr_ptr] + 32'd4);
            fbwq_push_links_stage_tail =
                   (fbwq_stage_strb == 4'hF)
                && (fbwq_push_strb == 4'hF)
                && (fbwq_stage_addr[11:0] <= 12'hFF8)
                && (fbwq_push_addr == fbwq_stage_addr + 32'd4);
            fbwq_push_link_tail = fbwq_stage_drain_now
                                 ? fbwq_push_links_stage_tail
                                 : fbwq_push_links_fifo_tail;

            // --------------------------------------------------------
            // Central AXI write drain.  The main FSM only enqueues
            // writes into fbwq; this block owns m_wr_* and preserves
            // write order across spans, clears, fences, and translucent
            // read-modify-write barriers.
            // --------------------------------------------------------
            if (fbwq_start_now) begin
                m_wr_awvalid <= 1'b1;
                m_wr_awaddr  <= fbwq_addr[fbwq_rd_ptr];
                m_wr_awlen   <= {4'b0, fbwq_start_burst_words - 4'd1};
                m_wr_wvalid  <= 1'b1;
                m_wr_wdata   <= fbwq_data[fbwq_rd_ptr];
                m_wr_wstrb   <= fbwq_strb[fbwq_rd_ptr];
                m_wr_wlast   <= (fbwq_start_burst_words == 4'd1);
                fbwq_rd_ptr  <= fbwq_rd_ptr + 1'b1;
                fbwq_burst_remaining <= fbwq_start_burst_words - 4'd1;
            end else if (fbwq_continue_now) begin
                m_wr_wvalid  <= 1'b1;
                m_wr_wdata   <= fbwq_data[fbwq_rd_ptr];
                m_wr_wstrb   <= fbwq_strb[fbwq_rd_ptr];
                m_wr_wlast   <= (fbwq_burst_remaining == 4'd1);
                fbwq_rd_ptr  <= fbwq_rd_ptr + 1'b1;
                fbwq_burst_remaining <= fbwq_burst_remaining - 4'd1;
            end

            if (fbwq_stage_drain_now) begin
                fbwq_addr[fbwq_wr_ptr] <= fbwq_stage_addr;
                fbwq_data[fbwq_wr_ptr] <= fbwq_stage_data;
                fbwq_strb[fbwq_wr_ptr] <= fbwq_stage_strb;
                fbwq_link_next[fbwq_wr_ptr] <= 1'b0;
                if (fbwq_has_tail_after_pop)
                    fbwq_link_next[fbwq_prev_wr_ptr] <= fbwq_stage_link_tail;
                fbwq_wr_ptr            <= fbwq_wr_ptr + 1'b1;
            end

            if (fbwq_push_req && fbwq_can_push) begin
                fbwq_stage_valid <= 1'b1;
                fbwq_stage_addr  <= fbwq_push_addr;
                fbwq_stage_data  <= fbwq_push_data;
                fbwq_stage_strb  <= fbwq_push_strb;
                fbwq_stage_link_tail <= fbwq_push_link_tail;
            end else if (fbwq_stage_drain_now) begin
                fbwq_stage_valid <= 1'b0;
                fbwq_stage_link_tail <= 1'b0;
            end

            fbwq_count <= fbwq_count
                        + (fbwq_stage_drain_now ? 5'd1 : 5'd0)
                        - fbwq_pop_count;
        end  // closes the housekeeping `begin` introduced for m_wr_inflight + gpu_swap_req auto-clear
    end
end

// Colormap BRAM initialises to zero in Cyclone V M10K.
// CPU uploads data via MMIO before use.

endmodule
