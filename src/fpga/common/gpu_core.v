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

    // ================================================================
    // External diagnostic inputs — composed in core_top from arbiter +
    // io_sdram + slave debug taps.  Currently only surfaced as MMIO
    // reads for kernel debug; not used for control.
    // ================================================================
    input  wire        slave_swap_pending,    // unused (legacy gate retired)
    input  wire [1:0]  arb_state_dbg,         // SDRAM arb state (0=IDLE,1=RD,2=WR)
    input  wire        cpu_pending_dbg,       // CPU has AR or AW pending
    input  wire [31:0] dbg_bus,               // composed bus diag for MMIO 0x38

    // ================================================================
    // Status outputs
    // ================================================================
    output wire        busy,
    output reg  [31:0] fence_reached,
    // Verilator-only diagnostic outputs.  Quartus prunes unused module
    // ports, so these cost zero ALMs in the bitstream; they exist only so
    // tb_gpu can print state during trace.
    output wire [5:0]  dbg_state,
    output wire [5:0]  dbg_setup_step,
    output wire [31:0] dbg_tri_det,
    output wire [31:0] dbg_frag
);

wire active = reset_n & gpu_enable;

assign dbg_state = state;
assign dbg_setup_step = setup_step;
assign dbg_tri_det = tri_det;
// Packed fragment-pipe diagnostic.  bit0=p0_v, 1=p1_v, 2=p2_v, 3=p2b_v,
// 4=p3_v, 5=src_done, 6=tex_req_valid, 7=tex_req_ready,
// [11:8]=fbss[3:0], [13:12]=dma_state, [14]=cmd_is_draw_spans_batch,
// [15]=fp_pipe_stall, [18:16]=tex_dbg_state, [19]=tex_axi_arvalid,
// [20]=tex_axi_rvalid, [21]=cmap_resp_valid_b, [22]=persp_active,
// [23]=tri_active, [31:24]=sp_count[7:0].
assign dbg_frag = {sp_count[7:0],
                   tri_active,
                   persp_active,
                   cmap_resp_valid_b,
                   m_rd_rvalid,
                   m_rd_arvalid,
                   tex_dbg_state,
                   fp_pipe_stall,
                   cmd_is_draw_spans_batch,
                   dma_state,
                   fbss[3:0],
                   tex_req_ready,
                   tex_req_valid,
                   src_done,
                   p3_valid, p2b_valid, p2_valid, p1_valid, p0_valid};

// ================================================================
// MMIO Register Map
// ================================================================
// 0x00  GPU_CTRL          W   bit0=enable, bit1=soft_reset, bit2=ring_reset
// 0x04  GPU_RING_WRPTR    W   CPU write pointer (byte offset into ring BRAM)
// 0x08  GPU_RING_DATA     W   Write next word to ring BRAM (auto-increment)
// 0x0C  GPU_DMA_SRC       W   SDRAM byte address of command buffer to pull
// 0x10  GPU_RING_RDPTR    R   GPU read pointer
// 0x14  GPU_STATUS        R   bit0=busy, bit1=ring_empty, bit2=dma_busy,
//                              bit3=dma_overflow_sticky, [5:4]=dma_state,
//                              bit6=tex_m0_in_flight, bit7=blend_owns_m0,
//                              [15:8]=fsm_state
// 0x18  GPU_FENCE         R   Last completed fence token
// 0x1C  GPU_DMA_LEN       W   Word count to pull (max 4096)
// 0x20  GPU_TRANSLUC_ADDR W   transluc[] write address (15-bit, auto-inc by 4)
// 0x24  GPU_TRANSLUC_DATA W   transluc[] write data (32-bit word)
// 0x28  GPU_TEX_FLUSH     W   Flush texture cache (write any value)
// 0x2C  GPU_DMA_KICK      W   Write 1 to fire DMA pull from (SRC, LEN)
//
// DMA pull semantics: fabric-side AXI INCR-burst read of LEN words from
// SRC, streamed into the ring BRAM at the auto-incrementing wr_addr,
// with ring_wrptr auto-advanced per word so the decoder picks them up
// in parallel.  CPU is free as soon as KICK is written.  Bursts split
// at the 256-beat AXI4 boundary internally.  Polls GPU_STATUS:dma_busy
// to know when the SDRAM source buffer is safe to overwrite.

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

// CPU upload address for the transluc[] LUT (32 KB).  Colormap storage
// retired — palookups now live in SDRAM and the GPU reads them through
// gpu_tex_cache port B; only transluc[] still lives in on-chip BRAM
// because its random-access blend lookup pattern doesn't fit a single-
// line cache.
reg [14:0] transluc_wr_addr;   // Auto-increment byte address into transluc[]
reg        tex_flush_req;      // Pulse to flush texture cache
reg        soft_reset;         // Pulse: resets FSM state + ring pointers
reg        ring_reset;         // Pulse: reset ring_rdptr (from MMIO, consumed by FSM)

// ---- Doorbell-DMA pull from SDRAM into ring BRAM ----
// Latched on MMIO writes; consumed by the dedicated DMA FSM below.
// dma_words_left counts words remaining in the entire kick; the FSM
// re-issues an AR for each 256-beat sub-burst until the count drains.
reg [31:0] dma_src_latched;       // SDRAM byte addr (from GPU_DMA_SRC)
reg [12:0] dma_len_latched;       // Total words to pull (max 4096; 13-bit fits)
reg        dma_kick;              // Pulse: latch SRC/LEN snapshot into burst regs
localparam DMA_S_IDLE    = 2'd0;
localparam DMA_S_AR      = 2'd1;
localparam DMA_S_R       = 2'd2;
localparam DMA_S_PUBLISH = 2'd3;  // 1-cycle pulse to publish ring_wrptr
reg [1:0]  dma_state;
reg        dma_publish_wrptr;     // 1-cycle pulse, latched by ring_wrptr
reg [31:0] dma_burst_addr;        // SDRAM byte addr of next sub-burst
reg [12:0] dma_words_left;        // Words remaining in the kick (across sub-bursts)
reg [8:0]  dma_burst_words;       // Words remaining in current sub-burst (1..256)
wire       dma_busy = (dma_state != DMA_S_IDLE) || dma_kick;

// Anti-starvation counter for DMA_S_AR: count cycles where DMA wants to
// assert AR but is held off by !dma_bus_idle (sustained tex/blend traffic).
// At STARVE_THRESHOLD (1024 cycles ≈ 10 µs at 100 MHz), force AR
// regardless of the idle gate.  The downstream tex/blend AR mux already
// blocks new tex ARs while dma_owns_ar=1, so any in-flight tex
// transaction drains naturally and DMA's AR lands on the next free slot
// at the slave.  Real DMA path is ~25 µs/batch end-to-end; the bound is
// shorter than that, so it only triggers under genuine starvation —
// the wedge case observed in Duke3D's heavy textured-rendering frames.
localparam DMA_STARVE_THRESHOLD = 11'd1024;
reg [10:0] dma_starve_count;

wire ring_empty = (ring_rdptr == ring_wrptr);
wire [15:0] ring_mask = (RING_WORDS * 4) - 1;  // 0x3FFF for 16 KB

assign busy = !ring_empty || (state != S_IDLE);

// Port B: GPU read (synchronous, 1-cycle latency)
always @(posedge clk)
    ring_rd_data <= ring_bram[ring_rdptr[RING_ADDR_BITS+1:2]];

// DMA word-write into ring BRAM port A: asserted by the DMA FSM
// when an R-beat lands (dma_state==DMA_S_R && m_rd_rvalid).  These
// "raw" wires are driven below where the DMA FSM lives.
wire        dma_ring_wr_raw;
wire [31:0] dma_ring_wdata_raw;

// ============================================================
// Port-A collision skid (doorbell-DMA freeze fix)
// ------------------------------------------------------------
// gpu_core's m_rd_* AXI master has no `rready` — the slave
// (sdram_arb) drives R-beats unconditionally and we MUST swallow
// them on the cycle they arrive.  That collides with CPU MMIO
// writes to GPU_RING_DATA: the previous mux gave DMA priority and
// silently dropped the CPU's word while still advancing
// ring_wr_addr by 1.  The dropped CPU word produced a stale
// ring_bram cell that the decoder later interpreted as a span's
// sp_fb_addr — and the GPU's m_wr_* wrote pixels into CPU code /
// stack regions, manifesting as an mcause=2 trap several frames
// later (PocketDukeNukem-SDK / Duke3D batched-spans freeze).
//
// Fix: 1-deep skid for DMA beats.  CPU MMIO always wins port-A in
// the cycle it fires; the DMA beat parks in `dma_pend_*` and
// commits on the next cycle (assuming no further collision).
// `dma_overflow_sticky` latches if a second collision arrives
// while the skid is still occupied — exposed via GPU_STATUS bit
// 3 so the SDK can detect the (rare) double-collision case.
// ============================================================
wire cpu_ring_write = reg_wr && (reg_addr == 4'd2);

reg        dma_pend_valid;
reg [31:0] dma_pend_data;
reg        dma_overflow_sticky;

// Drain the skid whenever no CPU MMIO is colliding this cycle.
wire dma_pend_drain = dma_pend_valid && !cpu_ring_write;
// Park a fresh DMA beat only when CPU MMIO collides AND skid is empty.
wire dma_pend_load  = dma_ring_wr_raw && cpu_ring_write && !dma_pend_valid;
// Overflow: DMA beat arrives, CPU collides, skid already full.
wire dma_pend_overflow = dma_ring_wr_raw && cpu_ring_write && dma_pend_valid;

always @(posedge clk) begin
    if (!reset_n) begin
        dma_pend_valid      <= 1'b0;
        dma_pend_data       <= 32'b0;
        dma_overflow_sticky <= 1'b0;
    end else begin
        if (dma_pend_load) begin
            dma_pend_valid <= 1'b1;
            dma_pend_data  <= dma_ring_wdata_raw;
        end else if (dma_pend_drain) begin
            dma_pend_valid <= 1'b0;
        end
        if (dma_pend_overflow)
            dma_overflow_sticky <= 1'b1;
    end
end

// Effective DMA→ring write that drives port A.  Either the live
// beat (no CPU collision, skid empty) or the parked beat (no CPU
// collision, skid loaded).  If CPU collides AND beat fires, the
// live beat goes to the skid and DMA does NOT commit this cycle —
// CPU wins port-A, ring_wr_addr advances by 1 for the CPU write.
wire        dma_ring_wr    = (dma_ring_wr_raw && !cpu_ring_write && !dma_pend_valid)
                          || dma_pend_drain;
wire [31:0] dma_ring_wdata = dma_pend_valid ? dma_pend_data : dma_ring_wdata_raw;

// Canonical altsyncram-inferable single-port write to ring_bram.  The
// always block has EXACTLY ONE `ring_bram[X] <= D` statement under a
// single conditional — Quartus's M10K pattern matcher requires this
// shape, otherwise the 4096×32 array gets thrown into LUT-based
// registers and synthesis blows up by ~128 K FFs.  CPU MMIO wins the
// mux now (changed from the prior DMA-priority shape — see skid above).
wire        ring_a_we    = cpu_ring_write || dma_ring_wr;
wire [31:0] ring_a_wdata = cpu_ring_write ? reg_wdata : dma_ring_wdata;
always @(posedge clk) begin
    if (ring_a_we)
        ring_bram[ring_wr_addr] <= ring_a_wdata;
end

// MMIO write handling + ring_wr_addr management (BRAM index pointer).
// The actual ring_bram write lives in the dedicated always block above
// to keep its inference shape clean.
always @(posedge clk) begin
    if (!reset_n) begin
        ring_wrptr      <= 0;
        ring_wr_addr    <= 0;
        transluc_wr_addr <= 0;
        tex_flush_req   <= 0;
        soft_reset      <= 0;
        ring_reset      <= 0;
        dma_src_latched <= 32'd0;
        dma_len_latched <= 13'd0;
        dma_kick        <= 1'b0;
    end else begin
        tex_flush_req <= 0;
        soft_reset    <= 0;
        ring_reset    <= 0;
        dma_kick      <= 1'b0;

        // ring_wr_addr advances once per port-A write (CPU MMIO or DMA
        // beat).  Keeping it here (not in the BRAM-write block) means
        // the BRAM block stays canonical.
        if (ring_a_we)
            ring_wr_addr <= ring_wr_addr + 1'b1;

        // DMA end-of-kick publishes ring_wrptr atomically (covering
        // header + every payload word).  Otherwise the decoder doesn't
        // see in-flight DMA words (it gates only S_IDLE on ring_empty).
        if (dma_publish_wrptr)
            ring_wrptr <= {2'b0, ring_wr_addr, 2'b0};
            // ring_wr_addr (12 bits) << 2 → byte offset (14 bits),
            // zero-extended to 16-bit ring_wrptr.  Always ≤ ring_mask
            // (0x3FFF) since ring_wr_addr ≤ 0xFFF.

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
                4'd1: begin  // GPU_RING_WRPTR — explicit kick
                    if (!dma_ring_wr) ring_wrptr <= reg_wdata[15:0];
                end
                // 4'd2 GPU_RING_DATA: ring_bram write happens in the
                // dedicated BRAM-write always block above; ring_wr_addr
                // increment is handled by the `ring_a_we` branch above.
                4'd3: begin  // GPU_DMA_SRC
                    dma_src_latched <= reg_wdata;
                end
                4'd7: begin  // GPU_DMA_LEN — clamp to ring depth
                    dma_len_latched <= reg_wdata[12:0];
                end
                4'd8: begin  // GPU_TRANSLUC_ADDR
                    transluc_wr_addr <= reg_wdata[14:0];
                end
                4'd9: begin  // GPU_TRANSLUC_DATA
                    transluc_wr_addr <= transluc_wr_addr + 15'd4;
                end
                4'd10: begin // GPU_TEX_FLUSH
                    tex_flush_req <= 1;
                end
                4'd11: begin // GPU_DMA_KICK — fire pull
                    if (reg_wdata[0] && dma_state == DMA_S_IDLE)
                        dma_kick <= 1'b1;
                end
                default: ;
            endcase
        end
    end
end

// MMIO read mux
always @(*) begin
    case (reg_addr)
        4'd1:    reg_rdata = {16'b0, ring_wrptr};
        4'd4:    reg_rdata = {16'b0, ring_rdptr};
        // GPU_STATUS exposes:
        //   bit 0     = busy
        //   bit 1     = ring_empty
        //   bit 2     = dma_busy
        //   bit 3     = dma_overflow_sticky (port-A skid back-to-back
        //               collision — sticky until reset)
        //   bits[5:4] = dma_state (0=IDLE, 1=AR, 2=R, 3=PUBLISH)
        //   bit 6     = tex_m0_in_flight (texture-cache fill on the bus)
        //   bit 7     = blend_owns_m0 (FBSS_BLEND read holding the bus)
        //   bits[15:8]= main FSM state
        // The watchdog dump in of_gpu_draw_spans_batch reads this register
        // when the DMA wedge fires; the dma_state/tex/blend bits tell us
        // exactly which sub-FSM is stuck without per-bit MMIO probes.
        4'd5:    reg_rdata = {16'b0, state,
                              blend_owns_m0, tex_m0_in_flight, dma_state,
                              dma_overflow_sticky,
                              dma_busy, ring_empty, busy};
        4'd6:    reg_rdata = fence_reached;
        // CMD_FLIP debug counters — see reg-decl block above for layout.
        // Reads at 4'd10 / 4'd11 are non-destructive; the same addresses
        // accept writes that pulse tex_flush / dma_kick respectively.
        4'd10:   reg_rdata = cmd_flip_enter_count;
        4'd11:   reg_rdata = cmd_flip_drain_done_count;
        4'd12:   reg_rdata = swap_pulse_count;
        4'd13:   reg_rdata = bvalid_count;
        4'd14:   reg_rdata = awvalid_handshake_count;
        default: reg_rdata = 32'b0;
    endcase
end

// ================================================================
// transluc[] LUT — 32 KB BUILD-style indexed-color blend table
// ================================================================
// Stored as 8192 × 32-bit words (32 M10K under Quartus inference).
// Keep the access pattern word-aligned with no byte-enables, or BRAM
// inference collapses to FFs (the 16 KB colormap that used to live
// here had this exact failure mode at 145k registers).  CPU loads via
// GPU_TRANSLUC_ADDR / GPU_TRANSLUC_DATA on the MMIO.
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

wire transluc_cpu_wr = reg_wr && (reg_addr == 4'd9);
always @(posedge clk) begin
    if (transluc_cpu_wr)
        transluc_bram[transluc_wr_addr[14:2]] <= reg_wdata;
end

// Cmap read path — formerly cmap_bram, now routed through gpu_tex_cache
// port B (added by the TDP conversion in commit 1 of the cmap-via-cache
// plan).  The address pipeline is unchanged in spirit: at p1→p2 shift,
// latch the SDRAM byte address for the upcoming p2 fragment's cmap
// lookup; tex_cache port B accepts the request combinationally on cycle
// p2; the byte response is available combinationally one cycle later
// (resp_data_b[7:0] at p2b's cycle), exactly mirroring the prior
// cmap_bram timing.  Misses (which the dedicated BRAM never had) stall
// the pipeline via fp_pipe_stall's cmap_pipe_wait term until the fill
// completes — see fp_pipe_stall below.
//
// PALOOKUP_BASE + (st_colormap_id << 14) anchors the slot; the per-pixel
// term (light << 8 | texel) indexes within the slot.  Slot encoding
// matches the SDK's of_gpu_palookup_upload() on the host side.
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
// Why not gate on full !fp_pipe_stall: that ALSO catches the
// cmap_pipe_wait + persp_issue_stall stalls, where cmap_req_addr_reg
// is correctly tracking the pixel in p2 — gating there breaks the
// segment-boundary handoff in 32-pixel persp spans (persp_2seg_px15).
// The fbss-only gate is the minimum needed to plug the FB-flush race.
wire        cmap_req_valid_b = p2_valid && p2_flags[SPAN_COLORMAP]
                            && (fbss == FBSS_IDLE);
wire        cmap_req_ready_b;
wire        cmap_resp_valid_b;
wire [15:0] cmap_resp_data_b;
wire [7:0]  cmap_rd_data = cmap_resp_data_b[7:0];

// (cmap_rd_data is now a continuous-assign wire above — no always block
// needed.  The byte mux that previously selected a lane out of the 32-bit
// cmap_bram word is now subsumed into tex_cache port B's resp_data_b,
// which already returns the byte at req_addr_b[1:0] when req_wide_b == 0.)

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
// Used by triangle setup AND perspective span setup.
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
reg signed [31:0] dsp2_a;
reg signed [31:0] dsp2_b;
(* multstyle = "dsp" *) reg signed [63:0] dsp2_p;
always @(posedge clk) dsp2_p <= dsp2_a * dsp2_b;
reg signed [31:0] dsp3_a;
reg signed [31:0] dsp3_b;
(* multstyle = "dsp" *) reg signed [63:0] dsp3_p;
always @(posedge clk) dsp3_p <= dsp3_a * dsp3_b;

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
// in the same cycle — shortening the cone to a single carry chain.
//
// Phase 2: dedicated DSP-input shadow regs.  The earlier shape used the
// 32-bit `tri_A[i]` / `tri_B[i]` register fields directly as multiplier
// inputs.  Quartus couldn't fit the 32×32 mult into one DSP18 — it
// cascaded across two DSP blocks and left input/output FFs in fabric,
// producing the worst path on the system clock (`tri_A[2]` →
// `tri_e_init_Apx[2]`, slack -0.567 ns).  The narrow shadow regs below
// shrink the operands to their actual signed ranges:
//   * tri_A[i] = v_y[a] - v_y[b] with v_y in 12.4 signed, so tri_A
//     fits in 17-bit signed [-65536, 65535] (truncation matches the
//     stored value exactly, no precision loss).
//   * tri_xmin/tri_ymin are clamped to [0, 2047] (12-bit unsigned),
//     so {tri_xmin[11:0], 4'b0} is 16-bit unsigned ≤ 32752; padding
//     to 17-bit signed keeps the leading bit zero.
// Result: 17×17 signed → 34-bit product, fits ONE DSP18 with input
// + output FFs all packed inside the block.  Adds 1 cycle of latency
// (matches the existing tri_ymin_x_stride pipeline), absorbed by the
// already-present S_TRI_MUL_WAIT2 — the consumer (S_TRI_INIT_ATTRIB
// → S_TRI_ROW) doesn't read tri_e_init_Apx/Bpy until after that wait.
reg signed [16:0] tri_A_dsp_in        [0:2];
reg signed [16:0] tri_B_dsp_in        [0:2];
reg signed [16:0] tri_xmin_sub_dsp_in;
reg signed [16:0] tri_ymin_sub_dsp_in;
always @(posedge clk) begin
    tri_A_dsp_in[0]      <= tri_A[0][16:0];
    tri_A_dsp_in[1]      <= tri_A[1][16:0];
    tri_A_dsp_in[2]      <= tri_A[2][16:0];
    tri_B_dsp_in[0]      <= tri_B[0][16:0];
    tri_B_dsp_in[1]      <= tri_B[1][16:0];
    tri_B_dsp_in[2]      <= tri_B[2][16:0];
    // {sign(0), 12-bit tri_xmin, 4'b0} = 17-bit signed, value tri_xmin<<4.
    tri_xmin_sub_dsp_in  <= $signed({1'b0, tri_xmin[11:0], 4'b0});
    tri_ymin_sub_dsp_in  <= $signed({1'b0, tri_ymin[11:0], 4'b0});
end

(* multstyle = "dsp" *) reg signed [31:0] tri_e_init_Apx [0:2];
(* multstyle = "dsp" *) reg signed [31:0] tri_e_init_Bpy [0:2];
always @(posedge clk) begin
    if (!reset_n) begin
        tri_e_init_Apx[0] <= 0; tri_e_init_Apx[1] <= 0; tri_e_init_Apx[2] <= 0;
        tri_e_init_Bpy[0] <= 0; tri_e_init_Bpy[1] <= 0; tri_e_init_Bpy[2] <= 0;
    end else begin
        tri_e_init_Apx[0] <= tri_A_dsp_in[0] * tri_xmin_sub_dsp_in;
        tri_e_init_Apx[1] <= tri_A_dsp_in[1] * tri_xmin_sub_dsp_in;
        tri_e_init_Apx[2] <= tri_A_dsp_in[2] * tri_xmin_sub_dsp_in;
        tri_e_init_Bpy[0] <= tri_B_dsp_in[0] * tri_ymin_sub_dsp_in;
        tri_e_init_Bpy[1] <= tri_B_dsp_in[1] * tri_ymin_sub_dsp_in;
        tri_e_init_Bpy[2] <= tri_B_dsp_in[2] * tri_ymin_sub_dsp_in;
    end
end

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

wire [2:0] tex_dbg_state;
wire       tex_dbg_pipe_valid;

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
    .axi_rlast(tex_axi_rlast),
    .dbg_state(tex_dbg_state),
    .dbg_pipe_valid(tex_dbg_pipe_valid)
);

// ================================================================
// AXI4 Read Master — texture cache + translucent-blend FB readback
//                    + doorbell-DMA command pull
// ================================================================
// Three consumers share M0: the texture cache (multi-beat line fills),
// the translucent-blend FB readback (single-beat, only fires when
// SPAN_TRANSLUC is set on a fragment), and the doorbell-DMA puller
// that streams a CPU-prepared command buffer from SDRAM into the ring
// BRAM.  Arbitration is split between AR and R channels:
//   * AR channel: DMA reserves as soon as it's in S_AR/S_R, so no
//     new tex/blend AR is accepted while a DMA burst is being set up.
//   * R channel: DMA only masks tex/blend R-beats while it's actually
//     in S_R (the data phase).  While DMA waits in S_AR for
//     `dma_bus_idle`, an in-flight tex burst MUST keep receiving its
//     R-beats — masking them would leave the cache deadlocked in
//     S_FILL_DATA after DMA's burst eventually starts.
wire blend_owns_m0  = (fbss == FBSS_BLEND_AR_WAIT)
                   || (fbss == FBSS_BLEND_R_WAIT);
wire dma_owns_ar    = (dma_state == DMA_S_AR)
                   || (dma_state == DMA_S_R);
wire dma_owns_r     = (dma_state == DMA_S_R);
// Legacy alias retained for places that conservatively want "DMA is
// somehow active" (e.g. dma_busy in MMIO status).  Same coverage as
// the old `dma_owns_m0`.
wire dma_owns_m0    = (dma_state != DMA_S_IDLE);

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
// Streams dma_len_latched words from dma_src_latched into ring BRAM
// port A, splitting at 256-beat AXI4 burst boundaries.  Enters DMA_S_AR
// only when the M0 bus is idle (no blend in flight, no texture-cache
// AR/R outstanding) so no fight for the bus mid-burst.  Per accepted
// R-beat, drives dma_ring_wr/dma_ring_wdata which the always-block
// above splices into ring BRAM port A and auto-advances ring_wrptr.
wire dma_bus_idle = !blend_owns_m0 && !tex_m0_in_flight;
// _raw drivers from the DMA FSM — these feed the port-A skid above
// (see "Port-A collision skid" comment block) which mediates between
// live DMA beats, the parked beat, and CPU MMIO writes.
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
        dma_starve_count  <= 11'd0;
    end else begin
        dma_publish_wrptr <= 1'b0;     // one-cycle pulse default

        // Starvation counter: increments while DMA is in S_AR with
        // arvalid not yet asserted AND the idle gate is blocking.
        // Resets the moment we leave S_AR (or assert arvalid).
        if (dma_state == DMA_S_AR && !dma_arvalid && !dma_bus_idle)
            dma_starve_count <= dma_starve_count + 11'd1;
        else
            dma_starve_count <= 11'd0;

        case (dma_state)
        DMA_S_IDLE: begin
            dma_arvalid <= 1'b0;
            if (dma_kick && dma_len_latched != 13'd0) begin
                dma_burst_addr <= dma_src_latched;
                dma_words_left <= dma_len_latched;
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
            // 1-cycle pulse to atomically lift ring_wrptr from its old
            // value (header position) to the post-DMA ring_wr_addr*4
            // (covering header + every payload word in this kick).
            //
            // The earlier wait-for-skid-drain shape (`if (!dma_pend_valid)`)
            // produced a silent wedge in Duke3D's batched-DMA path: the
            // CPU's `while (GPU_STATUS & GPU_STATUS_DMA_BUSY)` spin has
            // no watchdog, so any path where DMA never publishes
            // = forever-spin.  Reverting to unconditional publish here
            // eliminates the wedge.  Risk: if the LAST DMA beat collided
            // with a CPU MMIO and parked in the skid, publish fires
            // before ring_bram's last cell is written — one corrupt
            // span per rare collision.  Acceptable trade for now;
            // proper fix is to delay publish by 2 cycles unconditionally
            // or remove the skid path entirely.
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
localparam CMD_NOP            = 8'h01;
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
localparam CMD_CLEAR          = 8'h10;
localparam CMD_CLEAR_RECT     = 8'h11;  // 3-word payload:
                                          // word0 = start byte addr (CPU
                                          //   pre-computes fb_base + y*stride
                                          //   + x);
                                          // word1 = {w[31:16], h[15:0]};
                                          // word2 = {stride[31:16], pad[15:8],
                                          //   color[7:0]} — stride==0 falls
                                          //   back to st_fb_stride (legacy);
                                          //   color's low 8 bits are
                                          //   replicated 4× per word, matching
                                          //   CMD_CLEAR.
localparam CMD_SET_TEXTURE    = 8'h20;
// 0x21 CMD_SET_DEPTH_FUNC retired with the Z-buffer in lean Phase 2.
localparam CMD_SET_FB         = 8'h23;
// 0x24 CMD_SET_ZB           retired with the Z-buffer in lean Phase 2.
localparam CMD_DRAW_TRIANGLES    = 8'h30;
localparam CMD_DRAW_SPAN         = 8'h40;
// Batched span dispatch: header payload_words = 15 * N (no count word —
// N derives from header).  Decoder loops the existing CMD_DRAW_SPAN
// fragment-pipe path once per 15-word span, returning to S_PAY_DATA
// instead of S_IDLE between spans.  Saves both the per-span MMIO header
// (one word out of 16) and — when paired with the doorbell-DMA path —
// lets the CPU stream commands at cached-store speed instead of one
// blocking AXI write per word.
localparam CMD_DRAW_SPANS_BATCH  = 8'h41;
// Removed commands (reserved opcodes, do not reuse):
//   0x22 CMD_SET_BLEND      — no combine path in the datapath
//   0x25 CMD_SET_SHADE      — Gouraud gradient dropped in the FMax push
//   0x26 CMD_SET_ALPHA_REF  — no alpha test in the datapath
//   0x31 CMD_DRAW_INDEXED   — ~400 ALMs of dynamic pay_buf mux fabric;
//                             expand indices CPU-side and emit per-tri
//   0x42 CMD_DRAW_SPRITE    — 2-triangle sprite is cheaper and rotates
localparam CMD_SET_SKIP_ZERO  = 8'h27;  // 1-word payload: global SKIP_ZERO enable
localparam CMD_SET_COLORMAP_ID = 8'h28; // 1-word payload: [3:0] = palookup slot
                                         // (selects which 16-KB palookup
                                         // page in SDRAM the cmap reads
                                         // index into; see PALOOKUP_BASE
                                         // / PALOOKUP_STRIDE below)

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
reg [31:0] st_fb_addr;
reg [15:0] st_fb_stride;

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
// triangle path sets sp_light = v_r[0] flat, sp_light_step = 0.
reg signed [31:0] sp_light_q;
reg signed [31:0] sp_light_step;
wire [7:0]        sp_light = sp_light_q[23:16];
reg [7:0]  sp_flags;
// Per-span colormap_id.  Decoded from word 6 bits [31:28] at span dispatch
// (count was previously 16 bits at [31:16]; now 12 bits at [27:16] with the
// upper nibble repurposed as colormap_id).  When the wire-format field is
// zero, sp_colormap_id is loaded from the sticky st_colormap_id default —
// preserving bit-identical behaviour for callers that never set the field
// (i.e. count <= 4095 with no upper-nibble use).  Triangle span emit copies
// st_colormap_id into sp_colormap_id at S_TRI_PIX.  This is what eliminates
// the "single colormap per batch" constraint in CMD_DRAW_SPANS_BATCH —
// adjacent spans with different palookups coexist without a flush in between.
reg [3:0]  sp_colormap_id;
reg signed [15:0] sp_fb_stride;
reg [15:0] sp_tex_width;
// POT wrap masks: sp_s_int & sp_tex_w_mask, sp_t_int & sp_tex_h_mask
// before the address math.  Default 16'hFFFF (no-op) so callers that
// don't set word 8 see the legacy multiply-mode behaviour.  The masks
// reproduce BUILD's hlineasm4 shift-mode wrap exactly when tex_w/tex_h
// are powers of two (always true for BUILD/Quake/Doom textures).
reg [15:0] sp_tex_w_mask;
reg [15:0] sp_tex_h_mask;

// Span flags
//
// SPAN_COLORMAP (bit 0) gates the cmap LUT lookup in the p2b → p3 mux:
//   set:   p3_color = palookup[colormap_id][light][texel]
//   clear: p3_color = texel  (raw passthrough — UI text, untextured glyphs)
//
// IMPORTANT: the triangle path (S_TRI_PIX) sets this bit unconditionally.
// Every triangle fragment goes through cmap[colormap_id][v_r[0]][texel].
// The host MUST upload a populated cmap row for every `r` value any
// vertex carries, or that fragment renders 0x00.  The convention used
// by tb_gpu and the SDK is "row 0 = identity (cm[i]=i)" so that
// triangles drawn with v_r=0 render as raw textured (unlit).
localparam SPAN_COLORMAP    = 0;
// bit 1 reserved (was SPAN_COLUMN — never wired)
localparam SPAN_SKIP_ZERO   = 2;
// bit 3 reserved (was SPAN_DEPTH_TEST  — Z-buffer dropped in lean Phase 2)
// bit 4 reserved (was SPAN_DEPTH_WRITE — Z-buffer dropped in lean Phase 2)
localparam SPAN_PERSP       = 5;
localparam SPAN_TRANSLUC    = 6;  // route p3_color through transluc[] LUT
// bit 7 reserved (was SPAN_TRANSLUC_REV — REV variant dropped in lean Phase 2)

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
// 6'd23/24 (S_CLEAR_ZB / S_CLEAR_ZB_WAIT) retired with the Z-buffer.
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

// Rect-clear states — for partial-rect FB clears (letterbox bars,
// status-bar wipes, menu pane backgrounds).  Issues word-by-word AXI
// writes through M_WR with byte-strobed partial-word edges; mirrors
// CMD_CLEAR's per-word shape but driven by a (start_addr, w, h, color)
// payload instead of the hardcoded 320×200 extent.
localparam S_CLEAR_RECT       = 6'd39;  // entry: per-row setup (no-op if h=0)
localparam S_CLEAR_RECT_WORD  = 6'd40;  // emit AXI write for current word
localparam S_CLEAR_RECT_WAIT  = 6'd41;  // wait for AXI bvalid; advance addr/row

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
reg cmd_is_clear_rect;
reg cmd_is_set_texture;
reg cmd_is_set_fb;
reg cmd_is_draw_span;
// cmd_is_draw_spans_batch: 1 while processing a CMD_DRAW_SPANS_BATCH —
// drives the per-span re-entry to S_PAY_DATA after each span renders.
// span_field_idx counts 0..14 inside the current span's 15-word payload
// (resets each span's start); used as the case index instead of pay_idx.
reg cmd_is_draw_spans_batch;
reg [4:0] span_field_idx;
reg cmd_is_set_skip_zero;
reg cmd_is_set_colormap_id;
reg cmd_is_draw_triangles;
reg cmd_is_flip;

// Outstanding-write tracker for CMD_FENCE / CMD_FLIP drain semantics.
// Increments on m_wr_* AW handshake (single-beat AWs only — GPU never
// bursts on this master).  Decrements on m_wr_bvalid (slave pulses it
// for one cycle since axi_sdram_slave's bready is hardwired to 1
// upstream).  CMD_FENCE/CMD_FLIP stall in S_EXECUTE while non-zero,
// so fence_reached / gpu_swap_req only fire after pixel writes
// commit to SDRAM.  4 bits is plenty (typical inflight is 1-3).
reg [3:0] m_wr_inflight;

// CMD_FLIP diagnostic counters — surfaced via MMIO 0x28-0x38.  These
// are saturation-free 32-bit free-running counters; the CPU reads
// pre/post-frame deltas to localise where CMD_FLIP stalls.
//   cmd_flip_enter_count       — S_DECODE saw cmd_type==CMD_FLIP (per command)
//   cmd_flip_drain_done_count  — S_EXECUTE drain completed for cmd_is_flip
//   swap_pulse_count           — gpu_swap_req asserted (should equal drain_done)
//   bvalid_count               — m_wr_bvalid pulses observed
//   awvalid_handshake_count    — m_wr_awvalid && m_wr_awready handshakes
reg [31:0] cmd_flip_enter_count;
reg [31:0] cmd_flip_drain_done_count;
reg [31:0] swap_pulse_count;
reg [31:0] bvalid_count;
reg [31:0] awvalid_handshake_count;

// Latched payload for CMD_FENCE / CMD_FLIP — published only after
// m_wr_inflight drains in S_EXECUTE.  Pre-CR the fence token was
// written to fence_reached directly in S_PAY_DATA, which raced with
// pending pixel writes (see cr-gpu-fence-write-completion.md).
reg [31:0] pending_fence_token;
reg [1:0]  pending_swap_idx;
// Global SKIP_ZERO (color-key at texel 0xFF) state — set via CMD_SET_SKIP_ZERO,
// ORed into every triangle-emitted span's flags so color-keyed sprites
// (emitted as 2 triangles) get the transparency treatment.
reg        st_skip_zero;

// Active palookup slot for colormap reads.  Updated by CMD_SET_COLORMAP_ID;
// fed into the cmap address compute as the high bits of the SDRAM address
// (PALOOKUP_BASE + (st_colormap_id << 14) + per-pixel offset).  4-bit means
// 16 simultaneous palookup pages addressable, which is well above any real
// Duke3D scene.  Default 0 keeps single-palookup callers working without
// any new commands.
reg [3:0]  st_colormap_id;

// SDRAM address layout for palookups.  PALOOKUP_BASE is the byte offset of
// slot 0; PALOOKUP_STRIDE is the spacing between slots.  Each slot is the
// same 16 KB shape as the original on-chip cmap_bram (32 shade rows × 256
// entries × 2 bytes = 16 KB Quake-shape; Duke3D uses 32 × 256 × 1 byte =
// 8 KB but pads to 16 KB so the slot index multiplier is a clean shift).
// Both are CPU-known constants so palookup uploads (CPU → SDRAM) and GPU
// cmap reads agree on layout without any per-slot register state.
// Was 26'h0100000 (1 MB into SDRAM) but that overlapped FB1 at
// OF_TARGET_FB1_BASE = 0x10100000 — every GPU FB write trampled the
// palookup table, producing a uniform-colour fixed point on screen
// regardless of (light, texel).  Moved to the dedicated 3 MB gap
// between heap end (0x13400000) and sample pool start (0x13700000).
// Keep in sync with OF_GPU_PALOOKUP_AXI_OFFSET in firmware/api/of_gpu.h.
localparam [25:0] PALOOKUP_BASE   = 26'h3400000;  // 52 MB into SDRAM
localparam [25:0] PALOOKUP_STRIDE = 26'h0004000;  // 16 KB per slot

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
// p0: pre-issue stage. Holds the pixel whose multiply (tx_mul_q) is being
// computed by the registered DSP this cycle. p0 → p1 transition is the
// "issue commit" event, gated on the cache asserting req_ready in the same
// cycle the consumer drives req_valid.
reg        p0_valid;
reg [7:0]  p0_light;
reg [7:0]  p0_flags;
reg [31:0] p0_fb_addr;
reg signed [15:0] p0_s_int;     // for the post-mul add
reg [31:0] p0_tex_base;         // sp_tex_addr at issue time

// DSP-pipelined texture multiply. Registered output gives the path a clean
// register-to-register boundary that the fitter can pack into a DSP slice.
// Loaded conditionally (only on issue commit OR when p0 is being primed).
(* multstyle = "dsp" *) reg signed [31:0] tx_mul_q;

reg        p1_valid;
reg [7:0]  p1_light;
reg [7:0]  p1_flags;
reg [31:0] p1_fb_addr;

reg        p2_valid;
reg [7:0]  p2_color;          // tex result
reg [7:0]  p2_light;
reg [7:0]  p2_flags;
reg [31:0] p2_fb_addr;
reg        p2_discard;        // skip-zero outcome

// p2b: 1-cycle delay between p2 (cmap addr issued) and p3 (cmap data captured).
// Cmap BRAM has 2-cycle effective latency from NB-set of cmap_rd_addr to
// cmap_rd_data being valid for that index, so we need a no-op shift stage.
reg        p2b_valid;
reg [7:0]  p2b_color;
reg [7:0]  p2b_flags;
reg [31:0] p2b_fb_addr;
reg        p2b_discard;

reg        p3_valid;
reg [7:0]  p3_color;          // final color (post-cmap if applicable)
reg [7:0]  p3_flags;
reg [31:0] p3_fb_addr;
reg        p3_discard;

// FB write sub-FSM (lives within S3, pauses pipeline when not IDLE)
localparam FBSS_IDLE        = 4'd0;
localparam FBSS_FLUSH_AW    = 4'd1;  // emit AW, then resume into accumulate
localparam FBSS_FLUSH_W_RSP = 4'd2;  // wait for W handshake + B response
// FBSS_ZREAD/ZWAIT/ZWRWAIT (3/4/5) retired with the Z-buffer in lean Phase 2.
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

// cmap_pipe_wait: the p2b→p3 shift consumes cmap_rd_data (= resp_data_b
// byte) for the fragment in p2b.  If that fragment had SPAN_COLORMAP set
// and tex_cache port B isn't ready (miss in flight), stall the shift.
// On hit this is always 0 because resp_valid_b is high combinationally
// the same cycle pipe_addr_b matches.  See gpu_tex_cache.v for the held-
// response semantics this relies on.
wire cmap_pipe_wait = p2b_valid && p2b_flags[SPAN_COLORMAP] && !cmap_resp_valid_b;
// Stage 2a: m_wr_inflight is 4 bits (max 15).  FBSS now exits on AW+W
// handshake without waiting for B (multi-outstanding writes).  Cap at
// 14 outstanding to prevent counter overflow if SDRAM is slow to drain.
wire m_wr_inflight_near_full = (m_wr_inflight >= 4'd14);
wire fp_pipe_stall = (p1_valid && !tex_resp_valid)
                  || cmap_pipe_wait
                  || (fbss != FBSS_IDLE)
                  || m_wr_inflight_near_full;

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
// 4× per AXI write (matching CMD_CLEAR's shape).
reg [31:0] cr_addr;
reg [31:0] cr_row_addr;
reg [15:0] cr_w_remaining;
reg [15:0] cr_w_total;
reg [15:0] cr_y_remaining;
reg [15:0] cr_stride;        // per-command row advance; 0 = use st_fb_stride
reg [7:0]  cr_color;

// (AXI4 write handshakes managed per-state, no global tracking)

// ================================================================
// Triangle Registers
// ================================================================

// tri_active: 1 = fragment pipeline returns to triangle path. Read by span
// states unconditionally; synthesizer folds
// away `tri_active ? S_TRI_PIX : S_SPAN_STEP` to just `S_SPAN_STEP`.
reg        tri_active;

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
// Phase 4c.3 — perspective-divide reciprocal gradient.  Only meaningful
// when tri_persp_active; unused on affine triangles (gradient loop
// skips by jumping idx 5 → 8).
reg signed [31:0] grad_w_dx, grad_w_dy;
// Phase 4d Gouraud — light-row gradient (the colormap row index that
// sp_light_q[23:16] feeds the cmap LUT lookup with).  Computed
// unconditionally for all triangles so flat-shaded triangles emerge
// from the same path with a zero gradient (when v_r[1] == v_r[2] ==
// v_r[0]).  Without this the triangle path silently flat-shaded from
// v_r[0] only — engine D_ALIAS_GOURAUD=1 was a no-op, manifesting in
// Quake as model-shade flickering as triangle vertex order rotated.
// See test_triangle_gouraud_light_flat_from_v0 for the empirical
// pre-fix behaviour.
reg signed [31:0] grad_r_dx, grad_r_dy;
// Bounding box (integer pixel coords)
reg signed [15:0] tri_xmin, tri_xmax, tri_ymin, tri_ymax;
// Raw (pre-clamp) bbox in 12.4 subpixel space — registered in S_TRI_BBOX,
// consumed by S_TRI_BBOX_CLAMP. Splitting the bbox compute in two halves
// keeps each combinational chain shallow enough to hit the 10 ns target.
reg signed [15:0] tri_xmin_raw, tri_xmax_raw, tri_ymin_raw, tri_ymax_raw;

// Rasteriser current state
reg signed [15:0] tri_cur_x, tri_cur_y;
// Pre-registered "tri_cur_x > tri_xmax" so S_TRI_PIX's branch reads a
// single FF bit instead of a wide combinational cone (was mp_ram crit
// path -1.27 ns: tri_xmax → 16-bit compare → branch enable selectors
// → sp_tex_width mux → register, ~11 ns combinational).  Updated in
// the same cycle tri_cur_x advances; row_done_r reflects the *next*
// cycle's tri_cur_x value.
reg               row_done_r;
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
// Phase 4d — Gouraud light walker.  Q16.16; bits[23:16] of tri_r are
// the 8-bit cmap row index the fragment pipe sees.  Initialised at
// (xmin*16, ymin*16) by S_TRI_INIT_ATTRIB's round-4, advances per row
// in S_TRI_ROW_NEXT, per pixel in S_TRI_PIX.  Snapshotted into
// tri_span_r_start at the first inside pixel of each row's span.
// Replaces the prior flat `{8'b0, v_r[0], 16'b0}` capture.
reg signed [31:0] tri_r;
reg signed [31:0] tri_row_r;
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
// Flat per-triangle light loaded from v_r[0] at span emit.
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
reg signed [31:0] tri_det;
reg signed [15:0] tri_recip;  // scaled 1/|det| (fixed-point)
reg [5:0]  tri_clz;           // leading zeros of |det|
reg        tri_det_sign;       // 1 if det was negative

// Precomputed differences (used across setup steps)
reg signed [15:0] dX10, dY10, dX20, dY20;

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

// Pipeline register on the dV10/dV20 mux outputs.  The combinational
// chain `v_w[1..2] → 32-bit subtract → 5-way grad_idx mux → 2:1
// tri_persp_active mux → dsp_a/dsp2_a register input` was 11.2 ns at
// 100 MHz (-1.17 ns slack).  Registering it here breaks the path into
// flop→mux→flop (free) and flop→DSP_in (clean), closing timing.
// Updated every cycle; consumed by the S_TRI_GRAD loop at the
// dsp_a/dsp2_a load (one cycle after grad_idx settles, hence the
// added grad_sub=3'd7 settle cycle in the FSM below).
reg signed [31:0] grad_dV10_r, grad_dV20_r;
always @(posedge clk) begin
    grad_dV10_r <= grad_dV10;
    grad_dV20_r <= grad_dV20;
end

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
        fb_acc_valid <= 0;
        fb_acc_mask <= 0;
        fence_reached <= 0;
        cmd_type <= 0;
        cmd_payload_words <= 0;
        cmd_is_nop <= 0; cmd_is_fence <= 0; cmd_is_clear <= 0;
        cmd_is_clear_rect <= 0;
        cr_addr <= 0; cr_row_addr <= 0;
        cr_w_remaining <= 0; cr_w_total <= 0;
        cr_y_remaining <= 0; cr_stride <= 0; cr_color <= 0;
        cmd_is_set_texture <= 0;
        cmd_is_set_fb <= 0;
        cmd_is_draw_span <= 0;
        cmd_is_draw_spans_batch <= 0;
        span_field_idx <= 0;
        cmd_is_set_skip_zero <= 0;
        cmd_is_set_colormap_id <= 0;
        st_colormap_id <= 4'b0;
        st_skip_zero <= 0;
        cmd_is_draw_triangles <= 0;
        cmd_is_flip <= 0;
        m_wr_inflight       <= 4'b0;
        pending_fence_token <= 32'b0;
        pending_swap_idx    <= 2'b0;
        gpu_swap_req        <= 1'b0;
        gpu_swap_idx        <= 2'b0;
        cmd_flip_enter_count       <= 32'b0;
        cmd_flip_drain_done_count  <= 32'b0;
        swap_pulse_count           <= 32'b0;
        bvalid_count               <= 32'b0;
        awvalid_handshake_count    <= 32'b0;
        pay_idx <= 0;
        pay_remaining <= 0;
        frag_discard <= 0;
        clear_flags <= 0;
        // Pipelined fragment processor reset
        p0_valid <= 0; p0_light <= 0; p0_flags <= 0;
        p0_fb_addr <= 0;
        p0_s_int <= 0; p0_tex_base <= 0;
        tx_mul_q <= 0;
        p1_valid <= 0; p1_light <= 0; p1_flags <= 0;
        p1_fb_addr <= 0;
        p2_valid <= 0; p2_color <= 0; p2_light <= 0; p2_flags <= 0;
        p2_fb_addr <= 0; p2_discard <= 0;
        p2b_valid <= 0; p2b_color <= 0; p2b_flags <= 0;
        p2b_fb_addr <= 0; p2b_discard <= 0;
        p3_valid <= 0; p3_color <= 0; p3_flags <= 0;
        p3_fb_addr <= 0; p3_discard <= 0;
        transluc_rd_addr <= 15'b0;
        cmap_req_addr_reg <= 26'b0;
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
        dsp_a <= 0; dsp_b <= 0;
        dsp2_a <= 0; dsp2_b <= 0;
        recip_rd_addr <= 0;
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
        tri_r <= 0;
        tri_row_r <= 0;
        grad_w_dx <= 0; grad_w_dy <= 0;
        grad_r_dx <= 0; grad_r_dy <= 0;
        tri_xmin_raw <= 0; tri_xmax_raw <= 0;
        tri_ymin_raw <= 0; tri_ymax_raw <= 0;
        // State registers
        st_tex_addr <= 0; st_tex_width <= 0;
        sp_tex_w_mask <= 16'hFFFF; sp_tex_h_mask <= 16'hFFFF;
        st_fb_addr <= 0; st_fb_stride <= 320;
    end else begin
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
            m_wr_inflight <= 4'b0;
            gpu_swap_req <= 1'b0;
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
            // CMD_FLIP debug: count every AW handshake and B beat
            // independently so we can detect lost B beats (counts
            // diverge) or lost AW handshakes (drain never reaches 0).
            if (m_wr_awvalid && m_wr_awready)
                awvalid_handshake_count <= awvalid_handshake_count + 32'd1;
            if (m_wr_bvalid)
                bvalid_count <= bvalid_count + 32'd1;
            gpu_swap_req <= 1'b0;
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
            cmd_is_clear_rect     <= (cmd_type == CMD_CLEAR_RECT);
            cmd_is_set_texture    <= (cmd_type == CMD_SET_TEXTURE);
            cmd_is_set_fb         <= (cmd_type == CMD_SET_FB);
            cmd_is_draw_span        <= (cmd_type == CMD_DRAW_SPAN);
            cmd_is_draw_spans_batch <= (cmd_type == CMD_DRAW_SPANS_BATCH);
            span_field_idx          <= 5'd0;
            cmd_is_set_skip_zero  <= (cmd_type == CMD_SET_SKIP_ZERO);
            cmd_is_set_colormap_id <= (cmd_type == CMD_SET_COLORMAP_ID);
            cmd_is_draw_triangles <= (cmd_type == CMD_DRAW_TRIANGLES);
            cmd_is_flip           <= (cmd_type == CMD_FLIP);
            if (cmd_type == CMD_FLIP)
                cmd_flip_enter_count <= cmd_flip_enter_count + 32'd1;

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
                // Publish the token only AFTER outstanding m_wr_* writes
                // drain in S_EXECUTE — fixes the flashing-pixel race
                // documented in cr-gpu-fence-write-completion.md.
                if (pay_idx == 5'd0) pending_fence_token <= ring_rd_data;
            end
            else if (cmd_is_flip) begin
                // CMD_FLIP payload: word 0 = idx, word 1 = fence token.
                if (pay_idx == 5'd0) pending_swap_idx    <= ring_rd_data[1:0];
                if (pay_idx == 5'd1) pending_fence_token <= ring_rd_data;
            end
            else if (cmd_is_clear) begin
                if (pay_idx == 5'd0) begin
                    clear_flags <= ring_rd_data[17:16];
                    clear_color <= ring_rd_data[15:0];
                end else if (pay_idx == 5'd1) begin
                    clear_depth <= ring_rd_data[15:0];
                end
            end
            else if (cmd_is_clear_rect) begin
                if (pay_idx == 5'd0) begin
                    cr_addr     <= ring_rd_data;
                    cr_row_addr <= ring_rd_data;
                end else if (pay_idx == 5'd1) begin
                    cr_w_total     <= ring_rd_data[31:16];
                    cr_w_remaining <= ring_rd_data[31:16];
                    cr_y_remaining <= ring_rd_data[15:0];
                end else if (pay_idx == 5'd2) begin
                    // Word 2: {stride[31:16], pad[15:8], color[7:0]}.
                    // stride==0 ⇒ caller didn't set it; row advance falls
                    // back to st_fb_stride (the SET_FB global) for backwards
                    // compatibility with existing callers.
                    cr_stride <= ring_rd_data[31:16];
                    cr_color  <= ring_rd_data[7:0];
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
            else if (cmd_is_set_fb) begin
                if (pay_idx == 5'd0) st_fb_addr   <= ring_rd_data;
                else if (pay_idx == 5'd1) st_fb_stride <= ring_rd_data[15:0];
            end
            else if (cmd_is_set_skip_zero) begin
                if (pay_idx == 5'd0) st_skip_zero <= ring_rd_data[0];
            end
            else if (cmd_is_set_colormap_id) begin
                if (pay_idx == 5'd0) st_colormap_id <= ring_rd_data[3:0];
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
                        // Word 6 packing: [31:28]=colormap_id, [27:16]=count
                        // (12 bits, was 16), [15:8]=light, [7:0]=flags.
                        // colormap_id == 0 falls back to the sticky default
                        // (st_colormap_id) so existing single-colormap callers
                        // that never set the field stay bit-identical.
                        sp_count       <= {4'b0, ring_rd_data[27:16]};
                        sp_colormap_id <= (ring_rd_data[31:28] != 4'b0)
                                            ? ring_rd_data[31:28]
                                            : st_colormap_id;
                        sp_light_q     <= {8'b0, ring_rd_data[15:8], 16'b0};
                        sp_light_step  <= 32'b0;
                        sp_flags       <= ring_rd_data[7:0];
                    end
                    5'd7: begin
                        sp_fb_stride <= ring_rd_data[31:16];
                        sp_tex_width <= ring_rd_data[15:0];
                    end
                    5'd8: begin
                        sp_tex_w_mask <= (ring_rd_data[15:0]  == 16'd0)
                                         ? 16'hFFFF : ring_rd_data[15:0];
                        sp_tex_h_mask <= (ring_rd_data[31:16] == 16'd0)
                                         ? 16'hFFFF : ring_rd_data[31:16];
                    end
                    5'd9:  sp_sZ         <= ring_rd_data;
                    5'd10: sp_tZ         <= ring_rd_data;
                    5'd11: sp_zinv       <= ring_rd_data;
                    5'd12: sp_sZstep     <= ring_rd_data;
                    5'd13: sp_tZstep     <= ring_rd_data;
                    5'd14: sp_zinv_step  <= ring_rd_data;
                    default: ;
                endcase
            end
            else if (cmd_is_draw_spans_batch) begin
                // Separate routing block keyed on span_field_idx (0..14
                // wrapping per-span) so back-to-back spans inside a
                // batch payload land their words in the correct sp_* slots.
                case (span_field_idx)
                    5'd0: sp_fb_addr   <= ring_rd_data;
                    5'd1: sp_tex_addr  <= ring_rd_data;
                    5'd2: sp_s         <= ring_rd_data;
                    5'd3: sp_t         <= ring_rd_data;
                    5'd4: sp_sstep     <= ring_rd_data;
                    5'd5: sp_tstep     <= ring_rd_data;
                    5'd6: begin
                        // Word 6 packing matches the single-span path; see
                        // the cmd_is_draw_span branch for the full comment.
                        sp_count       <= {4'b0, ring_rd_data[27:16]};
                        sp_colormap_id <= (ring_rd_data[31:28] != 4'b0)
                                            ? ring_rd_data[31:28]
                                            : st_colormap_id;
                        sp_light_q     <= {8'b0, ring_rd_data[15:8], 16'b0};
                        sp_light_step  <= 32'b0;
                        sp_flags       <= ring_rd_data[7:0];
                    end
                    5'd7: begin
                        sp_fb_stride <= ring_rd_data[31:16];
                        sp_tex_width <= ring_rd_data[15:0];
                    end
                    5'd8: begin
                        sp_tex_w_mask <= (ring_rd_data[15:0]  == 16'd0)
                                         ? 16'hFFFF : ring_rd_data[15:0];
                        sp_tex_h_mask <= (ring_rd_data[31:16] == 16'd0)
                                         ? 16'hFFFF : ring_rd_data[31:16];
                    end
                    5'd9:  sp_sZ         <= ring_rd_data;
                    5'd10: sp_tZ         <= ring_rd_data;
                    5'd11: sp_zinv       <= ring_rd_data;
                    5'd12: sp_sZstep     <= ring_rd_data;
                    5'd13: sp_tZstep     <= ring_rd_data;
                    5'd14: sp_zinv_step  <= ring_rd_data;
                    default: ;
                endcase

                // Advance the per-span sub-counter, wrapping at 14 → 0
                // so the next span's words land in the right slots.
                if (span_field_idx == 5'd14)
                    span_field_idx <= 5'd0;
                else
                    span_field_idx <= span_field_idx + 5'd1;
            end
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

            if (pay_remaining <= 24'd1) begin
                state <= S_EXECUTE;
            end
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
            // Multi-span batch: each span is 15 words (span_field_idx
            // 0..14).  When span_field_idx hits 14 mid-batch, the
            // current span's sp_* regs are fully loaded; kick it
            // through S_EXECUTE → S_FRAG_PIPE.  The post-flush exit
            // routes back to S_PAY_DATA (see S_FB_FLUSH below) and
            // advances ring_rdptr there — NOT here.  Mirrors the
            // triangle batch: advancing rdptr at this dispatch exit
            // would cause the always-block to settle ring_rd_data at
            // the next-next word during the long S_FRAG_PIPE window,
            // skewing the re-entry by one slot.
            else if (cmd_is_draw_spans_batch && span_field_idx == 5'd14) begin
                state      <= S_EXECUTE;
            end
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
            if (cmd_is_fence) begin
                // Stall until all outstanding m_wr_* writes commit; then
                // publish the fence token and retire to S_IDLE.
                if (m_wr_inflight == 4'b0) begin
                    fence_reached <= pending_fence_token;
                    state         <= S_IDLE;
                end
                // else: stay in S_EXECUTE — m_wr_inflight ticks down via
                // the global counter update below.
            end
            else if (cmd_is_flip) begin
                // Same drain wait as CMD_FENCE, plus a one-cycle pulse
                // on gpu_swap_req that the slave latches into
                // fb_swap_pending=1 / fb_ready_idx.  The kernel's
                // of_video_acquire_next does the explicit
                // FB_SWAP_CTRL & 1 wait (per cr-acquire-next-vsync-wait),
                // so we publish fence_reached as soon as the m_wr drain
                // completes — leaves the GPU command processor free to
                // start the next frame's commands during the CPU's
                // vsync spin.
                if (m_wr_inflight == 4'b0) begin
                    gpu_swap_req  <= 1'b1;
                    gpu_swap_idx  <= pending_swap_idx;
                    fence_reached <= pending_fence_token;
                    state         <= S_IDLE;
                    cmd_flip_drain_done_count <= cmd_flip_drain_done_count + 32'd1;
                    swap_pulse_count          <= swap_pulse_count          + 32'd1;
                end
            end
            else if (cmd_is_nop
                || cmd_is_set_texture
                || cmd_is_set_fb
                || cmd_is_set_skip_zero
                || cmd_is_set_colormap_id) begin
                state <= S_IDLE;
            end
            else if (cmd_is_clear) begin
                state <= S_CLEAR_INIT;
            end
            else if (cmd_is_clear_rect) begin
                state <= S_CLEAR_RECT;
            end
            else if (cmd_is_draw_span || cmd_is_draw_spans_batch) begin
                // Either standalone span or one span inside a batch:
                // the sp_* regs are fully loaded for the current span.
                // sp_flags holds the flag byte written at field idx 6;
                // its SPAN_PERSP bit arms the perspective sub-FSM.
                persp_active      <= sp_flags[SPAN_PERSP];
                persp_first_done  <= 0;
                persp_seg_a_ready <= 0;
                persp_seg_b_ready <= 0;
                persp_pss         <= PSS_IDLE;
                persp_pass        <= PSS_PASS_ANCHOR;
                sp_seg_left       <= 0;
                src_mode     <= SRC_SPAN;
                src_done     <= 0;
                state        <= S_FRAG_PIPE;
            end
            else if (cmd_is_draw_triangles) begin
                // Vertices already loaded into v_*[] in S_PAY_DATA;
                // S_TRI_LOAD used to do the load in a separate cycle
                // but is now a pass-through state kept only for
                // schedule compatibility (setup_step reset).
                state <= S_TRI_LOAD;
            end
            else state <= S_IDLE;
        end

        // ============================================================
        // SPAN pixel loop

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
                           && !persp_issue_stall
                           && (!persp_active
                               || sp_seg_left != 4'd0
                               || persp_seg_b_ready)
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
                p3_discard   <= p2b_discard;

                // p2b <- p2  (no-op shift, gives cmap BRAM time to read)
                p2b_valid   <= p2_valid;
                p2b_color   <= p2_color;
                p2b_flags   <= p2_flags;
                p2b_fb_addr <= p2_fb_addr;
                p2b_discard <= p2_discard;

                // p2 <- p1  (captures tex_resp; issues cmap_rd_addr if needed)
                p2_valid   <= p1_valid;
                if (p1_valid) begin
                    p2_color   <= tex_resp_data[7:0];
                    p2_light   <= p1_light;
                    p2_flags   <= p1_flags;
                    p2_fb_addr <= p1_fb_addr;
                    p2_discard <= p1_flags[SPAN_SKIP_ZERO]
                               && (tex_resp_data[7:0] == 8'hFF);
                    if (p1_flags[SPAN_COLORMAP]) begin
                        // SDRAM byte address for the cmap lookup.  Slot
                        // base + per-pixel (shade × 256 + texel).  The
                        // (sp_colormap_id << 14) factor is exactly
                        // PALOOKUP_STRIDE when STRIDE = 16 KB; expressed
                        // as a shift here to avoid a multiplier.
                        // sp_colormap_id is per-span (loaded at span decode,
                        // or copied from st_colormap_id by the triangle path)
                        // so adjacent batched spans with different palookups
                        // coexist without a CMD_SET_COLORMAP_ID flush.
                        cmap_req_addr_reg <= PALOOKUP_BASE
                                           + {sp_colormap_id, 14'b0}
                                           + {12'b0, p1_light[5:0], tex_resp_data[7:0]};
                    end
                end

                // p1 <- p0 (issue commit). Cache accepted our request this
                // cycle. The OLD p0 metadata becomes p1 (the in-cache pixel).
                if (issue_committed) begin
                    p1_valid   <= 1;
                    p1_light   <= p0_light;
                    p1_flags   <= p0_flags;
                    p1_fb_addr <= p0_fb_addr;
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
                    sp_count   <= sp_count - 16'd1;
                    // Phase 4d — per-pixel light step.  Direct
                    // CMD_DRAW_SPAN payloads set sp_light_step = 0
                    // (flat lighting unchanged); triangle Gouraud
                    // spans walk through the gradient per pixel.
                    sp_light_q <= sp_light_q + sp_light_step;
                    if (span_last_issue) src_done <= 1;
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
                    // Translucent detour: if SPAN_TRANSLUC is set, capture p3
                    // state and run the read-modify-write blend flow.  The
                    // blend result is written to fb_acc by FBSS_BLEND_APPLY,
                    // so we deliberately do NOT touch fb_acc here.  fb_acc
                    // state is frozen for the duration of the blend (fbss !=
                    // IDLE keeps fp_pipe_stall asserted, so no new fragment
                    // can advance to p3 and clobber it).
                    if (p3_valid && !p3_discard && p3_flags[SPAN_TRANSLUC]) begin
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

                    // Stage 2a: exit on AW+W handshake, NOT on B response.
                    // The original code waited for m_wr_bvalid here, paying
                    // the full SDRAM round-trip per pixel-word flush.
                    // m_wr_inflight tracks outstanding Bs; CMD_FENCE/CMD_FLIP
                    // drain them at the end of the frame.  Same-master
                    // single-AWID AXI ordering preserves write semantics.
                    if (!m_wr_awvalid && !m_wr_wvalid) begin
                        if (fbss_pend_valid) begin : pend_apply
                            reg [31:0] pw_addr;
                            reg [1:0]  pw_lane;
                            pw_addr = fbss_pend_addr & 32'hFFFFFFFC;
                            pw_lane = fbss_pend_addr[1:0];

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
                // Translucent-blend sub-flow (5 states).  Entry from
                // FBSS_IDLE captured the pixel into blend_*; we now read
                // the existing FB byte (from SDRAM, with fb_acc bypass for
                // the same-word case), look up the blended byte in
                // transluc[], then accumulate it into fb_acc using the
                // same path as the IDLE fast path.
                // --------------------------------------------------------
                FBSS_BLEND_REQ: begin
                    // Three gates before issuing the BLEND read on M0:
                    //   1. !tex_axi_arvalid && !tex_m0_in_flight — texture
                    //      cache must be fully drained from M0 (no pending
                    //      AR, no in-flight read) so blend_owns_m0 doesn't
                    //      collide with an in-flight tex fill on the R
                    //      channel.
                    //   2. m_wr_inflight == 0 — RAW barrier.  Cross-word
                    //      fb_acc flushes hand off to m_wr, but the W beats
                    //      may still be in transit to SDRAM when we'd
                    //      otherwise issue this BLEND read.  If the
                    //      arbiter grants m_rd before the slave's pending
                    //      write commits, the read returns pre-flush data
                    //      and the blend uses a stale FB byte (visible as
                    //      a one-frame trail behind translucent surfaces
                    //      that overdraw their own previous lanes).  The
                    //      same-word fb_acc bypass below catches writes
                    //      that haven't yet flushed; the m_wr_inflight gate
                    //      catches the ones that have.
                    if (!tex_axi_arvalid && !tex_m0_in_flight
                        && m_wr_inflight == 4'b0) begin
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
                        // Key layout (15 bits): { src[7:1], fb_byte }.
                        // Source LSB drop is the 128×256 quantisation
                        // chosen in transluc.md.
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

            // ----------------------------------------------------------
            // Drain detection — when source done and pipe empty, flush.
            // If we're inside a triangle rasterisation, hand back to the
            // row walker instead of flushing fb_acc (more rows may follow
            // and fb_acc will keep coalescing writes).
            // ----------------------------------------------------------
            if (src_done && !p0_valid && !p1_valid && !p2_valid && !p2b_valid
                         && !p3_valid && fbss == FBSS_IDLE) begin
                src_done <= 0;
                persp_active      <= 0;  // disarm so PSS doesn't keep running
                persp_seg_a_ready <= 0;
                persp_seg_b_ready <= 0;
                persp_first_done  <= 0;
                if (tri_active)
                    state <= S_TRI_ROW_NEXT;
                else
                    state    <= S_FB_FLUSH;
            end
        end

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
                if (tri_active) tri_active <= 0;
                // Mid-batch span dispatch: more spans remain in the
                // payload, so re-enter S_PAY_DATA to read the next
                // span's 15 words.  Advance ring_rdptr by 4 here so
                // that on re-entry's first cycle, the always-block
                // delivers the next span's word 0 in ring_rd_data
                // (with the standard 1-cycle lag).  Matches the
                // triangle re-entry pattern exactly.
                // span_field_idx already wrapped to 0 in the previous
                // S_PAY_DATA cycle; cmd_is_draw_spans_batch / sp_*
                // regs persist across this transition.
                if (cmd_is_draw_spans_batch && pay_remaining > 24'd0) begin
                    ring_rdptr <= (ring_rdptr + 16'd4) & ring_mask;
                    state      <= S_PAY_DATA;
                end else
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
            // Stage 2a: exit on AW+W handshake (don't wait for B).
            // m_wr_inflight tracks outstanding Bs; CMD_FENCE/CMD_FLIP
            // drain.  This unblocks the producer FSM (S_TRI_PIX /
            // S_SPAN_STEP) to continue emitting fragments while the
            // previous write's B is round-tripping through SDRAM.
            if (!m_wr_awvalid && !m_wr_wvalid) begin
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
                    // Mid-batch span dispatch: re-enter S_PAY_DATA
                    // for the next span (and advance ring_rdptr by 4
                    // to prime ring_rd_data for the next span's word
                    // 0 — same trick as the post-S_DECODE prime, so
                    // re-entry cycle 1 reads the right word).  For
                    // standalone CMD_DRAW_SPAN we go straight to
                    // S_IDLE — bit-identical to the pre-batch flow,
                    // which is what BUILD's per-column path needs.
                    if (cmd_is_draw_spans_batch && pay_remaining > 24'd0) begin
                        ring_rdptr <= (ring_rdptr + 16'd4) & ring_mask;
                        state      <= S_PAY_DATA;
                    end else
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
            end else begin
                state <= S_IDLE;
            end
        end

        S_CLEAR_FB: begin
            if (clear_remaining == 0) begin
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

        // ============================================================
        // CMD_CLEAR_RECT — partial-rect FB clear (letterbox bars,
        // status-bar wipes, menu pane backgrounds, splash underlay).
        // Walks h rows × w bytes through M_WR with byte-strobed
        // partial-word edges.  Word-aligned full-width row strips hit
        // the 4-byte fast path (cr_strobe = 4'b1111, 4 bytes per AXI
        // burst); arbitrary x/w paths use the general byte-strobe.
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

            m_wr_awvalid <= 1;
            m_wr_awaddr  <= cr_addr & 32'hFFFFFFFC;
            m_wr_awlen   <= 0;
            m_wr_wvalid  <= 1;
            m_wr_wdata   <= {cr_color, cr_color, cr_color, cr_color};
            m_wr_wstrb   <= cr_strobe;
            m_wr_wlast   <= 1;
            state        <= S_CLEAR_RECT_WAIT;
        end

        S_CLEAR_RECT_WAIT: begin : clear_rect_wait_blk
            // Mirror of S_CLEAR_FB_WAIT: drain AW + W; on B response,
            // advance addr by the bytes we just wrote (computed the same
            // way as S_CLEAR_RECT_WORD's cr_bytes_this), wrap to the
            // next row at end-of-row, and finish the rect when the row
            // counter expires.
            reg  [1:0] cr_lane;
            reg  [2:0] cr_bytes_this;
            cr_lane = cr_addr[1:0];
            cr_bytes_this = (cr_w_remaining >= 16'd4
                             && cr_lane == 2'd0)
                              ? 3'd4
                              : ((cr_w_remaining >= {13'b0, 3'd4 - {1'b0, cr_lane}})
                                  ? (3'd4 - {1'b0, cr_lane})
                                  : cr_w_remaining[2:0]);

            if (m_wr_awvalid && m_wr_awready) m_wr_awvalid <= 0;
            if (m_wr_wvalid  && m_wr_wready ) m_wr_wvalid  <= 0;
            if (m_wr_bvalid) begin
                m_wr_awvalid <= 0;
                m_wr_wvalid  <= 0;
                if (cr_w_remaining <= {13'b0, cr_bytes_this}) begin
                    // Last word in this row.  Advance to next row.
                    if (cr_y_remaining == 16'd1) begin
                        // Last row finished — rect done.
                        state <= S_IDLE;
                    end else begin : cr_row_advance
                        // Per-command stride if non-zero; otherwise the
                        // SET_FB global (legacy behaviour for callers
                        // that don't fill the stride field).
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
        //   Step 6: det = dsp_p + dsp2_p (just register the sum)
        //   Step 7: tri_det_is_small_r = small_test(tri_det) (compare on registered sum)
        //   Step 8: degenerate check + CLZ of |det|
        //   Step 9: write recip_rd_addr
        //   Step 10: BRAM read latency wait
        //   Step 11: capture recip → transition to S_TRI_GRAD
        // 12 cycles end-to-end (was 11; +1 for the registered small-det
        // compare).  Previous mp_ram critical path on this design was
        // step 6's combined "register sum + 3-comparator small-test" =
        // DSP→ADD→3 compares→FF in one cycle, ~10.5 ns combinational,
        // -1.21 ns slack at 100 MHz.  Splitting the compare into its
        // own cycle puts each chunk on its own ≤4 ns path.
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
                // det = A0*dX20 + B0*dY20  (both multiplies done in parallel).
                // Register the sum only — the small-det test moves to
                // step 7 to keep the DSP→ADD→FF path under 5 ns.
                tri_det <= dsp_p[31:0] + dsp2_p[31:0];
            end
            7: begin
                // Pure compare-only stage on the registered tri_det:
                //   FF→3 compares→FF, ~3 ns combinational.  The result
                //   feeds step 8's branch as a single registered bit.
                tri_det_is_small_r <= (tri_det == 32'sd0)
                                       || (tri_det > -32'sd16 && tri_det < 32'sd16);
            end
            8: begin
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
            9: begin
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
            10: begin
                // BRAM read latency wait — recip_rd_addr was set at end
                // of step 9.  recip_rd_data is registered, so the
                // updated lookup result lands at end of this cycle.
                // Without this wait, tri_recip captured stale data
                // (always LUT[0] = 0x4000 on the first triangle, since
                // recip_rd_addr resets to 0).  Latent bug from before
                // 4d landed; existing tests passed because they only
                // checked the span's start pixel, where gradients are
                // multiplied by zero.
            end
            11: begin
                // Capture M10K read result, then enter rolled gradient loop.
                tri_recip <= recip_rd_data;
                state <= S_TRI_GRAD;
                grad_idx <= 0;
                // Start at the settle sub-cycle so grad_dV10_r/dV20_r
                // latch the new grad_idx=0 before sub_0 hands them to
                // the DSPs.  Without this the very first iteration of
                // the loop reads stale grad_dV*_r from the prior
                // triangle's last grad_idx.
                grad_sub <= 3'd7;
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
                    // Launch both cross-multiplies in parallel.  Reads
                    // from the pipeline-registered grad_dV10_r/dV20_r
                    // (one-cycle-behind copy of the deep mux output).
                    // grad_idx is stable since sub-cycle 7, so the
                    // registered values reflect the current iteration.
                    dsp_a  <= grad_dV10_r; dsp_b  <= grad_axis_b1;
                    dsp2_a <= grad_dV20_r; dsp2_b <= grad_axis_b2;
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
                3'd7: begin
                    // Settle cycle: grad_idx was advanced during 3'd6
                    // (or this is the first iteration after S_TRI_BBOX),
                    // so the deep grad_dV10/dV20 mux just changed
                    // inputs.  Wait one cycle for grad_dV10_r/dV20_r
                    // to latch the new value before sub-cycle 0 hands
                    // them to the DSPs.  Loop transition handled below.
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
                        // Phase 4d Gouraud — light gradient.  Q16.16
                        // matches sp_light_q's format (bits[23:16] are
                        // the 8-bit cmap row index).  v_r is 8-bit so
                        // the dsp product fits comfortably without the
                        // perspective-Q16.16 right-shift the persp
                        // attributes need.
                        4'd8: grad_r_dx <= dsp_p_shifted;
                        4'd9: grad_r_dy <= dsp_p_shifted;
                        default: ;
                    endcase
                    // grad_sub auto-increments to 3'd7 (settle cycle)
                    // and then wraps to 0 — no explicit reset here.
                    // Loop progression:
                    //   idx 5 → 6 (persp continues to w) or 8 (affine
                    //               jumps over w to r — Gouraud)
                    //   idx 7 → 8 (persp continues to r — Gouraud)
                    //   idx 9 → exit
                    if (grad_idx == 4'd9) begin
                        state <= S_TRI_BBOX;
                    end else if (grad_idx == 4'd5 && !tri_persp_active) begin
                        // Affine: skip w (idx 6/7), continue to r (idx 8).
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
                    // Capture t.  On perspective triangles launch w
                    // next (round 3 of 5); on affine triangles skip w
                    // and launch r (round 4) directly.  Either way the
                    // next step is a wait cycle for the DSP pipeline.
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
                    end else begin
                        // Affine path: skip w, launch r (Gouraud)
                        // directly.
                        dsp_a  <= grad_r_dx;
                        dsp_b  <= {{11{delta_x_subpix[20]}}, delta_x_subpix};
                        dsp2_a <= grad_r_dy;
                        dsp2_b <= {{11{delta_y_subpix[20]}}, delta_y_subpix};
                    end
                    init_step <= 4'd7;
                end
                4'd7: init_step <= 4'd8;
                4'd8: begin
                    if (tri_persp_active) begin
                        // Persp: capture w + launch r.
                        tri_row_w <= v_w[0]
                                   + $signed(dsp_p[31:0])
                                   + $signed(dsp2_p[31:0]);
                        dsp_a  <= grad_r_dx;
                        dsp_b  <= {{11{delta_x_subpix[20]}}, delta_x_subpix};
                        dsp2_a <= grad_r_dy;
                        dsp2_b <= {{11{delta_y_subpix[20]}}, delta_y_subpix};
                        init_step <= 4'd9;
                    end else begin
                        // Affine: this step's DSP product is the r
                        // round.  Capture and exit init.
                        // {8'b0, v_r[0], 16'b0} = v_r[0] << 16, the
                        // anchor in Q16.16 with the 8-bit row at
                        // bits[23:16] (matches sp_light_q layout).
                        tri_row_r <= {8'b0, v_r[0], 16'b0}
                                   + $signed(dsp_p[31:0])
                                   + $signed(dsp2_p[31:0]);
                        init_step <= 4'd0;
                        state     <= S_TRI_ROW;
                    end
                end
                4'd9: init_step <= 4'd10;
                4'd10: begin
                    // Persp: capture r → S_TRI_ROW.
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
            tri_active <= 1;
            tri_cur_x <= tri_xmin;
            tri_cur_y <= tri_ymin;
            // Pre-arm row_done_r for the first S_TRI_PIX cycle.  bbox
            // check above guarantees tri_xmin <= tri_xmax, so this is
            // always 0 on entry — set explicitly anyway to be robust
            // against any future bbox-allows-equal change.
            row_done_r <= (tri_xmin > tri_xmax);
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
            // S_TRI_MUL_WAIT (tri_ymin × st_fb_stride).
            tri_fb_row_addr <= st_fb_addr + tri_ymin_x_stride;
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
            if (row_done_r) begin
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
                    // Triangle path keeps using the sticky CMD_SET_COLORMAP_ID
                    // default — there's no comparable spans-in-batch problem
                    // for triangles since flat-shaded triangles already share
                    // state across all fragments.  The per-span colormap_id
                    // mechanism is for CMD_DRAW_SPAN(S_BATCH) only.
                    sp_colormap_id <= st_colormap_id;
                    // Phase 4d Gouraud: per-pixel light walk along x.
                    // tri_span_r_start is the interpolated r at this
                    // span's first inside pixel; sp_light_step is the
                    // per-pixel x-delta in Q16.16 (matches grad_r_dx
                    // scaled the same way as the s/t/z attribute steps).
                    // Replaces the prior flat-from-v0 path which made
                    // D_ALIAS_GOURAUD a no-op in Quake.
                    sp_light_q    <= tri_span_r_start;
                    sp_light_step <= grad_r_dx <<< 4;
                    // sp_flags bit 0 (SPAN_COLORMAP) is hard-set for every
                    // triangle: triangle fragments always route through the
                    // palookup LUT at cmap[colormap_id][v_r[0]][texel].  This
                    // mirrors BUILD/Duke3D's lit-textured pipeline.  Callers
                    // that want raw-texel triangles must upload an identity
                    // row at the cmap[r] they reference (typically r=0); a
                    // missing row reads 0 and renders the fragment black.
                    // PERSP flag (bit 5) folds in when tri_persp_active.
                    // bits 3/4 (DEPTH_TEST/WRITE) retired with the Z buffer.
                    sp_flags     <= 8'h01
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
                    // Phase 4d Gouraud: snapshot the per-pixel-walked
                    // light value at the first inside pixel.  Replaces
                    // the prior `{8'b0, v_r[0], 16'b0}` flat anchor.
                    tri_span_r_start <= tri_r;
                end
                tri_span_count <= tri_span_count + 16'd1;
                tri_cur_x <= tri_cur_x + 16'd1;
                // Pre-compare with tri_xmax so the next S_TRI_PIX cycle's
                // branch reads a registered bit, not a 16-bit compare cone.
                row_done_r <= ((tri_cur_x + 16'd1) > tri_xmax);
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
                row_done_r <= ((tri_cur_x + 16'd1) > tri_xmax);
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
                // Re-arm row_done_r for the new row's first S_TRI_PIX cycle.
                row_done_r <= (tri_xmin > tri_xmax);
                tri_row_e[0] <= tri_row_e[0] + (tri_B[0] <<< 4);
                tri_row_e[1] <= tri_row_e[1] + (tri_B[1] <<< 4);
                tri_row_e[2] <= tri_row_e[2] + (tri_B[2] <<< 4);
                tri_e[0] <= tri_row_e[0] + (tri_B[0] <<< 4);
                tri_e[1] <= tri_row_e[1] + (tri_B[1] <<< 4);
                tri_e[2] <= tri_row_e[2] + (tri_B[2] <<< 4);
                tri_fb_row_addr <= tri_fb_row_addr + {{16{st_fb_stride[15]}}, st_fb_stride};
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

        default: state <= S_IDLE;
        endcase
        end  // closes the housekeeping `begin` introduced for m_wr_inflight + gpu_swap_req auto-clear
    end
end

// Colormap BRAM initialises to zero in Cyclone V M10K.
// CPU uploads data via MMIO before use.

endmodule
