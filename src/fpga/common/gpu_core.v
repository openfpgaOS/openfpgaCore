//
// GPU Core — Phase 1: Span Rasterizer
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
// 0x24  GPU_CMAP_DATA   W   Colormap write data (byte)
// 0x28  GPU_TEX_FLUSH   W   Flush texture cache (write any value)
// 0x2C  GPU_STAT_SPANS  R   Span counter

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

reg [13:0] cmap_wr_addr;       // Colormap auto-increment address
reg        tex_flush_req;      // Pulse to flush texture cache
reg        soft_reset;         // Pulse: resets FSM state + ring pointers
reg        ring_reset;         // Pulse: reset ring_rdptr (from MMIO, consumed by FSM)

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
        cmap_wr_addr <= 0;
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
                end
                4'd8: begin  // GPU_CMAP_ADDR
                    cmap_wr_addr <= reg_wdata[13:0];
                end
                4'd9: begin  // GPU_CMAP_DATA (auto-increment)
                    cmap_wr_addr <= cmap_wr_addr + 14'd1;
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
always @(*) begin
    case (reg_addr)
        4'd1:    reg_rdata = {16'b0, ring_wrptr};
        4'd4:    reg_rdata = {16'b0, ring_rdptr};
        4'd5:    reg_rdata = {30'b0, ring_empty, busy};
        4'd6:    reg_rdata = fence_reached;
        4'd7:    reg_rdata = stat_pixels;
        4'd11:   reg_rdata = stat_spans;
        default: reg_rdata = 32'b0;
    endcase
end

// ================================================================
// Colormap BRAM — 16 KB dual-port
// ================================================================
// Port A: CPU writes (via MMIO cmap_data register)
// Port B: GPU reads (during fragment processing, 1-cycle latency)

reg [7:0] cmap_bram [0:16383];

// Port A: CPU write
wire cmap_cpu_wr = reg_wr && (reg_addr == 4'd9);
always @(posedge clk) begin
    if (cmap_cpu_wr)
        cmap_bram[cmap_wr_addr] <= reg_wdata[7:0];
end

// Port B: GPU read
reg [13:0] cmap_rd_addr;
reg [7:0]  cmap_rd_data;
always @(posedge clk) begin
    cmap_rd_data <= cmap_bram[cmap_rd_addr];
end

// ================================================================
// Texture Cache instance
// ================================================================
// tex_req_* drive: in Lite (FRAG_PIPELINE) these are combinational from the
// pipeline issue logic; in Full they're regs written by the sequential FSM.
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
    .axi_rlast(tex_axi_rlast)
);

// ================================================================
// AXI4 Read Master — texture cache only (ring moved to M10K BRAM)
// ================================================================
// No arbiter needed: M0 is exclusively for texture cache fills.

assign m_rd_arvalid    = tex_axi_arvalid;
assign m_rd_araddr     = tex_axi_araddr;
assign m_rd_arlen      = tex_axi_arlen;
assign tex_axi_arready = m_rd_arready;
assign tex_axi_rvalid  = m_rd_rvalid;
assign tex_axi_rdata   = m_rd_rdata;
assign tex_axi_rlast   = m_rd_rlast;

// ================================================================
// Command Types
// ================================================================
localparam CMD_NOP            = 8'h01;
localparam CMD_FENCE          = 8'h02;
localparam CMD_CLEAR          = 8'h10;
localparam CMD_SET_TEXTURE    = 8'h20;
localparam CMD_SET_DEPTH_FUNC = 8'h21;
localparam CMD_SET_BLEND      = 8'h22;
localparam CMD_SET_FB         = 8'h23;
localparam CMD_SET_ZB         = 8'h24;
localparam CMD_SET_SHADE      = 8'h25;
localparam CMD_SET_ALPHA_REF  = 8'h26;
localparam CMD_DRAW_TRIANGLES = 8'h30;
localparam CMD_DRAW_INDEXED   = 8'h31;
localparam CMD_DRAW_SPAN      = 8'h40;
localparam CMD_DRAW_SPANS     = 8'h41;

// ================================================================
// GPU State Registers (sticky, set by SET_* commands)
// ================================================================
reg [31:0] st_tex_addr;
reg [15:0] st_tex_width;
reg [15:0] st_tex_height;
reg [1:0]  st_tex_format;      // 0=I8, 1=RGB565
reg [1:0]  st_tex_wrap_s;
reg [1:0]  st_tex_wrap_t;
reg [2:0]  st_depth_func;      // 0=none,1=always,2=less,3=lequal,4=equal
reg [1:0]  st_blend_mode;
reg [7:0]  st_alpha_ref;
reg [31:0] st_fb_addr;
reg [15:0] st_fb_stride;
reg [31:0] st_zb_addr;
reg [15:0] st_zb_stride;
reg        st_gouraud;

// ================================================================
// Span Registers (loaded from command payload)
// ================================================================
reg [31:0] sp_fb_addr;
reg [31:0] sp_tex_addr;
reg signed [31:0] sp_s, sp_t;
reg signed [31:0] sp_sstep, sp_tstep;
reg [15:0] sp_count;
reg [7:0]  sp_light;
reg [7:0]  sp_flags;
reg signed [15:0] sp_fb_stride;
reg [15:0] sp_tex_width;
reg [7:0]  sp_tex_shift;
reg [7:0]  sp_tex_bits;
reg [31:0] sp_z_addr;
reg signed [31:0] sp_zi;
reg signed [31:0] sp_zistep;

// Span flags
localparam SPAN_COLORMAP   = 0;
localparam SPAN_COLUMN     = 1;
localparam SPAN_SKIP_ZERO  = 2;
localparam SPAN_DEPTH_TEST = 3;
localparam SPAN_DEPTH_WRITE= 4;
localparam SPAN_PERSP      = 5;

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

// Phase 2: triangle rasterisation states
localparam S_TRI_LOAD      = 6'd26;  // Extract vertices from payload
localparam S_TRI_SETUP     = 6'd27;  // Sequential edge/gradient computation
localparam S_TRI_BBOX      = 6'd28;  // Compute bounding box, clip to screen
localparam S_TRI_ROW       = 6'd29;  // Initialise scanline row
localparam S_TRI_PIX       = 6'd30;  // Test pixel / step X
localparam S_TRI_FRAG      = 6'd31;  // Set up fragment for tex pipeline
localparam S_TRI_GRAD      = 6'd32;  // Rolled gradient computation loop
localparam S_FRAG_PIPE     = 6'd33;  // Unified pipelined fragment processor

reg [5:0] state;

// Command decoding
reg [7:0]  cmd_type;
reg [23:0] cmd_payload_words;

// Payload buffer — up to 20 words (largest = DRAW_SPAN at 18)
reg [31:0] pay_buf [0:23];
reg [4:0]  pay_idx;
reg [4:0]  pay_remaining;

// Current pixel state
reg [7:0]  frag_texel;        // Texel value (I8)
reg [15:0] frag_color;        // Output color (after colormap / combine)
reg [15:0] frag_z;            // Fragment depth
reg        frag_discard;      // Alpha test / skip-zero result

// ================================================================
// Pipelined Fragment Processor — stage registers (LITE only)
// ================================================================
// LITE-only path. Full keeps the sequential S_SPAN_* FSM for now because
// its triangle rasterizer is intertwined with the old fragment states; the
// triangle refactor is deferred.
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
reg        p0_mode;             // 1 = multiply mode, 0 = shift mode
reg [31:0] p0_shift_addr;       // pre-computed full addr for shift mode

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

// FB write sub-FSM (lives within S3, pauses pipeline when not IDLE)
localparam FBSS_IDLE        = 4'd0;
localparam FBSS_FLUSH_AW    = 4'd1;  // emit AW, then resume into accumulate
localparam FBSS_FLUSH_W_RSP = 4'd2;  // wait for W handshake + B response
localparam FBSS_ZREAD       = 4'd3;  // (Full only) issue SRAM read for Z
localparam FBSS_ZWAIT       = 4'd4;
localparam FBSS_ZWRITE      = 4'd5;
localparam FBSS_ZWRWAIT     = 4'd6;
reg [3:0] fbss;

// Pending pixel queued during a flush — applied after AXI write completes.
reg        fbss_pend_valid;
reg [7:0]  fbss_pend_color;
reg [31:0] fbss_pend_addr;

// Source mode: which input feeds S0 each cycle.
localparam SRC_SPAN     = 1'b0;
localparam SRC_TRIANGLE = 1'b1;
reg src_mode;
reg src_done;            // source has issued its last pixel; pipeline draining

// ----------------------------------------------------------------
// Combinational tex_req drive (LITE pipeline only)
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

wire fp_pipe_stall = (p1_valid && !tex_resp_valid) || (fbss != FBSS_IDLE);

// Combinational tex address from p0 + DSP output
wire [31:0] fp_tex_addr_full = p0_mode
    ? (p0_tex_base + tx_mul_q + {{16{p0_s_int[15]}}, p0_s_int})
    : p0_shift_addr;

assign tex_req_valid = (state == S_FRAG_PIPE) && p0_valid
                    && !fp_pipe_stall;
assign tex_req_addr  = fp_tex_addr_full[25:0];
assign tex_req_wide  = 1'b0;
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

// Batch span state
reg [31:0] batch_remaining;   // Spans remaining in batch

// ================================================================
// Phase 2: Triangle Registers
// ================================================================

// tri_active: 1 = fragment pipeline returns to triangle path. Read by span
// states unconditionally; in LITE it's a constant 0 so the synthesizer folds
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

// Edge equation coefficients: E_i(x,y) = A_i*x + B_i*y + C_i
reg signed [31:0] tri_A [0:2], tri_B [0:2], tri_C [0:2];

// Attribute gradients (per sub-pixel step, fixed-point)
reg signed [31:0] grad_z_dx, grad_z_dy;
reg signed [31:0] grad_s_dx, grad_s_dy;
reg signed [31:0] grad_t_dx, grad_t_dy;
reg signed [31:0] grad_r_dx, grad_r_dy;

// Bounding box (integer pixel coords)
reg signed [15:0] tri_xmin, tri_xmax, tri_ymin, tri_ymax;

// Rasteriser current state
reg signed [15:0] tri_cur_x, tri_cur_y;
reg signed [31:0] tri_e [0:2];                  // edge function at current pixel
reg signed [31:0] tri_row_e [0:2];              // edge function at row start
reg signed [31:0] tri_z, tri_s, tri_t, tri_r;   // interpolated attribs
reg signed [31:0] tri_row_z, tri_row_s, tri_row_t, tri_row_r;

// Setup state
reg [6:0]  setup_step;
// Rolled gradient loop state (replaces setup steps 20-56)
reg [2:0]  grad_idx;          // 0..5: which of 6 gradients (Zdx,Zdy,Sdx,Sdy,Tdx,Tdy)
reg [2:0]  grad_sub;          // 0..6: sub-cycle within current gradient
reg signed [31:0] grad_partial; // first cross-product partial result
reg [31:0] tri_fb_row_addr;    // precomputed: st_fb_addr + cur_y * stride
reg [31:0] tri_zb_row_addr;    // precomputed: st_zb_addr + cur_y * zb_stride
reg signed [31:0] tri_det;
reg signed [15:0] tri_recip;  // scaled 1/|det| (fixed-point)
reg [5:0]  tri_clz;           // leading zeros of |det|
reg        tri_det_sign;       // 1 if det was negative

// Precomputed differences (used across setup steps)
reg signed [15:0] dX10, dY10, dX20, dY20;

// Registered DSP multiply (18×18 maps to one Cyclone V DSP block)
reg signed [31:0] dsp_a;
reg signed [31:0] dsp_b;
(* multstyle = "dsp" *) reg signed [63:0] dsp_p;
always @(posedge clk) dsp_p <= dsp_a * dsp_b;

// Reciprocal LUT: 256 × 16-bit in M10K (saves ~250 ALMs vs registers).
// Registered read port: set recip_rd_addr, result in recip_rd_data next cycle.
(* ramstyle = "M10K" *) reg [15:0] recip_lut [0:255];
reg [7:0]  recip_rd_addr;
reg [15:0] recip_rd_data;
always @(posedge clk) recip_rd_data <= recip_lut[recip_rd_addr];
integer ri;
initial begin
    for (ri = 0; ri < 256; ri = ri + 1)
        recip_lut[ri] = (4194304) / (256 + ri);
end

// Triangle stats
reg [31:0] stat_triangles;

// ----------------------------------------------------------------
// Gradient loop operand selectors (S_TRI_GRAD)
// grad_idx[2:1]: 00=Z, 01=S, 10=T  (selects vertex attribute)
// grad_idx[0]:   0=dx (cross with Y axes),
//                1=dy (cross with X axes, with sign flip)
// ----------------------------------------------------------------
wire signed [31:0] grad_dV10 =
    (grad_idx[2:1] == 2'd0) ? ({{16{v_z[1][15]}}, v_z[1]} - {{16{v_z[0][15]}}, v_z[0]}) :
    (grad_idx[2:1] == 2'd1) ? ({{16{v_s[1][15]}}, v_s[1]} - {{16{v_s[0][15]}}, v_s[0]}) :
                              ({{16{v_t[1][15]}}, v_t[1]} - {{16{v_t[0][15]}}, v_t[0]});
wire signed [31:0] grad_dV20 =
    (grad_idx[2:1] == 2'd0) ? ({{16{v_z[2][15]}}, v_z[2]} - {{16{v_z[0][15]}}, v_z[0]}) :
    (grad_idx[2:1] == 2'd1) ? ({{16{v_s[2][15]}}, v_s[2]} - {{16{v_s[0][15]}}, v_s[0]}) :
                              ({{16{v_t[2][15]}}, v_t[2]} - {{16{v_t[0][15]}}, v_t[0]});
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

// Shift mode (combinational, no multiply — fast)
wire [31:0] t_shifted = sp_t >> sp_tex_shift;
wire [31:0] tex_shift_result = (t_shifted << sp_tex_bits) | (sp_s >> (6'd32 - {1'b0, sp_tex_bits}));

// Pipeline registers (written in S_SPAN_PIXEL)
reg signed [15:0] tex_pipe_t_int;    // registered multiply input A
reg        [15:0] tex_pipe_width;    // registered multiply input B
reg        [31:0] tex_pipe_base;     // sp_tex_addr
reg signed [15:0] tex_pipe_s_int;    // s integer part
reg        [31:0] tex_pipe_shift_r;  // shift mode result (already computed)
reg               tex_pipe_mode;     // 0=shift, 1=multiply

// Tex addr multiply (combinational 16×16 — separate from setup DSP).
// Sharing with the registered DSP would add 1 cycle latency per pixel.
wire [31:0] tex_mul_result = $signed(tex_pipe_t_int) * $signed({1'b0, tex_pipe_width});
wire [31:0] tex_addr_final = tex_pipe_mode
    ? (tex_pipe_base + tex_mul_result + {{16{tex_pipe_s_int[15]}}, tex_pipe_s_int})
    : (tex_pipe_base + tex_pipe_shift_r);

// ================================================================
// Main FSM body
// ================================================================
always @(posedge clk) begin
    if (!reset_n) begin
        state <= S_IDLE;
        ring_rdptr <= 0;
`ifndef GPU_FEAT_FRAG_PIPELINE
        tex_req_valid <= 0;
`endif
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
        pay_idx <= 0;
        pay_remaining <= 0;
        frag_discard <= 0;
        clear_flags <= 0;
        batch_remaining <= 0;
        tex_pipe_t_int <= 0;
        tex_pipe_width <= 0;
        tex_pipe_base <= 0;
        tex_pipe_s_int <= 0;
        tex_pipe_shift_r <= 0;
        tex_pipe_mode <= 0;
`ifdef GPU_FEAT_FRAG_PIPELINE
        // Pipelined fragment processor reset
        p0_valid <= 0; p0_light <= 0; p0_flags <= 0;
        p0_fb_addr <= 0; p0_z_addr <= 0; p0_zi <= 0;
        p0_s_int <= 0; p0_tex_base <= 0; p0_mode <= 0; p0_shift_addr <= 0;
        tx_mul_q <= 0;
        p1_valid <= 0; p1_light <= 0; p1_flags <= 0;
        p1_fb_addr <= 0; p1_z_addr <= 0; p1_zi <= 0;
        p2_valid <= 0; p2_color <= 0; p2_light <= 0; p2_flags <= 0;
        p2_fb_addr <= 0; p2_z_addr <= 0; p2_zi <= 0; p2_discard <= 0;
        p2b_valid <= 0; p2b_color <= 0; p2b_flags <= 0;
        p2b_fb_addr <= 0; p2b_z_addr <= 0; p2b_zi <= 0; p2b_discard <= 0;
        p3_valid <= 0; p3_color <= 0; p3_flags <= 0;
        p3_fb_addr <= 0; p3_z_addr <= 0; p3_zi <= 0; p3_discard <= 0;
        fbss <= FBSS_IDLE;
        fbss_pend_valid <= 0; fbss_pend_color <= 0; fbss_pend_addr <= 0;
        src_mode <= SRC_SPAN;
        src_done <= 0;
`endif
`ifdef GPU_FEAT_TRIANGLE
        tri_active <= 0;
        setup_step <= 0;
        grad_idx <= 0;
        grad_sub <= 0;
        grad_partial <= 0;
        tri_det <= 0;
        tri_recip <= 0;
        tri_clz <= 0;
        tri_det_sign <= 0;
        stat_triangles <= 0;
        dsp_a <= 0; dsp_b <= 0;
`endif
        // State registers
        st_tex_addr <= 0; st_tex_width <= 0; st_tex_height <= 0;
        st_tex_format <= 0; st_tex_wrap_s <= 0; st_tex_wrap_t <= 0;
        st_depth_func <= 0; st_blend_mode <= 0; st_alpha_ref <= 0;
        st_fb_addr <= 0; st_fb_stride <= 320;
        st_zb_addr <= 0; st_zb_stride <= 640;
        st_gouraud <= 0;
    end else begin
        // Default: deassert one-shot signals
`ifndef GPU_FEAT_FRAG_PIPELINE
        tex_req_valid <= 0;
`endif
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
            if (cmd_payload_words == 0) begin
                state <= S_EXECUTE;
            end else begin
                pay_idx <= 0;
                pay_remaining <= (cmd_payload_words > 24) ? 5'd24
                               : cmd_payload_words[4:0];
                // Start first BRAM read (data arrives next cycle)
                ring_rdptr <= (ring_rdptr + 16'd4) & ring_mask;
                state <= S_PAY_DATA;
            end
        end

        // ============================================================
        // Payload — read words from BRAM (1 word per cycle)
        // ============================================================
        S_PAY_DATA: begin
            // ring_rd_data has the current payload word
            if (pay_idx < 24)
                pay_buf[pay_idx] <= ring_rd_data;
            pay_idx       <= pay_idx + 5'd1;
            pay_remaining <= pay_remaining - 5'd1;

            if (pay_remaining <= 1) begin
                state <= S_EXECUTE;
            end else begin
                // Advance rdptr for next word (BRAM read, 1-cycle latency)
                ring_rdptr <= (ring_rdptr + 16'd4) & ring_mask;
            end
        end

        // ============================================================
        // Execute command
        // ============================================================
        S_EXECUTE: begin
            case (cmd_type)

            CMD_NOP: state <= S_IDLE;

            CMD_FENCE: begin
                fence_reached <= pay_buf[0];
                state <= S_IDLE;
            end

            CMD_CLEAR: begin
                clear_flags <= pay_buf[0][17:16];
                clear_color <= pay_buf[0][15:0];
                clear_depth <= pay_buf[1][15:0];
                state       <= S_CLEAR_INIT;
            end

            CMD_SET_TEXTURE: begin
                st_tex_addr   <= pay_buf[0];
                st_tex_width  <= pay_buf[1][31:16];
                st_tex_height <= pay_buf[1][15:0];
                st_tex_format <= pay_buf[2][17:16];
                st_tex_wrap_s <= pay_buf[2][1:0];
                st_tex_wrap_t <= pay_buf[3][1:0];
                state <= S_IDLE;
            end

            CMD_SET_DEPTH_FUNC: begin
                st_depth_func <= pay_buf[0][2:0];
                state <= S_IDLE;
            end

            CMD_SET_BLEND: begin
                st_blend_mode <= pay_buf[0][1:0];
                state <= S_IDLE;
            end

            CMD_SET_ALPHA_REF: begin
                st_alpha_ref <= pay_buf[0][7:0];
                state <= S_IDLE;
            end

            CMD_SET_FB: begin
                st_fb_addr   <= pay_buf[0];
                st_fb_stride <= pay_buf[1][15:0];
                state <= S_IDLE;
            end

            CMD_SET_ZB: begin
                st_zb_addr   <= pay_buf[0];
                st_zb_stride <= pay_buf[1][15:0];
                state <= S_IDLE;
            end

            CMD_SET_SHADE: begin
                st_gouraud <= pay_buf[0][0];
                state <= S_IDLE;
            end

            CMD_DRAW_SPAN: begin
                // Load span parameters from payload
                sp_fb_addr   <= pay_buf[0];
                sp_tex_addr  <= pay_buf[1];
                sp_s         <= pay_buf[2];
                sp_t         <= pay_buf[3];
                sp_sstep     <= pay_buf[4];
                sp_tstep     <= pay_buf[5];
                sp_count     <= pay_buf[6][31:16];
                sp_light     <= pay_buf[6][15:8];
                sp_flags     <= pay_buf[6][7:0];
                sp_fb_stride <= pay_buf[7][31:16];
                sp_tex_width <= pay_buf[7][15:0];
                sp_tex_shift <= pay_buf[8][15:8];
                sp_tex_bits  <= pay_buf[8][7:0];
                sp_z_addr    <= pay_buf[9];
                sp_zi        <= pay_buf[10];
                sp_zistep    <= pay_buf[11];
                stat_spans   <= stat_spans + 32'd1;
`ifdef GPU_FEAT_FRAG_PIPELINE
                src_mode     <= SRC_SPAN;
                src_done     <= 0;
                state        <= S_FRAG_PIPE;
`else
                state        <= S_SPAN_PIXEL;
`endif
            end

            CMD_DRAW_SPANS: begin
                // Batch: first word is span count, then N×18 words
                batch_remaining <= pay_buf[0] - 32'd1;
                // First span starts at pay_buf[1..18]
                sp_fb_addr   <= pay_buf[1];
                sp_tex_addr  <= pay_buf[2];
                sp_s         <= pay_buf[3];
                sp_t         <= pay_buf[4];
                sp_sstep     <= pay_buf[5];
                sp_tstep     <= pay_buf[6];
                sp_count     <= pay_buf[7][31:16];
                sp_light     <= pay_buf[7][15:8];
                sp_flags     <= pay_buf[7][7:0];
                sp_fb_stride <= pay_buf[8][31:16];
                sp_tex_width <= pay_buf[8][15:0];
                sp_tex_shift <= pay_buf[9][15:8];
                sp_tex_bits  <= pay_buf[9][7:0];
                sp_z_addr    <= pay_buf[10];
                sp_zi        <= pay_buf[11];
                sp_zistep    <= pay_buf[12];
                stat_spans   <= stat_spans + 32'd1;
`ifdef GPU_FEAT_FRAG_PIPELINE
                src_mode     <= SRC_SPAN;
                src_done     <= 0;
                state        <= S_FRAG_PIPE;
`else
                state        <= S_SPAN_PIXEL;
`endif
            end

`ifdef GPU_FEAT_TRIANGLE
            CMD_DRAW_TRIANGLES: begin
                // pay_buf[0] = vertex_count (must be 3 for one triangle)
                // pay_buf[1..6] = vertex 0, [7..12] = vertex 1, [13..18] = vertex 2
                state <= S_TRI_LOAD;
            end

            CMD_DRAW_INDEXED: begin
                // pay_buf[0] = vert_count, pay_buf[1] = idx_count
                // pay_buf[2..N] = vertices, then indices packed 2 per word
                // Phase 2: extract 3 vertices via index lookup from pay_buf
                begin : indexed_load
                    reg [15:0] i0, i1, i2;
                    reg [4:0] vbase;  // word offset where vertices start
                    reg [4:0] ibase;  // word offset where indices start
                    vbase = 5'd2;
                    ibase = vbase + pay_buf[0][4:0] * 5'd6;
                    // Indices packed 2 per word: [31:16]=idx1, [15:0]=idx0
                    i0 = pay_buf[ibase][15:0];
                    i1 = pay_buf[ibase][31:16];
                    i2 = pay_buf[ibase + 5'd1][15:0];
                    // Copy indexed vertices into pay_buf[1..18] positions
                    // so S_TRI_LOAD can read them normally
                    pay_buf[1]  <= pay_buf[vbase + i0[2:0]*6];
                    pay_buf[2]  <= pay_buf[vbase + i0[2:0]*6 + 1];
                    pay_buf[3]  <= pay_buf[vbase + i0[2:0]*6 + 2];
                    pay_buf[4]  <= pay_buf[vbase + i0[2:0]*6 + 3];
                    pay_buf[5]  <= pay_buf[vbase + i0[2:0]*6 + 4];
                    pay_buf[6]  <= pay_buf[vbase + i0[2:0]*6 + 5];
                    pay_buf[7]  <= pay_buf[vbase + i1[2:0]*6];
                    pay_buf[8]  <= pay_buf[vbase + i1[2:0]*6 + 1];
                    pay_buf[9]  <= pay_buf[vbase + i1[2:0]*6 + 2];
                    pay_buf[10] <= pay_buf[vbase + i1[2:0]*6 + 3];
                    pay_buf[11] <= pay_buf[vbase + i1[2:0]*6 + 4];
                    pay_buf[12] <= pay_buf[vbase + i1[2:0]*6 + 5];
                    pay_buf[13] <= pay_buf[vbase + i2[2:0]*6];
                    pay_buf[14] <= pay_buf[vbase + i2[2:0]*6 + 1];
                    pay_buf[15] <= pay_buf[vbase + i2[2:0]*6 + 2];
                    pay_buf[16] <= pay_buf[vbase + i2[2:0]*6 + 3];
                    pay_buf[17] <= pay_buf[vbase + i2[2:0]*6 + 4];
                    pay_buf[18] <= pay_buf[vbase + i2[2:0]*6 + 5];
                end
                state <= S_TRI_LOAD;
            end
`endif // GPU_FEAT_TRIANGLE

            default: state <= S_IDLE;
            endcase
        end

        // ============================================================
        // SPAN pixel loop
`ifndef GPU_FEAT_FRAG_PIPELINE
        // ============================================================
        S_SPAN_PIXEL: begin
            if (sp_count == 0) begin
                // Span complete — flush FB accumulator
                state <= S_FB_FLUSH;
            end else begin
                // Pipeline stage 1: register inputs + load shared DSP
                tex_pipe_t_int   <= sp_t_int;
                tex_pipe_width   <= sp_tex_width;
                tex_pipe_base    <= sp_tex_addr;
                tex_pipe_s_int   <= sp_s_int;
                tex_pipe_shift_r <= tex_shift_result;
                tex_pipe_mode    <= (sp_tex_width != 0);
                state            <= S_SPAN_TEX_CALC;
            end
        end

        // Pipeline stage 2: finish tex addr, submit to cache
        S_SPAN_TEX_CALC: begin
            tex_req_valid <= 1;
            tex_req_addr  <= tex_addr_final[25:0];
            tex_req_wide  <= 1'b0;  // I8 for Phase 1
            state         <= S_SPAN_TEX_REQ;
        end

        S_SPAN_TEX_REQ: begin
            if (tex_req_ready) begin
                tex_req_valid <= 0;
                state <= S_SPAN_TEX_WAIT;
            end else begin
                tex_req_valid <= 1;  // Hold until accepted
            end
        end

        S_SPAN_TEX_WAIT: begin
            if (tex_resp_valid) begin
                frag_texel <= tex_resp_data[7:0];
                frag_color <= tex_resp_data;

                // Check skip-zero (transparency)
                if (sp_flags[SPAN_SKIP_ZERO] && tex_resp_data[7:0] == 8'hFF) begin
                    frag_discard <= 1;
                    state <= tri_active ? S_TRI_PIX : S_SPAN_STEP;
                end
                // Colormap path
                else if (sp_flags[SPAN_COLORMAP]) begin
                    cmap_rd_addr <= {sp_light[5:0], tex_resp_data[7:0]};
                    state <= S_SPAN_CMAP;
                end
                // No colormap — direct texel output
                else begin
                    frag_discard <= 0;
                    if (sp_flags[SPAN_DEPTH_TEST] || sp_flags[SPAN_DEPTH_WRITE])
                        state <= S_SPAN_ZREAD;
                    else
                        state <= S_SPAN_FB;
                end
            end
        end

        // ============================================================
        // Colormap lookup (1-cycle BRAM read latency)
        // ============================================================
        S_SPAN_CMAP: begin
            state <= S_SPAN_CMAP_WAIT;
        end

        S_SPAN_CMAP_WAIT: begin
            frag_color   <= {8'b0, cmap_rd_data};
            frag_discard <= 0;
            if (sp_flags[SPAN_DEPTH_TEST] || sp_flags[SPAN_DEPTH_WRITE])
                state <= S_SPAN_ZREAD;
            else
                state <= S_SPAN_FB;
        end

        // ============================================================
        // Z-buffer read / compare / write
        // ============================================================
        S_SPAN_ZREAD: begin
            if (!sram_busy) begin
                sram_rd   <= 1;
                sram_addr <= sp_z_addr[21:0];
                state     <= S_SPAN_ZWAIT;
            end
        end

        S_SPAN_ZWAIT: begin
            if (sram_rdata_valid) begin
                // Z-buffer stores 16-bit values; extract based on word alignment
                // Z addr is a byte address; [1] selects high/low halfword
                frag_z <= sp_zi[31:16];  // Current fragment Z (integer part)
                // Compare
                begin : z_compare
                    reg [15:0] old_z;
                    old_z = sp_z_addr[1] ? sram_rdata[31:16] : sram_rdata[15:0];
                    case (st_depth_func)
                        3'd2: frag_discard <= !(frag_z < old_z);     // LESS
                        3'd3: frag_discard <= !(frag_z <= old_z);    // LEQUAL
                        3'd4: frag_discard <= !(frag_z == old_z);    // EQUAL
                        3'd1: frag_discard <= 0;                      // ALWAYS
                        default: frag_discard <= 0;                   // NONE
                    endcase
                end
                state <= S_SPAN_ZCMP;
            end
        end

        S_SPAN_ZCMP: begin
            if (frag_discard) begin
                state <= tri_active ? S_TRI_PIX : S_SPAN_STEP;
            end else if (sp_flags[SPAN_DEPTH_WRITE] && !sram_busy) begin
                // Write new Z value
                sram_wr    <= 1;
                sram_addr  <= sp_z_addr[21:0];
                sram_wdata <= sp_z_addr[1]
                    ? {sp_zi[31:16], sram_rdata[15:0]}
                    : {sram_rdata[31:16], sp_zi[31:16]};
                sram_wstrb <= sp_z_addr[1] ? 4'b1100 : 4'b0011;
                state      <= S_SPAN_ZWWAIT;
            end else begin
                state <= S_SPAN_FB;
            end
        end

        S_SPAN_ZWWAIT: begin
            if (!sram_busy)
                state <= S_SPAN_FB;
        end

        // ============================================================
        // FB write — accumulate pixels, flush when word boundary crossed
        // ============================================================
        S_SPAN_FB: begin
            if (!frag_discard) begin
                stat_pixels <= stat_pixels + 32'd1;

                begin : fb_accumulate
                    reg [31:0] pixel_word_addr;
                    reg [1:0]  pixel_byte_lane;
                    pixel_word_addr = sp_fb_addr & 32'hFFFFFFFC;
                    pixel_byte_lane = sp_fb_addr[1:0];

                    if (fb_acc_valid && fb_acc_addr != pixel_word_addr) begin
                        // Different word — flush old accumulator first
                        m_wr_awvalid <= 1;
                        m_wr_awaddr  <= fb_acc_addr;
                        m_wr_awlen   <= 0;
                        m_wr_wvalid  <= 1;
                        m_wr_wdata   <= fb_acc_data;
                        m_wr_wstrb   <= fb_acc_mask;
                        m_wr_wlast   <= 1;
                        // Reset accumulator (pixel added after flush completes)
                        fb_acc_addr  <= pixel_word_addr;
                        fb_acc_data  <= 32'b0;
                        fb_acc_mask  <= 4'b0;
                        state <= S_FB_FLUSH_WAIT;
                    end else begin
                        // Same word (or first pixel) — accumulate
                        fb_acc_valid <= 1;
                        fb_acc_addr  <= pixel_word_addr;
                        case (pixel_byte_lane)
                            2'd0: begin fb_acc_data[7:0]   <= frag_color[7:0]; fb_acc_mask[0] <= 1; end
                            2'd1: begin fb_acc_data[15:8]  <= frag_color[7:0]; fb_acc_mask[1] <= 1; end
                            2'd2: begin fb_acc_data[23:16] <= frag_color[7:0]; fb_acc_mask[2] <= 1; end
                            2'd3: begin fb_acc_data[31:24] <= frag_color[7:0]; fb_acc_mask[3] <= 1; end
                        endcase
                        state <= tri_active ? S_TRI_PIX : S_SPAN_STEP;
                    end
                end
            end else begin
                state <= tri_active ? S_TRI_PIX : S_SPAN_STEP;
            end
        end

        // ============================================================
        // Step to next pixel (span mode only)
        // ============================================================
        S_SPAN_STEP: begin
            sp_s        <= sp_s + sp_sstep;
            sp_t        <= sp_t + sp_tstep;
            sp_fb_addr  <= sp_fb_addr + {{16{sp_fb_stride[15]}}, sp_fb_stride};
            sp_count    <= sp_count - 16'd1;
            sp_zi       <= sp_zi + sp_zistep;
            sp_z_addr   <= sp_z_addr + 32'd2;
            frag_discard <= 0;
            state       <= S_SPAN_PIXEL;
        end
`endif // !GPU_FEAT_FRAG_PIPELINE (old SPAN states)

`ifdef GPU_FEAT_FRAG_PIPELINE
        // ============================================================
        // Pipelined Fragment Processor (LITE)
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
            load_p0         = (issue_committed || !p0_valid)
                           && (sp_count != 16'd0) && !src_done;
            span_last_issue = (sp_count == 16'd1);

            // ----------------------------------------------------------
            // Pipeline shift — only when not stalled
            // ----------------------------------------------------------
            if (!fp_pipe_stall) begin
                // p3 <- p2b  (merges cmap result if cmap was used; cmap_rd_data
                // is now valid because p2b gave us 1 extra cycle of latency)
                p3_valid    <= p2b_valid;
                p3_color    <= p2b_flags[SPAN_COLORMAP] ? cmap_rd_data : p2b_color;
                p3_flags    <= p2b_flags;
                p3_fb_addr  <= p2b_fb_addr;
                p3_z_addr   <= p2b_z_addr;
                p3_zi       <= p2b_zi;
                p3_discard  <= p2b_discard;

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
                    p0_s_int     <= sp_s[31:16];
                    p0_tex_base  <= sp_tex_addr;
                    p0_mode      <= (sp_tex_width != 16'd0);
                    // Pre-compute shift-mode address combinationally and
                    // register into p0; the multiply-mode path uses tx_mul_q.
                    p0_shift_addr <= sp_tex_addr
                        + (((sp_t >> sp_tex_shift) << sp_tex_bits)
                          | (sp_s >> (6'd32 - {1'b0, sp_tex_bits})));

                    // DSP-pipelined multiply: registered output. The DSP
                    // slice will be inferred via the (* multstyle = "dsp" *)
                    // attribute on tx_mul_q's declaration.
                    tx_mul_q <= $signed({{1'b0}, sp_t[31:16]})
                              * $signed({1'b0, sp_tex_width});

                    // Advance span source
                    sp_s       <= sp_s + sp_sstep;
                    sp_t       <= sp_t + sp_tstep;
                    sp_fb_addr <= sp_fb_addr + {{16{sp_fb_stride[15]}}, sp_fb_stride};
                    sp_zi      <= sp_zi + sp_zistep;
                    sp_z_addr  <= sp_z_addr + 32'd2;
                    sp_count   <= sp_count - 16'd1;
                    if (span_last_issue) src_done <= 1;
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
                    // Process p3 if it has a non-discard pixel
                    if (p3_valid && !p3_discard) begin : fb_acc_blk
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
                            stat_pixels  <= stat_pixels + 32'd1;
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

                            stat_pixels  <= stat_pixels + 32'd1;
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

                default: fbss <= FBSS_IDLE;
            endcase

            // ----------------------------------------------------------
            // Drain detection — when source done and pipe empty, flush.
            // ----------------------------------------------------------
            if (src_done && !p0_valid && !p1_valid && !p2_valid && !p2b_valid
                         && !p3_valid && fbss == FBSS_IDLE) begin
                src_done <= 0;
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
        S_CLEAR_INIT: begin
            if (clear_flags[0]) begin
                clear_addr      <= st_fb_addr;
                clear_remaining <= 18'd16000;  // 320*200/4 words
                state           <= S_CLEAR_FB;
            end else if (clear_flags[1]) begin
                clear_addr      <= st_zb_addr;
                clear_remaining <= 18'd32000;
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
                sram_addr  <= clear_addr[21:0];
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
        // Triangle: Load vertices from payload
        // ============================================================
        S_TRI_LOAD: begin
            // Vertex 0: pay_buf[1..6]
            v_x[0] <= pay_buf[1][31:16]; v_y[0] <= pay_buf[1][15:0];
            v_z[0] <= pay_buf[2][31:16];
            v_s[0] <= pay_buf[3][31:16]; v_t[0] <= pay_buf[4][31:16];
            v_r[0] <= pay_buf[6][7:0];
            // Vertex 1: pay_buf[7..12]
            v_x[1] <= pay_buf[7][31:16]; v_y[1] <= pay_buf[7][15:0];
            v_z[1] <= pay_buf[8][31:16];
            v_s[1] <= pay_buf[9][31:16]; v_t[1] <= pay_buf[10][31:16];
            v_r[1] <= pay_buf[12][7:0];
            // Vertex 2: pay_buf[13..18]
            v_x[2] <= pay_buf[13][31:16]; v_y[2] <= pay_buf[13][15:0];
            v_z[2] <= pay_buf[14][31:16];
            v_s[2] <= pay_buf[15][31:16]; v_t[2] <= pay_buf[16][31:16];
            v_r[2] <= pay_buf[18][7:0];
            setup_step <= 0;
            state <= S_TRI_SETUP;
        end

        // ============================================================
        // Triangle: Sequential setup (edges, determinant, gradients)
        // ============================================================
        // DSP multiply has 1-cycle registered latency:
        //   Step N:   set dsp_a, dsp_b
        //   Step N+1: (pipeline, dsp computes)
        //   Step N+2: read dsp_p
        // Even steps set inputs, odd steps are pipeline delay, even+2 reads.
        // We interleave useful work into the delay slots where possible.
        S_TRI_SETUP: begin
            setup_step <= setup_step + 7'd1;
            case (setup_step)
            // --- Edges A, B + precompute diffs (no multiply needed) ---
            0: begin
                tri_A[0] <= v_y[0] - v_y[1]; tri_B[0] <= v_x[1] - v_x[0];
                tri_A[1] <= v_y[1] - v_y[2]; tri_B[1] <= v_x[2] - v_x[1];
                tri_A[2] <= v_y[2] - v_y[0]; tri_B[2] <= v_x[0] - v_x[2];
                dX10 <= v_x[1] - v_x[0]; dY10 <= v_y[1] - v_y[0];
                dX20 <= v_x[2] - v_x[0]; dY20 <= v_y[2] - v_y[0];
                // Set up C0 first multiply: v0.x * v1.y
                dsp_a <= {{16{v_x[0][15]}}, v_x[0]};
                dsp_b <= {{16{v_y[1][15]}}, v_y[1]};
            end
            1: begin end // DSP pipeline delay
            2: begin // Read C0 partial; set up C0 second: v1.x * v0.y
                tri_C[0] <= dsp_p[31:0];
                dsp_a <= {{16{v_x[1][15]}}, v_x[1]};
                dsp_b <= {{16{v_y[0][15]}}, v_y[0]};
            end
            3: begin end // pipeline delay
            4: begin // C0 complete; set up C1 first: v1.x * v2.y
                tri_C[0] <= tri_C[0] - dsp_p[31:0];
                dsp_a <= {{16{v_x[1][15]}}, v_x[1]};
                dsp_b <= {{16{v_y[2][15]}}, v_y[2]};
            end
            5: begin end
            6: begin // C1 partial; set up C1 second: v2.x * v1.y
                tri_C[1] <= dsp_p[31:0];
                dsp_a <= {{16{v_x[2][15]}}, v_x[2]};
                dsp_b <= {{16{v_y[1][15]}}, v_y[1]};
            end
            7: begin end
            8: begin // C1 complete; set up C2 first: v2.x * v0.y
                tri_C[1] <= tri_C[1] - dsp_p[31:0];
                dsp_a <= {{16{v_x[2][15]}}, v_x[2]};
                dsp_b <= {{16{v_y[0][15]}}, v_y[0]};
            end
            9: begin end
            10: begin // C2 partial; set up C2 second: v0.x * v2.y
                tri_C[2] <= dsp_p[31:0];
                dsp_a <= {{16{v_x[0][15]}}, v_x[0]};
                dsp_b <= {{16{v_y[2][15]}}, v_y[2]};
            end
            11: begin end
            12: begin // C2 complete; set up det part 1: A0 * dX20
                tri_C[2] <= tri_C[2] - dsp_p[31:0];
                dsp_a <= tri_A[0];
                dsp_b <= {{16{dX20[15]}}, dX20};
            end
            13: begin end
            14: begin // det partial; set up det part 2: B0 * dY20
                tri_det <= dsp_p[31:0];
                dsp_a <= tri_B[0];
                dsp_b <= {{16{dY20[15]}}, dY20};
            end
            15: begin end
            16: begin // det complete
                tri_det <= tri_det + dsp_p[31:0];
            end
            17: begin
                // Check determinant: skip degenerate
                if (tri_det == 0 || (tri_det > -16 && tri_det < 16)) begin
                    state <= S_IDLE;
                    setup_step <= 0;
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
            18: begin
                // Set M10K LUT read address (registered, 1-cycle latency)
                begin : recip_addr_set
                    reg [31:0] abs_d, norm;
                    abs_d = tri_det[31] ? -tri_det : tri_det;
                    norm = abs_d << tri_clz;
                    recip_rd_addr <= norm[30:23];
                end
            end
            19: begin
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
        S_TRI_GRAD: begin
            grad_sub <= grad_sub + 3'd1;
            case (grad_sub)
                3'd0: begin
                    dsp_a <= grad_dV10;
                    dsp_b <= grad_axis_b1;
                end
                3'd1: begin end
                3'd2: begin
                    grad_partial <= dsp_p[31:0];
                    dsp_a <= grad_dV20;
                    dsp_b <= grad_axis_b2;
                end
                3'd3: begin end
                3'd4: begin
                    // dx: cross = dV10*dY20 - dV20*dY10  → partial - dsp_p
                    // dy: cross = dV20*dX10 - dV10*dX20  → dsp_p - partial
                    dsp_a <= grad_idx[0] ? (dsp_p[31:0] - grad_partial)
                                         : (grad_partial - dsp_p[31:0]);
                    dsp_b <= {{16{1'b0}}, tri_recip};
                end
                3'd5: begin end
                3'd6: begin
                    case (grad_idx)
                        3'd0: grad_z_dx <= dsp_p >>> (6'd29 - tri_clz);
                        3'd1: grad_z_dy <= dsp_p >>> (6'd29 - tri_clz);
                        3'd2: grad_s_dx <= dsp_p >>> (6'd29 - tri_clz);
                        3'd3: grad_s_dy <= dsp_p >>> (6'd29 - tri_clz);
                        3'd4: grad_t_dx <= dsp_p >>> (6'd29 - tri_clz);
                        3'd5: grad_t_dy <= dsp_p >>> (6'd29 - tri_clz);
                        default: ;
                    endcase
                    grad_sub <= 0;
                    if (grad_idx == 3'd5) begin
                        // R gradient: set to 0 (per-vertex R uses v0 value).
                        // Full R gradient computation costs ~200 ALMs.
                        grad_r_dx <= 0; grad_r_dy <= 0;
                        state <= S_TRI_BBOX;
                    end else begin
                        grad_idx <= grad_idx + 3'd1;
                    end
                end
                default: ;
            endcase
        end

        // ============================================================
        // Triangle: Bounding box + clip to screen
        // ============================================================
        S_TRI_BBOX: begin
            // Compute bbox (registered) — decision made in S_TRI_ROW
            begin : bbox_calc
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
                tri_xmin <= (xmin < 0) ? 16'd0 : (xmin >>> 4);
                tri_xmax <= (xmax >>> 4 > 319) ? 16'd319 : (xmax >>> 4);
                tri_ymin <= (ymin < 0) ? 16'd0 : (ymin >>> 4);
                tri_ymax <= (ymax >>> 4 > 199) ? 16'd199 : (ymax >>> 4);
                state <= S_TRI_ROW;
            end
        end

        // ============================================================
        // Triangle: Initialise first row
        // ============================================================
        S_TRI_ROW: begin
            // Check for empty bbox (using registered values from S_TRI_BBOX)
            if (tri_xmin > tri_xmax || tri_ymin > tri_ymax) begin
                state <= S_IDLE;
            end else begin
            stat_triangles <= stat_triangles + 32'd1;
            tri_active <= 1;
            tri_cur_x <= tri_xmin;
            tri_cur_y <= tri_ymin;
            // Evaluate edge functions at (xmin*16, ymin*16) in 12.4 space
            begin : init_edges
                reg signed [31:0] px, py;
                px = {tri_xmin, 4'b0};  // pixel center in 12.4 (integer pixel << 4)
                py = {tri_ymin, 4'b0};
                tri_e[0] <= tri_A[0] * px + tri_B[0] * py + tri_C[0];
                tri_e[1] <= tri_A[1] * px + tri_B[1] * py + tri_C[1];
                tri_e[2] <= tri_A[2] * px + tri_B[2] * py + tri_C[2];
                tri_row_e[0] <= tri_A[0] * px + tri_B[0] * py + tri_C[0];
                tri_row_e[1] <= tri_A[1] * px + tri_B[1] * py + tri_C[1];
                tri_row_e[2] <= tri_A[2] * px + tri_B[2] * py + tri_C[2];
            end
            // Initialise attributes at v0 (simple — proper interpolation later)
            // Compute attributes at (xmin, ymin) by stepping from v0:
            // A(xmin,ymin) = A(v0) + (xmin*16 - v0.x) * dAdx + (ymin*16 - v0.y) * dAdy
            // Simplified: dx = xmin*16 - v0.x, dy = ymin*16 - v0.y (in 12.4 units)
            // Attributes in 16.16 fixed-point.
            // Init at v0 (bbox-origin offset computed during rasterisation
            // via incremental stepping — avoids costly fabric multiplies).
            tri_z     <= {v_z[0], 16'b0};
            tri_s     <= {v_s[0], 16'b0};
            tri_t     <= {v_t[0], 16'b0};
            tri_r     <= {8'b0, v_r[0][7:0], 16'b0};
            tri_row_z <= {v_z[0], 16'b0};
            tri_row_s <= {v_s[0], 16'b0};
            tri_row_t <= {v_t[0], 16'b0};
            tri_row_r <= {8'b0, v_r[0][7:0], 16'b0};
            // tri_ymin is registered (from S_TRI_BBOX), so this multiply has
            // registered inputs — Quartus can pipeline it through DSP.
            tri_fb_row_addr <= st_fb_addr + (tri_ymin * {{16{st_fb_stride[15]}}, st_fb_stride});
            tri_zb_row_addr <= st_zb_addr + (tri_ymin * 640);
            state <= S_TRI_PIX;
            end // else (bbox not empty)
        end

        // ============================================================
        // Triangle: Pixel test + step
        // ============================================================
        S_TRI_PIX: begin
            if (tri_cur_x > tri_xmax) begin
                // End of row
                if (tri_cur_y >= tri_ymax) begin
                    // Triangle done — flush FB
                    tri_active <= 0;
                    state <= S_FB_FLUSH;
                end else begin
                    // Next row: step Y, reset X
                    tri_cur_y <= tri_cur_y + 16'd1;
                    tri_cur_x <= tri_xmin;
                    // Step edge functions by B (Y step = 16 sub-pixels)
                    tri_row_e[0] <= tri_row_e[0] + (tri_B[0] <<< 4);
                    tri_row_e[1] <= tri_row_e[1] + (tri_B[1] <<< 4);
                    tri_row_e[2] <= tri_row_e[2] + (tri_B[2] <<< 4);
                    tri_e[0] <= tri_row_e[0] + (tri_B[0] <<< 4);
                    tri_e[1] <= tri_row_e[1] + (tri_B[1] <<< 4);
                    tri_e[2] <= tri_row_e[2] + (tri_B[2] <<< 4);
                    // Step row base addresses
                    tri_fb_row_addr <= tri_fb_row_addr + {{16{st_fb_stride[15]}}, st_fb_stride};
                    tri_zb_row_addr <= tri_zb_row_addr + 32'd640;
                    // Step attributes by Y gradient
                    tri_row_z <= tri_row_z + (grad_z_dy <<< 4);
                    tri_row_s <= tri_row_s + (grad_s_dy <<< 4);
                    tri_row_t <= tri_row_t + (grad_t_dy <<< 4);
                    tri_row_r <= tri_row_r + (grad_r_dy <<< 4);
                    tri_z <= tri_row_z + (grad_z_dy <<< 4);
                    tri_s <= tri_row_s + (grad_s_dy <<< 4);
                    tri_t <= tri_row_t + (grad_t_dy <<< 4);
                    tri_r <= tri_row_r + (grad_r_dy <<< 4);
                end
            end else if (!tri_e[0][31] && !tri_e[1][31] && !tri_e[2][31]) begin
                // Inside triangle — set up fragment, enter tex pipeline
                // Compute FB address
                sp_fb_addr <= tri_fb_row_addr + {{16{1'b0}}, tri_cur_x};
                sp_flags <= (st_depth_func != 0 ? 8'h18 : 8'h00) |
                            8'h01;  // COLORMAP (always for I8 triangle path)
                sp_light <= tri_r[23:16];  // integer part of 16.16 R
                sp_z_addr <= tri_zb_row_addr + {tri_cur_x, 1'b0};
                sp_zi <= tri_z;  // Z compare uses full 32-bit (upper 16 = integer Z)
                // Set up tex pipeline + load shared DSP for tex multiply
                tex_pipe_t_int  <= tri_t[31:16];
                tex_pipe_width  <= st_tex_width;
                tex_pipe_base   <= st_tex_addr;
                tex_pipe_s_int  <= tri_s[31:16];
                tex_pipe_shift_r <= 0;
                tex_pipe_mode   <= 1;
                state <= S_TRI_FRAG;
            end else begin
                // Outside triangle — step to next pixel
                tri_cur_x <= tri_cur_x + 16'd1;
                tri_e[0] <= tri_e[0] + (tri_A[0] <<< 4);  // X step = 16 sub-pixels
                tri_e[1] <= tri_e[1] + (tri_A[1] <<< 4);
                tri_e[2] <= tri_e[2] + (tri_A[2] <<< 4);
                tri_z <= tri_z + (grad_z_dx <<< 4);
                tri_s <= tri_s + (grad_s_dx <<< 4);
                tri_t <= tri_t + (grad_t_dx <<< 4);
                tri_r <= tri_r + (grad_r_dx <<< 4);
            end
        end

        // ============================================================
        // Triangle: Fragment setup → enter shared tex pipeline
        // ============================================================
        S_TRI_FRAG: begin
            // tex_pipe registers set in S_TRI_PIX, tex_addr_final available next cycle
            tex_req_valid <= 1;
            tex_req_addr  <= tex_addr_final[25:0];
            tex_req_wide  <= (st_tex_format == 1);  // RGB565
            // Step triangle rasteriser for when we return
            tri_cur_x <= tri_cur_x + 16'd1;
            tri_e[0] <= tri_e[0] + (tri_A[0] <<< 4);
            tri_e[1] <= tri_e[1] + (tri_A[1] <<< 4);
            tri_e[2] <= tri_e[2] + (tri_A[2] <<< 4);
            tri_z <= tri_z + (grad_z_dx <<< 4);
            tri_s <= tri_s + (grad_s_dx <<< 4);
            tri_t <= tri_t + (grad_t_dx <<< 4);
            tri_r <= tri_r + (grad_r_dx <<< 4);
            state <= S_SPAN_TEX_REQ;
        end
`endif // GPU_FEAT_TRIANGLE

        default: state <= S_IDLE;
        endcase
    end
end

// Colormap BRAM initialises to zero in Cyclone V M10K.
// CPU uploads data via MMIO before use.

endmodule
