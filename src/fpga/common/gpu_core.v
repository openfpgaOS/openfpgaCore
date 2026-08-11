//------------------------------------------------------------------------------
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileType: SOURCE
// SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
//------------------------------------------------------------------------------

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

module gpu_core #(
    // ----------------------------------------------------------------
    // Per-target feature gate: the 0x49 CMD_DRAW_PARAM_TRI param-triangle
    // path (header-carried planes + 3 vertices through the shared edge
    // walker).  When 0, the 0x49 decode term is constant 0, so the opcode
    // takes the unrecognised-command payload-drain no-op path.  When this and
    // INCLUDE_VERT_TRI and INCLUDE_PARAM_TRI_RECS are ALL 0, NO triangle
    // command remains and the shared gpu_edge_walker (tri_walker) loses every
    // producer — it constant-folds away entirely (the os25 reclaim).
    //
    // Must track axi_periph_slave's INCLUDE_PARAM_TRI (HW_FEATURES bit 19)
    // on the same target so apps don't submit 0x49 to a core that drains it.
    parameter INCLUDE_PARAM_TRI = 1,

    // ----------------------------------------------------------------
    // Per-target feature gate: hardware vertex-triangle plane derivation
    // (CMD_SET_TRI_STATE 0x4A + CMD_DRAW_VERT_TRI 0x4B + the S_TRI_DERIVE
    // derivation sub-FSM).  When 0, both opcodes take the same payload-drain
    // no-op path an unrecognised command takes, and every writer of the
    // vert-tri staging / derivation registers goes constant-inactive so
    // Quartus sweeps the ~2k ALMs of derivation logic (verified via the
    // pocket map "Estimate of Logic" prune proof — see
    // docs/gpu-utilization-handoff.md).
    //
    // SCOPE: this gates ONLY the 0x4A/0x4B vertex-tri path.  The 0x49
    // CMD_DRAW_PARAM_TRI param-triangle path is gated by INCLUDE_PARAM_TRI;
    // the shared gpu_edge_walker is present iff ANY triangle command is.
    parameter INCLUDE_VERT_TRI = 1,

    // os30/SM64 lean: 0x4F clip-tri is the only live producer of the S_XFORM
    // front-end (0x51 folds via GPU_XFORM_MAC=0, 0x52 via XFORM_RGB=0).  Gating
    // it (EXCLUDE_CLIP_TRI on os30) prunes S_XFORM + XF_RECIP/XF_PROJ + xf_* regs.
    parameter INCLUDE_CLIP_TRI = 1,

    // ----------------------------------------------------------------
    // Per-target feature gate: records-only param-tri (CMD_DRAW_PARAM_TRI_RECS
    // 0x4D).  When 0, the 0x4D decode term is constant 0, so the opcode takes
    // the unknown-command payload-drain no-op path and Quartus sweeps its
    // S_PAY_DATA routing arm and S_EXECUTE bring-up arm.  When BOTH this and
    // INCLUDE_VERT_TRI are 0 the 0x4A sticky persistents (tri_state_valid +
    // tri_state_clip_*) lose their last consumers (the 0x4D/0x4B EXECUTE
    // gates are spelled as the same constants) and sweep too; the 0x4A
    // decode itself stays ungated — its payload lands in the SHARED spanprod
    // staging, which the 0x48/0x49 paths keep alive on every target.
    //
    // Must track axi_periph_slave's INCLUDE_PARAM_TRI_RECS (HW_FEATURES bit
    // 22) on the same target so apps don't submit 0x4D to a core that drains it.
    parameter INCLUDE_PARAM_TRI_RECS = 1,

    // ----------------------------------------------------------------
    // Z read-window depth: 4 (default) or 1.
    //   4 — the z-test detour fills the whole 16-byte z line in one 4-beat
    //       burst and caches the sibling words (window hit + write snoop
    //       logic live; see the "Z read window" block below).
    //   1 — degenerates to the pre-window shape: a single-word fill
    //       (arlen 0) of exactly the word under test, behind the same drain
    //       barrier.  zw_valid is then never set non-zero, so the window-hit
    //       arm, the zw_word/zw_base storage and the fbwq write snoop are
    //       all constant-dead and Quartus sweeps them.
    // Only the values 1 and 4 are supported.
    parameter GPU_Z_READ_WINDOW = 4,

    // Truecolor-blend dst read-window size, in 32-bit words: 4, 2, or 1.
    // 4 = one aligned 4-beat burst serves 8 RGB565 pixels (fastest, ~128
    // FFs + the widest muxes); 2 = halves the storage/mux widths for
    // ALM-pressed variants at ~most of the win; 1 = the window prunes
    // entirely and the CB flow degenerates to the legacy barrier + one
    // single-word read per pixel (measurement/fallback).  Behavior is
    // byte-identical at every size — only the read amortisation changes.
    parameter GPU_CB_READ_WINDOW = 4,

    // ----------------------------------------------------------------
    // Edge-walker slope-divide layout, forwarded verbatim to
    // gpu_edge_walker's EW_PARALLEL_DIVS (see the header comment there).
    //   1 (default) — three parallel restoring dividers (~48 cy setup/tri).
    //   0 — single shared divider sequenced over the edges (~110 cy
    //       setup/tri); sheds the two extra dividers' registers and
    //       subtract/compare chains on area-constrained variants.
    // Quotients are bit-identical between the two configs, so emitted
    // spans — and therefore pixels — do not change.
    parameter GPU_EW_PARALLEL_DIVS = 1,

    // ----------------------------------------------------------------
    // Texture/cmap cache size, forwarded verbatim to gpu_tex_cache's
    // SET_BITS (2^SET_BITS sets x 16 B/line).  Default 10 = 16 KB — the
    // Pocket geometry, unchanged.  MiSTer passes 11 = 32 KB: with no
    // CRAM1 fast-tex chip every texel + cmap read there is SDRAM-backed
    // through this cache.  Size-only knob: hit/miss protocol, fill
    // machine and both port interfaces are identical at any value.  Block
    // cost doubles per step (replicated dual-read RAM — see the
    // gpu_tex_cache header before going bigger).
    parameter GPU_TEX_CACHE_SET_BITS = 10,

    // ----------------------------------------------------------------
    // 0x48 compact-direct lane form (4-word header + 7 words/lane, payload
    // 11/18/25/32 words) and the 4-lane spanprod_direct_* staging bank plus
    // the sp_fastpath relaxed continuation it enables.
    //
    // PRUNE GATE: when 0, the 0x48 size bound in S_DECODE rises from >=11
    // to >=33 (long-form record-style only) and spanprod_compact_direct is
    // a constant 0, so an 11-32-word 0x48 drains word-by-word through
    // S_PAY_DATA with no destination writes and retires at S_EXECUTE as the
    // unrecognised-command no-op — EXCEPT the sticky-state contract, which
    // is variant-invariant: the S_DECODE tri_state_valid clear fires on the
    // raw opcode/size ranges full hardware would decode (see S_DECODE).
    // With the flag constant 0 the compact loader branch, the
    // spanprod_cur_direct_* capture muxes, the direct arm of
    // spanprod_load_generated_span, sp_fastpath and its early-handoff /
    // tail-safe-bounce logic all fold; spanprod_direct_affine has no
    // remaining 1-writer so every direct-affine branch folds with it.
    // Pocket OS30 (Quake2: long-form 2D + 0x49 world + 0x4B alias) sets 0;
    // OS25 and MiSTer keep the default 1.  Must track axi_periph_slave's
    // INCLUDE_COMPACT_SPAN (HW_FEATURES bit 23) on the same target.
    parameter INCLUDE_COMPACT_SPAN = 1,

    // ----------------------------------------------------------------
    // CMD_DRAW_COLUMN_LIST (0x4C) decode.  The column loader is the compact
    // arms behind column_compact_idx_remap, so this feature REQUIRES
    // INCLUDE_COMPACT_SPAN — the effective gate below forces 0 when the
    // compact machinery is absent (a 0x4C then drains as a no-op, exactly
    // like a wrong-sized payload on full hardware).
    // Must track axi_periph_slave's INCLUDE_COLUMN_LIST (HW_FEATURES bit 21).
    parameter INCLUDE_COLUMN_LIST = 1,

    // ----------------------------------------------------------------
    // Dedicated fast texture memory present (a target-specific sync-burst
    // texture chip, routed externally by the wrapper).
    // When 1, the texture-cache line fills can be redirected off the SDRAM
    // read master onto the gpu_tex_mem_* fill master (toggled by the
    // GPU_TEX_MEM_ROUTE MMIO register), and the GPU exposes the upload regs
    // that drive the external fast-texture controller.  When 0, the
    // gpu_tex_mem_* outputs are held idle, the upload regs no-op, the route
    // register reads 0, and texture fills always stay on SDRAM (m_rd_*) —
    // every redirect-mux and upload-reg branch constant-folds away.  Must
    // track axi_periph_slave's INCLUDE_TEX_MEM (HW_FEATURES bit 25).
    parameter INCLUDE_TEX_MEM = 1,
    // Direct-color (truecolor RGB565) fragment path.  A per-surface flag
    // (0x4A/0x49 control word bit 7) selects a 16-bit texel -> RGB565
    // framebuffer write, bypassing the palookup colormap.  Default 0 forces
    // the truecolor flag constant 0 so the whole path prunes on non-truecolor
    // builds and the palettized pipeline stays byte-exact.
    parameter INCLUDE_DIRECT_COLOR = 0,
    // T1: GPU transform front-end TRUECOLOR draw (0x52 CMD_DRAW_XFORM_TRI_RGB).
    // Reuses the S_XFORM transform, then runs the full 7-attr 0x4E derive so the
    // transform path can produce per-vertex RGB565 Gouraud (not just the 0x51
    // single-light path).  Requires INCLUDE_VERT_TRI + INCLUDE_DIRECT_COLOR.
    // Default 0 prunes the 0x52 decode arm and its loader.
    parameter INCLUDE_XFORM_RGB = 0,
    // On-GPU matrix transform: the 64-bit M*v MAC + xf_M M10K + 0x51 + the 0x50
    // matrix load.  Default 1 (back-compat).  Set 0 (EXCLUDE_GPU_XFORM_MAC) when
    // the host pre-transforms and uses CMD_DRAW_CLIP_TRI (0x4F) — then the matrix
    // MAC + xf_M fold away and only XF_RECIP/XF_PROJ remain.
    parameter INCLUDE_GPU_XFORM_MAC = 1,
    // T3: GPU vertex cache + indexed draw (0x53/0x56 loads / 0x54
    // DRAW_INDEXED_TRI) — transform-once / draw-many.  Requires INCLUDE_XFORM_RGB.
    parameter INCLUDE_VTX_CACHE = 0,
    // T4: GPU per-vertex lighting (0x55 SET_LIGHT_STATE + normal transform +
    // N.L in S_XFORM generating per-vertex RGB565).  Requires INCLUDE_XFORM_RGB.
    parameter INCLUDE_GPU_LIGHT = 0,
    // Truecolor-only builds gate out the 8-bit palettized/colormap fragment
    // lane.  Default 1 keeps the palettized pipeline byte-exact; set 0 (os30/
    // SM64) to prune the colormap request/response pipe and the 8-bit lane.
    parameter INCLUDE_PALETTE = 1,
    // Truecolor texel*C+D combiner (HILITE/specular, 0x4A control bit 30).
    // Default 1.  Set 0 (EXCLUDE_COMBINE, SM64-dedicated os30) to const-0
    // spanprod_cd_combine so the texel*C cone + rgb565_cd_finish + additive-D
    // staging fold away (the same fold os25 gets via INCLUDE_DIRECT_COLOR=0).
    parameter INCLUDE_COMBINE = 1,
    // Param-span/tri Q29 dynamic-scale precision mode (perspective + zi shift).
    // Default 1.  Set 0 (os30/SM64) to const-0 spanprod_attr_q29 so the entire
    // Q29 cone folds: the q29_restore_z_saturating 64-bit barrel shift feeding
    // sp_q29_z_value_step (the GPU's #1 critical path) AND the sp_persp_q29_mode
    // perspective branches.  SM64 never arms Q29 (gpu_q29_word stub=0, vt_q29_en=0,
    // no param-span/tri path), so it is dead logic on os30; os25/mister keep it
    // (Quake param-span perspective precision).
    parameter INCLUDE_PARAM_SPAN_Q29 = 1
) (
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
    // AXI4 Read Master — fast texture memory fills.  When the
    // GPU_TEX_MEM_ROUTE MMIO register is set, the texture cache's line
    // fills are redirected off m_rd_* to THIS master, which the target
    // routes to its fast-texture controller (a dedicated sync-burst chip).
    // Same AXI fill contract as m_rd_* (arlen=3, no rready).  Independent
    // of m_rd_*, so fast-texture fills never contend with DMA/blend on
    // SDRAM.  Idle (held 0) when INCLUDE_TEX_MEM == 0.
    // ================================================================
    output wire        gpu_tex_mem_arvalid,
    input  wire        gpu_tex_mem_arready,
    output wire [31:0] gpu_tex_mem_araddr,
    output wire [7:0]  gpu_tex_mem_arlen,
    input  wire        gpu_tex_mem_rvalid,
    input  wire [31:0] gpu_tex_mem_rdata,
    input  wire        gpu_tex_mem_rlast,

    // ================================================================
    // Fast texture memory upload (MMIO).  reg 2 (0x08) = word address,
    // reg 15 (0x3C) = data word (kicks a word_wr to the fast-texture
    // controller and auto-increments the address).  reg 15 read =
    // upload-in-flight.  The GPU owns its texture store, so uploads route
    // through the GPU regs.  No-op when INCLUDE_TEX_MEM == 0.
    // ================================================================
    output reg         gpu_tex_mem_up_wr,
    output reg  [21:0] gpu_tex_mem_up_addr,
    output reg  [31:0] gpu_tex_mem_up_wdata,
    input  wire        gpu_tex_mem_up_busy,

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

// GPU byte-address datapath width.  The SDRAM slave (axi_sdram_slave.v)
// physically decodes only address bits [25:2] — a 64 MB (2^26-byte) space —
// and ignores [31:26].  Every framebuffer / z-buffer / texture byte-address
// register and adder in this core therefore carries 6 dead high bits when
// kept at 32.  Narrowing the address datapath to GPU_ADDR_W bits drops those
// dead bits; the mod-2^26 wraparound is identical to mod-2^32 truncated at
// the slave, so address compares and +N steps keep the same semantics.
// Where a narrowed address drives a 32-bit AXI port it is zero-padded with
// {6'b0, addr}; where a 32-bit source loads a narrowed reg it is sliced.
localparam GPU_ADDR_W = 26;

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
// 0x08  GPU_TEX_MEM_UP_ADDR W   Fast-texture upload word pointer
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
// 0x30  GPU_PALOOKUP_BASE W/R SDRAM byte base for 16x16KB colormap slots
// 0x34  GPU_DBG_WR_INFLIGHT R Low 4 bits = outstanding FB write responses
// 0x38  GPU_TEX_MEM_ROUTE   W   Fast-texture-memory route select
// 0x3C  GPU_TEX_MEM_UP_DATA W   Fast-texture upload word data
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

// Default matches the fixed address used by recent SDKs.  New SDKs program
// this register to app-owned, linker-accounted storage during of_gpu_init().
localparam [25:0] PALOOKUP_BASE_DEFAULT = 26'h3fc0000;  // byte offset 0x03FC0000
reg [25:0] palookup_base;

// When set, texture-cache line fills are redirected off the SDRAM read master
// (m_rd_*) to the fast-texture fill master (gpu_tex_mem_* master).  The
// texture's GPU byte address then indexes the fast-texture chip directly (the
// adapter masks the low 24 bits = the 16 MB chip), so firmware uploads each
// texture to the SAME byte offset the app addresses it at.  Toggle only while
// the GPU is idle (no in-flight tex fill) so the fill-response routing can't
// switch domains mid-burst.  Held constant 0 when INCLUDE_TEX_MEM == 0.
reg gpu_tex_mem_route;

// Fast-texture upload state.  gpu_tex_mem_up_ptr is the running word pointer
// set by reg 2 and auto-bumped per reg-15 data write; gpu_tex_mem_up_inflight
// is the upload-busy status (saw-busy gated on the controller's word_busy).
reg [21:0] gpu_tex_mem_up_ptr;
reg        gpu_tex_mem_up_inflight;
reg        gpu_tex_mem_up_busy_seen;

// ---- Doorbell-DMA pull from SDRAM into ring BRAM ----
// Latched on MMIO writes; consumed by the dedicated DMA FSM below.
// dma_words_left counts words remaining in the entire kick; the FSM
// re-issues an AR for each 16-beat sub-burst until the count drains.
reg [GPU_ADDR_W-1:0] dma_src_latched;  // SDRAM byte addr (from GPU_DMA_SRC)
reg [12:0] dma_len_latched;       // Total words to pull (max 4096; 13-bit fits)
localparam DMA_S_IDLE    = 2'd0;
localparam DMA_S_AR      = 2'd1;
localparam DMA_S_R       = 2'd2;
localparam DMA_S_PUBLISH = 2'd3;  // 1-cycle pulse to publish ring_wrptr
reg [1:0]  dma_state;
reg        dma_publish_wrptr;     // 1-cycle pulse, latched by ring_wrptr
reg [GPU_ADDR_W-1:0] dma_burst_addr;  // SDRAM byte addr of next sub-burst
reg [12:0] dma_words_left;        // Words remaining in the kick (across sub-bursts)

reg [GPU_ADDR_W-1:0] dma_desc_src [0:1];
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
        dma_src_latched <= {GPU_ADDR_W{1'b0}};
        dma_len_latched <= 13'd0;
        palookup_base   <= PALOOKUP_BASE_DEFAULT;
        gpu_tex_mem_route        <= 1'b0;
        gpu_tex_mem_up_wr        <= 1'b0;
        gpu_tex_mem_up_addr      <= 22'd0;
        gpu_tex_mem_up_wdata     <= 32'd0;
        gpu_tex_mem_up_ptr       <= 22'd0;
        gpu_tex_mem_up_inflight  <= 1'b0;
        gpu_tex_mem_up_busy_seen <= 1'b0;
    end else begin
        tex_flush_req <= 0;
        soft_reset    <= 0;
        ring_reset    <= 0;
        gpu_tex_mem_up_wr <= 1'b0;   // single-cycle word_wr pulse to the controller

        // upload-in-flight saw-busy: hold inflight from the data write until the
        // controller's word_busy has risen then fallen (so firmware polling
        // reg 15 never false-completes in the kick->busy gap).  Constant-dead
        // when INCLUDE_TEX_MEM == 0 (gpu_tex_mem_up_inflight is never set).
        if ((INCLUDE_TEX_MEM != 0) && gpu_tex_mem_up_inflight) begin
            if (gpu_tex_mem_up_busy)            gpu_tex_mem_up_busy_seen <= 1'b1;
            else if (gpu_tex_mem_up_busy_seen)  gpu_tex_mem_up_inflight  <= 1'b0;
        end

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
                4'd2: begin // GPU_TEX_MEM_UP_ADDR: set the upload word pointer
                    // No-op when INCLUDE_TEX_MEM == 0 (the ptr has no consumer).
                    if (INCLUDE_TEX_MEM != 0)
                        gpu_tex_mem_up_ptr <= reg_wdata[21:0];
                end
                4'd3: begin  // GPU_DMA_SRC
                    dma_src_latched <= reg_wdata[GPU_ADDR_W-1:0];
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
                4'd12: begin // GPU_PALOOKUP_BASE
                    palookup_base <= reg_wdata[25:0];
                end
                4'd14: begin // GPU_TEX_MEM_ROUTE: bit0 routes tex fills to fast tex
                    // No-op when INCLUDE_TEX_MEM == 0 (route stays constant 0,
                    // so the redirect mux always selects the SDRAM master).
                    if (INCLUDE_TEX_MEM != 0)
                        gpu_tex_mem_route <= reg_wdata[0];
                end
                4'd15: begin // GPU_TEX_MEM_UP_DATA: write data word, auto-inc ptr
                    // No-op when INCLUDE_TEX_MEM == 0 (no fast-tex controller).
                    if (INCLUDE_TEX_MEM != 0) begin
                        gpu_tex_mem_up_addr      <= gpu_tex_mem_up_ptr;
                        gpu_tex_mem_up_wdata     <= reg_wdata;
                        gpu_tex_mem_up_wr        <= 1'b1;
                        gpu_tex_mem_up_inflight  <= 1'b1;
                        gpu_tex_mem_up_busy_seen <= 1'b0;
                        gpu_tex_mem_up_ptr       <= gpu_tex_mem_up_ptr + 22'd1;
                    end
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
        4'd12:   reg_rdata = {6'b0, palookup_base};
        // Compact current-inflight readback for the write-balance test.
        4'd13:   reg_rdata = {28'b0, m_wr_inflight};
        // reg14 reads the route bit (0 when INCLUDE_TEX_MEM == 0);
        // reg15 reads upload-busy (also 0 when fast tex is absent).
        4'd14:   reg_rdata = {31'b0, (INCLUDE_TEX_MEM != 0) ? gpu_tex_mem_route       : 1'b0};
        4'd15:   reg_rdata = {31'b0, (INCLUDE_TEX_MEM != 0) ? gpu_tex_mem_up_inflight : 1'b0};  // upload busy
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
reg [1:0]  transluc_cache_valid;
reg [13:0] transluc_cache_addr [0:1];
reg [15:0] transluc_cache_data [0:1];
reg        transluc_cache_replace;

wire transluc_sram_lookup_ready =
    (lutsram_state == LUTSRAM_IDLE) && !sram_busy;

assign transluc_upload_busy = (lutsram_state != LUTSRAM_IDLE);

`ifndef INCLUDE_TRANSLUC
// Targets without INCLUDE_TRANSLUC (Pocket OS30): remove the transluc[] LUT
// upload/lookup FSM and its 2-entry transluc_cache entirely.  The GPU's
// external SRAM port (used only for the blend LUT) goes idle and the cache
// registers hold 0, so transluc_cache_hit always reads 0 in the (now
// unreachable, chokepointed) FBSS_BLEND states.  INCLUDE_TRANSLUC
// keeps the full LUT upload window + cache below.
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
        transluc_cache_valid    <= 2'b0;
        transluc_cache_addr[0]  <= 14'd0;
        transluc_cache_addr[1]  <= 14'd0;
        transluc_cache_data[0]  <= 16'd0;
        transluc_cache_data[1]  <= 16'd0;
        transluc_cache_replace  <= 1'b0;
    end else begin
        // SRAM port permanently idle; cache held at 0.
        sram_rd      <= 1'b0;
        sram_wr      <= 1'b0;
        sram_rd_half <= 1'b0;
        sram_rd_hi   <= 1'b0;
        sram_wstrb   <= 4'd0;
    end
end
`else
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
        transluc_cache_valid    <= 2'b0;
        transluc_cache_addr[0]  <= 14'd0;
        transluc_cache_addr[1]  <= 14'd0;
        transluc_cache_data[0]  <= 16'd0;
        transluc_cache_data[1]  <= 16'd0;
        transluc_cache_replace  <= 1'b0;
    end else begin
        sram_rd    <= 1'b0;
        sram_wr    <= 1'b0;
        sram_rd_half <= 1'b0;
        sram_rd_hi   <= 1'b0;
        sram_wstrb <= 4'd0;

        if (reg_wr && reg_addr == 4'd8) begin
            transluc_wr_addr <= reg_wdata[14:0];
            transluc_cache_valid <= 2'b0;
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
                    transluc_cache_valid <= 2'b0;
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
                    transluc_cache_replace <= ~transluc_cache_replace;
                    lutsram_state         <= LUTSRAM_IDLE;
                end
            end

            default: begin
                lutsram_state <= LUTSRAM_IDLE;
            end
        endcase
    end
end
`endif // INCLUDE_TRANSLUC

// Cmap read path through gpu_tex_cache port B.  At the p1→p2 shift, the
// SDRAM byte address for the fragment's cmap lookup is staged into the
// pending-request slot (cmap_pending_*); the registered request is
// presented to tex_cache port B while the fragment sits in p2, and the
// byte response is consumed one stage later when the fragment retires
// p2b→p3.  Cache misses stall the pipeline via fp_pipe_stall's
// cmap_pipe_wait term until the fill completes.
//
// palookup_base + (colormap_id << 14) anchors the slot for fragments.
// Direct-affine records carry a per-lane slot; parametric spans carry a
// per-command slot.  The per-pixel term (light << 8 | texel) indexes
// within the slot.  Slot encoding matches the SDK's
// of_gpu_palookup_upload() on the host side.
reg        cmap_pending_valid;
reg [25:0] cmap_pending_addr;
wire [11:0] cmap_slot_page = palookup_base[25:14] + {8'b0, p1_colormap_id};
wire [25:0] cmap_slot_addr = {cmap_slot_page, 14'b0};

// Port B response-queue protocol (see gpu_tex_cache.v): the cache holds
// up to TWO accepted, unconsumed responses in pixel order (a held skid
// in front of the pipe slot), always presenting the OLDEST on
// resp_*_b.  cmap_resp_pop_b pulses on the p2b→p3 shift that captures
// the response, retiring the head of the queue.  Because an accept can
// no longer destroy a still-needed older response (the skid preserves
// it), the registered request is presented unconditionally and
// back-to-back accepts sustain 1 px/cycle on lit spans.
//
// Historical context — two prior off-by-one byte-lane bugs in this
// path (pipe_addr_b drifting past the pixel waiting in p2b, surfacing
// as triCK_y1_x2 / w300_r1_px6 / Duke3D artifacts) were caused by
// issuing combinational requests while the pipe was frozen.  The
// structural fixes here are (a) requests only ENTER the pending slot on
// an actual p1→p2 shift, and (b) the cache serves responses strictly
// oldest-first with an explicit consume pulse, so a stalled p2b's
// response can never be displaced by a younger request.
//
// Stall terms:
//   * p2b holds a cmap fragment whose response isn't at the queue head
//     yet (miss in flight, or its request is still pending unaccepted).
//   * p1 wants to stage a new request but the pending slot is occupied
//     and the cache isn't accepting it this cycle (queue full or
//     mid-fill) — shifting would overwrite the unaccepted request.
wire        cmap_resp_valid_b;
wire        fp_pipe_shift_blocked;
wire        cmap_req_ready_b;
wire        cmap_pipe_wait = (p2b_valid && p2b_flags[SPAN_COLORMAP]
                              && !cmap_resp_valid_b)
                          || (p1_valid && p1_flags[SPAN_COLORMAP]
                              && cmap_pending_valid && !cmap_req_ready_b);
wire        cmap_req_valid_b = cmap_pending_valid;
wire [25:0] cmap_req_addr_b = cmap_pending_addr;
wire [15:0] cmap_resp_data_b;
wire [7:0]  cmap_rd_data = cmap_resp_data_b[7:0];
wire        cmap_resp_pop_b = (state == S_FRAG_PIPE)
                           && !(fp_pipe_shift_blocked || cmap_pipe_wait)
                           && p2b_valid && p2b_flags[SPAN_COLORMAP];

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

// ---- DSP operand routing (B7 collapse) ----
// ONE shared operand register pair per DSP, written directly by every
// launch site; the former per-domain banks (sp_dsp_*/pss_dsp_*/
// drv_dsp_*) and the 3:1 owner-select mux are gone.  Bit-exact because
// the three writer domains are keyed on mutually exclusive FSM states
// (spanprod: S_SPANPROD_SETUP/MUL_WAIT/CAPTURE arms; PSS: persp_pss
// arms inside S_FRAG_PIPE; derive+xform: S_TRI_DERIVE/S_XFORM arms),
// every product is consumed exactly two cycles after its own domain's
// launch with the FSM confined to that domain's states in between,
// and every S_FRAG_PIPE exit is !persp_active-gated — the same cycle
// that makes an exit eligible also re-idles persp_pss — so no foreign
// write can interpose between a launch and its capture.  The operand
// regs self-clear each cycle (always-on housekeeping block) exactly
// as the domain banks did, so an idle cycle contributes the same
// 0-operand product either way.


// Reciprocal LUT: 1024 × 16-bit in M10K (Phase 4b — widened from 256 to
// 1024 to give 10-bit input precision instead of 8-bit, the simpler of
// the two precision options the 2026-04-25 bug report called out).
// 1024×16 doesn't fit in a single M10K (10 Kbits), so this synthesises
// to 2 M10K blocks; FB/cmap/etc. unchanged.  Registered read port: set
// recip_rd_addr, result in recip_rd_data next cycle.
// Stored value: recip_lut[i] = 0x1000000 / (1024 + i) → 16-bit Q14
// (i.e. recip_lut[0] = 16384 = 1.0 in Q14, recip_lut[1023] ≈ 0.501).
// LUT output Q-format unchanged — the PSS_RECIP_SHIFT funnel slice
// offset (13) is independent of the input bit-width, so no shift change.
(* ramstyle = "M10K" *) reg [15:0] recip_lut [0:1023];
reg [9:0]  recip_rd_addr;
reg [15:0] recip_rd_data;
always @(posedge clk) recip_rd_data <= recip_lut[recip_rd_addr];
// T3: vertex-cache registered read port (M10K).  Single read port — 0x54 reads
// its 3 indices sequentially in S_VCREAD.  Omitted entirely when the cache is off.
generate if (INCLUDE_VTX_CACHE != 0) begin : g_vcache_rd
    always @(posedge clk) vc_q <= vc_mem[vc_raddr];
end endgenerate
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

// Port B is wired to the cmap read path: cmap_pending_addr holds the
// per-pixel SDRAM byte address, staged at the p1→p2 shift.  Tex_cache
// returns the byte combinationally in resp_data_b[7:0] (req_wide_b = 0
// → byte mode), oldest-outstanding first; cmap_resp_pop_b retires the
// head response on the p2b→p3 capture.  Misses route through the
// shared AXI fill machine; the consumer-side stall is enforced by
// fp_pipe_stall's cmap_pipe_wait term.
gpu_tex_cache #(
    .SET_BITS(GPU_TEX_CACHE_SET_BITS)
) tex_cache (
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
    .req_addr_b(cmap_req_addr_b),
    .req_wide_b(1'b0),
    .resp_valid_b(cmap_resp_valid_b),
    .resp_data_b(cmap_resp_data_b),
    .resp_pop_b(cmap_resp_pop_b),
    .resp_flush_b(soft_reset),
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
wire blend_owns_m0  = (fbss == FBSS_ZTEST_R_WAIT)
                   || (fbss == FBSS_BLEND_R_WAIT)
                   || (fbss == FBSS_CB_FILLR);
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
// Texture-cache fills go to fast texture memory (gpu_tex_mem_* master) when
// GPU_TEX_MEM_ROUTE is set; otherwise they share the SDRAM read master below
// exactly as before.  DMA and blend ALWAYS stay on SDRAM.  When redirected, the
// SDRAM tex fallthrough is suppressed and the tex-cache's fill responses come
// from the fast-texture adapter.  (tex_route==0 collapses every branch below to
// the original behaviour; when INCLUDE_TEX_MEM == 0, gpu_tex_mem_route is held
// constant 0, so tex_route is a constant 0 and the whole redirect folds away.)
wire tex_route = (INCLUDE_TEX_MEM != 0) ? gpu_tex_mem_route : 1'b0;

assign m_rd_arvalid    = dma_owns_ar   ? dma_arvalid
                       : blend_owns_m0 ? blend_arvalid
                       : tex_route     ? 1'b0
                       :                 tex_axi_arvalid;
assign m_rd_araddr     = dma_owns_ar   ? dma_araddr
                       : blend_owns_m0 ? {{(32-GPU_ADDR_W){1'b0}}, blend_araddr}
                       :                 tex_axi_araddr;
assign m_rd_arlen      = dma_owns_ar   ? dma_arlen
                       : blend_owns_m0 ? {6'b0, blend_arlen_r}
                       :                 tex_axi_arlen;

// Fast-texture fill master — driven only when redirected.  Independent of the
// SDRAM bus, so a fast-texture fill can overlap a DMA/blend SDRAM read.  Held
// idle (arvalid 0) when INCLUDE_TEX_MEM == 0 (tex_route is then constant 0).
assign gpu_tex_mem_arvalid = tex_route ? tex_axi_arvalid : 1'b0;
assign gpu_tex_mem_araddr  = tex_axi_araddr;
assign gpu_tex_mem_arlen   = tex_axi_arlen;

// Texture-cache fill responses: from fast texture memory when redirected, else
// the SDRAM read master (gated by DMA/blend ownership, exactly as before).
// With INCLUDE_TEX_MEM == 0, tex_route is constant 0 and every fill response
// folds to the SDRAM master.
assign tex_axi_arready = tex_route ? gpu_tex_mem_arready
                       : (dma_owns_ar || blend_owns_m0) ? 1'b0 : m_rd_arready;
assign tex_axi_rvalid  = tex_route ? gpu_tex_mem_rvalid
                       : (dma_owns_r  || blend_owns_m0) ? 1'b0 : m_rd_rvalid;
assign tex_axi_rdata   = tex_route ? gpu_tex_mem_rdata : m_rd_rdata;
assign tex_axi_rlast   = tex_route ? gpu_tex_mem_rlast
                       : (dma_owns_r  || blend_owns_m0) ? 1'b0 : m_rd_rlast;

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
        dma_burst_addr    <= {GPU_ADDR_W{1'b0}};
        dma_words_left    <= 13'd0;
        dma_arvalid       <= 1'b0;
        dma_araddr        <= 32'd0;
        dma_arlen         <= 8'd0;
        dma_publish_wrptr <= 1'b0;
        dma_starve_count  <= 10'd0;
        dma_desc_src[0]   <= {GPU_ADDR_W{1'b0}};
        dma_desc_src[1]   <= {GPU_ADDR_W{1'b0}};
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
                end else begin
                    dma_arlen       <= dma_words_left[7:0] - 8'd1;
                end
                dma_araddr  <= {{(32-GPU_ADDR_W){1'b0}}, dma_burst_addr};
                dma_arvalid <= 1'b1;
            end
            // else: bus is busy and starvation bound not yet hit — wait
            // without asserting AR.
        end

        DMA_S_R: begin
            dma_arvalid <= 1'b0;
            if (m_rd_rvalid) begin
                dma_words_left  <= dma_words_left  - 13'd1;
                dma_burst_addr  <= dma_burst_addr  + {{(GPU_ADDR_W-3){1'b0}}, 3'd4};
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
    end else if (tex_route) begin
        // Tex fills are on the fast-texture master, never the SDRAM bus, so
        // the doorbell-DMA bus-idle test must not count them.
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
// Triangle form of the param-span command: payload words 0-30 are the
// identical param-span header (planes/control/z); words 31-32 carry the
// clip rect and words 33-35 the three vertices (x Q12.4, y integer).
// The edge walker generates the {u,v,count} records the CPU would have
// packed after word 30.
localparam CMD_DRAW_PARAM_TRI         = 8'h49;
//
// CMD_SET_TRI_STATE (0x4A) + CMD_DRAW_VERT_TRI (0x4B): hardware triangle
// plane derivation.  0x4A latches a sticky surface/control bank (mirroring an
// 0x49 PERSP header field-for-field); each 0x4B then carries only raw
// per-vertex {x,y,s,t,zi,light}, and the GPU derives the four attribute planes
// (szi, tzi, zi, light) the 0x49 client used to solve in software.  0x49 stays
// the byte-exact fallback ABI; 0x4B without a prior 0x4A is a no-op.
//
// CONTRACT CHANGE (staging-dedup, 2026-06): 0x4A no longer keeps a dedicated
//   sticky surface/control bank.  Its payload words are decoded DIRECTLY into
//   the SHARED spanprod staging regs (the same regs an 0x49 PERSP header /
//   0x48 span-list header fill), and only the clip rect (4×16b) + a
//   tri_state_valid flag persist.  Consequences for the client:
//     * The sticky surface state now LIVES in the shared param staging.  A
//       subsequent 0x48 DRAW_PARAM_SPAN_LIST or 0x49 DRAW_PARAM_TRI header
//       OVERWRITES it.  After interleaving an 0x48/0x49 between an 0x4A and a
//       later 0x4B, the client MUST re-issue 0x4A before more 0x4B draws.
//     * To make a stale-state 0x4B a guarded no-op (not garbage) instead of
//       reusing the overwritten staging, tri_state_valid is CLEARED whenever an
//       0x48/0x49 header decodes.  A 0x4B with cleared tri_state_valid drains
//       and retires with no draw, exactly like a 0x4B with no prior 0x4A.
//     * Per-0x4B attribute-plane overwrites by the derivation FSM are fine and
//       expected: each 0x4B rederives all four attr planes (szi/tzi/zi/light)
//       into the staging from scratch, so back-to-back 0x4B after a single 0x4A
//       still works (only the surface/control/clamp/z fields are sticky, and
//       those the derivation never touches).
//
// 0x4A payload (16 words; tri_state_valid set on accept, cleared by soft_reset
//   AND by any 0x48/0x49 header decode).  Layout is field-compatible with the
//   0x49 header where the formats match (control word == 0x49 word 7
//   semantics); each word is decoded into the shared spanprod staging reg the
//   matching 0x49 header field would land in:
//     w0  = fb_base            (byte addr, like 0x49 w0)
//     w1  = fb_major_step      (signed, like 0x49 w1)
//     w2  = fb_minor_step      (signed, like 0x49 w2)
//     w3  = tex_addr           (byte addr, like 0x49 w3)
//     w4  = tex_width[15:0]    (like 0x49 w4)
//     w5  = {tex_h_mask[31:16], tex_w_mask[15:0]}  (packed; 0x49 split w5/w6)
//     w6  = control            (== 0x49 w7: flags[7:0], colormap_id[11:8],
//                               attr_mode[15:12], span_axis[16],
//                               z_mode[25:24]; record fmt nibble forced
//                               to U16V16_COUNT16 / attr_mode PERSP by 0x4B)
//     w7  = clamp0_min   (s clamp lo, like 0x49 w20)
//     w8  = clamp0_max   (s clamp hi, like 0x49 w21)
//     w9  = clamp1_min   (t clamp lo, like 0x49 w22)
//     w10 = clamp1_max   (t clamp hi, like 0x49 w23)
//     w11 = z_base       (byte addr, like 0x49 w26)
//     w12 = z_major_step (signed, like 0x49 w27)
//     w13 = z_minor_step (signed, like 0x49 w28)
//     w14 = {clip_x1[31:16], clip_x0[15:0]}  (same pack as 0x49 w31)
//     w15 = {clip_y1[31:16], clip_y0[15:0]}  (same pack as 0x49 w32)
//
// 0x4B payload (14 words, one triangle).  Same packed vertex format and fill
//   convention as 0x49 (x Q12.4, y int; ceil both edges, left-closed
//   right-open):
//     w0  = {v0_y[31:16], v0_x[15:0]}   (x Q12.4, y int)
//     w1  = {v1_y[31:16], v1_x[15:0]}
//     w2  = {v2_y[31:16], v2_x[15:0]}
//     w3  = s0   (Q16.16 raw texel s, NOT pre-multiplied by zi)
//     w4  = s1
//     w5  = s2
//     w6  = t0   (Q16.16 raw texel t)
//     w7  = t1
//     w8  = t2
//     w9  = zi0  (Q16.16, the scale the z window consumes)
//     w10 = zi1
//     w11 = zi2
//     w12 = packed per-vertex light rows {l2[17:12], l1[11:6], l0[5:0]} (Q6)
//     w13 = reserved / 0
//   On payload: the szi/tzi numerator products are folded INTO payload arrival
//   (when zi_k lands at w9-11 the DSPs — idle during S_PAY_DATA — launch
//   s_k*zi_k / t_k*zi_k and capture into the dv_szi/dv_tzi storage), so the
//   derivation FSM no longer needs its per-vertex product states.
//   On EXECUTE: the spanprod staging regs already hold the 0x4A surface/control
//   state (decoded directly in S_PAY_DATA, not copied), so just derive the four
//   attribute planes (szi=s*zi, tzi=t*zi, zi, light) from the raw verts, start
//   the walker, and run the derivation FSM.  Rejected (payload drained, no
//   draw) if tri_state_valid is clear or the payload is the wrong size.
localparam CMD_SET_TRI_STATE          = 8'h4A;
localparam CMD_DRAW_VERT_TRI          = 8'h4B;
// 0x4E DRAW_VERT_TRI_RGB: like 0x4B but words 12-14 carry per-vertex RGB565
// colour (replacing the single packed light word); the truecolor fragment
// modulate becomes per-channel Gouraud (texel x vtxRGB).  Gated on
// INCLUDE_VERT_TRI && INCLUDE_DIRECT_COLOR — prunes everywhere but os30.
localparam CMD_DRAW_VERT_TRI_RGB      = 8'h4E;

// CMD_DRAW_PARAM_TRI_RECS (0x4D): records-only variant of CMD_DRAW_PARAM_TRI.
// A full 0x49 re-sends ~21 words of constant surface/control/clamp/z/clip
// state with every triangle; this variant carries ONLY the per-triangle
// payload and reuses the 0x4A sticky staging for everything else.  Available
// on EVERY target — the 0x4A sticky decode and this path are independent of
// INCLUDE_VERT_TRI (only the 0x4B plane DERIVATION needs that hardware), so
// the Pocket gets the header-dedup win even with vert-tri gated out.
//
// Payload (16 words):
//   w0..w11  = 0x49 header words 8..19 (attr planes ×3 + light plane),
//              decoded through the identical loader arms
//   w12      = 0x49 header word 30 (q29_attr_shift; same validation —
//              a malformed word clears spanprod_header_supported exactly
//              like 0x49 w30 would)
//   w13..w15 = vertices, same packing as 0x49 w33..35
//              ({y int [31:16], x Q12.4 [15:0]})
//
// Sticky reuse contract (mirrors 0x4B's):
//   * REQUIRES tri_state_valid (a prior 0x4A, not invalidated since).
//     Without it the payload drains and the command retires as a no-op.
//   * Does NOT clear tri_state_valid and does not touch the sticky
//     surface/control/clamp/z fields — back-to-back 0x4D draws after one
//     0x4A work, and 0x4B/0x4D can interleave under the same 0x4A.
//   * Clip rect comes from the 0x4A (tri_state_clip_*).
//   * The attr2 clamps (0x49 w24/w25) are NOT in the 0x4A payload; a 0x4D
//     inherits whatever the staging holds.  Clients that need attr2 clamps
//     must use full 0x49.
//   * An interleaved 0x48/0x49/0x4C overwrites the shared staging and
//     clears tri_state_valid — re-issue 0x4A before more 0x4D (or 0x4B)
//     draws, exactly per the 0x4A CONTRACT CHANGE rules above.
// Advertised in HW_FEATURES bit 22 (OF_HW_GPU_PARAM_TRI_RECS).
localparam CMD_DRAW_PARAM_TRI_RECS    = 8'h4D;
localparam CMD_SET_OBJECT_STATE       = 8'h50;  // sticky transform matrix + proj consts
localparam CMD_DRAW_XFORM_TRI         = 8'h51;  // raw verts -> GPU transform+project+derive
localparam CMD_DRAW_XFORM_TRI_RGB     = 8'h52;  // T1: as 0x51 + per-vertex RGB565 truecolor
localparam CMD_LOAD_VERTS             = 8'h53;  // T3: transform N raw verts -> vertex cache
localparam CMD_DRAW_INDEXED_TRI       = 8'h54;  // T3: 3 cache indices -> derive (draw-many)
localparam CMD_SET_LIGHT_STATE        = 8'h55;  // T4: sticky light dir/colour/ambient
localparam CMD_LOAD_VERT_LIT          = 8'h57;  // T4: transform+light one vert -> cache
localparam CMD_LOAD_VERT_CLIP         = 8'h56;  // T3: park ONE pre-transformed clip-space
                                                //   vert {x,y,w} in the cache (XF_CLIP_FEED,
                                                //   skips the MAC entirely) -- the cache-load
                                                //   analogue of 0x4F, for hosts that do their
                                                //   own model/view/projection (Quake2).
localparam CMD_DRAW_CLIP_TRI          = 8'h4F;  // clip-space feed: CPU sends M*v clip {x,y,w},
                                                //   GPU does ONLY recip+project (skips the MAC).
                                                //   Wire-identical to 0x52 (18w), truecolor.

// CMD_DRAW_COLUMN_LIST (0x4C): bandwidth-optimised variant of the 0x48
// direct-affine span list for VERTICAL 1-wide textured columns (Doom/Wolf3D/
// Duke3D walls + sprites), where the s (u) coordinate and its per-pixel step
// are ALWAYS 0 — a column samples one texture column straight down.  Dropping
// the constant s/sstep words shrinks each lane record from 7 words to 5,
// cutting CPU→GPU command traffic ~28% for column-heavy renderers.
//
// Same 4-word header as the 0x48 direct-affine variant:
//   w0 = {count_nibble[31:28], flags[27:20]}  (lane count 1..4, span flags)
//   w1 = tex_width[15:0]
//   w2 = {tex_h_mask[31:16], tex_w_mask[15:0]}
//   w3 = fb_step  (byte step per pixel inside each column == fb_minor_step)
// Then a 5-word lane record per lane (4 lanes native, same cap as 0x48):
//   +0 fb_addr   (==0x48 lane +0)
//   +1 tex_addr  (==0x48 lane +1)
//   +2 {colormap_id[31:28], light[21:16], count[15:0]}  (==0x48 lane +2)
//   +3 t         (==0x48 lane +4 — the v coordinate)
//   +4 tstep     (==0x48 lane +6 — the per-pixel v step)
// The decoder FORCES spanprod_direct_s[lane]=0 and spanprod_direct_sstep[lane]
// =0 internally (the dropped 0x48 +3/+5 words), so everything downstream
// (spanprod → fragment pipe p0a..p3 → fbwq/tex) is BYTE-IDENTICAL to a 0x48
// direct-affine column the client sent with s=0/sstep=0.  This is a pure
// command-traffic optimisation: ZERO pixel difference vs the 0x48 equivalent.
localparam CMD_DRAW_COLUMN_LIST       = 8'h4C;
// ════════════════════════════════════════════════════════════════════════
// Module dependency resolver (single source of truth — see docs/MODULES.md).
// A module's cone is built iff it is requested AND its prerequisites are
// present; centralizing the dependency edges here keeps a build config from
// desyncing (e.g. enabling combine without the truecolor datapath it needs).
// These edges are AND-down (a leaf folds when its prereq is absent) and are
// behaviour-identical to the per-site guards they replace.  The additive
// pull-up form (a requested leaf forcing its prereq ON, e.g.
// EFF_TRUECOLOR |= INCLUDE_COMBINE) lands together with the INCLUDE-polarity
// flip + default→0 change, so it cannot mis-fire on today's default=1 params.
// INCLUDE_TRI_WALKER (near the edge-walker instance) and INCLUDE_COLUMN_LIST_EFF
// (just below) are the pre-existing resolver edges.
// ════════════════════════════════════════════════════════════════════════
localparam EFF_TRUECOLOR = (INCLUDE_DIRECT_COLOR != 0);              // truecolor RGB565 fragment datapath
localparam EFF_COMBINE   = EFF_TRUECOLOR && (INCLUDE_COMBINE != 0); // texel*C+D HILITE: needs truecolor
localparam EFF_Q29       = (INCLUDE_PARAM_SPAN_Q29 != 0);           // param-span/tri Q29 dynamic-scale precision (folds the z-step cone when 0)

// 0x4C delegates its payload to the 0x48 compact-direct loader arms, so the
// column decode can only exist where the compact machinery does.  Deriving
// the effective gate here (instead of trusting the instantiation) makes the
// broken combination INCLUDE_COLUMN_LIST=1 && INCLUDE_COMPACT_SPAN=0
// degrade to a drained no-op instead of a dead loader.
localparam INCLUDE_COLUMN_LIST_EFF =
    (INCLUDE_COMPACT_SPAN != 0) ? INCLUDE_COLUMN_LIST : 0;

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
reg [GPU_ADDR_W-1:0] sp_fb_addr;
reg [GPU_ADDR_W-1:0] sp_tex_addr;
reg signed [31:0] sp_s, sp_t;
reg signed [31:0] sp_sstep, sp_tstep;
// Per-pixel running 2nd-difference (curvature) for the QUADRATIC perspective
// sub-segment interpolation: sp_sstep += sp_scc each pixel so s/t trace a
// parabola through three perspective-exact anchors instead of an affine chord,
// killing the residual two-triangle texture seam.  CONSTANT within a segment,
// loaded at slot swap.  Forced 0 on the affine / palettized / non-truecolor
// path, so sp_sstep += sp_scc is a bit-exact hold there.
reg signed [31:0] sp_scc, sp_tcc;
reg [15:0] sp_count;
// Phase 4d — Gouraud-capable light.  Palookups have 64 shade rows, so
// light is carried as signed Q6.16 and the fragment pipe sees bits [21:16].
// sp_light_step is signed Q6.16, the per-pixel x delta.  Direct-affine
// records set sp_light_step = 0 for flat lighting.
reg signed [23:0] sp_light_q;
reg signed [23:0] sp_light_step;
wire [5:0]        sp_light = sp_light_q[21:16];
// Truecolor RGB Gouraud red/blue per-pixel accumulators (green = sp_light).
// 5-bit channels read from [20:16].
reg signed [23:0] sp_R_q, sp_R_step;
reg signed [23:0] sp_B_q, sp_B_step;
wire [4:0]        sp_R = sp_R_q[20:16];
wire [4:0]        sp_B = sp_B_q[20:16];
// Combiner D (additive) per-pixel accumulators — second per-vertex RGB triple
// interpolated identically to R/light/B; consumed by rgb565_cd_finish.  Kept in
// ALM (ramstyle logic) — the texel*C+D pipeline must not spill to M10K.
(* ramstyle = "logic" *) reg signed [23:0] sp_Dr_q, sp_Dr_step;
(* ramstyle = "logic" *) reg signed [23:0] sp_Dg_q, sp_Dg_step;
(* ramstyle = "logic" *) reg signed [23:0] sp_Db_q, sp_Db_step;
wire [4:0]        sp_Dr = sp_Dr_q[20:16];
wire [5:0]        sp_Dg = sp_Dg_q[21:16];
wire [4:0]        sp_Db = sp_Db_q[20:16];
reg [3:0]  sp_flags;
// Round-2 early record handoff (cheap B1 subset): set once at EMIT.
// True only for the opaque non-z direct fastpath — direct-affine record
// (which forces z write/test off and persp inactive) with SPAN_TRANSLUC
// clear.  Only such records may take the relaxed inter-record
// continuation in the S_FRAG_PIPE drain detector.
reg        sp_fastpath;
// Per-span colormap_id for direct-affine and parametric dispatch.
reg [3:0]  sp_colormap_id;
// Address delta (byte stride).  Narrowed to the address datapath width;
// signed semantics are preserved because mod-2^26 wraparound matches the
// SDRAM-visible address arithmetic.
reg signed [GPU_ADDR_W-1:0] sp_fb_stride;
reg [15:0] sp_tex_width;
reg        sp_truecolor;       // sticky: this surface renders direct RGB565
reg        sp_rgb;             // sticky: this surface modulates by per-vertex RGB (0x4E)
reg        sp_blend;           // sticky: src-over alpha blend (OF_GPU_SPAN_BLEND)
reg [7:0]  sp_const_alpha;     // sticky: per-surface src alpha (0x4A word 16)
reg [6:0]  sp_a6;              // sticky: precomputed blend weight 0..64 (off the per-pixel path)
// POT wrap masks: (sp_s[31:16] & sp_tex_w_mask) and
// (sp_t[31:16] & sp_tex_h_mask)
// before the address math.  Default 16'hFFFF (no-op) so callers that
// don't set word 8 see the multiply-mode behaviour.  The masks
// reproduce BUILD's hlineasm4 shift-mode wrap exactly when tex_w/tex_h
// are powers of two (always true for BUILD/Quake/Doom textures).
reg [15:0] sp_tex_w_mask;
reg [15:0] sp_tex_h_mask;
// EMIT-hoist: span-constant mask+1 (mod 2^16, so the default 16'hFFFF mask
// yields octave 16'h0000 — the mirror test then never fires, exactly as the
// old per-pixel `mask + 16'd1` wrap behaved).  Written at EVERY sp_tex_*_mask
// write site (EMIT load + reset) so the per-pixel mirror_idx() cone reads a
// flop instead of adding 1 to the mask per pixel.
reg [15:0] sp_tex_w_octave;
reg [15:0] sp_tex_h_octave;
reg        sp_mirror_s;       // sticky: G_TX_MIRROR on S / T (control bits 28/29)
reg        sp_mirror_t;
reg        sp_cd_combine;     // sticky: texel*C+D combine enable (control bit 30)
reg [1:0]  sp_clamp_enable;
reg signed [31:0] sp_s_clamp_min;
reg signed [31:0] sp_s_clamp_max;
reg signed [31:0] sp_t_clamp_min;
reg signed [31:0] sp_t_clamp_max;
reg        sp_z_write_enable;
reg        sp_z_test_enable;
reg [GPU_ADDR_W-1:0] sp_z_addr;
// z ADDRESS delta (byte stride into the z-buffer) — narrow.  Distinct from
// sp_z_value/sp_z_value_step below, which are the 32-bit z VALUES written.
reg signed [GPU_ADDR_W-1:0] sp_z_step;
reg signed [31:0] sp_z_value;
reg signed [31:0] sp_z_value_step;
// Pipelined z_compress (UNIFIED z/depth pipe): zc_s1 = stage1 (CLZ) reg,
// zc_s2 = stage2 (shift) reg.  source_z_half reads zc_s2 (a flop) instead of
// running z_compress combinationally on the z-write path.  Fed by ONE 2-deep
// lookahead accumulator sp_zc_la (= value + 2*step) so zc_s2 ==
// z_compress(current value) in steady state; a 2-cycle warm (g_zwarm) fills
// the pipe at span start (single cone/cycle).  The operand pair is
// mode-selected at span EMIT using the same-cycle value written into sp_rgb:
// rgb spans (0x4E/0x52/0x54/0x4F) load the decoupled depth start/step
// (high-range 1/w depth, interpolated affine, float-compressed into the
// z-buffer instead of the perspective zi) — matching the old sp_rgb-wins
// priority even when truecolor is also set; all other spans load the
// sp_z_value (attr2) start/step, so truecolor 0x4B spans see z_compress(zi)
// exactly as the former dedicated z pipe produced.
reg [37:0] zc_s1;  reg [15:0] zc_s2;
reg signed [31:0] sp_zc_la;      // lookahead: value + 2*step (mode-selected)
reg signed [31:0] sp_zc_step;    // per-pixel step for sp_zc_la (mode-selected)
reg [1:0]  g_zwarm;              // span-start fill counter (2 -> 0), direct-color builds only
reg        sp_q29_z_enable;
reg signed [31:0] sp_q29_z_value;
reg signed [31:0] sp_q29_z_value_step;
// Free-running registered copy of the z-step plane operand (timing: 99 of
// the 200 worst paths on the second OS30 fit ran spanprod_span_axis ->
// attr2 du/dv mux -> the 64-bit q29_restore_z_saturating barrel shift ->
// sp_q29_z_value_step in ONE cycle at span EMIT).  All inputs are header
// fields, stable from S_EXECUTE on, and EMIT is always >=3 states after
// the header lands — so this register simply re-captures the mux every
// cycle and EMIT shifts from a plain register.  No reset needed: it is
// unconditionally reloaded every cycle and only consumed >=1 cycle after
// its inputs settle.
reg signed [31:0] q29_zstep_op_r;

// Unified span front-end.  Parametric records derive scalar spans from
// compact screen-space planes.  Direct-affine records carry already-computed
// lane state and use the same scalar span emitter.
reg        spanprod_active;
reg        spanprod_compact_direct;
reg        spanprod_direct_affine;
reg [1:0]  spanprod_idx;
reg [2:0]  spanprod_record_count;
reg [15:0] spanprod_records_left;
// 4-bit so the truecolor RGB path can add R (8) and B (9) setup steps after
// light (4); palettized/scalar codes 0..5,7 are unchanged.
reg [3:0]  spanprod_calc_step;
// Round-2 MUL_WAIT pipelining: identity of the NEXT setup product to
// launch (same encoding as spanprod_calc_step, plus 7 = none).  The
// capture pointer (calc_step) trails the launch pointer by exactly two
// pipeline slots; both walk the same flag-stable successor chain
// (spanprod_next_calc).
reg [3:0]  spanprod_launch_step;
reg [GPU_ADDR_W-1:0] spanprod_fb_base;
// fb/z major/minor steps are byte-address deltas.  They are sign-extended
// to 32 bits where they drive the shared DSP multiplier (address-product
// math) and assigned straight into the narrowed sp_fb_stride/sp_z_step.
reg signed [GPU_ADDR_W-1:0] spanprod_fb_major_step;
reg signed [GPU_ADDR_W-1:0] spanprod_fb_minor_step;
reg [GPU_ADDR_W-1:0] spanprod_tex_addr;
reg [15:0] spanprod_tex_width;
// EMIT-hoist: (spanprod_tex_width != 0), written at BOTH load sites of the
// register (compact w1 / long w4 — its only writers; it is stable from load
// to EMIT and carries no reset).  Both power up 0 (flag 0 -> "zero" -> EMIT
// substitutes 16'd1, exactly what the old (reg==0) test produced), so the
// flag mirrors the register from time zero.
reg        spanprod_tex_width_nz;
reg        spanprod_mirror_s;   // staged G_TX_MIRROR S/T (control word bits 28/29)
reg        spanprod_mirror_t;
reg        spanprod_cd_combine; // staged texel*C+D combine enable (control bit 30)
reg        spanprod_subpix_y;   // sticky: vert-tri vertex Y is Q12.4 subpixel (control bit 31); const 0 on palettized
reg [15:0] spanprod_tex_w_mask;
reg [15:0] spanprod_tex_h_mask;
reg [3:0]  spanprod_flags;
reg [3:0]  spanprod_colormap_id;
reg        spanprod_truecolor;   // staged direct-color flag (control word bit 7)
reg        spanprod_blend;       // staged alpha-blend flag (control flag bit 1)
reg [7:0]  spanprod_const_alpha; // staged per-surface src alpha (0x4A word 16)
reg        spanprod_attr_persp;
reg        spanprod_attr_q29;
reg [4:0]  spanprod_q29_attr_shift;
reg        spanprod_span_axis;
reg        spanprod_header_supported;
reg        spanprod_z_write;
reg        spanprod_z_test;
// B4: attribute-plane staging bank.  The ten planes' {origin, du, dv} live
// in three MLAB-hinted register files indexed by the SPANPROD step code
// (attr0=1, attr1=2, attr2=3, light=4, R=8, B=9, depth=10, Dr=11, Dg=12,
// Db=13; rows 0/5-7/14-15 unused).  24-bit planes (light/R/B/Dr/Dg/Db) are
// stored SIGN-EXTENDED to 32 bits at write time, so an indexed row read is
// bit-identical to the old {{8{v[23]}},v} per-plane operand muxes.  Exactly
// one write port each: the payload loader writes one field per cycle and
// the derive FSM writes one plane's origin+du+dv per DRV_ORG_FORM pass —
// mutually exclusive states of the one command FSM.  One async read address
// per array: the launch pointer for du/dv (spanprod_launch_step_mul), the
// capture pointer for origin (spanprod_capture_origin_w).  Writes never
// coincide with a consumed read (payload/derive states vs the S_SPANPROD
// walk), and the staging had no reset before, so last-written-value
// semantics are preserved exactly.
(* ramstyle = "MLAB, no_rw_check" *) reg signed [31:0] spanprod_pl_origin [0:15];
(* ramstyle = "MLAB, no_rw_check" *) reg signed [31:0] spanprod_pl_du     [0:15];
(* ramstyle = "MLAB, no_rw_check" *) reg signed [31:0] spanprod_pl_dv     [0:15];
// Walk-time pre-captures of (span_axis ? dv : du) for the two planes whose
// walk slots run UNCONDITIONALLY on every parametric record (chain
// 0 -> [5] -> 1 -> 2): loaded when launch slot 1/2 fires, consumed at EMIT.
// Bit-exact vs the old EMIT-time staging-reg mux because the plane rows and
// span_axis are only written in payload/derive states — never between a
// record's walk and its EMIT (q29_zstep_op_r is the precedent).
reg signed [31:0] spanprod_attr0_step_pre;
reg signed [31:0] spanprod_attr1_step_pre;
// Flop mirrors for the CONDITIONALLY-walked planes' du/dv, written wherever
// the corresponding array row is written.  Their EMIT reads (sp_zinv_step /
// sp_z_value_step / sp_zc_step / sp_light_step / sp_R,B,Dr,Dg,Db_step) are
// unconditional while their walk slots are gated (attr2: attr_persp; light:
// colormap||truecolor; R/B/depth: the rgb chain; D*: cd_combine), so a
// walk-slot pre-capture is NOT provably equivalent for degenerate command
// streams — these stay flops.  attr2's mirror also feeds the free-running
// q29_zstep_op_r capture.
reg signed [31:0] spanprod_attr2_du;
reg signed [31:0] spanprod_attr2_dv;
// Decoupled depth plane (0x4E truecolor): a 32-bit affine 1/w-scaled depth,
// independent of the perspective zi, float-compressed for the z-buffer.  Mirrors
// attr2; prunes when INCLUDE_DIRECT_COLOR=0 (only the 0x4E path writes it).
reg signed [31:0] spanprod_depth_du;
reg signed [31:0] spanprod_depth_dv;
reg signed [23:0] spanprod_light_du;
reg signed [23:0] spanprod_light_dv;
// Truecolor RGB Gouraud (0x4E): red & blue planes mirror the light plane
// (green reuses the light slot).  Only written/read on the RGB path; prune
// when INCLUDE_DIRECT_COLOR=0 (spanprod_rgb folds to a constant 0).
reg signed [23:0] spanprod_R_du, spanprod_R_dv;
reg signed [23:0] spanprod_B_du, spanprod_B_dv;
// Combiner D triple (additive per-vertex RGB): three more planes mirroring
// R/light/B, only derived/read on the combine path (spanprod_cd_combine).
reg signed [23:0] spanprod_Dr_du, spanprod_Dr_dv;
reg signed [23:0] spanprod_Dg_du, spanprod_Dg_dv;
reg signed [23:0] spanprod_Db_du, spanprod_Db_dv;
// The RGB path keys off cmd_is_draw_vert_tri_rgb (stable DECODE..EMIT, and a
// constant 0 when INCLUDE_DIRECT_COLOR=0) — no separate spanprod_rgb reg.
reg signed [31:0] spanprod_clamp0_min;
reg signed [31:0] spanprod_clamp0_max;
reg signed [31:0] spanprod_clamp1_min;
reg signed [31:0] spanprod_clamp1_max;
// EMIT-hoist: 1-bit (!=0) mirrors of the four clamp staging words above,
// computed at the payload-load sites (0x49/0x4A words 20-23 write, compact
// 0x48 w0 clear — the ONLY write sites of the 32-bit regs) so the EMIT
// clamp-enable derivation reads flags instead of four 32-bit reductions.
// Deliberately no reset term: the 32-bit regs carry none either, and both
// initialize to 0 (Verilator runtime default / Cyclone V FF power-up), so
// flag == (reg != 0) holds from time zero.
reg spanprod_clamp0_min_nz;
reg spanprod_clamp0_max_nz;
reg spanprod_clamp1_min_nz;
reg spanprod_clamp1_max_nz;
reg [GPU_ADDR_W-1:0] spanprod_z_base;
reg signed [GPU_ADDR_W-1:0] spanprod_z_major_step;
reg signed [GPU_ADDR_W-1:0] spanprod_z_minor_step;
// B5: the 4-lane record/lane staging banks are MLAB-hinted register files
// (single write port each — the payload loader / tri-fill write one lane
// per cycle; the old multi-lane clears are replaced by the per-lane
// validity masks below).  Single async read site: S_SPANPROD_SELECT
// captures lane [spanprod_idx] into the spanprod_cur_* registers, in an
// FSM state mutually exclusive with every writer state, so
// last-written-value semantics are preserved exactly.  MLABs don't reset;
// the masks reset to "reads-as-0", reproducing the old arrays' pre-first-
// write simulation value (the arrays had no reset clause before either).
//
// Per-lane architectural-zero tracking (replaces the multi-lane clears):
//   cnt_valid[L]  — lane L's count word written since the last clear site
//                   (compact w0 / long-form w29 / prepare_next_record_chunk).
//                   Clear = the old count[L]<=0; SELECT reads 0 when clear.
//   cmap_valid[L] — same for colormap_id (cleared at compact w0 / long w29,
//                   set ONLY by the compact per-lane count word — long-form
//                   count words never wrote colormap, exactly as before).
//   s_zeroed[L] / sstep_zeroed[L] — lane L's s/sstep architecturally 0:
//                   set for ALL lanes by the 0x4C column header (which used
//                   to write 0 into the arrays), cleared when a compact
//                   0x48 lane s/sstep word overwrites the lane.  SELECT
//                   forces the capture to 0 while set, so the sticky
//                   cross-command zeroing of the old arrays is reproduced
//                   bit-exactly (invariant: old_array[L] == mask-gated read).
reg [3:0]  spanprod_cnt_valid;
reg [3:0]  spanprod_cmap_valid;
reg [3:0]  spanprod_s_zeroed;
reg [3:0]  spanprod_sstep_zeroed;
(* ramstyle = "MLAB, no_rw_check" *) reg signed [15:0] spanprod_u [0:3];
(* ramstyle = "MLAB, no_rw_check" *) reg signed [15:0] spanprod_v [0:3];
(* ramstyle = "MLAB, no_rw_check" *) reg [15:0] spanprod_count [0:3];
reg [GPU_ADDR_W-1:0] spanprod_fb_addr_r;
reg [GPU_ADDR_W-1:0] spanprod_z_addr_r;
reg signed [31:0] spanprod_attr0_start_r;
reg signed [31:0] spanprod_attr1_start_r;
reg signed [31:0] spanprod_attr2_start_r;
reg signed [23:0] spanprod_light_start_r;
reg signed [23:0] spanprod_R_start_r, spanprod_B_start_r;
reg signed [23:0] spanprod_Dr_start_r, spanprod_Dg_start_r, spanprod_Db_start_r;
reg signed [31:0] spanprod_depth_start_r;
(* ramstyle = "MLAB, no_rw_check" *) reg [GPU_ADDR_W-1:0] spanprod_direct_fb_addr [0:3];
(* ramstyle = "MLAB, no_rw_check" *) reg [GPU_ADDR_W-1:0] spanprod_direct_tex_addr [0:3];
(* ramstyle = "MLAB, no_rw_check" *) reg signed [31:0] spanprod_direct_s [0:3];
(* ramstyle = "MLAB, no_rw_check" *) reg signed [31:0] spanprod_direct_t [0:3];
(* ramstyle = "MLAB, no_rw_check" *) reg signed [31:0] spanprod_direct_sstep [0:3];
(* ramstyle = "MLAB, no_rw_check" *) reg signed [31:0] spanprod_direct_tstep [0:3];
(* ramstyle = "MLAB, no_rw_check" *) reg [3:0]  spanprod_direct_colormap_id [0:3];
(* ramstyle = "MLAB, no_rw_check" *) reg [5:0]  spanprod_direct_light [0:3];
reg signed [15:0] spanprod_cur_u;
reg signed [15:0] spanprod_cur_v;
reg [15:0] spanprod_cur_count;
reg        spanprod_cur_nonzero;
reg [GPU_ADDR_W-1:0] spanprod_cur_direct_fb_addr;
reg [GPU_ADDR_W-1:0] spanprod_cur_direct_tex_addr;
reg signed [31:0] spanprod_cur_direct_s;
reg signed [31:0] spanprod_cur_direct_t;
reg signed [31:0] spanprod_cur_direct_sstep;
reg signed [31:0] spanprod_cur_direct_tstep;
reg [3:0]  spanprod_cur_direct_colormap_id;
reg [5:0]  spanprod_cur_direct_light;

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
        // Chokepoint SPAN_COLORMAP to 0 on truecolor-only targets (Pocket OS30 =
        // EXCLUDE_PALETTE -> INCLUDE_PALETTE 0).  The whole palettized colormap
        // lane then constant-folds: the p*_flags[SPAN_COLORMAP] chain, the p3
        // cmap-merge mux, cmap_resp_pop_b/cmap_pipe_wait, and gpu_tex_cache's
        // dead PORT B (the cmap read port + its response skid) all prune.  os30
        // firmware issues no colormap spans, so this is behavior-preserving;
        // the cmap REQUEST side is already INCLUDE_PALETTE-gated.  Mirrors the
        // SPAN_TRANSLUC chokepoint below.  Untouched on os25/MiSTer (PALETTE on).
        span_flags_from_wire[SPAN_COLORMAP] = (INCLUDE_PALETTE != 0) ? flags[0] : 1'b0;
        span_flags_from_wire[SPAN_SKIP_ZERO] = flags[2];
        span_flags_from_wire[SPAN_PERSP] = GPU_ENABLE_PERSP && flags[5];
`ifndef INCLUDE_TRANSLUC
        // Targets without INCLUDE_TRANSLUC (Pocket OS30): chokepoint the
        // per-pixel translucent flag to 0.  Every SPAN_TRANSLUC fragment then
        // falls through to the opaque-write path (p3_needs_fb_flush below),
        // so the FBSS_BLEND_* states, transluc_cache, the GPU_TRANSLUC_ADDR/
        // DATA LUT-upload window, and blend_group logic all become unreachable
        // and prune away (~388 ALM).  Translucent spans render as opaque
        // instead of blended — they never hang.  INCLUDE_TRANSLUC
        // keeps the full blend path.  Opaque/z/colormap pipeline untouched.
        span_flags_from_wire[SPAN_TRANSLUC] = 1'b0;
`else
        span_flags_from_wire[SPAN_TRANSLUC] = flags[6];
`endif
    end
endfunction

// Texel-index wrap with optional N64-style MIRROR (G_TX_MIRROR).  For a
// power-of-two texture (mask = W-1, mask+1 = W = the "octave" bit) the
// coordinate mirrors with period 2W: indices [0,W) map normally, [W,2W)
// reverse.  mirror_en=0 is the plain bitmask wrap (byte-exact unchanged).
// Two's-complement & handles negative coords (e.g. raw=-31,W=32: -31&32 set
// -> reversed -> 31-(-31&31)=31-1=30, matching N64 mirror(-31)).
function [15:0] mirror_idx;
    input [15:0] raw;        // clamped coord integer part
    input [15:0] mask;       // sp_tex_w_mask / sp_tex_h_mask (W-1 for POT)
    input [15:0] octave;     // precomputed mask+1 mod 2^16 (span-constant;
                             // sp_tex_w_octave / sp_tex_h_octave)
    input        mirror_en;
    reg   [15:0] wrapped;
    begin
        wrapped = raw & mask;
        if (mirror_en && ((raw & octave) != 16'd0))
            mirror_idx = mask - wrapped;   // reversed half of the 2W period
        else
            mirror_idx = wrapped;
    end
endfunction

wire [1:0] spanprod_last_idx =
      (spanprod_record_count >= 3'd4) ? 2'd3
    : (spanprod_record_count == 3'd3) ? 2'd2
    : (spanprod_record_count == 3'd2) ? 2'd1
    : 2'd0;

// Hoisted shared compare: "more packed records remain beyond the current
// chunk".  Spelled identically at three sites (the S_PAY_DATA chunk
// hand-off, the S_SPANPROD_SETUP continuation, and the fragment-pipe
// retire continuation) — one 16-bit comparator instead of three.
wire spanprod_more_records_w =
      (spanprod_records_left > {13'd0, spanprod_record_count});

task load_param_span_list_payload_word;
    input [5:0]  idx;
    input [31:0] data;
    begin
        // Branch select doubles as the INCLUDE_COMPACT_SPAN prune gate:
        // with the parameter 0 the S_DECODE setter is constant 0 so the reg
        // already folds, but spelling the constant here too keeps the whole
        // compact case dead even if a future writer of
        // spanprod_compact_direct appears.
        if ((INCLUDE_COMPACT_SPAN != 0) && spanprod_compact_direct) begin
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
                    spanprod_truecolor    <= INCLUDE_DIRECT_COLOR && data[27];
                    spanprod_mirror_s     <= 1'b0;   // compact 0x48 path: no mirror
                    spanprod_mirror_t     <= 1'b0;
                    spanprod_cd_combine   <= 1'b0;   // compact 0x48 path: no combine
                    spanprod_blend        <= 1'b0;   // compact 0x48 path: no blend
                    spanprod_const_alpha  <= 8'd0;
                    spanprod_attr_persp   <= 1'b0;
                    spanprod_attr_q29     <= 1'b0;
                    spanprod_q29_attr_shift <= 5'd0;
                    spanprod_span_axis    <= 1'b0;
                    spanprod_header_supported <= 1'b1;
                    spanprod_z_write      <= 1'b0;
                    spanprod_z_test       <= 1'b0;
                    spanprod_clamp0_min   <= 32'sd0;
                    spanprod_clamp0_max   <= 32'sd0;
                    spanprod_clamp1_min   <= 32'sd0;
                    spanprod_clamp1_max   <= 32'sd0;
                    spanprod_clamp0_min_nz <= 1'b0;
                    spanprod_clamp0_max_nz <= 1'b0;
                    spanprod_clamp1_min_nz <= 1'b0;
                    spanprod_clamp1_max_nz <= 1'b0;
                    // B5: multi-lane count/colormap clear -> mask clear
                    // (SELECT reads 0 for un-rewritten lanes, as before).
                    spanprod_cnt_valid  <= 4'b0000;
                    spanprod_cmap_valid <= 4'b0000;
                end
                6'd1: begin spanprod_tex_width <= data[15:0];
                            spanprod_tex_width_nz <= (data[15:0] != 16'd0); end
                6'd2: begin
                    spanprod_tex_w_mask <= (data[15:0]  == 16'd0) ? 16'hFFFF : data[15:0];
                    spanprod_tex_h_mask <= (data[31:16] == 16'd0) ? 16'hFFFF : data[31:16];
                end
                6'd3: spanprod_fb_minor_step <= data[GPU_ADDR_W-1:0];
                6'd4: spanprod_direct_fb_addr[0] <= data[GPU_ADDR_W-1:0];
                6'd5: spanprod_direct_tex_addr[0] <= data[GPU_ADDR_W-1:0];
                6'd6: begin spanprod_count[0] <= data[15:0]; spanprod_direct_light[0] <= data[21:16]; spanprod_direct_colormap_id[0] <= data[31:28]; spanprod_cnt_valid[0] <= 1'b1; spanprod_cmap_valid[0] <= 1'b1; end
                6'd7: begin spanprod_direct_s[0] <= data; spanprod_s_zeroed[0] <= 1'b0; end
                6'd8: spanprod_direct_t[0] <= data;
                6'd9: begin spanprod_direct_sstep[0] <= data; spanprod_sstep_zeroed[0] <= 1'b0; end
                6'd10: spanprod_direct_tstep[0] <= data;
                6'd11: spanprod_direct_fb_addr[1] <= data[GPU_ADDR_W-1:0];
                6'd12: spanprod_direct_tex_addr[1] <= data[GPU_ADDR_W-1:0];
                6'd13: begin spanprod_count[1] <= data[15:0]; spanprod_direct_light[1] <= data[21:16]; spanprod_direct_colormap_id[1] <= data[31:28]; spanprod_cnt_valid[1] <= 1'b1; spanprod_cmap_valid[1] <= 1'b1; end
                6'd14: begin spanprod_direct_s[1] <= data; spanprod_s_zeroed[1] <= 1'b0; end
                6'd15: spanprod_direct_t[1] <= data;
                6'd16: begin spanprod_direct_sstep[1] <= data; spanprod_sstep_zeroed[1] <= 1'b0; end
                6'd17: spanprod_direct_tstep[1] <= data;
                6'd18: spanprod_direct_fb_addr[2] <= data[GPU_ADDR_W-1:0];
                6'd19: spanprod_direct_tex_addr[2] <= data[GPU_ADDR_W-1:0];
                6'd20: begin spanprod_count[2] <= data[15:0]; spanprod_direct_light[2] <= data[21:16]; spanprod_direct_colormap_id[2] <= data[31:28]; spanprod_cnt_valid[2] <= 1'b1; spanprod_cmap_valid[2] <= 1'b1; end
                6'd21: begin spanprod_direct_s[2] <= data; spanprod_s_zeroed[2] <= 1'b0; end
                6'd22: spanprod_direct_t[2] <= data;
                6'd23: begin spanprod_direct_sstep[2] <= data; spanprod_sstep_zeroed[2] <= 1'b0; end
                6'd24: spanprod_direct_tstep[2] <= data;
                6'd25: spanprod_direct_fb_addr[3] <= data[GPU_ADDR_W-1:0];
                6'd26: spanprod_direct_tex_addr[3] <= data[GPU_ADDR_W-1:0];
                6'd27: begin spanprod_count[3] <= data[15:0]; spanprod_direct_light[3] <= data[21:16]; spanprod_direct_colormap_id[3] <= data[31:28]; spanprod_cnt_valid[3] <= 1'b1; spanprod_cmap_valid[3] <= 1'b1; end
                6'd28: begin spanprod_direct_s[3] <= data; spanprod_s_zeroed[3] <= 1'b0; end
                6'd29: spanprod_direct_t[3] <= data;
                6'd30: begin spanprod_direct_sstep[3] <= data; spanprod_sstep_zeroed[3] <= 1'b0; end
                6'd31: spanprod_direct_tstep[3] <= data;
                default: ;
            endcase
        end else begin
            case (idx)
                6'd0:  spanprod_fb_base <= data[GPU_ADDR_W-1:0];
                6'd1:  spanprod_fb_major_step <= data[GPU_ADDR_W-1:0];
                6'd2:  spanprod_fb_minor_step <= data[GPU_ADDR_W-1:0];
                6'd3:  spanprod_tex_addr <= data[GPU_ADDR_W-1:0];
                6'd4:  begin spanprod_tex_width <= data[15:0];
                             spanprod_tex_width_nz <= (data[15:0] != 16'd0); end
                6'd5:  spanprod_tex_w_mask <= (data[15:0] == 16'd0) ? 16'hFFFF : data[15:0];
                6'd6:  spanprod_tex_h_mask <= (data[15:0] == 16'd0) ? 16'hFFFF : data[15:0];
                6'd7: begin
                    spanprod_direct_affine <= 1'b0;
                    spanprod_flags         <= span_flags_from_wire(data[7:0]);
                    spanprod_truecolor     <= INCLUDE_DIRECT_COLOR && data[7];
                    spanprod_blend         <= INCLUDE_DIRECT_COLOR && data[1];  // OF_GPU_SPAN_BLEND
                    spanprod_const_alpha   <= 8'd0;   // default; 17-word 0x4A overrides at w16
                    spanprod_colormap_id   <= data[11:8];
                    spanprod_mirror_s      <= data[28];   // G_TX_MIRROR S (live on os25 — palettized tex mirroring)
                    spanprod_mirror_t      <= data[29];   // G_TX_MIRROR T (live on os25 — palettized tex mirroring)
                    // Combine is truecolor-only (its C/D operands come from the vert-tri RGB
                    // derive states); gate with the rest of the truecolor lane so the combine
                    // mux + rgb565_cd_finish + texel*C cone constant-folds away on os25.
                    spanprod_cd_combine    <= EFF_COMBINE && data[30];   // texel*C+D combine (resolver: EFF_COMBINE = truecolor && combine; folds when either absent)
                    spanprod_subpix_y      <= INCLUDE_DIRECT_COLOR && data[31];   // vertex Y is Q12.4 subpixel (vert-tri walker)
                    spanprod_attr_persp    <= GPU_ENABLE_PERSP && data[12] && (data[23:20] == PARAM_RECORD_U16V16_COUNT16);
                    spanprod_attr_q29      <= EFF_Q29 && data[13] && data[12] && (data[23:20] == PARAM_RECORD_U16V16_COUNT16);
                    spanprod_q29_attr_shift <= 5'd0;
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
                // B4: plane fields land in the MLAB bank rows (attr0=1,
                // attr1=2, attr2=3, light=4).  One field per word = one
                // write per array per cycle.  attr2 du/dv and light du/dv
                // also mirror into their dedicated flops (EMIT/r4 readers).
                // light is stored sign-extended so indexed reads reproduce
                // the old {{8{v[23]}},v} operand exactly.
                6'd8:  spanprod_pl_origin[4'd1] <= data;
                6'd9:  spanprod_pl_du[4'd1] <= data;
                6'd10: spanprod_pl_dv[4'd1] <= data;
                6'd11: spanprod_pl_origin[4'd2] <= data;
                6'd12: spanprod_pl_du[4'd2] <= data;
                6'd13: spanprod_pl_dv[4'd2] <= data;
                6'd14: spanprod_pl_origin[4'd3] <= data;
                6'd15: begin
                    spanprod_pl_du[4'd3] <= data;
                    spanprod_attr2_du <= data;
                end
                6'd16: begin
                    spanprod_pl_dv[4'd3] <= data;
                    spanprod_attr2_dv <= data;
                end
                6'd17: spanprod_pl_origin[4'd4] <= {{8{data[23]}}, data[23:0]};
                6'd18: begin
                    spanprod_pl_du[4'd4] <= {{8{data[23]}}, data[23:0]};
                    spanprod_light_du <= data[23:0];
                end
                6'd19: begin
                    spanprod_pl_dv[4'd4] <= {{8{data[23]}}, data[23:0]};
                    spanprod_light_dv <= data[23:0];
                end
                6'd20: begin spanprod_clamp0_min <= data;
                             spanprod_clamp0_min_nz <= (data != 32'd0); end
                6'd21: begin spanprod_clamp0_max <= data;
                             spanprod_clamp0_max_nz <= (data != 32'd0); end
                6'd22: begin spanprod_clamp1_min <= data;
                             spanprod_clamp1_min_nz <= (data != 32'd0); end
                6'd23: begin spanprod_clamp1_max <= data;
                             spanprod_clamp1_max_nz <= (data != 32'd0); end
                6'd26: spanprod_z_base <= data[GPU_ADDR_W-1:0];
                6'd27: spanprod_z_major_step <= data[GPU_ADDR_W-1:0];
                6'd28: spanprod_z_minor_step <= data[GPU_ADDR_W-1:0];
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
                    // B5: multi-lane count/colormap clear -> mask clear.
                    spanprod_cnt_valid  <= 4'b0000;
                    spanprod_cmap_valid <= 4'b0000;
                end
                6'd30: begin
                    spanprod_q29_attr_shift <= spanprod_attr_q29 ? data[4:0] : 5'd0;
                    if ((data[31:5] != 27'd0)
                        || (!spanprod_attr_q29 && (data[4:0] != 5'd0)))
                        spanprod_header_supported <= 1'b0;
                end
                6'd31: begin
                    spanprod_u[0] <= data[15:0];
                    spanprod_v[0] <= data[31:16];
                end
                6'd32: begin
                    spanprod_count[0] <= data[15:0];
                    spanprod_cnt_valid[0] <= 1'b1;
                    spanprod_u[1] <= data[31:16];
                end
                6'd33: begin
                    spanprod_v[1] <= data[15:0];
                    spanprod_count[1] <= data[31:16];
                    spanprod_cnt_valid[1] <= 1'b1;
                end
                6'd34: begin
                    spanprod_u[2] <= data[15:0];
                    spanprod_v[2] <= data[31:16];
                end
                6'd35: begin
                    spanprod_count[2] <= data[15:0];
                    spanprod_cnt_valid[2] <= 1'b1;
                    spanprod_u[3] <= data[31:16];
                end
                6'd36: begin
                    spanprod_v[3] <= data[15:0];
                    spanprod_count[3] <= data[31:16];
                    spanprod_cnt_valid[3] <= 1'b1;
                end
                default: ;
            endcase
        end
    end
endtask

// CMD_DRAW_COLUMN_LIST (0x4C) payload loader.  Reuses the SAME shared spanprod
// staging regs as the 0x48 direct-affine path (so S_EXECUTE / S_SPANPROD / the
// fragment pipe are byte-identical), but parses a 5-word lane record instead of
// 7 words: the always-zero s/sstep words are NOT in the wire payload, so we
// force spanprod_direct_s[lane]=0 and spanprod_direct_sstep[lane]=0 here.
//
//   idx 0..3   -> the IDENTICAL 4-word direct-affine header decode (we just
//                 reuse the compact-direct word-0..3 arms of
//                 load_param_span_list_payload_word — same fields, same
//                 spanprod_direct_affine<=1 / record_count / flags / masks /
//                 fb_minor_step setup, and the same per-lane s/sstep=0
//                 initialisation that word 0 already performs below).
//   lane L word 0 (idx 4+5L)   -> spanprod_direct_fb_addr[L]
//   lane L word 1 (idx 5+5L)   -> spanprod_direct_tex_addr[L]
//   lane L word 2 (idx 6+5L)   -> {colormap_id<<28, light<<16, count}
//   lane L word 3 (idx 7+5L)   -> spanprod_direct_t[L]
//   lane L word 4 (idx 8+5L)   -> spanprod_direct_tstep[L]
// s and sstep are forced to 0 in word 0 (all four lanes) and never written.
// 0x4C -> 0x48 compact-direct payload index remap.  Column lane records
// are 5 words {fb, tex, count/light/cmap, t, tstep} at idx 4+5L; the
// compact-direct record is 7 words at idx 4+7L with s at +3 and sstep at
// +5 (both absent from the column wire format).  Header words 0-3 map
// 1:1.  Out-of-range indexes steer to an unused compact index (6'd62,
// no case arm — same no-op as the old loader's `default`).
function [5:0] column_compact_idx_remap;
    input [5:0] idx;
    begin
        case (idx)
            // ---- 4-word header ----
            6'd0:  column_compact_idx_remap = 6'd0;
            6'd1:  column_compact_idx_remap = 6'd1;
            6'd2:  column_compact_idx_remap = 6'd2;
            6'd3:  column_compact_idx_remap = 6'd3;
            // ---- lane 0 (column idx 4..8 -> compact idx 4..10) ----
            6'd4:  column_compact_idx_remap = 6'd4;
            6'd5:  column_compact_idx_remap = 6'd5;
            6'd6:  column_compact_idx_remap = 6'd6;
            6'd7:  column_compact_idx_remap = 6'd8;
            6'd8:  column_compact_idx_remap = 6'd10;
            // ---- lane 1 (idx 9..13 -> 11..17) ----
            6'd9:  column_compact_idx_remap = 6'd11;
            6'd10: column_compact_idx_remap = 6'd12;
            6'd11: column_compact_idx_remap = 6'd13;
            6'd12: column_compact_idx_remap = 6'd15;
            6'd13: column_compact_idx_remap = 6'd17;
            // ---- lane 2 (idx 14..18 -> 18..24) ----
            6'd14: column_compact_idx_remap = 6'd18;
            6'd15: column_compact_idx_remap = 6'd19;
            6'd16: column_compact_idx_remap = 6'd20;
            6'd17: column_compact_idx_remap = 6'd22;
            6'd18: column_compact_idx_remap = 6'd24;
            // ---- lane 3 (idx 19..23 -> 25..31) ----
            6'd19: column_compact_idx_remap = 6'd25;
            6'd20: column_compact_idx_remap = 6'd26;
            6'd21: column_compact_idx_remap = 6'd27;
            6'd22: column_compact_idx_remap = 6'd29;
            6'd23: column_compact_idx_remap = 6'd31;
            default: column_compact_idx_remap = 6'd62;
        endcase
    end
endfunction

// (load_column_list_payload_word removed: the 0x4C decode is the shared
// compact-direct loader behind column_compact_idx_remap, called from the
// unified S_PAY_DATA loader site — every 0x4C word is field-identical to
// a 0x48 compact-direct word, with s/sstep forced to 0 at the header.)

task spanprod_select_current_record;
    begin
        spanprod_cur_u <= spanprod_u[spanprod_idx];
        spanprod_cur_v <= spanprod_v[spanprod_idx];
        // B5: the lane banks are MLAB register files; the old multi-lane
        // clears are per-lane validity masks gating this one read site.
        // A lane whose count/colormap word was not (re)written since the
        // last clear reads as 0 — identical to the old cleared array.
        spanprod_cur_count <= spanprod_cnt_valid[spanprod_idx]
                            ? spanprod_count[spanprod_idx] : 16'd0;
        spanprod_cur_nonzero <= spanprod_cnt_valid[spanprod_idx]
                            && (spanprod_count[spanprod_idx] != 16'd0);
        // The eight per-lane captures below are compact-direct-only (their
        // sole reader is the direct branch of spanprod_load_generated_span);
        // this record select runs on EVERY path — 0x49/0x4B walker records
        // and 0x48 long-form included — so the 4:1 capture muxes must be
        // explicitly gated or they survive the INCLUDE_COMPACT_SPAN=0 sweep
        // as live fabric fed by the (swept) lane arrays.
        if (INCLUDE_COMPACT_SPAN != 0) begin
            spanprod_cur_direct_fb_addr <= spanprod_direct_fb_addr[spanprod_idx];
            spanprod_cur_direct_tex_addr <= spanprod_direct_tex_addr[spanprod_idx];
            // s/sstep read as 0 while the sticky column zeroing holds
            // (the 0x4C header used to write 0 into all four lanes; a
            // later compact s/sstep word clears the lane's mask bit).
            spanprod_cur_direct_s <= spanprod_s_zeroed[spanprod_idx]
                                   ? 32'sd0 : spanprod_direct_s[spanprod_idx];
            spanprod_cur_direct_t <= spanprod_direct_t[spanprod_idx];
            spanprod_cur_direct_sstep <= spanprod_sstep_zeroed[spanprod_idx]
                                   ? 32'sd0 : spanprod_direct_sstep[spanprod_idx];
            spanprod_cur_direct_tstep <= spanprod_direct_tstep[spanprod_idx];
            spanprod_cur_direct_colormap_id <= spanprod_cmap_valid[spanprod_idx]
                                   ? spanprod_direct_colormap_id[spanprod_idx] : 4'd0;
            spanprod_cur_direct_light <= spanprod_direct_light[spanprod_idx];
        end
    end
endtask

task spanprod_launch_fb_mul;
    // Address-step operands are GPU_ADDR_W-bit signed; sign-extend to the
    // 32-bit DSP operand width so the screen-space address product is exact.
    begin
        if (spanprod_span_axis) begin
            dsp_a  <= $signed({{16{spanprod_cur_u[15]}}, spanprod_cur_u});
            dsp_b  <= {{(32-GPU_ADDR_W){spanprod_fb_major_step[GPU_ADDR_W-1]}}, spanprod_fb_major_step};
            dsp2_a <= $signed({{16{spanprod_cur_v[15]}}, spanprod_cur_v});
            dsp2_b <= {{(32-GPU_ADDR_W){spanprod_fb_minor_step[GPU_ADDR_W-1]}}, spanprod_fb_minor_step};
        end else begin
            dsp_a  <= $signed({{16{spanprod_cur_v[15]}}, spanprod_cur_v});
            dsp_b  <= {{(32-GPU_ADDR_W){spanprod_fb_major_step[GPU_ADDR_W-1]}}, spanprod_fb_major_step};
            dsp2_a <= $signed({{16{spanprod_cur_u[15]}}, spanprod_cur_u});
            dsp2_b <= {{(32-GPU_ADDR_W){spanprod_fb_minor_step[GPU_ADDR_W-1]}}, spanprod_fb_minor_step};
        end
    end
endtask

task spanprod_launch_z_mul;
    begin
        if (spanprod_span_axis) begin
            dsp_a  <= $signed({{16{spanprod_cur_u[15]}}, spanprod_cur_u});
            dsp_b  <= {{(32-GPU_ADDR_W){spanprod_z_major_step[GPU_ADDR_W-1]}}, spanprod_z_major_step};
            dsp2_a <= $signed({{16{spanprod_cur_v[15]}}, spanprod_cur_v});
            dsp2_b <= {{(32-GPU_ADDR_W){spanprod_z_minor_step[GPU_ADDR_W-1]}}, spanprod_z_minor_step};
        end else begin
            dsp_a  <= $signed({{16{spanprod_cur_v[15]}}, spanprod_cur_v});
            dsp_b  <= {{(32-GPU_ADDR_W){spanprod_z_major_step[GPU_ADDR_W-1]}}, spanprod_z_major_step};
            dsp2_a <= $signed({{16{spanprod_cur_u[15]}}, spanprod_cur_u});
            dsp2_b <= {{(32-GPU_ADDR_W){spanprod_z_minor_step[GPU_ADDR_W-1]}}, spanprod_z_minor_step};
        end
    end
endtask

task spanprod_launch_attr_mul;
    input signed [31:0] du;
    input signed [31:0] dv;
    begin
        dsp_a  <= $signed({{16{spanprod_cur_u[15]}}, spanprod_cur_u});
        dsp_b  <= du;
        dsp2_a <= $signed({{16{spanprod_cur_v[15]}}, spanprod_cur_v});
        dsp2_b <= dv;
    end
endtask

// ----------------------------------------------------------------
// Round-2 S_SPANPROD pipelining: the per-record setup products are
// independent of each other (every launch reads only spanprod_cur_u/v
// plus per-attribute du/dv staging constants, never a prior product),
// and the registered dsp_a/dsp_b -> dsp_p path accepts new operands every
// cycle (a product launched in state N is readable in state N+2 — see
// the DRV_PROD_* schedule comment).  Instead of the strictly serial
// (MUL_WAIT + CAPTURE) ping-pong, the schedule now launches the next
// product every cycle and captures the product launched two cycles
// earlier.  Capture order equals launch order, and the staging flags
// (z/persp/colormap) are stable across the record, so ONE successor
// chain describes both pointers.  Products and capture slices are
// bit-identical to the serial schedule; only launch timing changed.
//
// Product codes reuse the spanprod_calc_step encoding:
//   0 = fb addr, 5 = z addr, 1 = attr0, 2 = attr1, 3 = attr2,
//   4 = light, 7 = none (chain exhausted)
localparam [3:0] SPANPROD_STEP_NONE = 4'd7;
localparam [3:0] SPANPROD_STEP_R    = 4'd8;   // truecolor RGB: red start_r
localparam [3:0] SPANPROD_STEP_B    = 4'd9;   // truecolor RGB: blue start_r
localparam [3:0] SPANPROD_STEP_DEPTH = 4'd10; // 0x4E decoupled depth start_r
localparam [3:0] SPANPROD_STEP_DR   = 4'd11;  // combine: D-red start_r
localparam [3:0] SPANPROD_STEP_DG   = 4'd12;  // combine: D-green start_r
localparam [3:0] SPANPROD_STEP_DB   = 4'd13;  // combine: D-blue start_r

function [3:0] spanprod_next_calc;
    input [3:0] cur;
    begin
        case (cur)
            4'd0: spanprod_next_calc = (spanprod_z_write || spanprod_z_test)
                                     ? 4'd5 : 4'd1;
            4'd5: spanprod_next_calc = 4'd1;
            4'd1: spanprod_next_calc = 4'd2;
            4'd2: spanprod_next_calc = spanprod_attr_persp ? 4'd3
                                     : ((spanprod_flags[SPAN_COLORMAP] || spanprod_truecolor)
                                        ? 4'd4 : SPANPROD_STEP_NONE);
            4'd3: spanprod_next_calc = (spanprod_flags[SPAN_COLORMAP] || spanprod_truecolor)
                                     ? 4'd4 : SPANPROD_STEP_NONE;
            // After light (4): truecolor RGB surfaces continue to R then B so
            // sp_R_q/sp_B_q get their span-start values (green is the light slot).
            4'd4: spanprod_next_calc = (cmd_is_draw_vert_tri_rgb || cmd_is_draw_xform_tri_rgb
                                        || cmd_is_draw_indexed_tri || cmd_is_draw_clip_tri) ? SPANPROD_STEP_R
                                                                : SPANPROD_STEP_NONE;
            SPANPROD_STEP_R: spanprod_next_calc = SPANPROD_STEP_B;
            SPANPROD_STEP_B: spanprod_next_calc = SPANPROD_STEP_DEPTH;
            // After depth: combine surfaces continue to the D triple (Dr,Dg,Db);
            // legacy RGB terminates at DEPTH exactly as before.
            SPANPROD_STEP_DEPTH: spanprod_next_calc = spanprod_cd_combine
                                     ? SPANPROD_STEP_DR : SPANPROD_STEP_NONE;
            SPANPROD_STEP_DR: spanprod_next_calc = SPANPROD_STEP_DG;
            SPANPROD_STEP_DG: spanprod_next_calc = SPANPROD_STEP_DB;
            SPANPROD_STEP_DB: spanprod_next_calc = SPANPROD_STEP_NONE;
            default: spanprod_next_calc = SPANPROD_STEP_NONE;
        endcase
    end
endfunction

// Launch dispatch — the SAME dedicated launch tasks the serial schedule
// used, selected by the pending launch code.  No new operand muxes: these
// case arms are the identical DSP-operand write sites the old calc_step-keyed
// CAPTURE arms produced; only the select term changed (launch pointer
// instead of capture pointer).  The DSP owner mux is untouched.
task spanprod_launch_step_mul;
    input [3:0] stepv;
    begin
        case (stepv)
            4'd0: spanprod_launch_fb_mul;
            4'd5: spanprod_launch_z_mul;
            SPANPROD_STEP_NONE: ;  // none pending — operands default-clear, product unused
            default: begin
                // B4: ONE indexed async row read per array replaces the ten
                // per-plane operand mux arms (rows for the 24-bit planes hold
                // the sign-extended value, so the read IS the old
                // {{8{v[23]}},v}).  Codes 6/14/15 never occur: launch_step is
                // only ever 7 (init) or a spanprod_next_calc output.
                spanprod_launch_attr_mul(spanprod_pl_du[stepv],
                                         spanprod_pl_dv[stepv]);
                // Walk-time pre-capture of the EMIT step operand for the two
                // unconditionally-walked planes (slots 1/2 fire exactly once
                // per parametric record, strictly before that record's EMIT;
                // the rows and span_axis are stable from here to EMIT).
                if (stepv == 4'd1)
                    spanprod_attr0_step_pre <= spanprod_span_axis
                        ? spanprod_pl_dv[stepv] : spanprod_pl_du[stepv];
                if (stepv == 4'd2)
                    spanprod_attr1_step_pre <= spanprod_span_axis
                        ? spanprod_pl_dv[stepv] : spanprod_pl_du[stepv];
            end
        endcase
    end
endtask

// S_SPANPROD_CAPTURE shared adder — every capture arm computes
// origin + dsp_p + dsp2_p into a different register, differing only in
// the origin operand and destination width (26-bit addr, 32-bit
// attr/depth, 24-bit color).  All product slices are LSB-aligned, so
// ONE 32-bit ternary adder serves every arm: the low N bits of the
// 32-bit sum depend only on the low N bits of the operands, making
// each truncated capture bit-identical to a dedicated N-bit adder.
// B4: the per-plane origin operand is ONE indexed async MLAB row read (the
// capture pointer is the row id); only the fb/z ADDRESS bases — which are
// not planes — remain as mux arms.  calc_step is only ever 0 or a
// spanprod_next_calc output, so every plane-row index it presents (1-4,
// 8-13) addresses a row written by this command's payload/derive exactly
// where the old per-plane staging reg was written; the 24-bit planes'
// rows hold the sign-extended value the old 24->32 function return
// produced.
wire signed [31:0] spanprod_capture_origin_w =
      (spanprod_calc_step == 4'd0)
        ? $signed({{(32-GPU_ADDR_W){1'b0}}, spanprod_fb_base})
    : (spanprod_calc_step == 4'd5)
        ? $signed({{(32-GPU_ADDR_W){1'b0}}, spanprod_z_base})
    : spanprod_pl_origin[spanprod_calc_step];

wire signed [31:0] spanprod_capture_sum =
    spanprod_capture_origin_w
    + $signed(dsp_p[31:0]) + $signed(dsp2_p[31:0]);

// Same-cycle rgb-mode select for the unified zc pipeline: this is the exact
// value being written into sp_rgb at EMIT (NOT the stale sp_rgb register,
// which still holds the previous surface's mode during the EMIT cycle).
wire spanprod_rgb_mode_w = cmd_is_draw_vert_tri_rgb || cmd_is_draw_xform_tri_rgb
                        || cmd_is_draw_indexed_tri || cmd_is_draw_clip_tri;

task spanprod_load_generated_span;
    begin
        sp_count       <= spanprod_cur_count;
        sp_fb_stride   <= spanprod_fb_minor_step;
        // EMIT-hoist: the 16-bit ==0 reduction moved to the payload-load
        // sites (spanprod_tex_width_nz).
        sp_tex_width   <= spanprod_tex_width_nz ? spanprod_tex_width : 16'd1;
        sp_truecolor   <= spanprod_truecolor;
        sp_blend       <= spanprod_blend;
        sp_const_alpha <= spanprod_const_alpha;
        sp_a6          <= (spanprod_const_alpha == 8'd255) ? 7'd64
                                                          : {1'b0, spanprod_const_alpha[7:2]};
        sp_rgb         <= spanprod_rgb_mode_w; // per-vertex RGB modulate (0x4E/0x52/0x54/0x4F)
        sp_tex_w_mask  <= spanprod_tex_w_mask;
        sp_tex_h_mask  <= spanprod_tex_h_mask;
        // Span-rate +1 (16-bit wrap) replaces the per-pixel add inside
        // mirror_idx() — bit-identical because the old `mask + 16'd1` was
        // also a 16-bit self-determined add (0xFFFF -> 0x0000).
        sp_tex_w_octave <= spanprod_tex_w_mask + 16'd1;
        sp_tex_h_octave <= spanprod_tex_h_mask + 16'd1;
        sp_mirror_s    <= spanprod_mirror_s;
        sp_mirror_t    <= spanprod_mirror_t;
        sp_cd_combine  <= spanprod_cd_combine;

        if (spanprod_direct_affine) begin
            // Fastpath gate: direct && !z && !transluc (&& !persp — this
            // branch forces persp_active 0 and clears the PERSP flag).
            sp_fastpath    <= !spanprod_flags[SPAN_TRANSLUC];
            sp_fb_addr     <= spanprod_cur_direct_fb_addr;
            sp_tex_addr    <= spanprod_cur_direct_tex_addr;
            sp_colormap_id <= spanprod_cur_direct_colormap_id;
            sp_light_q     <= {2'b00, spanprod_cur_direct_light, 16'b0};
            sp_light_step  <= 24'sd0;
            sp_R_q <= 24'sd0; sp_R_step <= 24'sd0;   // direct-affine never RGB
            sp_B_q <= 24'sd0; sp_B_step <= 24'sd0;
            sp_Dr_q <= 24'sd0; sp_Dr_step <= 24'sd0; // direct-affine never combine
            sp_Dg_q <= 24'sd0; sp_Dg_step <= 24'sd0;
            sp_Db_q <= 24'sd0; sp_Db_step <= 24'sd0;
            sp_flags       <= spanprod_flags & ~(4'b0001 << SPAN_PERSP);
            sp_clamp_enable <= 2'b00;
            sp_z_write_enable <= 1'b0;
            sp_z_test_enable  <= 1'b0;
            sp_z_addr       <= {GPU_ADDR_W{1'b0}};
            sp_z_step       <= {GPU_ADDR_W{1'b0}};
            sp_z_value      <= 32'sd0;
            sp_z_value_step <= 32'sd0;
            sp_zc_step      <= 32'sd0;
            sp_zc_la <= 32'sd0; g_zwarm <= 2'd0;
            sp_q29_z_enable     <= 1'b0;
            sp_q29_z_value      <= 32'sd0;
            sp_q29_z_value_step <= 32'sd0;
            sp_s           <= spanprod_cur_direct_s;
            sp_t           <= spanprod_cur_direct_t;
            sp_sstep       <= spanprod_cur_direct_sstep;
            sp_tstep       <= spanprod_cur_direct_tstep;
            sp_sZ          <= 32'sd0;
            sp_tZ          <= 32'sd0;
            sp_zinv        <= 32'sd0;
            sp_sZstep      <= 32'sd0;
            sp_tZstep      <= 32'sd0;
            sp_zinv_step   <= 32'sd0;
            sp_zinv_step_zero <= 1'b1;
            sp_persp_q29_mode <= 1'b0;
            persp_active      <= 1'b0;
            persp_first_done  <= 1'b0;
            persp_swap_pending <= 1'b0;
            persp_pss         <= PSS_IDLE;
            persp_pass        <= PSS_PASS_ANCHOR;
            sp_seg_left       <= 4'd0;
        end else begin
            sp_fastpath    <= 1'b0;
            sp_fb_addr     <= spanprod_fb_addr_r;
            sp_tex_addr    <= spanprod_tex_addr;
            sp_colormap_id <= spanprod_colormap_id;
            sp_light_q     <= spanprod_light_start_r;
            sp_light_step  <= spanprod_span_axis
                            ? spanprod_light_dv : spanprod_light_du;
            // Truecolor RGB red/blue per-pixel accumulators (green = light).
            sp_R_q    <= spanprod_R_start_r;
            sp_R_step <= spanprod_span_axis ? spanprod_R_dv : spanprod_R_du;
            sp_B_q    <= spanprod_B_start_r;
            sp_B_step <= spanprod_span_axis ? spanprod_B_dv : spanprod_B_du;
            // Combine D triple span accumulators (mirror R/light/B).
            sp_Dr_q    <= spanprod_Dr_start_r;
            sp_Dr_step <= spanprod_span_axis ? spanprod_Dr_dv : spanprod_Dr_du;
            sp_Dg_q    <= spanprod_Dg_start_r;
            sp_Dg_step <= spanprod_span_axis ? spanprod_Dg_dv : spanprod_Dg_du;
            sp_Db_q    <= spanprod_Db_start_r;
            sp_Db_step <= spanprod_span_axis ? spanprod_Db_dv : spanprod_Db_du;
            sp_flags       <= spanprod_attr_persp
                            ? (spanprod_flags | (4'b0001 << SPAN_PERSP))
                            : (spanprod_flags & ~(4'b0001 << SPAN_PERSP));
            // EMIT-hoist: the four 32-bit (!=0) reductions moved to the
            // payload-load sites (the *_nz flags above); this is now a
            // 2-bit OR.  Signed-vs-unsigned compare against zero is
            // bit-identical, so the flags reproduce the old reductions.
            sp_clamp_enable[0] <= spanprod_clamp0_min_nz
                               || spanprod_clamp0_max_nz;
            sp_clamp_enable[1] <= spanprod_clamp1_min_nz
                               || spanprod_clamp1_max_nz;
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
            sp_zc_step  <= spanprod_rgb_mode_w
                         ? (spanprod_span_axis ? spanprod_depth_dv : spanprod_depth_du)
                         : (spanprod_span_axis ? spanprod_attr2_dv : spanprod_attr2_du);
            // Prime the unified z_compress lookahead to the span start (depth
            // plane for rgb spans, attr2/zi otherwise — same-cycle sp_rgb value
            // selects); the 2-cycle warm (g_zwarm) advances it to the
            // steady-state +2*step and fills the pipe so pixel 0 sees
            // z_compress(start).  Direct-color builds only.
            sp_zc_la    <= spanprod_rgb_mode_w ? spanprod_depth_start_r
                                               : spanprod_attr2_start_r;
            g_zwarm     <= (INCLUDE_DIRECT_COLOR != 0) ? 2'd2 : 2'd0;
            // EFF_Q29 (localparam) gates these directly so the whole z-step
            // cone provably const-folds when Q29 is excluded (os30).  Gating
            // spanprod_attr_q29 (a reg) alone was insufficient — the per-pixel
            // feedback on sp_q29_z_value defeated const-propagation, leaving
            // q29_restore_z_saturating (spanprod_q29_attr_shift -> sp_q29_z_value)
            // live as a critical path.  EFF_Q29 forces the fold.
            sp_q29_z_enable <= EFF_Q29 && spanprod_attr_q29
                             && (spanprod_z_write || spanprod_z_test);
            sp_q29_z_value <= (EFF_Q29 && spanprod_attr_q29
                              && (spanprod_z_write || spanprod_z_test))
                             ? q29_restore_z_saturating(spanprod_attr2_start_r,
                                                        spanprod_q29_attr_shift)
                             : 32'sd0;
            // Operand comes from the free-running q29_zstep_op_r capture
            // (see its declaration) — the EMIT cycle pays only the barrel
            // shift + saturate, not the span_axis mux in front of it.
            sp_q29_z_value_step <= (EFF_Q29 && spanprod_attr_q29
                                   && (spanprod_z_write || spanprod_z_test))
                                  ? q29_restore_z_saturating(
                                      q29_zstep_op_r,
                                      spanprod_q29_attr_shift)
                                  : 32'sd0;
            // B4: attr0/attr1 step operands were pre-captured at their walk
            // slots (always visited, strictly before EMIT) — the wide
            // EMIT-cycle staging muxes are gone.
            sp_sZstep      <= spanprod_attr0_step_pre;
            sp_tZstep      <= spanprod_attr1_step_pre;
            sp_zinv_step   <= spanprod_span_axis
                            ? spanprod_attr2_dv : spanprod_attr2_du;
            sp_zinv_step_zero <= ((spanprod_span_axis
                                  ? spanprod_attr2_dv : spanprod_attr2_du) == 32'sd0);
            sp_light_step  <= spanprod_span_axis
                            ? spanprod_light_dv : spanprod_light_du;
            sp_persp_q29_mode <= spanprod_attr_q29;
            if (spanprod_attr_persp) begin
            sp_s           <= 32'sd0;
            sp_t           <= 32'sd0;
            sp_sstep       <= 32'sd0;
            sp_tstep       <= 32'sd0;
            sp_scc         <= 32'sd0;
            sp_tcc         <= 32'sd0;
            sp_sZ          <= spanprod_attr0_start_r;
            sp_tZ          <= spanprod_attr1_start_r;
            sp_zinv        <= spanprod_attr2_start_r;
            persp_active      <= 1'b1;
            persp_first_done  <= 1'b0;
            persp_prev_valid    <= 1'b0;    // each span starts linear (no A_{N-1})
            persp_prev_anchor_s <= 32'sd0;
            persp_prev_anchor_t <= 32'sd0;
            persp_pend_scc      <= 32'sd0;
            persp_pend_tcc      <= 32'sd0;
            persp_seg_a_ready <= 1'b0;
            persp_seg_b_ready <= 1'b0;
            persp_swap_pending <= 1'b0;
            persp_pss         <= PSS_IDLE;
            persp_pass        <= PSS_PASS_ANCHOR;
            sp_seg_left       <= 4'd0;
            end else begin
                sp_s           <= spanprod_attr0_start_r;
                sp_t           <= spanprod_attr1_start_r;
                sp_sstep       <= spanprod_attr0_step_pre;
                sp_tstep       <= spanprod_attr1_step_pre;
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

// N64-style monotonic floating-point depth code (truecolor z-buffer only).
// Maps a 32-bit interpolated z magnitude to a 16-bit code: 5-bit exponent
// (MSB position 0..31) in [15:11] + 11-bit mantissa (the 11 bits below the
// leading 1) in [10:0].  Monotonic non-decreasing in v, so the existing
// unsigned `>=` depth compare keeps the same near-occludes-far meaning while
// giving relative (float) precision across the depth range instead of a flat
// linear slice.  Only instantiated on sp_truecolor surfaces (which are const 0
// when INCLUDE_DIRECT_COLOR=0, so this whole cone sweeps on palettized builds).
function [15:0] z_compress;
    input [31:0] v;
    integer i;
    reg [4:0]  e;
    reg        found;
    reg [10:0] mant;
    begin
        if (v == 32'd0) begin
            z_compress = 16'd0;
        end else begin
            e = 5'd0; found = 1'b0;
            for (i = 31; i >= 0; i = i - 1) begin
                if (!found && v[i]) begin
                    e = i[4:0];
                    found = 1'b1;
                end
            end
            if (e >= 5'd11)
                mant = v >> (e - 5'd11);   // 11 bits below the leading 1
            else
                mant = v << (5'd11 - e);   // small value: left-align low bits
            z_compress = {e, mant};
        end
    end
endfunction

// ---- z_compress split into 2 pipeline stages (timing) -----------------------
// zc_stage1 = zero-detect + CLZ exponent (packs {is_zero, e[4:0], v[31:0]} = 38b)
// zc_stage2 = the e-indexed barrel shift -> {e,mant}.  Together they are
// bit-identical to z_compress(v) above (same zero case, same CLZ, same shift).
// A register between them halves the ~3ns z_compress cone so the truecolor
// z-write path (source_z_half -> z_src_pending_half) starts from a flop.
function [37:0] zc_stage1;
    input [31:0] v;
    integer i;
    reg [4:0] e; reg found;
    begin
        e = 5'd0; found = 1'b0;
        for (i = 31; i >= 0; i = i - 1)
            if (!found && v[i]) begin e = i[4:0]; found = 1'b1; end
        zc_stage1 = {(v == 32'd0), e, v};   // [37]=is_zero, [36:32]=e, [31:0]=v
    end
endfunction
function [15:0] zc_stage2;
    input [37:0] s1;
    reg is_zero; reg [4:0] e; reg [31:0] v; reg [10:0] mant;
    begin
        is_zero = s1[37]; e = s1[36:32]; v = s1[31:0];
        if (is_zero) zc_stage2 = 16'd0;
        else begin
            if (e >= 5'd11) mant = v >> (e - 5'd11);
            else            mant = v << (5'd11 - e);
            zc_stage2 = {e, mant};
        end
    end
endfunction

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

// N64 RDP magic-square ordered dither (4x4, values 0..7).  The screen phase
// comes from the FB byte address: px = addr[2:1] (pixel x & 3), py =
// addr[8:7] (stride 640 -> +1 per row; x crossing a 64-px multiple shifts
// the matrix row, which is harmless for an ordered pattern).  Applied in the
// CONST-ALPHA BLEND repack only (cb_dsum_*): adding (d*8+4) before the >>6
// truncation turns the dropped fraction into sub-LSB spatial noise instead
// of stationary contour bands on smooth translucent surfaces (cloud/water/
// shadow) — the artifact the N64's blender dither existed to prevent.  The
// addend is < 1 output LSB, so exactly-representable results (alpha 0/64
// passthrough) are never perturbed.  Deliberately NOT applied in the
// p1->p2 modulate/gouraud cones: those adders widened the texture-pipe
// placement footprint ~0.1 ns of fitter floor across a 50-seed sweep and
// both top placements failed on silicon (TEXTGUARD +8 slip / mixer-init
// bit-30 drop, 2026-07-28); the blend stage (CB_BLEND2) has register-split
// slack and costs nothing.  Opaque smooth-gradient banding (sky, menu bg)
// is a known, unreported residual; if it ever matters, restage the
// modulate quantize into the p2->p2b no-op stage instead of p1->p2.
function [2:0] dither_mag;
    input [1:0] px;
    input [1:0] py;
    begin
        case ({py, px})
            4'd0:  dither_mag = 3'd0; 4'd1:  dither_mag = 3'd6;
            4'd2:  dither_mag = 3'd1; 4'd3:  dither_mag = 3'd7;
            4'd4:  dither_mag = 3'd4; 4'd5:  dither_mag = 3'd2;
            4'd6:  dither_mag = 3'd5; 4'd7:  dither_mag = 3'd3;
            4'd8:  dither_mag = 3'd3; 4'd9:  dither_mag = 3'd5;
            4'd10: dither_mag = 3'd2; 4'd11: dither_mag = 3'd4;
            4'd12: dither_mag = 3'd7; 4'd13: dither_mag = 3'd1;
            4'd14: dither_mag = 3'd6; 4'd15: dither_mag = 3'd0;
        endcase
    end
endfunction

// Truecolor Gouraud: scale an RGB565 texel by a 6-bit per-pixel brightness
// (the interpolated `light` value, 0..63, reused from the palettized path).
// scale = light+1 (1..64); each channel * scale >> 6 keeps full range at 63.
function [15:0] rgb565_modulate;
    input [15:0] texel;
    input [5:0]  light;
    reg   [6:0]  scale;
    reg   [11:0] pr, pg, pb;
    begin
        scale = {1'b0, light} + 7'd1;
        pr = texel[15:11] * scale;
        pg = texel[10:5]  * scale;
        pb = texel[4:0]   * scale;
        rgb565_modulate = {pr[10:6], pg[11:6], pb[10:6]};
    end
endfunction

// Truecolor per-channel Gouraud: scale each RGB565 texel channel by its own
// interpolated vertex-colour channel (r 5b, g 6b, b 5b).  scale = ch+1 so a
// full-range vertex colour (31,63,31) is identity (texel passes through), and
// an untextured white texel (0xFFFF) yields the vertex colour itself.
function [15:0] rgb565_gouraud;
    input [15:0] texel;
    input [4:0]  r;     // red   0..31
    input [5:0]  g;     // green 0..63 (reuses the light slot)
    input [4:0]  b;     // blue  0..31
    reg   [5:0]  sr, sb;
    reg   [6:0]  sg;
    reg   [10:0] pr, pb;   // 5b * 6b -> <=992
    reg   [12:0] pg;       // 6b * 7b -> <=4032
    begin
        sr = {1'b0, r} + 6'd1;
        sg = {1'b0, g} + 7'd1;
        sb = {1'b0, b} + 6'd1;
        pr = texel[15:11] * sr;
        pg = texel[10:5]  * sg;
        pb = texel[4:0]   * sb;
        rgb565_gouraud = {pr[9:5], pg[11:6], pb[9:5]};
    end
endfunction

// SPIKE (Phase-0 timing): second stage of clamp(texel*C + D).  The signed
// per-channel products texel*C are computed and registered in p1->p2; this
// function does the +D add, clamp, and RGB565 repack in p2->p2b so the MAC is
// split across the existing pipe boundary (not deepening p1->p2).
function [15:0] rgb565_cd_finish;
    input signed [13:0] pr;   // texel_r * C_r  (signed)
    input signed [13:0] pg;   // texel_g * C_g
    input signed [13:0] pb;   // texel_b * C_b
    input [4:0] dr;           // D red   (additive)
    input [5:0] dg;           // D green
    input [4:0] db;           // D blue
    reg signed [15:0] ar, ag, ab;
    reg [4:0] or5, ob5;
    reg [5:0] og6;
    begin
        ar = (pr >>> 5) + $signed({2'b0, dr});
        ag = (pg >>> 6) + $signed({2'b0, dg});
        ab = (pb >>> 5) + $signed({2'b0, db});
        or5 = ar[15] ? 5'd0 : (ar > 16'sd31 ? 5'd31 : ar[4:0]);
        og6 = ag[15] ? 6'd0 : (ag > 16'sd63 ? 6'd63 : ag[5:0]);
        ob5 = ab[15] ? 5'd0 : (ab > 16'sd31 ? 5'd31 : ab[4:0]);
        rgb565_cd_finish = {or5, og6, ob5};
    end
endfunction

// Src-over alpha blend of two RGB565 colors: out = (src*a + dst*(64-a) +
// dither) >> 6, per channel.  a in 0..64 (64 = fully src/opaque, 0 = fully
// dst).  Reference spec for the truecolor constant-alpha blend path
// (OF_GPU_SPAN_BLEND) — implemented as the pipelined blend fold: the
// multiplies register at the p2b->p3 shift (cbm_sum_*_w) and cb_mixed_w
// does the dither + >>6 + repack at the IDLE commit.
function [15:0] rgb565_blend;
    input [15:0] src;
    input [15:0] dst;
    input [6:0]  a;        // 0..64
    input [2:0]  d;        // magic-square dither (see dither_mag)
    reg   [6:0]  na;
    reg   [12:0] pr, pg, pb;
    begin
        na = 7'd64 - a;
        pr = src[15:11] * a + dst[15:11] * na + {d, 3'b100};   // max 1984+60
        pg = src[10:5]  * a + dst[10:5]  * na + {d, 3'b100};   // max 4032+60
        pb = src[4:0]   * a + dst[4:0]   * na + {d, 3'b100};
        rgb565_blend = {pr[10:6], pg[11:6], pb[10:6]};
    end
endfunction

function signed [31:0] q29_restore_z_saturating;
    input signed [31:0] value;
    input [4:0] shift;
    reg signed [63:0] wide;
    begin
        wide = $signed({{32{value[31]}}, value}) <<< shift;
        if (wide[63:31] != {33{wide[31]}})
            q29_restore_z_saturating = wide[63] ? 32'sh80000000 : 32'sh7fffffff;
        else
            q29_restore_z_saturating = wide[31:0];
    end
endfunction

function signed [31:0] sat_add32;
    input signed [31:0] a;
    input signed [31:0] b;
    reg signed [32:0] sum;
    begin
        sum = {a[31], a} + {b[31], b};
        if (sum[32] != sum[31])
            sat_add32 = sum[32] ? 32'sh80000000 : 32'sh7fffffff;
        else
            sat_add32 = sum[31:0];
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
localparam S_SPANPROD_SELECT  = 6'd13; // register current record before setup
localparam S_TRI_FILL         = 6'd14; // collect walker records into chunk regs
localparam S_TRI_DERIVE       = 6'd15; // run the 0x4B plane-derivation sub-FSM
localparam S_XFORM            = 6'd16; // 0x51 transform front-end (feeds S_TRI_DERIVE)
localparam S_VCREAD           = 6'd17; // T3: sequential 3-vert cache read (0x54)
                                       // (overlaps the walker setup window)

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
reg cmd_is_draw_column_list;
reg cmd_is_draw_param_tri;
reg cmd_is_set_tri_state;
reg cmd_is_draw_vert_tri;
reg cmd_is_draw_vert_tri_rgb;    // 0x4E (truecolor per-vertex RGB)
reg cmd_is_draw_param_tri_recs;
reg cmd_is_set_object_state;     // 0x50
reg cmd_is_draw_xform_tri;       // 0x51
reg cmd_is_draw_xform_tri_rgb;   // 0x52 (T1 truecolor transform tri)
reg cmd_is_draw_clip_tri;        // 0x4F clip-space-feed truecolor tri (skips the MAC)
reg cmd_is_load_verts;
reg cmd_is_load_vert_clip;           // 0x53 (T3 transform -> vertex cache)
reg cmd_is_draw_indexed_tri;     // 0x54 (T3 indexed draw from cache)
reg cmd_is_set_light_state;      // 0x55 (T4 sticky light state)
reg cmd_is_load_vert_lit;        // 0x57 (T4 transform+light -> cache)
reg cmd_is_flip;

// Registered command class for the S_PAY_DATA / S_EXECUTE dispatch.
// The cmd_is_* flags above are one-hot by construction (each decodes a
// distinct cmd_type), but Quartus cannot prove that, so an if/else
// priority chain over them folds every higher flag's negation into each
// lower branch's register enables — multiplied across ~50 shared
// destination regs.  cmd_class is decoded in S_DECODE (same cycle, same
// cmd_type/cmd_payload_words expressions as the flags) with one code per
// finest-common partition cell of the two chains, so both dispatches
// become a parallel case.  CMDCLS_NONE is the unrecognised / wrong-size /
// feature-gated-out drain: no S_PAY_DATA destination writes, S_EXECUTE
// retires to S_IDLE — exactly the old chains' fall-through.  Sticky-only
// commands with no S_EXECUTE arm (0x50/0x55) share that default arm.
localparam [4:0] CMDCLS_NONE             = 5'd0;
localparam [4:0] CMDCLS_FENCE            = 5'd1;
localparam [4:0] CMDCLS_FLIP             = 5'd2;
localparam [4:0] CMDCLS_CLEAR_RECT       = 5'd3;
localparam [4:0] CMDCLS_SET_FB           = 5'd4;
localparam [4:0] CMDCLS_SET_TEXTURE      = 5'd5;
localparam [4:0] CMDCLS_SPAN_COL         = 5'd6;   // 0x48 span list / 0x4C column list
localparam [4:0] CMDCLS_PARAM_TRI        = 5'd7;   // 0x49
localparam [4:0] CMDCLS_SET_TRI_STATE    = 5'd8;   // 0x4A
localparam [4:0] CMDCLS_VERT_TRI         = 5'd9;   // 0x4B
localparam [4:0] CMDCLS_VERT_TRI_RGB     = 5'd10;  // 0x4E
localparam [4:0] CMDCLS_PARAM_TRI_RECS   = 5'd11;  // 0x4D
localparam [4:0] CMDCLS_SET_OBJECT_STATE = 5'd12;  // 0x50
localparam [4:0] CMDCLS_XFORM_TRI        = 5'd13;  // 0x51
localparam [4:0] CMDCLS_XFORM_RGB_CLIP   = 5'd14;  // 0x52 / 0x4F (identical wire)
localparam [4:0] CMDCLS_LOAD_VERTS       = 5'd15;  // 0x53
localparam [4:0] CMDCLS_INDEXED_TRI      = 5'd16;  // 0x54
localparam [4:0] CMDCLS_SET_LIGHT_STATE  = 5'd17;  // 0x55
localparam [4:0] CMDCLS_LOAD_VERT_LIT    = 5'd18;  // 0x57
localparam [4:0] CMDCLS_LOAD_VERT_CLIP   = 5'd19;  // 0x56
reg [4:0] cmd_class;

// ================================================================
// Triangle edge walker (CMD_DRAW_PARAM_TRI)
// ================================================================
// The walker turns three vertices into the same {u,v,count} records the
// packed span list carries after word 30.  S_TRI_FILL drains its record
// stream into the spanprod_u/v/count chunk regs four at a time; the
// chunk-advance sites return to S_TRI_FILL (instead of the ring refill)
// while the walker still has output.
reg signed [15:0] tri_v0_x, tri_v0_y;
reg signed [15:0] tri_v1_x, tri_v1_y;
reg signed [15:0] tri_v2_x, tri_v2_y;
reg signed [15:0] tri_clip_x0, tri_clip_x1;
reg signed [15:0] tri_clip_y0, tri_clip_y1;
reg               tri_start;        // 1-cycle start pulse into the walker
reg [2:0]         tri_fill_idx;     // chunk slot being filled (0..4)

wire               tri_busy;
wire               tri_rec_valid;
wire signed [15:0] tri_rec_u;
wire signed [15:0] tri_rec_v;
wire       [15:0]  tri_rec_count;
// Capture happens on the same edge the walker consumes the handshake.
wire               tri_rec_ready = (state == S_TRI_FILL);

// The shared triangle rasterizer is present iff ANY triangle command is
// included (0x49 param-tri, 0x4A/0x4B vertex-tri, or 0x4D records-tri).  When
// all three are 0 (os25) the walker has no producer at all, so we drop the
// instance and tie its outputs to the idle/"done" constants — every
// cmd_is_tri_walker path is already constant-dead, and tri_walker_done folds
// to 1.  Quartus then sweeps the ~690-ALM edge walker.
localparam INCLUDE_TRI_WALKER =
    ((INCLUDE_PARAM_TRI != 0) || (INCLUDE_VERT_TRI != 0)
     || (INCLUDE_PARAM_TRI_RECS != 0)) ? 1 : 0;

generate
if (INCLUDE_TRI_WALKER != 0) begin : g_tri_walker
    gpu_edge_walker #(
        .EW_PARALLEL_DIVS(GPU_EW_PARALLEL_DIVS)
    ) tri_walker (
        .clk      (clk),
        .reset_n  (reset_n),
        .abort    (soft_reset),
        .start    (tri_start),
        .subpix_y (spanprod_subpix_y),
        .v0_x     (tri_v0_x), .v0_y (tri_v0_y),
        .v1_x     (tri_v1_x), .v1_y (tri_v1_y),
        .v2_x     (tri_v2_x), .v2_y (tri_v2_y),
        .clip_x0  (tri_clip_x0), .clip_x1 (tri_clip_x1),
        .clip_y0  (tri_clip_y0), .clip_y1 (tri_clip_y1),
        .busy     (tri_busy),
        .rec_valid(tri_rec_valid),
        .rec_u    (tri_rec_u),
        .rec_v    (tri_rec_v),
        .rec_count(tri_rec_count),
        .rec_ready(tri_rec_ready)
    );
end else begin : g_no_tri_walker
    // No triangle command included: idle the walker outputs (never busy,
    // never a record) so tri_walker_done is constant 1 and the S_TRI_FILL
    // arms are unreachable.
    assign tri_busy      = 1'b0;
    assign tri_rec_valid = 1'b0;
    assign tri_rec_u     = 16'sd0;
    assign tri_rec_v     = 16'sd0;
    assign tri_rec_count = 16'd0;
end
endgenerate

// Walker has emitted everything and gone idle: the start pulse has been
// consumed, no walk in progress, no record waiting.  The tri_start term
// covers the one-cycle dispatch-to-busy gap (saw-busy rule).
wire tri_walker_done = !tri_start && !tri_busy && !tri_rec_valid;

// All walker-sourced commands (0x49 param-tri, 0x4B vertex-tri, 0x4D
// records-only param-tri) drain the walker's record stream through
// S_TRI_FILL, so the S_TRI_FILL transitions and the drain-gate "more records
// from the walker?" decision key off this.  The only difference is the front
// end: 0x49 carries planes in its header, 0x4B derives them in S_TRI_DERIVE,
// 0x4D carries planes in its (short) payload and reuses the 0x4A sticky
// surface state for everything else.
wire cmd_is_tri_walker = cmd_is_draw_param_tri || cmd_is_draw_vert_tri
                       || cmd_is_draw_param_tri_recs || cmd_is_draw_xform_tri
                       || cmd_is_draw_vert_tri_rgb || cmd_is_draw_xform_tri_rgb
                       || cmd_is_draw_indexed_tri || cmd_is_draw_clip_tri;

// ================================================================
// CMD_SET_TRI_STATE (0x4A) sticky state
// ================================================================
// Staging-dedup: 0x4A no longer keeps a dedicated surface/control shadow bank.
// Its surface/control/clamp/z words are decoded DIRECTLY into the shared
// spanprod staging regs in S_PAY_DATA (the same regs a 0x48/0x49 header fills),
// so the only state that must persist across the gap from the 0x4A to a later
// 0x4B is the clip rect (the walker consumes it per draw; 0x49 carries its own
// clip in words 31-32, so there is no staging overlap with the spanprod regs)
// and the tri_state_valid flag.
//
// tri_state_valid gates 0x4B: a 0x4B with no valid sticky state drains its
// payload and retires as a no-op.  It is SET when an 0x4A retires, and CLEARED
// by soft_reset AND by any 0x48/0x49 header decode (because that header
// overwrites the shared spanprod staging the 0x4A wrote — see the opcode
// CONTRACT CHANGE comment).
reg                  tri_state_valid;
reg signed [15:0]    tri_state_clip_x0, tri_state_clip_x1;
reg signed [15:0]    tri_state_clip_y0, tri_state_clip_y1;

// ================================================================
// CMD_DRAW_VERT_TRI (0x4B) — raw per-vertex attribute latches
// ================================================================
// Latched straight from the payload (pre-sort).  The derivation FSM y-sorts
// them with the walker's exact compare rule so the anchor follows sorted v0.
// Staging-dedup: raw s/t are NOT kept in dedicated vt_s/vt_t regs.  s_k/t_k
// arrive at w3-5 / w6-8 and are parked directly in the dv_szi/dv_tzi product
// slots (declared with the derivation FSM below).  raw zi (vt_zi) and light
// (vt_lrow) are parked as zi/light w9-12.  TIMING DE-FOLD (2026-06): the
// s_k*zi_k / t_k*zi_k products are NO longer folded into payload arrival; they
// run in the dedicated DRV_PROD_* derivation-FSM states (off the S_PAY_DATA
// decode cone), overwriting dv_szi/dv_tzi in place before DRV_DELTA.
reg signed [31:0] vt_zi[0:2];   // Q16.16 zi
reg signed [31:0] vt_depth[0:2]; // 0x4E per-vertex decoupled depth (1/w high-range)
reg [5:0]         vt_lrow [0:2]; // Q6 light rows (= green for 0x4E RGB)
reg [4:0]         vt_rrow [0:2]; // 0x4E per-vertex red   (5-bit)
reg [4:0]         vt_brow [0:2]; // 0x4E per-vertex blue  (5-bit)
// Combine D triple per-vertex inputs (22-word 0x4E words 19/20/21, RGB565).
reg [4:0]         vt_Drrow [0:2]; // D red   (5-bit)
reg [5:0]         vt_Dgrow [0:2]; // D green (6-bit)
reg [4:0]         vt_Dbrow [0:2]; // D blue  (5-bit)
// Per-triangle Q29 override (0x4B w13): q29_en selects Q29-scaled attr planes
// (s*zi/t*zi/zi) for the world pre-lit-cache sampling path; shift is the shared
// CPU magnitude estimate.  w13==0 => legacy Q16.16 derive (backward compatible).
reg               vt_q29_en;
reg [4:0]         vt_q29_shift;

// ================================================================
// Triangle plane derivation FSM
// ================================================================
// Derives the four attribute planes (szi = s*zi, tzi = t*zi, zi, light) that
// an 0x49 PERSP header would have carried, from the raw 0x4B vertices.  Runs
// in parallel with the walker's setup (dsp/dsp2 are idle there; since the
// walker's slope divides went parallel its setup is ~48 cycles, so the
// derivation — not the walker — is the setup-phase long pole).  The
// FSM only enters S_TRI_FILL (which drains the walker's records into the
// spanprod path) from S_TRI_DERIVE's DRV_DONE, so the four planes are always
// staged before the first record is consumed — the interlock the spec calls
// for, enforced structurally by the state sequencing.
//
// FIXED-POINT CONTRACT (RTL and the acceptance C reference share these EXACTLY):
//   * Vertices sorted with the walker's rule: 3 strict-(y1<y0) compare-swaps in
//     order (v0,v1),(v1,v2),(v0,v1) carrying the {x,y,s,t,zi,lrow} tuple.
//   * Anchor = sorted v0.  Edge deltas from the anchor:
//       d1x = x1-x0, d2x = x2-x0  (Q12.4, 17-bit signed)
//       d1y = y1-y0, d2y = y2-y0  (integer scanline, 17-bit signed)
//   * Per-vertex numerator products (truncate toward -inf):
//       szi_k = (s_k * zi_k) >>> 16   (Q16.16 * Q16.16 -> Q16.16, arith shift)
//       tzi_k = (t_k * zi_k) >>> 16
//       zi plane uses zi_k directly; light plane uses (lrow_k << 16) as Q6.16.
//     Staging-dedup: these products are NOT computed by the derivation FSM.
//     They are folded into payload arrival — when zi_k lands (w9-11) the idle
//     S_PAY_DATA DSPs launch s_k*zi_k / t_k*zi_k and capture into dv_szi/dv_tzi
//     one cycle later (as the next zi word arrives).  The math/truncation is
//     bit-identical; only the schedule moved earlier.  da is clamped to signed
//     32 bits (DERIV_SAT32) before the plane solve so every numerator multiply
//     stays within one 32x32 DSP pass; this is the same clamp the C ref applies.
//   * Determinant:  det = d1x*d2y - d2x*d1y  (Q12.4-scaled, signed 35-bit).
//   * Reciprocal:  rdet = min(2^N + (|det|>>1)) / |det|, RDET_MAX) with N=44.
//       N=44 gives >=12 guard bits over a <=1024px bbox at Q16.16 attrs.
//       RDET_MAX = 2^32-1 caps rdet so it fits one 32-bit DSP operand; for
//       sliver triangles (|det| so small that 2^44/|det| > 2^32-1) rdet
//       saturates, the plane slopes blow up bounded, and the final du/dv
//       clamp to INT32 — deterministic, overflow-free.  |det|==0 (collinear)
//       is treated as |det|==1; such triangles also clip out in the walker.
//   * Plane terms, per attribute a (da1 = a1-a0, da2 = a2-a0, both clamped):
//       num_du = 16 * (da1*d2y - da2*d1y)   (the x16 corrects det's Q12.4 scale
//                                            to per-integer-pixel-x du units)
//       num_dv =      (da2*d1x - da1*d2x)   (dv numerator already in Q12.4 x
//                                            units -> unit-correct vs det)
//       du = DERIV_SAT32( ((num_du * rdet) >>> N) * sign(det) )
//       dv = DERIV_SAT32( ((num_dv * rdet) >>> N) * sign(det) )
//       num*rdet is exact via a two-pass split at bit 24 on the SHARED DSP
//       (num_hi*rdet then num_lo*rdet), recombined as
//         (num_hi*rdet) + (num_lo*rdet >>> SPLIT)  arith->> (N-SPLIT)
//       which is bit-identical to ((H<<SPLIT)+L)>>>N but keeps the single
//       shared accumulator/shift/saturate at 64 bits (no 96-bit cone).
//
// DSP SHARING / MUTUAL EXCLUSION:
//   EVERY multiply in this derivation goes through the two shared DSP ports
//   dsp_a/dsp_b->dsp_p and dsp2_a/dsp2_b->dsp2_p (signed 32x32->64, 1-cycle).
//   Wide products are decomposed into multiple 32x32 passes across FSM states
//   (e.g. num_hi*rdet then num_lo*rdet); no operand wider than 32 bits is ever
//   written to a dsp operand register.  Per-plane du/dv (8 results) reuse ONE
//   accumulator (dv_acc), ONE shift, and ONE saturate, computed strictly
//   sequentially — nothing is replicated per plane.
//   The derivation owns the DSP ports inside S_TRI_DERIVE — ALL of its multiplies
//   (TIMING DE-FOLD, 2026-06: including the per-vertex szi/tzi numerator products,
//   which used to fold into S_PAY_DATA but now run in the DRV_PROD_* states).
//   Keeping the szi/tzi fold in S_PAY_DATA put a DSP multiply + the dsp_p[47:16]
//   capture in the per-word payload decode cone that also writes spanprod_count,
//   deepening the pay_idx -> spanprod_count setup path; moving it into the
//   derivation FSM takes it off that combinational path.  PSS (the perspective
//   Newton-Raphson) owns the DSPs only inside S_FRAG_PIPE.  The two windows are
//   mutually exclusive in the top FSM: 0x4B advances S_PAY_DATA (operand parking
//   only, no DSP) -> S_EXECUTE -> S_TRI_DERIVE (all DSP work: DRV_PROD_* products
//   then the plane solve) -> (DRV_DONE) -> S_TRI_FILL -> S_SPANPROD_*, and PSS
//   bring-up (persp_pss) only runs once spans are being filled in S_FRAG_PIPE,
//   long after DRV_DONE released the DSPs.  So no DSP usage window overlaps
//   another; no arbitration.
//   * Origin lands the spanprod plane in the SAME (0,0)-extrapolated form the
//     0x49 header uses:  origin = (a0 - du*x0_px - dv*y0) truncated to 32 bits,
//     where x0_px = x0(Q12.4) >>> 4 (floor).  spanprod evaluates
//     origin + u*du + v*dv mod 2^32 at each record's absolute integer (u,v);
//     the mod-2^32 wraparound cancels the large anchor offset, so the visible
//     value equals a0 + (u-x0_px)*du + (v-y0)*dv with no overflow (the spec's
//     "anchoring" property).  The light plane truncates origin/du/dv to 24 bits
//     (Q6.16) to match spanprod_light_*.
//
// FINAL CONSTANTS (validated against the acceptance C reference, bit-for-bit):
//   N = 44  : rounded reciprocal Q-format.  >=12 guard bits over a <=1024px
//             bbox at Q16.16 attrs; rdet = round(2^44/|det|) caps at 2^31-1.
//   SPLIT = 24 : DSP two-pass numerator split (num_hi*rdet then num_lo*rdet).
//   |det| floored to 1 (collinear); rdet saturates at 2^31-1 for slivers, then
//   du/dv clamp to INT32 — deterministic, overflow-free, mirrored in the ref.
// STORAGE: vertices are NOT physically sorted.  The 3 compare-swaps only build
//   the order permutation dv_ord[]; x/y come straight from tri_v*_x/y and zi/
//   light straight from vt_zi/vt_lrow, both indexed through dv_ord.  The
//   s*zi / t*zi products live in dv_szi/dv_tzi, which (staging-dedup) ALSO
//   serve as the raw-s/t parking slots during payload — there is no separate
//   vt_s/vt_t bank.  This removes the sorted dv_x/dv_y copies, the dv_ziv/
//   dv_lit copies, the wide attribute swap-mux network of the original
//   six-array sort, AND the 6x32-bit vt_s/vt_t raw-attr bank.
// SCHEDULE: ~123 cycles (3 sort + 5 szi/tzi products + 3 delta/det + 46
//   reciprocal divide + 4 attrs x ~17 plane terms).  TIMING DE-FOLD (2026-06):
//   the per-vertex szi/tzi products run in the 5 DRV_PROD_* states between the
//   y-sort and the edge-delta stage (DRV_SORT_C -> DRV_PROD_L0 -> ... ->
//   DRV_PROD_C2 -> DRV_DELTA), rather than being folded into payload arrival.
//   The walker's edge-divide setup runs in parallel (started in S_EXECUTE;
//   ~48 cycles now that its three slope divides are parallel instances), so
//   the derivation is the setup-phase long pole and its length sets the
//   pre-walk latency directly; the +5 product cycles are still free in
//   throughput terms (the GPU is fragment-bound; triangle setup is
//   control-rate).  Per-triangle GPU cost stays at/under the 0x49 path
//   while removing ~180 cy/triangle of CPU plane-solve work.
localparam DERIV_N      = 6'd44;
localparam DERIV_SPLIT  = 6'd24;
// rdet is fed to the signed 32x32 DSP, so it is capped at 2^31-1 (always
// non-negative — sign(det) is applied separately to the final du/dv).  The cap
// only engages for extreme slivers where the true gradient already exceeds
// Q16.16; those du/dv then clamp to INT32 deterministically (see rdet_ovf).

// Saturating clamp of a signed 64-bit value to int32 — the single shared
// saturate unit for the num_hi shift before the DSP multiply and for the final
// (num*rdet)>>>N quotient (the deterministic du/dv sliver/overflow rule).
function signed [31:0] deriv_sat32;
    input signed [63:0] v;
    begin
        if (v > 64'sd2147483647)              // > INT32_MAX
            deriv_sat32 = 32'sh7FFFFFFF;
        else if (v < -64'sd2147483648)        // < INT32_MIN
            deriv_sat32 = -32'sh80000000;
        else
            deriv_sat32 = v[31:0];
    end
endfunction

// Saturating clamp of a 33-bit signed difference (a_k - a0) to int32.  Used
// for the per-attribute da numerators — these are differences of two signed
// 32-bit attributes, so 33 bits is exact and avoids a 64-bit comparator cone.
function signed [31:0] deriv_sat33;
    input signed [32:0] v;
    begin
        if (v > 33'sd2147483647)              // > INT32_MAX
            deriv_sat33 = 32'sh7FFFFFFF;
        else if (v < -33'sd2147483648)        // < INT32_MIN
            deriv_sat33 = -32'sh80000000;
        else
            deriv_sat33 = v[31:0];
    end
endfunction

// Saturating clamp of a signed 64-bit value to int16 — the transform
// front-end's screen-coordinate clamp (matches the world path's int16 clamp).
function signed [15:0] xf_sat16;
    input signed [63:0] v;
    begin
        if (v > 64'sd32767)       xf_sat16 = 16'sd32767;
        else if (v < -64'sd32768) xf_sat16 = -16'sd32768;
        else                      xf_sat16 = v[15:0];
    end
endfunction

// Per-vertex working set, kept in RAW (unsorted) vertex order.  Rather than
// physically swapping six attribute arrays through the sort (a wide register
// file plus a big swap-mux network), the sort only produces a 3-element order
// permutation dv_ord[]; every later read indexes the raw arrays through it.
// This removes the dv_x/dv_y sorted copies (x/y come straight from tri_v*_x/y
// via dv_ord) and all attribute-swap muxing.  The attribute values are
// order-independent per vertex: dv_szi/dv_tzi start as raw s/t and are
// overwritten in place with s*zi / t*zi.
// Staging-dedup: dv_szi/dv_tzi ARE the only raw-s/t storage (there is no
// separate vt_s/vt_t bank).  S_PAY_DATA parks s_k at dv_szi[k] (w3-5) and t_k
// at dv_tzi[k] (w6-8).  TIMING DE-FOLD (2026-06): the s_k*zi_k / t_k*zi_k
// products are formed in the DRV_PROD_* derivation states (after the y-sort,
// before DRV_DELTA) and overwritten IN PLACE over dv_szi[k]/dv_tzi[k] there —
// NOT during payload arrival.  This keeps the DSP multiply + dsp_p[47:16]
// capture out of the S_PAY_DATA decode cone (which also writes spanprod_count).
// zi and light are read directly from the raw vt_zi/vt_lrow inputs (no copy).
reg signed [31:0] dv_szi [0:2];   // Q16.16: raw s (parked at w3-5), then s*zi
reg signed [31:0] dv_tzi [0:2];   // Q16.16: raw t (parked at w6-8), then t*zi
// Sorted order: dv_ord[s] = raw vertex index in sorted slot s (0 = top vertex).
reg [1:0]         dv_ord [0:2];
// Raw vertex x (Q12.4) / y (int) as an indexable bus for the delta/origin
// stages (read through dv_ord; no separate sorted copy is stored).
wire signed [15:0] dvx [0:2];
wire signed [15:0] dvy [0:2];
assign dvx[0] = tri_v0_x; assign dvx[1] = tri_v1_x; assign dvx[2] = tri_v2_x;
// subpix_y: the derive consumes the SAME Q12.4 vertex Y the walker does, so a
// thin sliver whose vertices share an integer scanline is NOT degenerate here
// (rounding Y collapsed det -> garbage du/dv on sharp-angle planes).  Using
// Q12.4 Y makes du still correct (its x16 + det's extra y-x16 cancel) but dv
// 16x too small and the origin's y0 in Q12.4 — both compensated below
// (num_dv <<4 and dd_y0 = sy0>>4 under spanprod_subpix_y).
assign dvy[0] = tri_v0_y; assign dvy[1] = tri_v1_y; assign dvy[2] = tri_v2_y;

// Edge deltas + determinant.
reg signed [16:0] dd1x, dd2x, dd1y, dd2y;
reg               dd_detsign;     // 1 = det<0
reg [34:0]        dd_detabs;
reg signed [15:0] dd_x0px, dd_y0;

// Serial restoring divider for rdet = (2^N rounded) / |det|.
// Dividend is the 45-bit rounded numerator (2^44 + (|det|>>1)); divisor is the
// 35-bit |det|.  Quotient is capped at 2^31-1 (rdet_ovf) — a sliver with |det|
// small enough that the rounded reciprocal needs >=2^31 saturates there, so
// rdet stays a non-negative signed-32 DSP operand.  Mirrors the walker's
// restoring divider; 46 beats (1 load + 45 iterate).
reg [31:0]  rdet_q;               // running quotient (>=2^31 trips rdet_ovf)
reg [44:0]  rdet_dividend;
reg [34:0]  rdet_divisor;
reg [34:0]  rdet_rem;             // partial remainder (< divisor < 2^35)
reg [5:0]   rdet_cnt;
reg         rdet_ovf;             // quotient reached >=2^31 -> saturate rdet
// Reciprocal as a signed 32b DSP operand: capped non-negative value.
wire signed [31:0] rdet_operand = rdet_ovf ? 32'sh7FFFFFFF : {1'b0, rdet_q[30:0]};
wire [35:0] rdet_try  = {rdet_rem, rdet_dividend[44]};
wire        rdet_ge   = rdet_try >= {1'b0, rdet_divisor};
// When rdet_ge, rdet_try - divisor < divisor < 2^35, so the 35-bit subtract is
// exact; when !rdet_ge, rdet_try < 2^35 already.
wire [34:0] rdet_next = rdet_ge ? (rdet_try[34:0] - rdet_divisor) : rdet_try[34:0];

// Per-attribute plane working registers.
reg signed [31:0] dv_du, dv_dv;
// Scale accumulator: holds H = num_hi*rdet (signed, <=63 bits) across the two
// DSP passes.  The final quotient is (H + (num_lo*rdet >> SPLIT)) >>> (N-SPLIT)
// — algebraically identical to ((H<<SPLIT) + num_lo*rdet) >>> N, but the
// equivalent (H + L>>SPLIT) form keeps the shared accumulator/add at 64 bits
// instead of 96 (the low SPLIT bits of num_lo*rdet are below bit SPLIT of the
// recombined product and are discarded by the final >>>N anyway).  Verified
// bit-identical to the C reference across the full acceptance suite.
reg signed [63:0] dv_acc;
// DRV_SCALE_LS -> DRV_SCALE_LF staging register: the recombined 64-bit sum
// (H + L>>>SPLIT), registered so the shift/negate/saturate in DRV_SCALE_LF
// starts from a register instead of behind the 64-bit adder.  No reset:
// written by DRV_SCALE_LS strictly before DRV_SCALE_LF reads it.
reg signed [63:0] drv_qsum_r;
// Q29 timing pipeline (the -3.97 dv_du cone): the variable barrel shift is split
// coarse(byte)/fine(0-7) across two registered stages + a negate/sat32 third.
// q29_shamt is precomputed in DRV_SCALE_LS so the shift control is a register,
// not a combinational mux off vt_q29_shift.
reg [5:0]         q29_shamt_r;
reg signed [63:0] drv_q_coarse, drv_q_fine;
// Shared DSP-product capture registers for the derivation FSM.  The derivation
// is strictly sequential (one DSP launch/capture in flight at a time), so one
// 64-bit capture per DSP port is enough.  Splitting "read dsp_p -> arithmetic
// -> next dsp operand" into a capture cycle + a form cycle pulls the DSP output
// off the combinational path into the operand mux (the WNS -1.588 MiSTer chain:
// Mult1~mult_ll_pl -> 64-bit add -> shift -> saturate -> dsp_a, ~11.3 ns).
reg signed [63:0] drv_prod_r;
reg signed [63:0] drv2_prod_r;
// Q29 anchor a0<<(13-sh) pre-formed in DRV_ORG_CAP (variable barrel shift) so
// DRV_ORG_FORM is only the two 32-bit subtracts — splits the shift+sub+sub cone
// that became the worst GPU path once tri_v* was decoupled.  deriv_a0_q +
// vt_q29_shift + dv_attr are all stable between DRV_ORG_CAP and DRV_ORG_FORM.
reg signed [31:0] a0_eff_r;
// Low SPLIT bits of the current numerator (unsigned), preserved across the
// hi-product DSP latency so DRV_SCALE_HC can launch the lo product.
reg [DERIV_SPLIT-1:0] dv_num_lo;
reg               dv_doing_dv;    // 0 = computing du, 1 = computing dv
reg [3:0]         dv_attr;        // 0=szi 1=tzi 2=zi 3=light 4=R 5=B 6=depth 7=Dr 8=Dg 9=Db
reg [4:0]         dstate;         // derivation sub-FSM state

// Per-attribute clamped edge differences da1 = sat33(a1-a0), da2 = sat33(a2-a0)
// (area-shrink Lever 1, 2026-06).  Both du and dv numerators reuse the SAME two
// clamped differences (du: da1*d2y-da2*d1y; dv: da2*d1x-da1*d2x), so they are
// computed ONCE per attribute in DRV_DA_PREP and held here.  This hoists the two
// 33-bit subtract+deriv_sat33 cones OUT of the dsp_a/dsp2_a operand-select muxes
// (DRV_PLANE_NUM now loads plain registers) — shrinking the wide operand muxes
// the fitter flagged (dsp_a 50:1, dsp2_a 42:1/33:1) — and also removes the
// duplicate sat33 evaluation that the du and dv passes used to each recompute.
// Bit-exact: the operand values reaching the DSP are unchanged.
reg signed [31:0] da1_sat, da2_sat;

// Registered copies of the attribute-select mux outputs, captured in
// DRV_DA_SEL one cycle before DRV_DA_PREP (timing: WNS -1.508 fix on the
// first OS30 fit — the dv_attr 4:1 attr mux + dv_ord 3:1 sorted-slot mux
// fed the 33-bit subtract + sat33 compare + da*_sat sclr in ONE cycle,
// ~4.5 ns of mux/routing before the adder even started).  Same
// capture-split medicine as the DRV_*_FORM states below.  DRV_ORG_FORM's
// origin subtract reads deriv_a0_q too, taking the same mux cone off that
// path.  Values are bit-identical; the derive pays +1 cycle per attribute
// (+4 of ~123), still overlapped with the walker's serial setup.
reg signed [31:0] deriv_a0_q, deriv_a1_q, deriv_a2_q;

// Current attribute's per-vertex values in SORTED order.  dv_attr selects the
// attribute (0=szi, 1=tzi, 2=zi, 3=light); dv_ord[] maps each sorted slot back
// to the raw vertex index, so a0/a1/a2 are the sorted-anchor/edge-1/edge-2
// values without keeping a separate sorted attribute copy.
reg signed [31:0] deriv_attr [0:2];   // current-attribute values, raw order
reg signed [31:0] deriv_a0, deriv_a1, deriv_a2;
always @(*) begin
    case (dv_attr)
        3'd0: begin deriv_attr[0] = dv_szi[0]; deriv_attr[1] = dv_szi[1]; deriv_attr[2] = dv_szi[2]; end
        3'd1: begin deriv_attr[0] = dv_tzi[0]; deriv_attr[1] = dv_tzi[1]; deriv_attr[2] = dv_tzi[2]; end
        3'd2: begin deriv_attr[0] = vt_zi[0]; deriv_attr[1] = vt_zi[1]; deriv_attr[2] = vt_zi[2]; end
        // attr 4 = RED, attr 5 = BLUE (truecolor RGB; 5-bit -> Q*.16).  Only
        // reached when cmd_is_draw_vert_tri_rgb extends the derive loop past 3.
        3'd4: begin
            deriv_attr[0] = {11'd0, vt_rrow[0], 16'd0};
            deriv_attr[1] = {11'd0, vt_rrow[1], 16'd0};
            deriv_attr[2] = {11'd0, vt_rrow[2], 16'd0};
        end
        3'd5: begin
            deriv_attr[0] = {11'd0, vt_brow[0], 16'd0};
            deriv_attr[1] = {11'd0, vt_brow[1], 16'd0};
            deriv_attr[2] = {11'd0, vt_brow[2], 16'd0};
        end
        3'd6: begin      // attr 6 = decoupled depth (0x4E), full 32-bit
            deriv_attr[0] = vt_depth[0];
            deriv_attr[1] = vt_depth[1];
            deriv_attr[2] = vt_depth[2];
        end
        4'd7: begin      // attr 7 = D-red (combine), 5-bit -> Q*.16
            deriv_attr[0] = {11'd0, vt_Drrow[0], 16'd0};
            deriv_attr[1] = {11'd0, vt_Drrow[1], 16'd0};
            deriv_attr[2] = {11'd0, vt_Drrow[2], 16'd0};
        end
        4'd8: begin      // attr 8 = D-green (combine), 6-bit -> Q*.16
            deriv_attr[0] = {10'd0, vt_Dgrow[0], 16'd0};
            deriv_attr[1] = {10'd0, vt_Dgrow[1], 16'd0};
            deriv_attr[2] = {10'd0, vt_Dgrow[2], 16'd0};
        end
        4'd9: begin      // attr 9 = D-blue (combine), 5-bit -> Q*.16
            deriv_attr[0] = {11'd0, vt_Dbrow[0], 16'd0};
            deriv_attr[1] = {11'd0, vt_Dbrow[1], 16'd0};
            deriv_attr[2] = {11'd0, vt_Dbrow[2], 16'd0};
        end
        default: begin   // attr 3 = light (= green for RGB, in vt_lrow)
            deriv_attr[0] = {10'd0, vt_lrow[0], 16'd0};
            deriv_attr[1] = {10'd0, vt_lrow[1], 16'd0};
            deriv_attr[2] = {10'd0, vt_lrow[2], 16'd0};
        end
    endcase
    deriv_a0 = deriv_attr[dv_ord[0]];
    deriv_a1 = deriv_attr[dv_ord[1]];
    deriv_a2 = deriv_attr[dv_ord[2]];
end

// Derivation sub-FSM states (run inside S_TRI_DERIVE).
// TIMING DE-FOLD (2026-06): the per-vertex szi/tzi numerator products run in the
// DRV_PROD_* states (between the y-sort and the edge-delta stage), NOT during
// payload arrival.  S_PAY_DATA only parks the raw operands; this takes the DSP
// multiply + capture off the pay_idx->spanprod_count combinational decode cone.
localparam DRV_SORT_A   = 5'd0;   // compare-swap (v0,v1)  — walker's sort rule
localparam DRV_SORT_B   = 5'd1;   // compare-swap (v1,v2)
localparam DRV_SORT_C   = 5'd2;   // compare-swap (v0,v1)
localparam DRV_DELTA    = 5'd3;   // edge deltas + launch det products
localparam DRV_DET_W    = 5'd4;   // DSP latency
localparam DRV_DET_CAP  = 5'd5;   // form det, set up reciprocal divide
localparam DRV_RDET     = 5'd6;   // serial restoring divide for rdet
localparam DRV_PLANE_NUM = 5'd7;  // launch the current attr's numerator products
localparam DRV_PLANE_NW  = 5'd8;  // DSP latency
localparam DRV_PLANE_NC  = 5'd9;  // form num_du or num_dv, launch hi*rdet
localparam DRV_SCALE_HW  = 5'd10; // DSP latency (hi pass)
localparam DRV_SCALE_HC  = 5'd11; // capture hi product, launch lo*rdet
localparam DRV_SCALE_LW  = 5'd12; // DSP latency (lo pass)
localparam DRV_SCALE_LC  = 5'd13; // combine, shift, saturate -> du or dv
localparam DRV_PLANE_ORG = 5'd14; // launch origin offset products (du*x0,dv*y0)
localparam DRV_ORG_W     = 5'd15; // DSP latency
localparam DRV_ORG_CAP   = 5'd16; // form origin, store plane, advance attr
localparam DRV_DONE      = 5'd17;
// Capture states: each splits a former one-cycle "consume dsp_p -> arithmetic ->
// reg" cone in two.  The capture state latches the DSP product(s) into the
// shared drv_prod_r/drv2_prod_r pair; the following *_FORM state does the
// accumulate/shift/saturate/subtract from those captures, off the critical
// DSP-output-to-operand-register path.  (timing: WNS -1.588 fix — see header.)
localparam DRV_DET_FORM  = 5'd18; // form det from captured products
localparam DRV_PLANE_NF  = 5'd19; // form num, shift/sat, launch hi*rdet
localparam DRV_SCALE_LF  = 5'd20; // recombine (H + L>>SPLIT)>>>(N-SPLIT) -> du/dv
localparam DRV_ORG_FORM  = 5'd21; // form origin from captured products, store plane
// Pre-stage the two clamped edge differences for the current attribute (da1_sat,
// da2_sat) ONE cycle before DRV_PLANE_NUM, on fresh-attr entry only.  Lets
// DRV_PLANE_NUM load the DSP operands from plain registers instead of two
// deriv_sat33 subtract cones, collapsing the dsp_a/dsp2_a operand muxes.
localparam DRV_DA_PREP   = 5'd22; // sat33(a1-a0), sat33(a2-a0) -> da1_sat/da2_sat
localparam DRV_DA_SEL    = 5'd28; // capture attr-mux outputs -> deriv_a*_q
                                  // (one cycle ahead of DRV_DA_PREP, so the
                                  // subtract/saturate runs on plain registers)
localparam DRV_SCALE_LS  = 5'd29; // register the 64-bit recombine sum
                                  // (H + L>>>SPLIT) -> drv_qsum_r, one cycle
                                  // ahead of DRV_SCALE_LF's shift/negate/sat
localparam DRV_SCALE_LF2 = 5'd30; // Q29 barrel pipeline: fine shift (0-7)
localparam DRV_SCALE_LF3 = 5'd31; // Q29 barrel pipeline: negate + sat32
// TIMING DE-FOLD (2026-06): per-vertex szi/tzi numerator products, formed in the
// derivation FSM (off the S_PAY_DATA decode cone).  Run after the y-sort, before
// the edge-delta stage, inside the walker's idle setup window.  Raw s_k/t_k are
// parked in dv_szi/dv_tzi (S_PAY_DATA w3-8) and raw zi_k in vt_zi (w9-11); these
// states launch s_k*zi_k on dsp / t_k*zi_k on dsp2 (1-cycle DSP) and capture
// dsp_p[47:16] (Q16.16*Q16.16 -> Q16.16, arith trunc toward -inf) back into the
// SAME dv_szi[k]/dv_tzi[k] slot.  Launch trails capture by 2 cycles (DSP
// latency), so launch k=0/1/2 then capture k=0/1/2 two states later.
localparam DRV_PROD_L0   = 5'd23; // launch k=0 (s0*zi0, t0*zi0)
localparam DRV_PROD_L1   = 5'd24; // launch k=1
localparam DRV_PROD_L2C0 = 5'd25; // launch k=2, capture k=0
localparam DRV_PROD_C1   = 5'd26; // capture k=1
localparam DRV_PROD_C2   = 5'd27; // capture k=2 -> DRV_DELTA

// ============================================================
// CMD_DRAW_XFORM_TRI (0x51) transform front-end registers.
// Per-vertex cam = M*{v,1} (Q16.16) + perspective projection, producing the
// derive's existing inputs (tri_v*_x/y screen, dv_szi/dv_tzi raw s/t, vt_zi).
// Matrix + proj consts are loaded by CMD_SET_OBJECT_STATE (0x50, sticky).
// ============================================================
// M10K-backed (frees ~the 640 FF + the two async 20:1 read muxes from ALMs).
// Single sync read port (xf_M_q): the MAC loop serializes the 4 reads per row
// (M[row][0..2] products + M[row][3] translate) through it; no_rw_check because
// the 0x50 sticky load and the transform reads never overlap in time.
(* ramstyle = "M10K, no_rw_check" *)
reg signed [31:0] xf_M [0:19];          // up to 5x4 matrix (rows 0-2 cam,
                                        // 3-4 = s/t for N=5 world), Q16.16
reg signed [31:0] xf_xc, xf_yc;         // screen center (px)
reg signed [31:0] xf_xscale, xf_yscale; // pixel scale
reg signed [31:0] xf_nearclip;          // Q16.16 min cam.z
reg signed [31:0] xf_vx [0:2];          // raw verts {x,y,z} Q16.16
reg signed [31:0] xf_vy [0:2];
reg signed [31:0] xf_vz [0:2];
reg signed [31:0] xf_camx, xf_camy, xf_camz;
reg signed [63:0] xf_acc;               // MAC accumulator (full products, Q32.32)
reg [1:0]  xf_vtx, xf_idx;
reg [2:0]  xf_row;                      // 0-2 cam, 3-4 s/t (N=5 world)
reg [2:0]  xf_rows;                     // matrix row count: 3 (alias) or 5 (world)
reg        xf_q29_en;                   // world path: Q29-scale the derived planes
reg [4:0]  xf_q29_shift;                // CPU conservative magnitude shift
reg [4:0]  xf_state;                    // transform sub-FSM
reg [1:0]  xf_behind;                    // T2: count of verts with cam.z<nearclip
                                        // (==3 -> whole tri behind near -> reject)
reg signed [63:0] xf_sproj_r;           // projection: the screen.x/y 64-bit add
                                        // (shared — lifetimes disjoint: x is
                                        // captured in XF_PROJ_XS2 before
                                        // XF_PROJ_YS overwrites with y)
wire signed [15:0] xf_sproj_sat = xf_sat16(xf_sproj_r);  // single sat16 cone for both
// Dedicated projected-vertex holding regs: the transform writes these, and a
// single XF_PROJ_LAUNCH state parallel-loads them into the shared tri_v*_x/y.
// Keeps the saturating projection compute OUT of the tri_v* next-state mux,
// which (merged with the param/vert payload writers) was the -1.59 ns cone.
reg signed [15:0] xf_sx [0:2], xf_sy [0:2];
                                        // result, registered to split it from the
                                        // sat16 (the post-Q29-fix worst cone).
// transform reciprocal: zi = floor(2^32 / cz), restoring division (33 beats).
// SHARED DATAPATH: reuses the rdet_* divider registers + try/compare/subtract
// cone (S_XFORM and S_TRI_DERIVE are mutually exclusive states of the one main
// FSM; each mode fully reloads every shared register at its own init beat, and
// each mode's quotient is consumed before the other mode can load — xf_div_q's
// last read is XF_PROJ_YS2, before XF_PROJ_LAUNCH enters the derive; rdet's
// last read is DRV_SCALE, inside S_TRI_DERIVE — so the lifetimes never
// overlap).  The 33-bit dividend (2^32) is loaded LEFT-ALIGNED at bit 44 so
// the shared MSB tap rdet_dividend[44] sees exactly the bit sequence the old
// xf_div_dividend[32] tap did; divisor/remainder zero-extend (rem < divisor
// < 2^32 keeps the top remainder bits 0, and a zero divisor makes ge stuck-1
// at either width), so the quotient bits shifted into rdet_q are bit-identical
// to the old xf_div_q.  rdet_ovf is NOT written in recip mode (only DRV_RDET
// writes it, and both its entry points re-initialize it before use).
wire [31:0] xf_div_q = rdet_q;    // zi quotient (shared register, read-only alias)
// zi divisor: max(cam.z, near_clip).  Signed compare (as before); the selected
// value's bits then become the unsigned divisor, exactly like the old 32-bit reg.
wire [31:0] xf_recip_divisor = (xf_camz < xf_nearclip) ? xf_nearclip : xf_camz;
// xf_M M10K read port: xf_idx walks 0..3 per row (0-2 = cam/texvec products,
// 3 = the translate column M[row][3]); xf_rd_addr = row*4 + xf_idx.  XF_MAC_A
// issues the read, xf_M_q is valid one cycle later in XF_MAC_L.
wire [4:0] xf_rd_addr = {xf_row, 2'b00} + {3'b0, xf_idx};
reg signed [31:0] xf_M_q;               // registered read data
reg signed [31:0] xf_transl;            // captured M[row][3] for XF_ROW_DONE
// AND in INCLUDE_VERT_TRI so xf_M's read port constant-folds to 0 on os25 (no
// writer there — the 0x50 set_object_state decode is INCLUDE_VERT_TRI-gated), so
// the 20x32 array prunes deterministically at elaboration instead of relying on
// Quartus late dead-output elimination (which left a synthesized-away warning).
always @(posedge clk) xf_M_q <= ((INCLUDE_GPU_XFORM_MAC != 0) && (INCLUDE_VERT_TRI != 0)) ? xf_M[xf_rd_addr] : 32'sd0;
localparam XF_MAC_L=5'd0, XF_MAC_W=5'd1, XF_MAC_C=5'd2, XF_ROW_DONE=5'd3,
           XF_RECIP_INIT=5'd4, XF_RECIP_RUN=5'd5,
           XF_PROJ_XA=5'd6,  XF_PROJ_XW=5'd7,  XF_PROJ_XC=5'd8,
           XF_PROJ_XW2=5'd9, XF_PROJ_XS=5'd10,
           XF_PROJ_YA=5'd11, XF_PROJ_YW=5'd12, XF_PROJ_YC=5'd13,
           XF_PROJ_YW2=5'd14, XF_PROJ_YS=5'd15,
           XF_PROJ_XS2=5'd16, XF_PROJ_YS2=5'd17,
           XF_PROJ_LAUNCH=5'd18;

// ============================================================
// T3: GPU vertex cache (transform-once / draw-many; Fast3D G_VTX/G_TRI).
// 0x53 LOAD_VERTS transforms one raw vert through S_XFORM and writes a cache
// slot; 0x54 DRAW_INDEXED_TRI reads 3 slots into the derive's existing inputs
// and launches the derive (sp_rgb=1).  Folds to a 1-slot stub when disabled.
// ============================================================
localparam VC_SLOTS = (INCLUDE_VTX_CACHE != 0) ? 32 : 1;
// One packed slot per entry: {b5,g6,r5, depth32, t32, s32, zi32, sy16, sx16}.
// Single write port (0x53/0x57) + single registered read port (0x54 reads the
// 3 indices SEQUENTIALLY over 3 cycles in S_VCREAD), so it must infer as RAM
// rather than a register file either way.
localparam VC_W = 176;
// M10K, not MLAB.  The original choice was MLAB to protect the near-full
// M10K budget, but on a design this close to the LAB ceiling that is the
// wrong resource to economise: an MLAB is a WHOLE LAB switched to memory
// mode and it can no longer host logic, so 176 bits (Cyclone V MLABs are
// 32x20 => 9-10 of them) costs 9-10 LABs outright.  os30 failed to fit the
// vertex cache by exactly 5 LABs while 23 M10K blocks sat free; an M10K
// instance costs 0.0 ALM here (every altsyncram child of gpu_core reports
// 0.0 in the fit hierarchy).  Behaviour is byte-identical -- the read is
// already synchronous, so nothing about the protocol changes.
// no_rw_check: 0x53/0x57 (write) and 0x54 (read) are distinct commands, never
// the same cycle, so read-during-write behaviour is don't-care — required or
// Quartus leaves vc_mem as a register file (RAM logic uninferred).
(* ramstyle = "M10K, no_rw_check" *) reg [VC_W-1:0] vc_mem [0:VC_SLOTS-1];
reg [VC_W-1:0] vc_q;            // registered read data
reg [4:0]      vc_raddr;        // cache read address
reg [1:0]      vcr_cnt;         // sequential 3-vert read counter (0x54)
reg        xf_to_cache;        // T3: this S_XFORM run writes the cache (0x53/0x57)
reg [1:0]  xf_last_vtx;        // T3: last vert index (0 for load, 2 for tri)
reg [4:0]  xf_load_slot;       // T3: destination cache slot for 0x53/0x57
reg [31:0] xf_load_depth;         // 0x56 w7: explicit slot depth (see payload arm)
reg [4:0]  vc_i0, vc_i1, vc_i2; // T3: 0x54 indexed-draw cache indices

// T4: GPU per-vertex lighting — sticky state (0x55) + lit cache-load (0x57).
// color = clamp(ambient + clamp(N.L,0) * lightcolor), per RGB565 channel.
reg signed [31:0] lt_lx, lt_ly, lt_lz;   // light direction (object space), Q16.16
reg [4:0]  lt_lr, lt_lb;                  // light colour R/B (5-bit)
reg [5:0]  lt_lg;                         // light colour G (6-bit)
reg [4:0]  lt_ar, lt_ab;                  // ambient R/B
reg [5:0]  lt_ag;                         // ambient G
reg        lt_enable;
reg signed [31:0] xf_nx, xf_ny, xf_nz;    // per-vertex normal (lit load), Q16.16
reg        xf_lit;                         // this S_XFORM run computes lighting
reg        xf_clip;                        // this S_XFORM run is a clip-space feed (skips the MAC)
reg signed [31:0] xf_dot;                  // clamped N.L (Q16.16, 0..0x10000)
localparam XF_LIT_DL=5'd19, XF_LIT_DW=5'd20, XF_LIT_DC=5'd21, XF_LIT_CLAMP=5'd22,
           XF_LIT_CL=5'd23, XF_LIT_CW=5'd24, XF_LIT_CC=5'd25;
localparam XF_MAC_A=5'd26;   // M10K read-issue cycle before XF_MAC_L (xf_M_q latency)
localparam XF_CLIP_FEED=5'd27;  // clip-tri: load cam{x,y,z}<=clip{x,y,w}, jump to XF_RECIP_INIT

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

// SDRAM address layout for palookups.  palookup_base is the byte offset
// of slot 0; slots are spaced 16 KB apart.  Each slot has 64 shade rows
// and 256 entries per row, padded to 16 KB so the slot index is a 14-bit
// shift.  SDKs program app-owned storage through GPU_PALOOKUP_BASE.

// Payload streaming state — ring_rd_data is routed directly to each
// destination reg in S_PAY_DATA; no intermediate payload array.
// pay_idx saturates at 63 (any payload word past that still drains the
// ring via pay_remaining but has nowhere to go).  6 bits is the full
// meaningful range: the largest decoded index is 36 (0x49 vertex words),
// and every destination decode below compares against constants <= 36.
reg [5:0] pay_idx;
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

        // B5: multi-lane count clear -> mask clear (lanes the next partial
        // chunk doesn't rewrite read count=0 at SELECT, exactly as before).
        spanprod_cnt_valid <= 4'b0000;
        spanprod_idx       <= 2'd0;
        spanprod_calc_step <= 3'd0;

        // Consume the first word of the next packed-record chunk and prime
        // ring_rd_data so S_PAY_DATA sees the first record word index.
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
// p0a: source snapshot / DSP-input stage.  This stage is intentionally small
// and local: it breaks the timing path from the wide sp_* source muxes into
// the texture-row multiply.
reg        p0a_valid;
reg [5:0]  p0a_light;
reg [4:0]  p0a_R, p0a_B;   // truecolor RGB Gouraud red/blue (green = p0a_light)
(* ramstyle = "logic" *) reg [4:0] p0a_Dr, p0a_Db;   // combine D red/blue
(* ramstyle = "logic" *) reg [5:0] p0a_Dg;            // combine D green
reg [3:0]  p0a_colormap_id;
reg [3:0]  p0a_flags;
reg [GPU_ADDR_W-1:0] p0a_fb_addr;
reg signed [15:0] p0a_s_int;
reg [GPU_ADDR_W-1:0] p0a_tex_base;
(* preserve *) reg signed [15:0] p0a_t_y;
(* preserve *) reg [15:0] p0a_tex_width;
reg        p0a_z_test;
reg        p0a_z_write;
reg [GPU_ADDR_W-1:0] p0a_z_addr;
reg [15:0] p0a_z_value;

// p0: cache-issue stage. Holds the pixel whose texture-row multiply output is
// already available in tx_mul_q. p0 -> p1 transition is the "issue commit"
// event, gated on the cache asserting req_ready in the same cycle p0 drives
// req_valid.
reg        p0_valid;
reg [5:0]  p0_light;
reg [4:0]  p0_R, p0_B;
(* ramstyle = "logic" *) reg [4:0] p0_Dr, p0_Db;
(* ramstyle = "logic" *) reg [5:0] p0_Dg;
reg [3:0]  p0_colormap_id;
reg [3:0]  p0_flags;
reg [GPU_ADDR_W-1:0] p0_fb_addr;
reg signed [15:0] p0_s_int;     // for the post-mul add
reg [GPU_ADDR_W-1:0] p0_tex_base;         // sp_tex_addr at issue time
reg        p0_z_test;
reg        p0_z_write;
reg [GPU_ADDR_W-1:0] p0_z_addr;
reg [15:0] p0_z_value;

// DSP-pipelined texture multiply.  The p0a source snapshot gives this DSP a
// narrow, local input boundary; tx_mul_q is updated only when that snapshot
// promotes into p0, so the following cycle's cache request sees the matching
// product.
(* multstyle = "dsp" *) reg signed [31:0] tx_mul_q;

reg        p1_valid;
reg [5:0]  p1_light;
reg [4:0]  p1_R, p1_B;
(* ramstyle = "logic" *) reg [4:0] p1_Dr, p1_Db;
(* ramstyle = "logic" *) reg [5:0] p1_Dg;
reg [3:0]  p1_colormap_id;
reg [3:0]  p1_flags;
reg [GPU_ADDR_W-1:0] p1_fb_addr;
reg        p1_z_test;
reg        p1_z_write;
reg [GPU_ADDR_W-1:0] p1_z_addr;
reg [15:0] p1_z_value;
reg        p1_tex_ready;
reg [15:0] p1_tex_color;       // 16-bit for truecolor RGB565; low byte = CI8

reg        p2_valid;
reg [15:0] p2_color;          // tex result (16-bit RGB565 in truecolor)
// Combiner texel*C+D: registered signed per-channel products texel*C computed
// in p1->p2; +D/clamp/RGB565-repack finished in p2->p2b (rgb565_cd_finish), so
// the MAC is split across the existing pipe boundary.  p2_dC_* stage the
// independent per-vertex D triple to the p2->p2b add.
(* ramstyle = "logic" *) reg signed [13:0] p2_pr, p2_pg, p2_pb;
(* ramstyle = "logic" *) reg [4:0]  p2_dC_r, p2_dC_b;   // staged D red/blue (p2->p2b)
(* ramstyle = "logic" *) reg [5:0]  p2_dC_g;            // staged D green
reg [3:0]  p2_flags;
reg [GPU_ADDR_W-1:0] p2_fb_addr;
reg        p2_discard;        // skip-zero outcome
reg        p2_z_test;
reg        p2_z_write;
reg [GPU_ADDR_W-1:0] p2_z_addr;
reg [15:0] p2_z_value;

// p2b: 1-cycle delay between p2 (cmap addr issued) and p3 (cmap data captured).
// Cmap BRAM has 2-cycle effective latency from NB-set of cmap_rd_addr to
// cmap_rd_data being valid for that index, so we need a no-op shift stage.
reg        p2b_valid;
reg [15:0] p2b_color;
reg [3:0]  p2b_flags;
reg [GPU_ADDR_W-1:0] p2b_fb_addr;
reg        p2b_discard;
reg        p2b_z_test;
reg        p2b_z_write;
reg [GPU_ADDR_W-1:0] p2b_z_addr;
reg [15:0] p2b_z_value;

reg        p3_valid;
reg [15:0] p3_color;          // final color (post-cmap; 16-bit RGB565 in truecolor)
reg [3:0]  p3_flags;
reg [GPU_ADDR_W-1:0] p3_fb_addr;
reg        p3_discard;
reg        p3_z_test;
reg        p3_z_write;
reg [GPU_ADDR_W-1:0] p3_z_addr;
reg [15:0] p3_z_value;

// FB write sub-FSM (lives within S3, pauses pipeline when not IDLE)
// AR handshakes have no dedicated wait states: blend_arvalid self-clears on
// blend_arready (one shared clear before the case), so every read flow jumps
// straight from its issue site to its R-wait state — AXI guarantees no R
// beat before the AR is accepted.
localparam FBSS_IDLE        = 4'd0;
localparam FBSS_FLUSH_W_RSP = 4'd2;  // wait for write-buffer AW/W acceptance
localparam FBSS_ZTEST_R_WAIT  = 4'd5;
localparam FBSS_ZTEST_ACC_EVAL = 4'd12;
// Translucent-blend sub-flow.  SPAN_TRANSLUC fragments are first collected
// into a same-word lane group while the fragment pipe keeps running.  The
// blend unit then reads the destination FB word once, serialises the
// transluc[] lookups for the active lanes, and commits the modified word
// back into fb_acc.  One active lane is the old single-pixel path; adjacent
// horizontal / span-group lanes can amortise the expensive FB read.
//   BLEND_REQ       — wait for M0 to be free of texture-cache traffic, issue AR
//   BLEND_R_WAIT    — wait for R, capture rdata + fb_acc same-word bypass
//   BLEND_SELECT    — find next active lane and issue transluc[] SRAM read
//   BLEND_LUT_WAIT  — wait for SRAM read response, merge byte, loop/finish
//   BLEND_APPLY     — write the grouped word-fragment into fb_acc
localparam FBSS_BLEND_REQ      = 4'd6;
localparam FBSS_BLEND_R_WAIT   = 4'd8;
localparam FBSS_BLEND_LUT_WAIT = 4'd9;
localparam FBSS_BLEND_APPLY    = 4'd10;
localparam FBSS_BLEND_SELECT   = 4'd11;
localparam FBSS_BLEND_LOOKUP   = 4'd13;
// Truecolor constant-alpha src-over blend (OF_GPU_SPAN_BLEND), PIPELINED (the
// blend fold — mirrors the p2b z-test fold): the dst halfword is probed from
// the cbw window (+ fb_acc overlay + same-edge commit forward) while the
// pixel sits in p2b, the 6-multiply weighted sums register at the p2b->p3
// shift into cb_sr/sg/sb, and a window-hit pixel commits through the SAME
// one-cycle fb_acc path as an opaque pixel (dither + >>6 + repack are the
// short cb_mixed_w cone off those regs).  The pipe never freezes on hits.
// Only a window MISS detours: CB_REQ (flush + barrier + burst fill) ->
// CB_FILLR (capture, cb_dst_r on the pixel's beat) -> CB_RESOLVE (one cycle:
// the same shared multipliers compute the sums from p3/cb_dst_r operands) ->
// IDLE, where the pixel commits like any other.  INCLUDE_DIRECT_COLOR-gated
// (unreachable when sp_blend is const 0).
localparam FBSS_CB_REQ         = 4'd1;   // flush fb_acc, wait drain, issue line read
localparam FBSS_CB_FILLR       = 4'd14;  // capture the burst into cbw_*, cb_dst_r
localparam FBSS_CB_RESOLVE     = 4'd3;   // miss path: sums from p3 operands -> IDLE
localparam FBSS_CB_REFRESH     = 4'd7;   // stale capture: re-derive sums from fb_acc
reg [3:0] fbss;
// Miss-path dst halfword captured in FBSS_CB_FILLR so the CB_RESOLVE
// multiplies run register-to-register (read-response cone split).
reg [15:0] cb_dst_r;
// Per-channel weighted sums (src*a6 + dst*(64-a6)); loaded at the p2b->p3
// shift on a window hit (the fold) or by CB_RESOLVE on a miss; consumed by
// the cb_mixed_w repack cone at the IDLE commit.
reg [12:0] cb_sr, cb_sg, cb_sb;
// Stage-1 probe results riding the p2b stage: the dst halfword (window +
// fb_acc overlay, mux-only cone off the p2->p2b shift), the hit verdict,
// and the p2-edge staleness flag.
reg [15:0] p2b_cb_dst;
reg        p2b_cb_hit;
reg        p2b_cb_stale;
// The fold verdict, shifted alongside the pixel: ready=1 -> cb_sr/sg/sb
// hold this pixel's blended sums (commit is a normal one-cycle fb_acc
// merge); ready=0 -> window miss (dispatch to FBSS_CB_REQ).  stale=1 -> a
// same-word commit landed at one of this pixel's capture edges, so the
// sums were computed from a pre-commit dst: spend one IDLE cycle
// re-capturing from fb_acc before committing.  Loaded by every shift; only
// blend pixels consult them.
reg       p3_cb_ready;
reg       p3_cb_stale;

// Registered accumulator-hit depth test.  The hot z path was previously:
// p3_z_addr -> z_acc_addr compare -> halfword select -> depth compare ->
// z_acc_hi/lo update, all in one 100 MHz cycle.  On a z_acc hit, capture the
// selected old halfword and apply the test in the following FBSS cycle.
reg [15:0] ztest_acc_old_half;
reg        ztest_acc_from_read;
reg [31:0] ztest_acc_word;

// BLEND same-word group state.  Source bytes are stored per framebuffer byte
// lane; duplicate lanes flush the current group first so overdraw order is
// preserved exactly.
reg        blend_group_active;
reg [GPU_ADDR_W-1:0] blend_group_word_addr;
reg [3:0]  blend_group_mask;
reg [31:0] blend_group_src_data;
reg [31:0] blend_result_word;
reg [1:0]  blend_lane_iter;
reg [1:0]  blend_lut_lane;
reg        blend_arvalid;
reg [GPU_ADDR_W-1:0] blend_araddr;
// Per-issue AR length for the blend/z M0 reads: 0 (translucent FB word
// read), CBW_ARLEN (blend-window fill: 0/1/3), or 3 (4-word z-window
// fill).  Stored as the 2-bit arlen value and zero-extended at the
// m_rd_arlen mux.
reg [1:0]  blend_arlen_r;

// ----------------------------------------------------------------
// Z read window (4 words = 8 z pixels).  The z-test detour used to
// issue one single-word SDRAM read — behind a full write-drain
// barrier — per 32-bit z word, i.e. every 2 z-tested pixels.  The
// window turns that into one 4-beat burst read per 16-byte z line:
// the requested word feeds the current test exactly as the old
// single-word read did, and the sibling words are cached for the
// following pixels.
//
// Exactness contract: the window is a pure READ cache and must
// reflect every prior write.  Three rules enforce that:
//   1. The fill itself sits behind the same drain-complete barrier
//      the single-word read used, so nothing is in flight when the
//      4 words are captured.
//   2. EVERY fbwq push (z, color, clear — all writes go through the
//      queue) that lands in the window's 16-byte line invalidates
//      that word, at push-accept time (before the write can even
//      reach the queue).  A later test of that word re-reads behind
//      the barrier.
//   3. The whole window is dropped at every command decode
//      (S_DECODE) and on soft reset, so CPU-side z-buffer writes
//      between commands (fence-synchronised) can never be shadowed.
// The z accumulator (z_acc) keeps priority over the window in the
// FBSS_IDLE arm, exactly as it had priority over the single-word
// read — the freshest copy always wins.
// ----------------------------------------------------------------
reg [3:0]              zw_valid;
reg [GPU_ADDR_W-5:0]   zw_base;       // byte addr [GPU_ADDR_W-1:4]
reg [31:0]             zw_word [0:3];
reg [1:0]              zw_fill_beat;

// Registered write snoop (timing: 156 of the 200 worst paths on the first
// OS30 fit ended at zw_valid — the push-address mux from fb_acc/z_acc/p3
// plus the queue-full enable fed the line compare + word decode in ONE
// cycle).  The snoop now runs one cycle late off fbwq_req_addr (which the
// queue ALREADY captures on push-accept), so the compare is register-to-
// register; zw_snoop_pending marks the in-flight cycle and SUPPRESSES
// window hits for exactly that cycle.  Visibility is identical to the
// combinational snoop: in both versions a push at cycle N is reflected in
// zw_valid reads from N+1 — the suppression covers N+1 while the delayed
// clear lands at N+2, after which zw_valid is identical again.  The
// same-cycle (N) hit case is unchanged and remains covered by z_acc
// priority, exactly as before (exactness rules 1-3 untouched).
reg                    zw_snoop_pending;

// ----------------------------------------------------------------
// Truecolor-blend dst read window (4 words = 8 RGB565 pixels).
// Same shape and exactness contract as the z read window above:
//   1. the fill runs behind CB_REQ's full write-drain barrier;
//   2. every fbwq push into the window's 16-byte line invalidates
//      that word (registered snoop, one-cycle hit suppression);
//   3. the window drops at S_DECODE and on soft reset.
// The CB path previously paid flush + drain barrier + one single-word
// SDRAM read PER TRANSLUCENT PIXEL (~30-60 cycles — the "translucent
// surfaces are very expensive" report: the SM64 letter alone is ~30k
// blended pixels/frame).  The window amortises one 4-beat burst
// across up to 8 pixels; the fb_acc same-word bypass covers the word
// the accumulator holds, so a horizontal blend run costs ~4
// cycles/pixel after the first.  Reachability-pruned without
// INCLUDE_DIRECT_COLOR: sp_blend is constant 0, the fill state is
// unreachable, cbw_valid stays 0 and the window sweeps.
localparam CBW_WORDS = (GPU_CB_READ_WINDOW >= 4) ? 4
                     : (GPU_CB_READ_WINDOW >= 2) ? 2 : 1;
localparam CBW_LG    = (CBW_WORDS == 4) ? 2 : (CBW_WORDS == 2) ? 1 : 0;
localparam CBW_LOW   = 2 + CBW_LG;              // byte-addr line shift (4/3/2)
localparam [1:0] CBW_ARLEN = CBW_WORDS - 1;     // fill burst arlen (3/1/0)
reg [3:0]                    cbw_valid;          // upper bits const-0 below 4 words
reg [GPU_ADDR_W-1-CBW_LOW:0] cbw_base;
reg [31:0]                   cbw_word [0:3];     // entries >= CBW_WORDS sweep
reg [1:0]                    cbw_fill_beat;
reg                          cbw_snoop_pending;
wire [7:0]  blend_lut_src_byte = fb_lane_read(blend_group_src_data, blend_lane_iter);
wire [7:0]  blend_lut_fb_byte  = fb_lane_read(blend_result_word, blend_lane_iter);
wire [14:0] blend_lut_addr_w   = {blend_lut_src_byte[7:1], blend_lut_fb_byte};
wire [13:0] transluc_cache_lookup_addr = transluc_rd_addr[14:1];
wire        transluc_cache_hit0 = transluc_cache_valid[0]
                               && (transluc_cache_addr[0] == transluc_cache_lookup_addr);
wire        transluc_cache_hit1 = transluc_cache_valid[1]
                               && (transluc_cache_addr[1] == transluc_cache_lookup_addr);
wire        transluc_cache_hit = transluc_cache_hit0
                              || transluc_cache_hit1;
wire [15:0] transluc_cache_half = transluc_cache_hit0 ? transluc_cache_data[0] :
                                  transluc_cache_data[1];
wire [7:0]  transluc_cache_byte = transluc_rd_addr[0]
                                ? transluc_cache_half[15:8]
                                : transluc_cache_half[7:0];
// ADDR dedup (audit A1): ONE shared lane-merge cone for the two blend
// LUT result states.  FBSS_BLEND_LOOKUP (transluc cache hit) and
// FBSS_BLEND_LUT_WAIT (SRAM response) previously each elaborated the
// IDENTICAL {lane} -> {4:32 byte-lane mask decode + 32-bit andnot/or
// merge} cone onto blend_result_word, differing only in the 8-bit
// blended-byte source.  The two states are mutually exclusive, so one
// merge cone fed by a 2:1 byte source mux replaces the two decoders +
// two 32-bit merge cones — byte-for-byte identical outputs (the per-
// state expressions were textually identical modulo the byte operand).
wire [7:0]  blend_sram_lut_byte_w   = sram_rdata >> {transluc_rd_addr[1:0], 3'b0};
wire [7:0]  blend_lut_result_byte_w = (fbss == FBSS_BLEND_LOOKUP)
                                    ? transluc_cache_byte
                                    : blend_sram_lut_byte_w;
wire [31:0] blend_lut_merged_word_w =
      (blend_result_word & ~fb_lane_data_mask(blend_lut_lane))
    | fb_lane_data(blend_lut_lane, blend_lut_result_byte_w);
// Pre-computed after the grouped FB read, consumed by FBSS_BLEND_APPLY.  Hoists
// the 32-bit equality compare `fb_acc_addr == blend_group_word_addr` out of
// the BLEND_APPLY cycle so the only logic between blend_group_word_addr (FF)
// and fb_acc_addr (FF) on the same-word merge path is a 1-bit selector
// rather than a 32-bit equality test.  Closes the worst GPU-internal
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
// FBWQ design notes (2026-06 simplification audit — both REFUTED, keep as-is):
//  - The burst coalescer here is NOT redundant with the arbiter's gpu_wq
//    run-merge: the arbiter's chains stop at WLAST ("one B per GPU AW"),
//    so it can only merge beats WITHIN an AW this queue built.  Emitting
//    single-beat writes would drain the SDRAM as 1-word bursts.
//  - Depth 16 (vs the arbiter's 8) is what lets 8-beat runs accumulate
//    while the head drains; halving it shortens formed bursts under
//    continuous flow.  FB write throughput is the fps-critical path.
localparam FBWQ_DEPTH = 16;
// addr/strb carry an MLAB hint: fbwq_data already infers as block RAM,
// and with the tail shadow below these two are 1W+1R-sync as well.  MLAB
// keeps them off the Pocket's fully-committed M10K budget (~576 FFs +
// two 16:1 mux trees reclaimed vs. the flattened form).
// no_rw_check is REQUIRED for inference: without it Quartus refuses the
// MLAB (Info 276009 "uninferred due to unsupported read-during-write
// behavior") and flattens both arrays — plus a duplicated fbwq_addr__dual
// — into FFs + 16:1 muxes.  The don't-care RDW semantics are safe here:
// reads are at fbwq_rd_ptr (head entry always counted), writes at
// fbwq_wr_ptr gated on !fbwq_full, and rd_ptr == wr_ptr only at count
// 0 or 16 — so a same-cycle same-address read-during-write never occurs.
// A/B'd 2026-08-11 (os30 fit crisis): forcing addr/strb to M10K instead was
// NOT a win — os30 went 18,344 ALM / 1,847 LABs to 18,692 / 1,904 and still
// failed to fit.  Both were failing fits so the numbers are estimates, and
// the delta is inside the ±800-ALM packing chaos this design shows above the
// 95% cliff, but there was no signal to justify spending 3 M10K on it.  The
// MLAB form stays.
(* ramstyle = "MLAB, no_rw_check" *) reg [GPU_ADDR_W-1:0] fbwq_addr [0:FBWQ_DEPTH-1];
reg [31:0] fbwq_data [0:FBWQ_DEPTH-1];
(* ramstyle = "MLAB, no_rw_check" *) reg [3:0]  fbwq_strb [0:FBWQ_DEPTH-1];
reg [15:0] fbwq_link_next;
// Shadow of the most-recently-enqueued entry (== the array slots at
// fbwq_prev_wr_ptr, which is only ever the last slot written).  The
// burst-coalesce check needs addr/strb combinationally; reading the
// arrays at a second, asynchronous address would force them into
// registers.
reg [GPU_ADDR_W-1:0] fbwq_tail_addr;
reg [3:0]  fbwq_tail_strb;
reg [3:0]  fbwq_rd_ptr;
reg [3:0]  fbwq_wr_ptr;
reg [4:0]  fbwq_count;
reg [3:0]  fbwq_burst_remaining;
// AW-ahead state: the next burst's AW has been issued while the current
// burst's W beats stream; its length is latched here until the W chain
// start consumes it.  See the AW-ahead comment block below.
reg        fbwq_aw_ahead_valid;
reg [3:0]  fbwq_aw_ahead_words;
reg        fbwq_req_valid;
reg [GPU_ADDR_W-1:0] fbwq_req_addr;
reg [31:0] fbwq_req_data;
reg [3:0]  fbwq_req_strb;
reg        fbwq_stage_valid;
reg [GPU_ADDR_W-1:0] fbwq_stage_addr;
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
wire fb_write_drain_complete = !z_flush_valid
                             && !z_src_pending_valid
                             && !fbwq_req_valid && !fbwq_stage_valid && fbwq_empty
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
// ----------------------------------------------------------------
// AW-ahead overlap: while the current burst's LAST W beat sits on the
// channel, the AW handshake for that burst completed long ago, so the
// NEXT burst's AW can already be presented.  The downstream (arbiter /
// axi_sdram_slave) only accepts an AW once the prior burst's W beats
// have drained, so presenting it early never reorders AW vs W — it
// just removes the master-side re-arm bubble between bursts (the old
// fbwq_output_idle serialization cost ~2 idle cycles per burst, which
// dominated when FB/Z interleave breaks the queue into short bursts).
//
// The issue point is gated at fbwq_burst_remaining == 0 (current run's
// beats all popped; only the last beat still presenting).  At that
// moment the next burst's head IS fbwq_rd_ptr and every entry counted
// by fbwq_count belongs to the next burst, so the EXISTING start-cone
// (fbwq_start_burst_words) computes the ahead burst's length — no
// shifted second cone.  Gating at the last beat (rather than mid-run)
// also preserves burst formation: the queue has had the whole run to
// accumulate linked entries, so 8-beat chains still form exactly as
// they did with the idle-start path.  The length is latched at issue
// (fbwq_aw_ahead_words); entries pushed later can no longer join (link
// bits of existing entries never drop, so the latched chain stays
// valid and the W chain delivers exactly that many beats).
// ----------------------------------------------------------------
// Chain-finality guard: pre-announcing latches the burst length, so it
// must only fire when waiting could never lengthen the chain — the
// chain is already the 8-beat max, or an entry EXISTS beyond it whose
// link is broken (structural break: FB/Z interleave, stride jump).  A
// chain capped only by fbwq_count (all queued entries linked, producer
// still streaming) falls through to the idle-start path, which keeps
// accumulating during the re-arm gap exactly as before — preserving
// the long-burst formation the SDRAM row engine wants.
wire fbwq_head_chain_final = (fbwq_start_burst_words == 4'd8)
                          || (fbwq_count > {1'b0, fbwq_start_burst_words});
wire fbwq_aw_ahead_issue = m_wr_wvalid && (fbwq_burst_remaining == 4'd0)
                        && !fbwq_aw_ahead_valid
                        && !m_wr_awvalid && !m_wr_inflight_near_full
                        && !fbwq_empty && fbwq_head_chain_final;
// W-channel chain start: the pre-announced burst's W beats begin the
// moment the current run's last beat is accepted (or, if the run ended
// before the flag registered, as soon as the W channel is idle).
wire fbwq_w_tail_retiring = m_wr_wvalid && m_wr_wready
                          && (fbwq_burst_remaining == 4'd0);
wire fbwq_w_chain_start = fbwq_aw_ahead_valid
                        && (fbwq_w_tail_retiring || !m_wr_wvalid);
wire fbwq_start_now = !fbwq_empty && fbwq_drain_can_load
                    && !fbwq_aw_ahead_valid;
wire fbwq_continue_now = m_wr_wvalid && m_wr_wready && (fbwq_burst_remaining != 4'd0);
wire fbwq_pop_now = fbwq_start_now || fbwq_continue_now || fbwq_w_chain_start;
wire [4:0] fbwq_pop_count = fbwq_pop_now ? 5'd1 : 5'd0;
// Push side keys on the REGISTERED count only.  The old `|| fbwq_pop_now`
// term dragged m_wr_awready/m_wr_wready — the SDRAM arbiter's combinational
// grant cone, which sees every other master incl. the audio mixer — through
// fb_write_can_issue into fp_pipe_stall and the p3_* register enables: the
// worst setup chain at 100 MHz once the CPU cones were fixed.  Cost of the
// cut: one idle push cycle when the queue is exactly full while a pop
// drains (the req->stage skid still buffers 2 entries, and at 16+ deep
// backlog the pipe is drain-bound regardless).  Byte-exact output proven
// by the acceptance suite.
wire fbwq_can_enqueue = !fbwq_full;
// Burst-link compares, hoisted to wires (also reused by the swap path
// below).  "older entry links newer" = both full-word strobes, older's
// address word-consecutive with newer's, and no 4 KB-page cross.
// The req-side ±4 offsets are computed ONCE and the compares moved onto
// them (equality under mod-2^GPU_ADDR_W addition is bijective, so
// (a == b + 4) <=> (a - 4 == b) — bit-identical) so the three predicates
// share two adders instead of embedding one each.
wire [GPU_ADDR_W-1:0] fbwq_req_m4 = fbwq_req_addr - {{(GPU_ADDR_W-3){1'b0}}, 3'd4};
wire [GPU_ADDR_W-1:0] fbwq_req_p4 = fbwq_req_addr + {{(GPU_ADDR_W-3){1'b0}}, 3'd4};
wire fbwq_req_links_fifo_tail_w =
       (fbwq_tail_strb == 4'hF)
    && (fbwq_req_strb == 4'hF)
    && (fbwq_tail_addr[11:0] <= 12'hFF8)
    && (fbwq_req_m4 == fbwq_tail_addr);
wire fbwq_req_links_stage_tail_w =
       (fbwq_stage_strb == 4'hF)
    && (fbwq_req_strb == 4'hF)
    && (fbwq_stage_addr[11:0] <= 12'hFF8)
    && (fbwq_req_m4 == fbwq_stage_addr);
wire fbwq_stage_links_req_w =
       (fbwq_req_strb == 4'hF)
    && (fbwq_stage_strb == 4'hF)
    && (fbwq_req_addr[11:0] <= 12'hFF8)
    && (fbwq_stage_addr == fbwq_req_p4);
// Tail-1 link repair ("skid swap"): FB/Z interleave lands entries in
// the order ...FB1, Z1, FB2... and the FB1->FB2 link is lost because Z1
// sits between them.  When the req slot holds an entry that EXTENDS the
// array-tail chain while the stage slot holds one that does not, write
// req's entry into the array AHEAD of stage's (stage simply waits one
// more slot).  Reordering two buffered writes is observable only
// through memory, and only if they alias — excluded by the word-address
// inequality term (every RMW reader — z-test, blend — gates on
// fb_write_drain_complete, so in-queue order is invisible to reads).
// The swap repairs the chain: ...FB1, FB2, Z1...
wire fbwq_swap_now = fbwq_stage_valid && fbwq_req_valid && fbwq_can_enqueue
                  && !fbwq_empty
                  && fbwq_req_links_fifo_tail_w
                  && !fbwq_stage_link_tail
                  && (fbwq_req_addr[GPU_ADDR_W-1:2]
                      != fbwq_stage_addr[GPU_ADDR_W-1:2]);
wire fbwq_stage_drain_now = fbwq_stage_valid && fbwq_can_enqueue
                         && !fbwq_swap_now;
wire fbwq_stage_can_load = !fbwq_stage_valid || fbwq_stage_drain_now;
wire fbwq_req_to_stage_now = fbwq_req_valid && fbwq_stage_can_load;
wire fbwq_can_push = !fbwq_req_valid || fbwq_req_to_stage_now || fbwq_swap_now;
wire fb_write_can_issue = fbwq_can_push;
wire [3:0] fbwq_prev_wr_ptr = fbwq_wr_ptr - 4'd1;
wire fbwq_has_tail_after_pop = (fbwq_count > fbwq_pop_count);
wire p3_needs_fb_flush = p3_valid && !p3_discard && !p3_flags[SPAN_TRANSLUC]
                       && fb_acc_valid
                       && (fb_acc_addr[GPU_ADDR_W-1:2] != p3_fb_addr[GPU_ADDR_W-1:2]);
wire fb_write_buffer_stall = p3_needs_fb_flush && !fb_write_can_issue;
wire [GPU_ADDR_W-1:0] p3_fb_word_addr_w = p3_fb_addr & {{(GPU_ADDR_W-2){1'b1}}, 2'b00};
// Truecolor writes a 16-bit RGB565 pixel (2 byte lanes, selected by
// p3_fb_addr[1]); palettized writes one CI8 byte lane (existing path).
wire [3:0]  p3_fb_lane_mask_w = sp_truecolor
                              ? (p3_fb_addr[1] ? 4'b1100 : 4'b0011)
                              : fb_lane_mask(p3_fb_addr[1:0]);
wire [31:0] p3_fb_lane_data_w = sp_truecolor
                              ? (p3_fb_addr[1] ? {p3_color[15:0], 16'b0}
                                               : {16'b0, p3_color[15:0]})
                              : fb_lane_data(p3_fb_addr[1:0], p3_color[7:0]);
// Constant-alpha blend (OF_GPU_SPAN_BLEND), pipelined to keep the 6-multiply
// cone off the critical path: weight sp_a6 (0..64) is precomputed at EMIT;
// cb_dst_half_w = read-response halfword captured into cb_dst_r in FBSS_CB_R;
// the fold multiplies register into cb_sr/cb_sg/cb_sb at the p2b->p3 shift
// (or in FBSS_CB_RESOLVE on a miss); cb_mixed_w is the >>6 + repack consumed
// by the IDLE commit.  Prunes when sp_blend const 0.
wire [15:0] cb_dst_half_w  = p3_fb_addr[1] ? blend_rdata[31:16] : blend_rdata[15:0];
wire [2:0]  cb_dith_w  = dither_mag(p3_fb_addr[2:1], p3_fb_addr[8:7]);
wire [12:0] cb_dsum_r  = cb_sr + {cb_dith_w, 3'b100};   // max 1984+60 = 2044
wire [12:0] cb_dsum_g  = cb_sg + {cb_dith_w, 3'b100};   // max 4032+60 = 4092
wire [12:0] cb_dsum_b  = cb_sb + {cb_dith_w, 3'b100};
wire [15:0] cb_mixed_w = {cb_dsum_r[10:6], cb_dsum_g[11:6], cb_dsum_b[10:6]};
wire [31:0] cb_lane_data_w = p3_fb_addr[1] ? {cb_mixed_w, 16'b0}
                                           : {16'b0, cb_mixed_w};
// Word index inside the blend window for p3 (the MISS-path pixel parked in
// p3 during CB_FILLR) / the snooped push (constant 0 with a 1-word window).
wire [1:0]  cbw_p3_idx     = (CBW_LG == 2) ? p3_fb_addr[3:2]
                           : (CBW_LG == 1) ? {1'b0, p3_fb_addr[2]} : 2'd0;
// ---- The blend fold: dst probe at p2b (mirrors the p2b z-test fold) ----
// A blend pixel's dst halfword is resolved while it sits in p2b, from
// registered state only, through THREE coherency overlays (freshest wins):
//   1. cbw window word (SDRAM content as of the fill, snoop-invalidated on
//      any fbwq push — a probe during the one registered-snoop cycle is
//      suppressed, mirroring the z window's exactness argument);
//   2. fb_acc same-word overlay (already-committed lanes not yet in SDRAM);
//   3. same-edge commit forward: the pixel committing OUT of p3 this cycle
//      lands its lanes in fb_acc at the same edge this probe registers —
//      without the forward a same-word successor (sibling half, or overdraw
//      of the same half) would blend against the pre-commit dst.  The
//      forwarded value is p3_commit_lane_data_w, which covers both opaque
//      and blended committers.
// PRUNE GATE: constant 0 with a 1-word window (cbw_valid never set) — every
// blend pixel takes the miss path, the legacy per-pixel barrier + read.
// STAGE 1 of the fold (p2->p2b shift): probe the dst from registered window
// + accumulator state, indexed by the P2 pixel — a pure mux cone, no
// arithmetic, registered into p2b_cb_dst/hit.  The same-edge commit hazard
// is NOT forwarded here (chaining the commit-fire cone into the probe made
// a 15 ns path); instead every capture edge records whether a same-word
// commit landed at that edge in a STALENESS flag, and a stale pixel spends
// one extra cycle at p3 re-capturing its dst from fb_acc (which, by
// construction, holds every prior same-word contribution by then).
wire [1:0]  cbw_p2_idx     = (CBW_LG == 2) ? p2_fb_addr[3:2]
                           : (CBW_LG == 1) ? {1'b0, p2_fb_addr[2]} : 2'd0;
wire [GPU_ADDR_W-1:0] p2_fb_word_addr_w
                           = p2_fb_addr & {{(GPU_ADDR_W-2){1'b1}}, 2'b00};
wire [GPU_ADDR_W-1:0] p2b_fb_word_addr_w
                           = p2b_fb_addr & {{(GPU_ADDR_W-2){1'b1}}, 2'b00};
wire        cbw_p2_hit_w   = (CBW_WORDS > 1)
                          && cbw_valid[cbw_p2_idx]
                          && (cbw_base == p2_fb_addr[GPU_ADDR_W-1:CBW_LOW])
                          && !cbw_snoop_pending;
wire        cbw_p2_acc_same_w = fb_acc_valid
                             && (fb_acc_addr == p2_fb_word_addr_w);
wire [31:0] cbw_acc_mask_w = {{8{fb_acc_mask[3]}}, {8{fb_acc_mask[2]}},
                              {8{fb_acc_mask[1]}}, {8{fb_acc_mask[0]}}};
wire [31:0] cbw_p2_word_w  = cbw_word[cbw_p2_idx];
wire [31:0] cbw_p2_acc_w   = cbw_p2_acc_same_w
                           ? ((cbw_p2_word_w & ~cbw_acc_mask_w)
                              | (fb_acc_data & cbw_acc_mask_w))
                           : cbw_p2_word_w;
wire [15:0] cbw_p2_dst_w   = p2_fb_addr[1] ? cbw_p2_acc_w[31:16]
                                           : cbw_p2_acc_w[15:0];
// A commit "lands" this cycle for staleness purposes whenever the IDLE arm
// can merge/load p3's lanes.  Queue-gated cross-word commits that PARK
// instead never coincide with a shift edge (fb_write_buffer_stall blocks
// the shift), so dropping the queue terms here is exact at capture edges.
wire        p3_commit_land_w = (fbss == FBSS_IDLE)
                            && p3_valid && !p3_discard && !p3_z_test
                            && !p3_flags[SPAN_TRANSLUC] && !blend_group_active
                            && (!(sp_truecolor && sp_blend)
                                || (p3_cb_ready && !p3_cb_stale));
// Staleness is PER-HALFWORD: a sibling-half commit never touches this
// pixel's dst bytes (the probed value stays correct — no refresh), and a
// same-half commit (overdraw) guarantees fb_acc owns the half the refresh
// reads.  Word-granular flags would send siblings to a refresh that reads
// acc bytes the mask does not own.
wire        cb_stale_p2_w  = p3_commit_land_w
                          && (p3_fb_addr[GPU_ADDR_W-1:1]
                              == p2_fb_addr[GPU_ADDR_W-1:1]);
wire        cb_stale_p2b_w = p3_commit_land_w
                          && (p3_fb_addr[GPU_ADDR_W-1:1]
                              == p2b_fb_addr[GPU_ADDR_W-1:1]);
// The refresh dst: fb_acc holds every prior same-word lane by the time a
// stale pixel re-captures (its predecessor merged or loaded this word at
// the edge that set the flag).
wire [15:0] cb_acc_dst_w   = p3_fb_addr[1] ? fb_acc_data[31:16]
                                           : fb_acc_data[15:0];
// STAGE 2 (p2b->p3 shift) / RESOLVE / REFRESH share one set of 6 multiplies,
// operand-muxed on REGISTERED STATE COMPARES only — a multi-term refresh
// decode ahead of the multipliers cost -1.5 ns (sp_truecolor -> cb_sr), so
// the stale refresh is a 1-cycle FSM state like RESOLVE, not an IDLE-cycle
// special case.
wire [15:0] cbm_src_w   = (fbss == FBSS_CB_RESOLVE || fbss == FBSS_CB_REFRESH)
                        ? p3_color : p2b_color;
wire [15:0] cbm_dst_w   = (fbss == FBSS_CB_REFRESH) ? cb_acc_dst_w
                        : (fbss == FBSS_CB_RESOLVE) ? cb_dst_r
                        :                             p2b_cb_dst;
wire [12:0] cbm_sum_r_w = cbm_src_w[15:11] * sp_a6 + cbm_dst_w[15:11] * (7'd64 - sp_a6);
wire [12:0] cbm_sum_g_w = cbm_src_w[10:5]  * sp_a6 + cbm_dst_w[10:5]  * (7'd64 - sp_a6);
wire [12:0] cbm_sum_b_w = cbm_src_w[4:0]   * sp_a6 + cbm_dst_w[4:0]   * (7'd64 - sp_a6);
// ADDR dedup (audit A1): the 32-bit byte-lane data mask is the byte-wise
// replication of the 4-bit strobe — derive it from the ONE lane decoder
// above instead of elaborating fb_lane_data_mask()'s second 2:4 decode.
// Bit-identical by the function definitions (0001->000000FF, etc.).
wire [31:0] p3_fb_lane_data_mask_w = {{8{p3_fb_lane_mask_w[3]}},
                                      {8{p3_fb_lane_mask_w[2]}},
                                      {8{p3_fb_lane_mask_w[1]}},
                                      {8{p3_fb_lane_mask_w[0]}}};
wire        fb_acc_p3_word_match = !fb_acc_valid
                                  || (fb_acc_addr == p3_fb_word_addr_w);
wire        fb_acc_blend_word_match = !fb_acc_valid
                                     || (fb_acc_addr == blend_group_word_addr);
// Blend-resolved commit: the pixel's lane data is the repacked blend result
// instead of the raw color.
wire        p3_cb_commit_w = sp_truecolor && sp_blend && p3_cb_ready;
wire [31:0] p3_commit_lane_data_w = p3_cb_commit_w ? cb_lane_data_w
                                                  : p3_fb_lane_data_w;
wire blend_group_pipe_block = (fbss == FBSS_IDLE)
                           && blend_group_active
                           && p3_valid
                           && !p3_discard
                           && (!p3_flags[SPAN_TRANSLUC]
                               || (blend_group_word_addr != p3_fb_word_addr_w)
                               || (|(blend_group_mask & p3_fb_lane_mask_w)));
assign fp_pipe_shift_blocked = (p1_valid && !p1_tex_ready)
                            || (fbss != FBSS_IDLE)
                            || (p3_valid && !p3_discard && p3_z_test)
                            // Blend-miss/stale hold (mirrors the z-test
                            // term): a blend pixel that missed the window
                            // must HOLD in p3 through the CB_REQ..CB_RESOLVE
                            // detour, and a stale capture must hold for its
                            // one refresh cycle — the commit reads the live
                            // p3 regs.  Self-releasing: CB_RESOLVE sets
                            // p3_cb_ready, the refresh clears p3_cb_stale.
                            || (p3_valid && !p3_discard
                                && sp_truecolor && sp_blend
                                && (!p3_cb_ready || p3_cb_stale))
                            || blend_group_pipe_block
                            || fb_write_buffer_stall
                            || m_wr_inflight_near_full;
wire fp_pipe_stall = fp_pipe_shift_blocked || cmap_pipe_wait;

// Combinational tex address from p0 + DSP output.  Multiply-mode only
// (sp_tex_width is always non-zero in every real caller — tested in
// tb_gpu with tex_width ∈ {1, 16, 32, 64, 300} and in gpudemo with
// tex_width = 64).  The old shift-mode p0_shift_addr path was dead
// code; removing it saves the 32-bit 2:1 mux + the p0_shift_addr
// register and its variable-barrel-shift update logic.
// Texture byte address = base + (t>>16)*width + (s>>16).  tx_mul_q and the
// sign-extended s_int are signed 32-bit contributions; the sum is taken
// mod-2^26 (the SDRAM-visible address space) and only [GPU_ADDR_W-1:0]
// reaches tex_req_addr / gpu_tex_cache, so the add lives in the narrow
// domain.  Slicing each addend to GPU_ADDR_W keeps the addition width-clean.
// Texel offset in texels (t*width + s); the GPU works in texel units so
// tex_width/masks stay identical for both formats.  Truecolor texels are
// 2 bytes, so the byte offset is the texel offset << 1.
wire [GPU_ADDR_W-1:0] fp_tex_offset = tx_mul_q[GPU_ADDR_W-1:0]
                             + {{(GPU_ADDR_W-16){p0_s_int[15]}}, p0_s_int};
wire [GPU_ADDR_W-1:0] fp_tex_addr_full = p0_tex_base
                             + (sp_truecolor ? (fp_tex_offset << 1) : fp_tex_offset);

assign tex_req_valid = (state == S_FRAG_PIPE) && p0_valid
                    && !p1_valid;
assign tex_req_addr  = fp_tex_addr_full;
assign tex_req_wide  = sp_truecolor;   // 16-bit texel fetch for direct-color surfaces

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
reg               sp_zinv_step_zero;
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
// Pending-slot (slot B) quadratic 2nd-difference, copied into sp_scc/sp_tcc on
// the segment swap.  Zero except on a TRUECOLOR full-16px quadratic segment.
reg signed [31:0] persp_pend_scc;
reg signed [31:0] persp_pend_tcc;
// Previous segment's start anchor A_{N-1} (the 3-point parabola baseline) plus
// its validity (0 until persp_anchor has advanced once this span, so the first
// full segment stays linear — no A_{N-1} exists yet).
reg signed [31:0] persp_prev_anchor_s;
reg signed [31:0] persp_prev_anchor_t;
reg        persp_prev_valid;
reg        persp_seg_b_ready;
reg        persp_swap_pending;

// PSS — segment-setup sub-FSM. Runs alongside the issue stage (and fbss)
// inside S_FRAG_PIPE. Drives dsp_a/dsp_b and recip_rd_addr; reads
// dsp_p / recip_rd_data. ~17 cycles per advanced pass (16 on the
// no-advance first pass) — +1 vs the pre-T-split shape for the
// PSS_FINAL_PROD raw-product stage.
//
// Setup-side pipeline (PSS_ADV → PSS_ADV_ISSUE → PSS_ADV_CLAMP →
// PSS_CLZ → PSS_TOP8)
// is split into register-bounded stages because the original
// 1-cycle combinational
// chain (sp_zinv → +step<<4 → abs → 32-line CLZ casez → 32-bit barrel
// shift → top8 → recip_rd_addr) was the worst critical path in the
// design with -3.451 ns slack at 50 MHz. The split is:
//   PSS_ADV       : plan advance amount from span counters
//   PSS_ADV_ISSUE : launch DSP advance (step * advance; 16 or tail)
//   PSS_ADV_CLAMP : register |sp_zinv_new| and clamp decision
//   PSS_CLZ       : compute CLZ from registered abs; register persp_clz
//   PSS_TOP8      : compute top8 from (abs << clz); write recip_rd_addr
// PSS_RECIP_NA shares the same PSS_CLZ → PSS_TOP8 tail by registering
// abs of un-advanced sp_zinv into persp_zinv_abs_r and falling through.
localparam PSS_IDLE      = 6'd0;
localparam PSS_ADV       = 6'd1;   // stage 1: plan projection advance
localparam PSS_CLZ       = 6'd2;   // stage 3: compute CLZ from registered abs
localparam PSS_TOP8      = 6'd3;   // stage 4: compute top8; write recip_rd_addr
localparam PSS_RECIP_W   = 6'd4;   // BRAM read latency
localparam PSS_MUL       = 6'd5;   // kick BOTH dsp + dsp2 multiplies (operands pre-registered)
localparam PSS_MUL_W     = 6'd6;   // DSP pipeline delay (shared, both multiplies)
localparam PSS_FINAL     = 6'd7;   // round/slice both projected endpoints (from pss_prod_*_r)
localparam PSS_RECIP_NA  = 6'd8;   // ANCHOR_ONLY entry — register abs without advance
localparam PSS_RECIP_SHIFT = 6'd9; // stage between RECIP_W and MUL: compute recip_q16
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
localparam PSS_NR_MUL_X    = 6'd10;  // launch x * y0
localparam PSS_NR_MUL_X_W  = 6'd11;  // DSP pipeline delay
localparam PSS_NR_SUB      = 6'd12;  // capture xy, register 2 - xy
localparam PSS_NR_MUL_Y    = 6'd13;  // launch y0 * (2 - xy)
localparam PSS_NR_MUL_Y_W  = 6'd14;  // DSP pipeline delay
localparam PSS_NR_CAPTURE  = 6'd15;  // refined recip → recip_q16_r
localparam PSS_ADV_CLAMP   = 6'd16;  // stage 2: register abs/clamp from old/new zinv
localparam PSS_SLOPE       = 6'd17;  // commit anchor/slope from registered endpoints
localparam PSS_CONSTZ_STEP_W = 6'd18; // constant-Z step multiplies in flight; capture raw products
localparam PSS_CONSTZ_STEP_CAPTURE = 6'd19; // round/slice/commit constant-Z affine step
localparam PSS_SLOPE_PREP = 6'd20; // derive slope class from registered deltas
localparam PSS_SLOPE_DIV_WAIT = 6'd21; // wait for reused DSP small-divisor slope multiplies
localparam PSS_SLOPE_DIV_COMMIT = 6'd22; // capture small-divisor quotients
localparam PSS_SLOPE_DIV_CORR_WAIT = 6'd23; // wait for DSP quotient*divisor correction products
localparam PSS_SLOPE_DIV_CORR_COMMIT = 6'd24; // capture correction flags from DSP products
localparam PSS_SLOPE_DIV_QUOT_COMMIT = 6'd25; // apply quotient correction
localparam PSS_SLOPE_DIV_STEP_COMMIT = 6'd26; // sign/commit small-divisor slopes
localparam PSS_ADV_TAIL_ST_WAIT = 6'd27; // wait for Q29 tail sZ/tZ advance products
localparam PSS_ADV_TAIL_ST_CAPTURE = 6'd28; // capture sZ/tZ products, launch zinv product
localparam PSS_ADV_TAIL_Z_WAIT = 6'd29; // wait for Q29 tail zinv advance product
localparam PSS_ADV_TAIL_COMMIT = 6'd30; // commit Q29 tail advance
localparam PSS_ADV_ISSUE = 6'd31; // launch unified DSP advance (full-16 and tail)
localparam PSS_FINAL_PROD = 6'd32; // register RAW endpoint products ahead of PSS_FINAL
                                    // (T-split of the STA #1 cone: Mult fabric
                                    // recombination terminates here; the shared
                                    // Q29/Q16 rounding adder + shift/slice run a
                                    // state later in PSS_FINAL.  +1 PSS cycle per
                                    // pass, absorbed by the seg_a/b_ready handshake.)
localparam [5:0] PSS_Q29_RECIP_EXTRA = 6'd4;
localparam integer PSS_Q29_RECIP_EXTRA_INT = 4;
reg [5:0] persp_pss;
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

// T-split pipeline registers (2026-07 STA #1: Mult0 fabric recombination ->
// shared Q29/Q16 rounding adder -> shift/slice -> sp_sstep-family commit was
// one cycle).  The RAW 64-bit dsp/dsp2 products are registered here one PSS
// state before the rounding adder + slice, so the multiplier's fabric
// recombination and the 64-bit rounding carry never share a cycle with the
// slice/commit.  Written by the unique predecessor (PSS_FINAL_PROD /
// PSS_CONSTZ_STEP_W) of the state that reads them — B6 idiom, no reset.
reg signed [63:0] pss_prod_s_r;
reg signed [63:0] pss_prod_t_r;
// Pre-decoded constant-Z launch qualifier (2026-07 STA #2: the PSS_SLOPE
// state decode + persp_pass compare + sp_zinv_step_zero cone fed the shared
// dsp2_b operand-select).  Registered every S_FRAG_PIPE cycle as
// (persp_pass == PSS_PASS_ANCHOR) && sp_zinv_step_zero; both inputs are
// stable from PSS_IDLE / span emit through the whole pass (persp_pass is
// only written in PSS_IDLE and at span emit, sp_zinv_step_zero only at span
// emit, and any PSS pass spends >=8 S_FRAG_PIPE cycles before PSS_FINAL),
// so the flag is valid wherever the PSS sub-FSM samples it.  This collapses
// the DSP operand-select term to one state bit AND one FF.
reg pss_constz_go_r;

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
    reg signed [31:0] bias;
    begin
        // Truncate-toward-zero divide by 2^shift as bias-then-arith-shift:
        // negatives add (1<<shift)-1 first, turning the floor of >>> into
        // trunc.  Bit-exact with the old negate/shift/negate form for ALL
        // inputs incl. -2^31 (-2^31 + 7 still fits), one shifter shorter.
        case (shift)
            2'd0: bias = 32'sd0;
            2'd1: bias = 32'sd1;
            2'd2: bias = 32'sd3;
            default: bias = 32'sd7;
        endcase
        pss_div_pow2_trunc = (value + (value[31] ? bias : 32'sd0)) >>> shift;
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
reg [GPU_ADDR_W-1:0] fb_acc_addr;       // Word-aligned SDRAM address
reg        fb_acc_valid;      // Has pending data

// Param-span z-write accumulator.  Quake's z buffer is a CPU-visible short*
// in SDRAM, so z writes use the same ordered AXI write queue as framebuffer
// writes.  Adjacent horizontal pixels merge into full 32-bit words, letting the
// existing write-queue burst combiner handle long z runs.
reg [15:0] z_acc_lo;
reg [15:0] z_acc_hi;
reg [3:0]  z_acc_mask;
reg [GPU_ADDR_W-1:0] z_acc_addr;
reg        z_acc_valid;
wire [31:0] z_acc_data = {z_acc_hi, z_acc_lo};

// ----------------------------------------------------------------
// Z-test fold (roadmap 3.2).  The depth test used to freeze the
// whole pipe for every z-tested pixel — even z_acc / z-window HITS
// took the FBSS_IDLE -> ZTEST_ACC_EVAL detour.  These predicates
// evaluate the test while the fragment sits in p2b, all operands
// registered (p2b_*, z_acc_*, zw_*), and the p2b->p3 shift commits
// the verdict: pass clears p3_z_test (and merges the write into
// z_acc at the same edge), fail sets p3_discard.  Only window/acc
// MISSES keep p2b_z_test set and take the detour unchanged.
//
// Same-word RAW (two 16-bit z per word) needs no bypass network: a
// folded write lands in z_acc at the edge that moves its pixel into
// p3, and the follower's compare — evaluated during its own p2b
// residency, one cycle later — reads z_acc registers that already
// carry it.  A miss pixel stalls the pipe from p3 until the detour
// resolves, so the follower re-evaluates against the detour's
// z_acc update for the same reason.
//
// Fold cases mirror the FBSS arms byte-exactly:
//   * acc hit               -> compare vs the z_acc half; a pass-
//                              write merges that half (ACC_EVAL
//                              merge arm).
//   * window hit, acc empty -> compare vs the zw word; a pass-write
//                              loads the acc from the window word
//                              (ACC_EVAL from_read arm).
//   * window hit, acc dirty on ANOTHER word -> only test-only
//                              pixels fold (a write has nowhere to
//                              land until the acc flushes); writers
//                              detour into FBSS_IDLE's flush-then-
//                              serve exactly as before.
//   * translucent fragments never fold: the FBSS TRANSLUC arm
//                              consumes them ahead of the z arm
//                              today, so their z_test is dead.
// The z_src_pending apply (write-only z streams, end of the always
// block) can never collide with a fold's z_acc write: load_p0a_z
// requires !sp_z_test_enable and the pipe drains at command
// boundaries before sp_ flags change — the same exclusivity
// ACC_EVAL's mid-block z_acc writes already rely on.
// ----------------------------------------------------------------
wire [GPU_ADDR_W-1:0] p2b_z_word_addr_w = p2b_z_addr & {{(GPU_ADDR_W-2){1'b1}}, 2'b00};
wire p2b_zf_acc_hit = z_acc_valid && (z_acc_addr == p2b_z_word_addr_w);
wire p2b_zf_zw_hit  = (GPU_Z_READ_WINDOW > 1)
                    && !zw_snoop_pending
                    && zw_valid[p2b_z_word_addr_w[3:2]]
                    && (zw_base == p2b_z_word_addr_w[GPU_ADDR_W-1:4]);
// PRUNE GATE: with GPU_Z_READ_WINDOW==1 the zw arm is constant 0 so the
// zw_word read below folds to the constant else-arm and the window
// storage stays write-only (swept), as before.
wire [31:0] p2b_zf_word = p2b_zf_acc_hit ? {z_acc_hi, z_acc_lo}
                        : (GPU_Z_READ_WINDOW > 1)
                          ? zw_word[p2b_z_word_addr_w[3:2]]
                          : 32'd0;
wire [15:0] p2b_zf_old_half = fb_halfword_read(p2b_zf_word, p2b_z_addr[1]);
wire p2b_zf_fold = p2b_valid && p2b_z_test && !p2b_discard
                 && !p2b_flags[SPAN_TRANSLUC]
                 && (p2b_zf_acc_hit
                     || (p2b_zf_zw_hit && (!z_acc_valid || !p2b_z_write)));
wire p2b_zf_pass = (p2b_z_value >= p2b_zf_old_half);
reg        z_flush_valid;
reg [GPU_ADDR_W-1:0] z_flush_addr;
reg [31:0] z_flush_data;
reg [3:0]  z_flush_strb;
reg        z_src_pending_valid;
reg [GPU_ADDR_W-1:0] z_src_pending_addr;
reg        z_src_pending_hi;
reg [15:0] z_src_pending_half;

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
reg [GPU_ADDR_W-1:0] cr_addr;
reg [GPU_ADDR_W-1:0] cr_row_addr;
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
// Multiply mode: addr = base + (t>>16)*width + (s>>16)

// The pipelined fragment processor uses tx_mul_q (the dedicated
// DSP-inferred register below) for the per-pixel tex-coord multiply.

task finish_fragment_stream_after_flush;
    begin
        fb_acc_valid <= 1'b0;
        fb_acc_mask  <= 4'b0;
        z_acc_valid  <= 1'b0;
        z_acc_mask   <= 4'b0;
        z_flush_valid <= 1'b0;
        z_src_pending_valid <= 1'b0;
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
    reg [GPU_ADDR_W-1:0] fbwq_push_addr;
    reg [31:0] fbwq_push_data;
    reg [3:0]  fbwq_push_strb;
    reg        fbwq_req_links_fifo_tail;
    reg        fbwq_req_links_stage_tail;
    reg        fbwq_req_link_tail;
    reg        z_flush_set;
    reg        z_src_push;
    reg [GPU_ADDR_W-1:0] z_src_push_addr;
    reg        z_src_push_hi;
    reg [15:0] z_src_push_half;
    reg        z_src_pending_consume;
    reg        z_src_pending_applied;
    reg        acc_drain_done;

    fbwq_push_req  = 1'b0;
    fbwq_push_addr = {GPU_ADDR_W{1'b0}};
    fbwq_push_data = 32'b0;
    fbwq_push_strb = 4'b0;
    fbwq_req_links_fifo_tail = 1'b0;
    fbwq_req_links_stage_tail = 1'b0;
    fbwq_req_link_tail = 1'b0;
    z_flush_set = 1'b0;
    z_src_push = 1'b0;
    z_src_push_addr = {GPU_ADDR_W{1'b0}};
    z_src_push_hi = 1'b0;
    z_src_push_half = 16'd0;
    z_src_pending_consume = z_src_pending_valid && !z_flush_valid;
    z_src_pending_applied = 1'b0;
    acc_drain_done = 1'b0;

    if (!reset_n) begin
        state <= S_IDLE;
        ring_rdptr <= 0;
        ring_rd_addr <= 0;
        m_wr_awvalid <= 0;
        m_wr_wvalid <= 0;
        fbwq_rd_ptr <= 4'b0;
        fbwq_wr_ptr <= 4'b0;
        fbwq_count  <= 5'b0;
        fbwq_aw_ahead_valid <= 1'b0;
        fbwq_aw_ahead_words <= 4'b0;
        fbwq_link_next <= 16'b0;
        fbwq_tail_addr <= {GPU_ADDR_W{1'b0}};
        fbwq_tail_strb <= 4'b0;
        fbwq_burst_remaining <= 4'b0;
        fbwq_req_valid <= 1'b0;
        fbwq_req_addr <= {GPU_ADDR_W{1'b0}};
        fbwq_req_data <= 32'd0;
        fbwq_req_strb <= 4'd0;
        fbwq_stage_valid <= 1'b0;
        fbwq_stage_addr <= {GPU_ADDR_W{1'b0}};
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
        z_flush_valid <= 1'b0;
        z_flush_addr <= {GPU_ADDR_W{1'b0}};
        z_flush_data <= 32'd0;
        z_flush_strb <= 4'd0;
        z_src_pending_valid <= 1'b0;
        z_src_pending_addr <= {GPU_ADDR_W{1'b0}};
        z_src_pending_hi <= 1'b0;
        z_src_pending_half <= 16'd0;
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
        cmd_is_draw_column_list <= 0;
        cmd_is_draw_param_tri <= 0;
        cmd_is_set_tri_state <= 0;
        cmd_is_draw_vert_tri <= 0;
        cmd_is_draw_vert_tri_rgb <= 0;
        cmd_is_draw_param_tri_recs <= 0;
        cmd_is_set_object_state <= 0;
        cmd_is_draw_xform_tri <= 0;
        cmd_is_draw_xform_tri_rgb <= 0;
        cmd_is_draw_clip_tri <= 0;
        cmd_is_load_verts <= 0;
        cmd_is_draw_indexed_tri <= 0;
        cmd_is_set_light_state <= 0;
        cmd_is_load_vert_lit <= 0;
        xf_to_cache <= 1'b0;
        xf_lit <= 1'b0;
        lt_enable <= 1'b0;
        // B6: zc_s1/zc_s2/sp_zc_la carry no reset — any consumed zc_s2 read
        // (load_p0a with z enabled, rgb/truecolor) is preceded by a parametric
        // EMIT writing sp_zc_la/sp_zc_step + the 2-cycle g_zwarm warm rewriting
        // both stages; direct-affine spans have z off, so their p0a_z_value
        // capture of zc_s2 is dead (every consumer gates on p3_z_test/z_write).
        // g_zwarm KEEPS its reset (control counter gating load_p0a).
        g_zwarm <= 2'd0;
        tri_state_valid <= 1'b0;
        tri_start <= 1'b0;
        tri_fill_idx <= 3'd0;
        cmd_is_flip <= 0;
        cmd_class <= CMDCLS_NONE;
        spanprod_compact_direct <= 1'b0;
        m_wr_inflight       <= 4'b0;
        gpu_swap_req        <= 1'b0;
        gpu_swap_idx        <= 2'b0;
        pay_idx <= 0;
        pay_remaining <= 0;
        // Pipelined fragment processor reset
        p0a_valid <= 0; p0a_light <= 0; p0a_colormap_id <= 0; p0a_flags <= 0;
        p0a_R <= 0; p0a_B <= 0; p0_R <= 0; p0_B <= 0; p1_R <= 0; p1_B <= 0;
        // B6: sp_R_q/sp_B_q/sp_Dr,g,b_q + their _step regs carry no reset —
        // written by BOTH arms of spanprod_load_generated_span at every span
        // EMIT, and read only in the S_FRAG_PIPE pixel loop (source snapshot /
        // per-pixel advance), which is reachable only through that EMIT.
        sp_rgb <= 0;
        p0a_Dr <= 0; p0a_Dg <= 0; p0a_Db <= 0;
        p0_Dr <= 0; p0_Dg <= 0; p0_Db <= 0; p1_Dr <= 0; p1_Dg <= 0; p1_Db <= 0;
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
        p1_tex_ready <= 1'b0;
        p1_tex_color <= 16'd0;
        p2_valid <= 0; p2_color <= 0; p2_flags <= 0;
        p2_pr <= 0; p2_pg <= 0; p2_pb <= 0;        // SPIKE
        p2_dC_r <= 0; p2_dC_g <= 0; p2_dC_b <= 0;  // SPIKE
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
        cmap_pending_valid <= 1'b0;
        cmap_pending_addr <= 26'b0;
        fbss <= FBSS_IDLE;
        ztest_acc_old_half <= 16'd0;
        ztest_acc_from_read <= 1'b0;
        ztest_acc_word <= 32'd0;
        blend_arvalid    <= 0;
        blend_araddr     <= 0;
        blend_arlen_r    <= 2'd0;
        zw_valid         <= 4'b0;
        zw_base          <= {(GPU_ADDR_W-4){1'b0}};
        zw_fill_beat     <= 2'd0;
        zw_snoop_pending <= 1'b0;
        cbw_valid        <= 4'b0;
        cbw_base         <= {(GPU_ADDR_W-CBW_LOW){1'b0}};
        cbw_fill_beat    <= 2'd0;
        cbw_snoop_pending <= 1'b0;
        blend_group_active <= 0;
        blend_group_word_addr <= 0;
        blend_group_mask <= 0;
        blend_group_src_data <= 0;
        blend_result_word <= 0;
        blend_lane_iter <= 0;
        blend_lut_lane <= 0;
        blend_p3_match_r <= 0;
        src_done <= 0;
        sp_fastpath <= 1'b0;
        sp_clamp_enable <= 2'b00;
        sp_z_write_enable <= 1'b0;
        sp_z_test_enable <= 1'b0;
        sp_persp_q29_mode <= 1'b0;
        sp_q29_z_enable <= 1'b0;
        sp_q29_z_value <= 32'sd0;
        sp_q29_z_value_step <= 32'sd0;
        spanprod_active <= 0;
        spanprod_compact_direct <= 1'b0;
        spanprod_direct_affine <= 1'b0;
        spanprod_idx <= 0;
        spanprod_record_count <= 0;
        spanprod_records_left <= 0;
        spanprod_calc_step <= 0;
        spanprod_launch_step <= 3'd7;
        spanprod_q29_attr_shift <= 5'd0;
        spanprod_header_supported <= 1'b0;
        spanprod_cur_u <= 16'sd0;
        spanprod_cur_v <= 16'sd0;
        spanprod_cur_count <= 16'd0;
        spanprod_cur_nonzero <= 1'b0;
        // B6: the eight spanprod_cur_direct_* capture regs carry no reset —
        // their sole reader is the direct-affine arm of
        // spanprod_load_generated_span (S_SPANPROD_EMIT), which is reachable
        // only via S_SPANPROD_SELECT -> S_SPANPROD_SETUP, and SELECT writes
        // all eight every pass.  (With INCLUDE_COMPACT_SPAN=0 the reader is
        // hard-dead: spanprod_direct_affine's only set-site is compact-gated.)
        // B5 lane-bank masks.  The MLAB banks themselves have no reset (the
        // old arrays had none either); mask=0 makes count/colormap read as 0
        // — identical to the pre-first-write behavior of the old uncleared
        // arrays, and every compact/long command re-clears them at w0/w29
        // before any SELECT.  spanprod_s_zeroed/sstep_zeroed are deliberately
        // NOT reset here: they mirror column-written zeros in the unreset
        // s/sstep banks, whose lifetime spanned warm resets in the old FF
        // form (FFs power up 0, so cold boot matches without a reset term).
        spanprod_cnt_valid    <= 4'b0000;
        spanprod_cmap_valid   <= 4'b0000;
        sp_sZ <= 0; sp_tZ <= 0; sp_zinv <= 0;
        sp_sZstep <= 0; sp_tZstep <= 0; sp_zinv_step <= 0; sp_zinv_step_zero <= 1'b1;
        persp_active <= 0;
        sp_seg_left <= 0;
        persp_seg_a_ready <= 0;
        persp_anchor_s <= 0; persp_anchor_t <= 0;
        persp_prev_anchor_s <= 0; persp_prev_anchor_t <= 0;
        persp_prev_valid <= 0;
        sp_scc <= 0; sp_tcc <= 0;
        persp_first_done <= 0;
        persp_pend_s <= 0; persp_pend_t <= 0;
        persp_pend_sstep <= 0; persp_pend_tstep <= 0;
        persp_pend_scc <= 0; persp_pend_tcc <= 0;
        persp_seg_b_ready <= 0;
        persp_swap_pending <= 1'b0;
        persp_pss <= PSS_IDLE;
        persp_pass <= PSS_PASS_ANCHOR;
        persp_zinv_abs_r <= 0;
        pss_zinv_clamp_r <= 0;
        pss_constz_go_r <= 1'b0;
        // B6: pss_prod_s_r/pss_prod_t_r carry no reset — written by the
        // unique predecessor (PSS_FINAL_PROD / PSS_CONSTZ_STEP_W) of the
        // state that reads them.
        pss_zinv_adv_r <= 0;
        pss_zinv_prev_r <= 0;
        pss_zinv_abs_na_r <= 0;
        pss_slope_divisor <= 5'd16;
        // B6: pss_slope_{s,t}_{delta,mag,quot} carry no reset — each is
        // written by the unique predecessor of its reading PSS state
        // (delta: PSS_SLOPE TO_A/TO_B -> SLOPE_PREP; mag: SLOPE_PREP ->
        // DIV_CORR_COMMIT; quot: DIV_COMMIT -> QUOT/STEP_COMMIT), and the
        // chain is only entered from PSS_IDLE, which every launch site forces.
        pss_slope_s_neg <= 1'b0;
        pss_slope_t_neg <= 1'b0;
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
        dsp_a <= 0; dsp_b <= 0; dsp2_a <= 0; dsp2_b <= 0;
        // B6: drv_prod_r/drv2_prod_r carry no reset — each reading derive
        // state (DRV_DET_FORM / DRV_PLANE_NF / DRV_SCALE_LS / DRV_ORG_FORM)
        // is entered ONLY from its capture predecessor, which writes both.
        recip_rd_addr <= 0;
        // State registers
        sp_tex_w_mask <= 16'hFFFF; sp_tex_h_mask <= 16'hFFFF;
        // octave = mask+1 mod 2^16: 16'hFFFF + 1 wraps to 0 (mirror test
        // can never fire on the default no-op mask, as before).
        sp_tex_w_octave <= 16'h0000; sp_tex_h_octave <= 16'h0000;
        sp_mirror_s <= 1'b0; sp_mirror_t <= 1'b0;
        sp_cd_combine <= 1'b0; spanprod_cd_combine <= 1'b0;
        spanprod_subpix_y <= 1'b0;
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
            fbwq_aw_ahead_valid <= 1'b0;
            fbwq_aw_ahead_words <= 4'b0;
            fbwq_link_next <= 16'b0;
            fbwq_tail_addr <= {GPU_ADDR_W{1'b0}};
            fbwq_tail_strb <= 4'b0;
            fbwq_burst_remaining <= 4'b0;
            fbwq_req_valid <= 1'b0;
            fbwq_req_addr <= {GPU_ADDR_W{1'b0}};
            fbwq_req_data <= 32'd0;
            fbwq_req_strb <= 4'd0;
            fbwq_stage_valid <= 1'b0;
            fbwq_stage_link_tail <= 1'b0;
            fb_acc_valid <= 0;
            fb_acc_mask  <= 0;
            z_acc_valid <= 1'b0;
            z_acc_mask  <= 4'b0;
            z_flush_valid <= 1'b0;
            z_flush_addr <= {GPU_ADDR_W{1'b0}};
            z_flush_data <= 32'd0;
            z_flush_strb <= 4'b0;
            z_src_pending_valid <= 1'b0;
            z_src_pending_addr <= {GPU_ADDR_W{1'b0}};
            z_src_pending_hi <= 1'b0;
            z_src_pending_half <= 16'd0;
            p0a_valid <= 1'b0;
            p0_valid <= 1'b0;
            p1_valid <= 1'b0;
            p1_tex_ready <= 1'b0;
            p1_tex_color <= 16'd0;
            p2_valid <= 1'b0;
            p2b_valid <= 1'b0;
            p3_valid <= 1'b0;
            p3_z_test <= 1'b0;
            p3_z_write <= 1'b0;
            sp_fastpath <= 1'b0;
            sp_z_write_enable <= 1'b0;
            sp_z_test_enable <= 1'b0;
            sp_zinv_step_zero <= 1'b1;
            sp_persp_q29_mode <= 1'b0;
            sp_q29_z_enable <= 1'b0;
            sp_q29_z_value <= 32'sd0;
            sp_q29_z_value_step <= 32'sd0;
            fbss         <= FBSS_IDLE;
            ztest_acc_old_half <= 16'd0;
            ztest_acc_from_read <= 1'b0;
            ztest_acc_word <= 32'd0;
            blend_arvalid <= 1'b0;
            blend_arlen_r <= 2'd0;
            zw_valid      <= 4'b0;
            zw_fill_beat  <= 2'd0;
            zw_snoop_pending <= 1'b0;
            cbw_valid     <= 4'b0;
            cbw_fill_beat <= 2'd0;
            cbw_snoop_pending <= 1'b0;
            blend_group_active <= 1'b0;
            blend_group_mask <= 4'b0;
            spanprod_active <= 1'b0;
            spanprod_compact_direct <= 1'b0;
            spanprod_direct_affine <= 1'b0;
            cmd_is_draw_column_list <= 1'b0;
            cmd_is_draw_param_tri <= 1'b0;
            cmd_is_set_tri_state <= 1'b0;
            cmd_is_draw_vert_tri <= 1'b0;
            cmd_is_draw_vert_tri_rgb <= 1'b0;
            cmd_is_draw_param_tri_recs <= 1'b0;
            cmd_is_set_object_state <= 1'b0;
            cmd_is_draw_xform_tri <= 1'b0;
            cmd_is_draw_xform_tri_rgb <= 1'b0;
            cmd_is_draw_clip_tri <= 1'b0;
            cmd_is_load_verts <= 1'b0;
            cmd_is_draw_indexed_tri <= 1'b0;
            cmd_is_set_light_state <= 1'b0;
            // 0x4A sticky bank is invalidated on soft_reset so a 0x4B after a
            // reset is a no-op until a fresh 0x4A re-arms the state.
            tri_state_valid <= 1'b0;
            tri_start <= 1'b0;
            tri_fill_idx <= 3'd0;
            spanprod_q29_attr_shift <= 5'd0;
            spanprod_cur_u <= 16'sd0;
            spanprod_cur_v <= 16'sd0;
            spanprod_cur_count <= 16'd0;
            spanprod_cur_nonzero <= 1'b0;
            m_wr_inflight <= 4'b0;
            gpu_swap_req <= 1'b0;
            transluc_lookup_fire <= 1'b0;
            cmap_pending_valid <= 1'b0;
            cmap_pending_addr <= 26'b0;
        end else begin
            // ------------------------------------------------------------
            // Always-on housekeeping (runs every non-reset cycle).
            //
            // Keep the shared DSP operand registers driven every cycle.
            // Otherwise Quartus maps the sparse state-machine assignments
            // into a wide DSP input clock-enable cone that reaches through
            // the texture-cache stall path.
            dsp_a <= 32'sd0;
            dsp_b <= 32'sd0;
            dsp2_a <= 32'sd0;
            dsp2_b <= 32'sd0;
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


            // --------------------------------------------------------
            // FLUSH dedup (audit A2): the ONE accumulator-drain chain.
            // S_SPANPROD_SETUP (zero-count record) and S_FB_FLUSH each
            // spelled this z_flush -> z_acc -> fb_acc priority drain
            // ("if valid & can-issue -> push {addr,data,strb}, clear")
            // verbatim in their case arms; this shared copy is gated on
            // exactly those two states and sets acc_drain_done so each
            // arm keeps its original same-cycle continuation.
            //
            // acc_drain_done's !z_src_pending_valid term closes a latent
            // drift bug the audit flagged: the FBSS_IDLE z_acc evict
            // (which deliberately skips the z_flush priority check — safe
            // for write ordering because z_flush_addr != z_acc_addr is
            // invariant while z_flush_valid) can leave {z_acc empty,
            // z_flush pending, z source half parked in the 1-deep
            // z_src_pending slot}.  The old per-arm copies declared
            // "drained" on the very cycle that parked half was being
            // applied to z_acc, retiring to S_IDLE with a dirty z
            // accumulator that fb_write_drain_complete cannot see — so a
            // following CMD_FENCE could publish with the final z half
            // still unwritten.  Holding done until the pending slot is
            // empty keeps the chain in place for the 1-2 cycles needed
            // to observe (and drain) the applied half.
            // --------------------------------------------------------
            if ((state == S_FB_FLUSH)
                || (state == S_SPANPROD_SETUP
                    && spanprod_active && !spanprod_cur_nonzero)) begin
                if (z_flush_valid) begin
                    if (fb_write_can_issue) begin
                        fbwq_push_req  = 1'b1;
                        fbwq_push_addr = z_flush_addr;
                        fbwq_push_data = z_flush_data;
                        fbwq_push_strb = z_flush_strb;
                        z_flush_valid  <= 1'b0;
                    end
                end else if (z_acc_valid && |z_acc_mask) begin
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
                        fb_acc_valid   <= 1'b0;
                        fb_acc_mask    <= 4'b0;
                        acc_drain_done = !z_src_pending_valid;
                    end
                end else begin
                    acc_drain_done = !z_src_pending_valid;
                end
            end

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

            // Z-window exactness rule 3: drop the read caches at every
            // command boundary so CPU z-buffer/framebuffer writes between
            // commands (fence-synchronised) can never be shadowed by stale
            // window contents.  The blend dst window follows the same rule.
            zw_valid  <= 4'b0;
            cbw_valid <= 4'b0;
            cmd_is_fence          <= (cmd_type == CMD_FENCE);
            cmd_is_clear_rect     <= (cmd_type == CMD_CLEAR_RECT);
            cmd_is_set_fb         <= (cmd_type == CMD_SET_FB);
            // PRUNE GATE: when INCLUDE_COMPACT_SPAN==0 the size bound rises
            // to the long-form minimum (31-word header + first record pair),
            // so a compact-sized 0x48 (11..32 words) falls to CMDCLS_NONE:
            // the S_PAY_DATA default arm (payload drains word-by-word via
            // pay_remaining, no destination writes) and the S_EXECUTE
            // `default: state <= S_IDLE` — the exact unrecognised-command
            // no-op drain path.  Long-form payloads (>=33 words) decode
            // identically in both configs.
            cmd_is_draw_param_span_list <=
                (cmd_type == CMD_DRAW_PARAM_SPAN_LIST && cmd_payload_words >=
                 ((INCLUDE_COMPACT_SPAN != 0) ? 13'd11 : 13'd33));
            // Decode dedup: 0x4C reuses the 0x48 compact-direct case arms of
            // load_param_span_list_payload_word through the 5-word->7-word
            // index remap (column_compact_idx_remap), so the column decode
            // ALSO selects the compact branch.  The flag is read only by that
            // loader's branch select, so this is decode-routing only.
            // PRUNE GATE: constant 0 when INCLUDE_COMPACT_SPAN==0 — the
            // compact loader branch, lane bank and capture muxes all fold.
            spanprod_compact_direct <= (INCLUDE_COMPACT_SPAN != 0) &&
                                    ((cmd_type == CMD_DRAW_PARAM_SPAN_LIST &&
                                        cmd_payload_words <= 13'd32)
                                    || (cmd_type == CMD_DRAW_COLUMN_LIST));
            // CMD_DRAW_COLUMN_LIST (0x4C): 4-word header + 5N lane records
            // (N=1..4), so a valid payload is 9/14/19/24 words.  Accept the
            // 9..24 range here (the header count nibble re-derives the lane
            // count exactly); wrong-sized payloads outside the range drain and
            // retire as a no-op.  The loader delegates to the 0x48
            // compact-direct arms via the index remap and forces s/sstep=0.
            // PRUNE GATE: INCLUDE_COLUMN_LIST_EFF==0 makes the decode term
            // constant 0 — a 0x4C then takes the same drained-no-op path as
            // a wrong-sized payload on full hardware.
            cmd_is_draw_column_list <= (INCLUDE_COLUMN_LIST_EFF != 0) &&
                (cmd_type == CMD_DRAW_COLUMN_LIST
                 && cmd_payload_words >= 13'd9 && cmd_payload_words <= 13'd24);
            // Staging-dedup contract, variant-invariant (0x48/0x4C/0x49
            // alike): a header that FULL hardware would decode overwrites
            // the shared spanprod staging an 0x4A may have written, so the
            // sticky vert-tri state is invalidated on the raw opcode/size
            // ranges — deliberately NOT on the param-gated cmd_is_* flags,
            // so a lean variant that drains the draw still honours the
            // contract and a stale 0x4B after it stays a guarded no-op.
            if ((cmd_type == CMD_DRAW_PARAM_SPAN_LIST
                 && cmd_payload_words >= 13'd11)
             || (cmd_type == CMD_DRAW_COLUMN_LIST
                 && cmd_payload_words >= 13'd9 && cmd_payload_words <= 13'd24)
             || (cmd_type == CMD_DRAW_PARAM_TRI
                 && cmd_payload_words == 13'd36))
                tri_state_valid <= 1'b0;
            // Triangle form: fixed 36-word payload (31 header + 2 clip +
            // 3 vertex).  Wrong-sized payloads drain through S_PAY_DATA
            // without decode and retire at S_EXECUTE as a no-op.
            //
            // PRUNE GATE: when INCLUDE_PARAM_TRI==0 the 0x49 decode term is
            // constant 0, so a 0x49 falls to the S_PAY_DATA / S_EXECUTE
            // default arms as the unrecognised-command no-op drain.  Combined with
            // INCLUDE_VERT_TRI==0 and INCLUDE_PARAM_TRI_RECS==0 (os25), the
            // edge walker loses all producers and constant-folds away.  Note
            // the tri_state_valid clear above stays keyed on the RAW opcode
            // (the variant-invariant sticky-state contract), NOT this flag.
            cmd_is_draw_param_tri <= (INCLUDE_PARAM_TRI != 0) &&
                (cmd_type == CMD_DRAW_PARAM_TRI && cmd_payload_words == 13'd36);
            // Vertex-triangle pair (hardware plane derivation): 0x4A latches the
            // sticky bank (16-word payload), 0x4B draws one triangle from raw
            // verts (14-word payload).  Wrong-sized payloads drain and retire.
            //
            // 0x4A is UNGATED (decodes on every target): its payload lands in
            // the shared spanprod staging + the small tri_state_clip_* /
            // tri_state_valid persistents, which the records-only 0x4D path
            // below consumes — no derivation hardware involved.  When BOTH
            // INCLUDE_VERT_TRI and INCLUDE_PARAM_TRI_RECS are 0, the sticky
            // persistents have no remaining consumer (the 0x4B and 0x4D
            // EXECUTE arms are both gated by spelled-out constants) and sweep
            // naturally; the 0x4A decode + payload routing stays, writing only
            // the shared staging the 0x48/0x49 paths keep alive.
            //
            // PRUNE GATE: when INCLUDE_VERT_TRI==0 the 0x4B decode term is
            // constant 0, so the flag is never set (and cmd_class falls to
            // CMDCLS_NONE: the S_PAY_DATA default arm — no destination
            // writes; the payload still drains word-by-word via
            // pay_remaining — and the S_EXECUTE `default: state <= S_IDLE`,
            // i.e. the exact unrecognised-command no-op drain path).  With the flag
            // constant 0, every writer of the 0x4B-only staging
            // (dv_szi/dv_tzi/vt_zi/vt_lrow) and of the derivation FSM (dstate
            // and the S_TRI_DERIVE-only regs, reachable only via
            // `state <= S_TRI_DERIVE` in the gated 0x4B EXECUTE arm — itself
            // ALSO wrapped in the same constant) stays constant-inactive, so
            // the derivation logic sweeps even though tri_state_valid is now
            // a live register on every target.
            // Accept 16-word (legacy) OR 17-word (w16 = OF_GPU_SPAN_BLEND const
            // alpha) 0x4A.  Forcing ==17 would no-op every existing 16-word
            // sender (palettized/Quake2, all current tests).
            cmd_is_set_tri_state <=
                (cmd_type == CMD_SET_TRI_STATE
                 && (cmd_payload_words == 13'd16 || cmd_payload_words == 13'd17));
            cmd_is_draw_vert_tri <= (INCLUDE_VERT_TRI != 0) &&
                (cmd_type == CMD_DRAW_VERT_TRI && cmd_payload_words == 13'd14);
            // 0x4E: per-vertex RGB truecolor triangle (16-word payload).  Gated
            // on BOTH INCLUDE_VERT_TRI and INCLUDE_DIRECT_COLOR so the whole RGB
            // path (extra derive passes, sp_R/B, modulate) prunes off os30.
            // Shrunk format: 17-word common, 19-word combine (was 19/22).  q29
            // word dropped + 3 RGB565 packed into 2 words; combine D packed 3->2.
            cmd_is_draw_vert_tri_rgb <= (INCLUDE_VERT_TRI != 0) && (INCLUDE_DIRECT_COLOR != 0) &&
                (cmd_type == CMD_DRAW_VERT_TRI_RGB &&
                 (cmd_payload_words == 13'd17 || cmd_payload_words == 13'd19));
            // Records-only param-tri (0x4D): per-triangle planes + verts on
            // top of the 0x4A sticky state.  Wrong-sized payloads drain and
            // retire as a no-op.
            //
            // PRUNE GATE: when INCLUDE_PARAM_TRI_RECS==0 the decode term is
            // constant 0, so a 0x4D falls to CMDCLS_NONE: the S_PAY_DATA
            // default arm (payload drains word-by-word via pay_remaining,
            // no destination writes) and the S_EXECUTE
            // `default: state <= S_IDLE` — the exact unrecognised-command
            // no-op drain path — and Quartus sweeps the 0x4D routing/bring-up arms.
            cmd_is_draw_param_tri_recs <= (INCLUDE_PARAM_TRI_RECS != 0) &&
                (cmd_type == CMD_DRAW_PARAM_TRI_RECS && cmd_payload_words == 13'd16);
            // 0x50 sticky transform matrix/proj; 0x51 raw-vertex transform tri.
            // Gated on INCLUDE_VERT_TRI (they feed the same derive datapath).
            cmd_is_set_object_state <= (INCLUDE_VERT_TRI != 0) &&
                ((INCLUDE_GPU_XFORM_MAC != 0) || (INCLUDE_XFORM_RGB != 0) || (INCLUDE_CLIP_TRI != 0)) &&
                (cmd_type == CMD_SET_OBJECT_STATE && cmd_payload_words == 13'd26);
            cmd_is_draw_xform_tri <= (INCLUDE_VERT_TRI != 0) && (INCLUDE_GPU_XFORM_MAC != 0) &&
                (cmd_type == CMD_DRAW_XFORM_TRI && cmd_payload_words == 13'd16);
            // T1: 0x52 truecolor transform tri.  18-word payload: 3 verts {x,y,z}
            // + 3 {s,t} + 3 RGB565.  GPU computes zi+depth (no per-draw zi/depth
            // words).  Gated on the RGB datapath (XFORM_RGB+DIRECT_COLOR+VERT_TRI).
            // MAC term (2026-08-04): without it a MAC-less config decodes 0x52,
            // runs the zeroed matrix and silently draws nothing (review finding).
            // Gated out it falls to CMDCLS_NONE and drains as a clean no-op,
            // exactly like 0x51 always has.
            cmd_is_draw_xform_tri_rgb <= (INCLUDE_VERT_TRI != 0) && (INCLUDE_DIRECT_COLOR != 0) &&
                (INCLUDE_XFORM_RGB != 0) && (INCLUDE_GPU_XFORM_MAC != 0) &&
                (cmd_type == CMD_DRAW_XFORM_TRI_RGB && cmd_payload_words == 13'd18);
            // Clip-space feed (0x4F): wire-identical to 0x52 (18w), but the 3
            // "verts" are CPU-computed M*v clip {x,y,w}; S_XFORM skips the matrix
            // MAC and feeds recip+project directly.  Truecolor; needs no matrix.
            cmd_is_draw_clip_tri <= (INCLUDE_VERT_TRI != 0) && (INCLUDE_DIRECT_COLOR != 0) &&
                (INCLUDE_CLIP_TRI != 0) &&
                (cmd_type == CMD_DRAW_CLIP_TRI && cmd_payload_words == 13'd18);
            // T3: 0x53 LOAD_VERTS (header word + up to 8 verts x 6 words -> <=49
            // payload words, fits the 6-bit pay_idx) and 0x54 DRAW_INDEXED_TRI
            // (1 word: 3 cache indices).  Gated on the vertex cache.
            cmd_is_load_verts <= (INCLUDE_VTX_CACHE != 0) && (INCLUDE_XFORM_RGB != 0) &&
                (INCLUDE_GPU_XFORM_MAC != 0) &&
                (cmd_type == CMD_LOAD_VERTS &&
                 cmd_payload_words >= 13'd7 && cmd_payload_words <= 13'd49);
            // T3 0x56: clip-space cache load -- 7 words wire-identical to 0x53
            // but w1-3 carry CPU-computed M*v clip {x,y,w}; S_XFORM enters at
            // XF_CLIP_FEED (recip+project only), so this deliberately has NO
            // MAC term: it is the load path for MAC-less configs and for hosts
            // that pre-transform (Quake2 CPU geometry).
            cmd_is_load_vert_clip <= (INCLUDE_VTX_CACHE != 0) && (INCLUDE_XFORM_RGB != 0) &&
                (cmd_type == CMD_LOAD_VERT_CLIP && cmd_payload_words == 13'd8);
            cmd_is_draw_indexed_tri <= (INCLUDE_VTX_CACHE != 0) && (INCLUDE_XFORM_RGB != 0) &&
                (cmd_type == CMD_DRAW_INDEXED_TRI && cmd_payload_words == 13'd1);
            // T4: 0x55 SET_LIGHT_STATE sticky (6 words: dir x/y/z, light RGB565,
            // ambient RGB565, enable/count).
            cmd_is_set_light_state <= (INCLUDE_GPU_LIGHT != 0) && (INCLUDE_XFORM_RGB != 0) &&
                (cmd_type == CMD_SET_LIGHT_STATE && cmd_payload_words == 13'd6);
            // T4: 0x57 LOAD_VERT_LIT (9 words: slot + xyz + normal-xyz + s/t) —
            // GPU transforms position AND computes lighting -> cache slot.
            cmd_is_load_vert_lit <= (INCLUDE_GPU_LIGHT != 0) && (INCLUDE_VTX_CACHE != 0) &&
                (INCLUDE_XFORM_RGB != 0) && (INCLUDE_GPU_XFORM_MAC != 0) &&
                (cmd_type == CMD_LOAD_VERT_LIT && cmd_payload_words == 13'd9);
            cmd_is_flip           <= (cmd_type == CMD_FLIP);
            // Registered command class — same decode, same cycle as the
            // cmd_is_* flags above, but as a parallel case on cmd_type so
            // the S_PAY_DATA / S_EXECUTE dispatch is a case select instead
            // of a priority chain (see the CMDCLS_* block).  Every size /
            // feature gate here MUST stay bit-identical to the flag it
            // mirrors; a failed gate falls to CMDCLS_NONE, the
            // unrecognised-command drain.
            case (cmd_type)
                CMD_FENCE:       cmd_class <= CMDCLS_FENCE;
                CMD_FLIP:        cmd_class <= CMDCLS_FLIP;
                CMD_CLEAR_RECT:  cmd_class <= CMDCLS_CLEAR_RECT;
                CMD_SET_FB:      cmd_class <= CMDCLS_SET_FB;
                CMD_SET_TEXTURE: cmd_class <= CMDCLS_SET_TEXTURE;
                CMD_DRAW_PARAM_SPAN_LIST:
                    cmd_class <= (cmd_payload_words >=
                                  ((INCLUDE_COMPACT_SPAN != 0) ? 13'd11 : 13'd33))
                               ? CMDCLS_SPAN_COL : CMDCLS_NONE;
                CMD_DRAW_COLUMN_LIST:
                    cmd_class <= ((INCLUDE_COLUMN_LIST_EFF != 0)
                                  && cmd_payload_words >= 13'd9
                                  && cmd_payload_words <= 13'd24)
                               ? CMDCLS_SPAN_COL : CMDCLS_NONE;
                CMD_DRAW_PARAM_TRI:
                    cmd_class <= ((INCLUDE_PARAM_TRI != 0)
                                  && cmd_payload_words == 13'd36)
                               ? CMDCLS_PARAM_TRI : CMDCLS_NONE;
                CMD_SET_TRI_STATE:
                    cmd_class <= (cmd_payload_words == 13'd16
                                  || cmd_payload_words == 13'd17)
                               ? CMDCLS_SET_TRI_STATE : CMDCLS_NONE;
                CMD_DRAW_VERT_TRI:
                    cmd_class <= ((INCLUDE_VERT_TRI != 0)
                                  && cmd_payload_words == 13'd14)
                               ? CMDCLS_VERT_TRI : CMDCLS_NONE;
                CMD_DRAW_VERT_TRI_RGB:
                    cmd_class <= ((INCLUDE_VERT_TRI != 0) && (INCLUDE_DIRECT_COLOR != 0)
                                  && (cmd_payload_words == 13'd17
                                      || cmd_payload_words == 13'd19))
                               ? CMDCLS_VERT_TRI_RGB : CMDCLS_NONE;
                CMD_DRAW_PARAM_TRI_RECS:
                    cmd_class <= ((INCLUDE_PARAM_TRI_RECS != 0)
                                  && cmd_payload_words == 13'd16)
                               ? CMDCLS_PARAM_TRI_RECS : CMDCLS_NONE;
                CMD_SET_OBJECT_STATE:
                    cmd_class <= ((INCLUDE_VERT_TRI != 0)
                                  && ((INCLUDE_GPU_XFORM_MAC != 0) || (INCLUDE_XFORM_RGB != 0)
                                      || (INCLUDE_CLIP_TRI != 0))
                                  && cmd_payload_words == 13'd26)
                               ? CMDCLS_SET_OBJECT_STATE : CMDCLS_NONE;
                CMD_DRAW_XFORM_TRI:
                    cmd_class <= ((INCLUDE_VERT_TRI != 0) && (INCLUDE_GPU_XFORM_MAC != 0)
                                  && cmd_payload_words == 13'd16)
                               ? CMDCLS_XFORM_TRI : CMDCLS_NONE;
                CMD_DRAW_XFORM_TRI_RGB:
                    cmd_class <= ((INCLUDE_VERT_TRI != 0) && (INCLUDE_DIRECT_COLOR != 0)
                                  && (INCLUDE_XFORM_RGB != 0) && (INCLUDE_GPU_XFORM_MAC != 0)
                                  && cmd_payload_words == 13'd18)
                               ? CMDCLS_XFORM_RGB_CLIP : CMDCLS_NONE;
                CMD_DRAW_CLIP_TRI:
                    cmd_class <= ((INCLUDE_VERT_TRI != 0) && (INCLUDE_DIRECT_COLOR != 0)
                                  && (INCLUDE_CLIP_TRI != 0)
                                  && cmd_payload_words == 13'd18)
                               ? CMDCLS_XFORM_RGB_CLIP : CMDCLS_NONE;
                CMD_LOAD_VERTS:
                    cmd_class <= ((INCLUDE_VTX_CACHE != 0) && (INCLUDE_XFORM_RGB != 0)
                                  && (INCLUDE_GPU_XFORM_MAC != 0)
                                  && cmd_payload_words >= 13'd7
                                  && cmd_payload_words <= 13'd49)
                               ? CMDCLS_LOAD_VERTS : CMDCLS_NONE;
                CMD_LOAD_VERT_CLIP:
                    cmd_class <= ((INCLUDE_VTX_CACHE != 0) && (INCLUDE_XFORM_RGB != 0)
                                  && cmd_payload_words == 13'd8)
                               ? CMDCLS_LOAD_VERT_CLIP : CMDCLS_NONE;
                CMD_DRAW_INDEXED_TRI:
                    cmd_class <= ((INCLUDE_VTX_CACHE != 0) && (INCLUDE_XFORM_RGB != 0)
                                  && cmd_payload_words == 13'd1)
                               ? CMDCLS_INDEXED_TRI : CMDCLS_NONE;
                CMD_SET_LIGHT_STATE:
                    cmd_class <= ((INCLUDE_GPU_LIGHT != 0) && (INCLUDE_XFORM_RGB != 0)
                                  && cmd_payload_words == 13'd6)
                               ? CMDCLS_SET_LIGHT_STATE : CMDCLS_NONE;
                CMD_LOAD_VERT_LIT:
                    cmd_class <= ((INCLUDE_GPU_LIGHT != 0) && (INCLUDE_VTX_CACHE != 0)
                                  && (INCLUDE_XFORM_RGB != 0) && (INCLUDE_GPU_XFORM_MAC != 0)
                                  && cmd_payload_words == 13'd9)
                               ? CMDCLS_LOAD_VERT_LIT : CMDCLS_NONE;
                default:         cmd_class <= CMDCLS_NONE;
            endcase

            if (cmd_payload_words == 0) begin
                state <= S_EXECUTE;
            end else begin
                pay_idx <= 6'd0;
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
        // cost is the payload index counter and pay_remaining.
        //
        S_PAY_DATA: begin
            if (pay_idx != 6'd63)
                pay_idx <= pay_idx + 6'd1;
            pay_remaining <= pay_remaining - 13'd1;

            // Per-command dispatch.  A parallel case on the cmd_class code
            // registered in S_DECODE — the cmd_is_* flags are one-hot but
            // a priority chain over them folds every higher flag's negation
            // into each lower branch's register enables (see the CMDCLS_*
            // block).  Shared arms still read individual cmd_is_* flags
            // where they must route per-command.
            case (cmd_class)
            CMDCLS_FENCE: begin
                // Publish the token only AFTER outstanding m_wr_* writes
                // drain in S_EXECUTE — fixes the flashing-pixel race
                // documented in cr-gpu-fence-write-completion.md.
                if (pay_idx == 6'd0) pending_fence_token <= ring_rd_data;
            end
            CMDCLS_FLIP: begin
                // CMD_FLIP payload: word 0 = idx, word 1 = fence token.
                if (pay_idx == 6'd0) pending_swap_idx    <= ring_rd_data[1:0];
                if (pay_idx == 6'd1) pending_fence_token <= ring_rd_data;
            end
            CMDCLS_CLEAR_RECT: begin
                if (pay_idx == 6'd0) begin
                    cr_addr     <= ring_rd_data[GPU_ADDR_W-1:0];
                    cr_row_addr <= ring_rd_data[GPU_ADDR_W-1:0];
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
            CMDCLS_SET_FB: begin
                if (pay_idx == 6'd1) st_fb_stride <= ring_rd_data[15:0];
            end
            CMDCLS_SPAN_COL, CMDCLS_PARAM_TRI: begin
                // Staging-dedup contract: tri_state_valid is cleared in
                // S_DECODE on the raw opcode/size ranges (variant-invariant
                // — see the comment there), not here, so lean variants that
                // drain these draws still invalidate the 0x4A sticky state.
                // Loader-expansion dedup: 0x48 (all words), 0x4C (via the
                // column->compact index remap; S_DECODE selects the compact
                // branch) and 0x49 words 0-30 route through ONE
                // variable-index expansion of the shared payload loader
                // instead of three — one copy of the ~60-arm index decode
                // feeding the staging registers, not three.
                if (!cmd_is_draw_param_tri || pay_idx <= 6'd30)
                    load_param_span_list_payload_word(
                        cmd_is_draw_column_list
                            ? column_compact_idx_remap(pay_idx)
                            : pay_idx,
                        ring_rd_data);
                if (cmd_is_draw_column_list && pay_idx == 6'd0) begin
                    // Force the dropped s/sstep words to 0 for every lane — a
                    // column never carries them, and the pixel path must match
                    // a 0x48 column with s=0/sstep=0 exactly.  B5: the arrays
                    // are MLABs now, so the 4-lane zeroing is the sticky
                    // per-lane masks (SELECT forces the capture to 0 until a
                    // compact s/sstep word rewrites the lane — the exact
                    // lifetime of the old written-0 array entries).
                    spanprod_s_zeroed     <= 4'b1111;
                    spanprod_sstep_zeroed <= 4'b1111;
                end
                // 0x49 words 31-35: clip rect + vertices.
                if (cmd_is_draw_param_tri) begin
                    if (pay_idx == 6'd31) begin
                        tri_clip_x0 <= ring_rd_data[15:0];
                        tri_clip_x1 <= ring_rd_data[31:16];
                    end else if (pay_idx == 6'd32) begin
                        tri_clip_y0 <= ring_rd_data[15:0];
                        tri_clip_y1 <= ring_rd_data[31:16];
                    end else if (pay_idx == 6'd33) begin
                        tri_v0_x <= ring_rd_data[15:0];
                        tri_v0_y <= ring_rd_data[31:16];
                    end else if (pay_idx == 6'd34) begin
                        tri_v1_x <= ring_rd_data[15:0];
                        tri_v1_y <= ring_rd_data[31:16];
                    end else if (pay_idx == 6'd35) begin
                        tri_v2_x <= ring_rd_data[15:0];
                        tri_v2_y <= ring_rd_data[31:16];
                    end
                end
            end
            CMDCLS_SET_TRI_STATE: begin
                // 0x4A sticky state.  Staging-dedup: surface/control/clamp/z
                // words are decoded DIRECTLY into the SHARED spanprod staging
                // regs (reusing the 0x49-header arms of
                // load_param_span_list_payload_word — the layouts are
                // field-compatible), so there is no dedicated tri_state_* bank
                // to copy at EXECUTE.  Only the clip rect persists (the walker
                // consumes it per draw and 0x49 carries its own clip, so it
                // doesn't overlap the spanprod staging).  See the opcode CONTRACT
                // CHANGE comment: a later 0x48/0x49 header overwrites this
                // staging and clears tri_state_valid, so the client must
                // re-issue 0x4A before more 0x4B draws.
                //   0x4A word -> spanprod_arm idx :
                //     w0..w4 -> 0..4 (fb_base/major/minor, tex_addr, tex_width)
                //     w5     -> packed {h_mask,w_mask}  (custom, see below)
                //     w6     -> 7  (control == 0x49 header word 7 semantics)
                //     w7..w10 -> 20..23 (clamps)
                //     w11..w13 -> 26..28 (z_base/major/minor)
                //     w14,w15 -> clip rect (kept)
                case (pay_idx)
                    6'd0:  load_param_span_list_payload_word(6'd0, ring_rd_data);
                    6'd1:  load_param_span_list_payload_word(6'd1, ring_rd_data);
                    6'd2:  load_param_span_list_payload_word(6'd2, ring_rd_data);
                    6'd3:  load_param_span_list_payload_word(6'd3, ring_rd_data);
                    6'd4:  load_param_span_list_payload_word(6'd4, ring_rd_data);
                    6'd5:  begin
                        // 0x4A packs both POT masks in one word; the 0x49 arms
                        // (idx 5/6) take one mask each from data[15:0], so
                        // unpack here instead of routing through the task.
                        spanprod_tex_w_mask <= (ring_rd_data[15:0]  == 16'd0)
                                             ? 16'hFFFF : ring_rd_data[15:0];
                        spanprod_tex_h_mask <= (ring_rd_data[31:16] == 16'd0)
                                             ? 16'hFFFF : ring_rd_data[31:16];
                    end
                    6'd6:  load_param_span_list_payload_word(6'd7,  ring_rd_data);
                    6'd7:  load_param_span_list_payload_word(6'd20, ring_rd_data);
                    6'd8:  load_param_span_list_payload_word(6'd21, ring_rd_data);
                    6'd9:  load_param_span_list_payload_word(6'd22, ring_rd_data);
                    6'd10: load_param_span_list_payload_word(6'd23, ring_rd_data);
                    6'd11: load_param_span_list_payload_word(6'd26, ring_rd_data);
                    6'd12: load_param_span_list_payload_word(6'd27, ring_rd_data);
                    6'd13: load_param_span_list_payload_word(6'd28, ring_rd_data);
                    6'd14: begin
                        tri_state_clip_x0 <= ring_rd_data[15:0];
                        tri_state_clip_x1 <= ring_rd_data[31:16];
                    end
                    6'd15: begin
                        tri_state_clip_y0 <= ring_rd_data[15:0];
                        tri_state_clip_y1 <= ring_rd_data[31:16];
                    end
                    6'd16: spanprod_const_alpha <= ring_rd_data[7:0]; // 17-word 0x4A: OF_GPU_SPAN_BLEND src alpha
                    default: ;
                endcase
            end
            CMDCLS_VERT_TRI: begin
                // 0x4B raw vertex triangle — see the opcode comment.
                // TIMING DE-FOLD (2026-06, OS30 pipelining): the szi/tzi
                // numerator products are NO LONGER folded into payload arrival.
                // Launching the DSP product (dsp_a<=dv_szi[k]; dsp_b<=ring_rd_data)
                // and capturing dsp_p[47:16] back into dv_szi[k] during S_PAY_DATA
                // put a DSP multiply + the [47:16] capture in the per-word decode
                // cone that also writes spanprod_count via the other command arms
                // (load_param_span_list_payload_word).  That deepened the
                // pay_idx -> spanprod_count setup path (OS30 WNS ~-1.7..-2.0).
                // Here we ONLY park the raw operands: s_k/t_k in dv_szi/dv_tzi
                // (w3-8), zi_k in vt_zi (w9-11), light in vt_lrow (w12).  The six
                // products run in DEDICATED derivation-FSM states (DRV_PROD_*),
                // inside the walker's idle setup window, off this combinational
                // path.  The DSP operands are NOT touched in S_PAY_DATA anymore.
                case (pay_idx)
                    6'd0: begin tri_v0_x <= ring_rd_data[15:0];
                                 tri_v0_y <= ring_rd_data[31:16]; end
                    6'd1: begin tri_v1_x <= ring_rd_data[15:0];
                                 tri_v1_y <= ring_rd_data[31:16]; end
                    6'd2: begin tri_v2_x <= ring_rd_data[15:0];
                                 tri_v2_y <= ring_rd_data[31:16]; end
                    // w3-5 / w6-8: park raw s_k / t_k in the product slots.
                    6'd3: dv_szi[0] <= ring_rd_data;
                    6'd4: dv_szi[1] <= ring_rd_data;
                    6'd5: dv_szi[2] <= ring_rd_data;
                    6'd6: dv_tzi[0] <= ring_rd_data;
                    6'd7: dv_tzi[1] <= ring_rd_data;
                    6'd8: dv_tzi[2] <= ring_rd_data;
                    // w9-11: park raw zi (the zi plane needs the raw value; the
                    // DRV_PROD_* states also consume it for s_k*zi_k/t_k*zi_k).
                    // No DSP launch here — products run in the derivation FSM.
                    6'd9:  vt_zi[0] <= ring_rd_data;
                    6'd10: vt_zi[1] <= ring_rd_data;
                    6'd11: vt_zi[2] <= ring_rd_data;
                    6'd12: begin
                        // latch light rows.
                        vt_lrow[0] <= ring_rd_data[5:0];
                        vt_lrow[1] <= ring_rd_data[11:6];
                        vt_lrow[2] <= ring_rd_data[17:12];
                    end
                    6'd13: begin
                        // per-triangle Q29 override: bit5 = q29 enable, [4:0] =
                        // shared attr shift (the CPU magnitude estimate).  w13==0
                        // keeps the legacy Q16.16 derive (fully backward-compat).
                        vt_q29_en    <= ring_rd_data[5];
                        vt_q29_shift <= ring_rd_data[4:0];
                    end
                    default: ;
                endcase
            end
            CMDCLS_VERT_TRI_RGB: begin
                // 0x4E (shrunk): w0-11 identical to 0x4B (verts, s, t, zi).
                // w12-13 carry the three per-vertex RGB565 colours PACKED
                // (w12=[rgb1|rgb0], w13=rgb2); w14-16 = per-vertex decoupled
                // depth; w17-18 (19-word combine payload only) add the packed
                // additive-D RGB565 triple.  The legacy per-tri Q29 word is GONE
                // (SM64 always sent 0); vt_q29_en is force-cleared at idx0 so a
                // prior 0x51/0x52 enable cannot leak in.  Combine is selected by
                // the 0x4A bit-30 sticky state (sp_cd_combine), NOT the payload
                // size, so a stale vt_Drrow on the 17-word path is never read.
                case (pay_idx)
                    6'd0: begin tri_v0_x <= ring_rd_data[15:0];
                                 tri_v0_y <= ring_rd_data[31:16];
                                 vt_q29_en <= 1'b0; vt_q29_shift <= 5'd0; end
                    6'd1: begin tri_v1_x <= ring_rd_data[15:0];
                                 tri_v1_y <= ring_rd_data[31:16]; end
                    6'd2: begin tri_v2_x <= ring_rd_data[15:0];
                                 tri_v2_y <= ring_rd_data[31:16]; end
                    6'd3: dv_szi[0] <= ring_rd_data;
                    6'd4: dv_szi[1] <= ring_rd_data;
                    6'd5: dv_szi[2] <= ring_rd_data;
                    6'd6: dv_tzi[0] <= ring_rd_data;
                    6'd7: dv_tzi[1] <= ring_rd_data;
                    6'd8: dv_tzi[2] <= ring_rd_data;
                    6'd9:  vt_zi[0] <= ring_rd_data;
                    6'd10: vt_zi[1] <= ring_rd_data;
                    6'd11: vt_zi[2] <= ring_rd_data;
                    // w12: vert0 RGB565 in [15:0], vert1 in [31:16] (packed).
                    // RGB565: R=[15:11], G(=light slot)=[10:5], B=[4:0].
                    6'd12: begin vt_rrow[0] <= ring_rd_data[15:11];
                                 vt_lrow[0] <= ring_rd_data[10:5];
                                 vt_brow[0] <= ring_rd_data[4:0];
                                 vt_rrow[1] <= ring_rd_data[31:27];
                                 vt_lrow[1] <= ring_rd_data[26:21];
                                 vt_brow[1] <= ring_rd_data[20:16]; end
                    // w13: vert2 RGB565 in [15:0]
                    6'd13: begin vt_rrow[2] <= ring_rd_data[15:11];
                                 vt_lrow[2] <= ring_rd_data[10:5];
                                 vt_brow[2] <= ring_rd_data[4:0]; end
                    // w14-16: per-vertex decoupled depth (high-range 1/w)
                    6'd14: vt_depth[0] <= ring_rd_data;
                    6'd15: vt_depth[1] <= ring_rd_data;
                    6'd16: vt_depth[2] <= ring_rd_data;
                    // w17-18 (19-word combine only): packed additive-D RGB565.
                    // w17 = vert0 [15:0] + vert1 [31:16]; w18 = vert2 [15:0].
                    6'd17: begin vt_Drrow[0] <= ring_rd_data[15:11];
                                 vt_Dgrow[0] <= ring_rd_data[10:5];
                                 vt_Dbrow[0] <= ring_rd_data[4:0];
                                 vt_Drrow[1] <= ring_rd_data[31:27];
                                 vt_Dgrow[1] <= ring_rd_data[26:21];
                                 vt_Dbrow[1] <= ring_rd_data[20:16]; end
                    6'd18: begin vt_Drrow[2] <= ring_rd_data[15:11];
                                 vt_Dgrow[2] <= ring_rd_data[10:5];
                                 vt_Dbrow[2] <= ring_rd_data[4:0]; end
                    default: ;
                endcase
            end
            CMDCLS_SET_OBJECT_STATE: begin
                // 0x50: load the up-to-5x4 transform matrix (row-major) + proj
                // consts + row count.  Sticky like 0x4A — does NOT clear
                // tri_state_valid or touch the surface staging.
                //   w0..w19  -> matrix (rows 0-2 cam, 3-4 s/t)
                //   w20..w24 -> xc/yc/xscale/yscale/nearclip
                //   w25      -> matrix row count (3 alias / 5 world)
                if (pay_idx < 6'd20) begin
                    if (INCLUDE_GPU_XFORM_MAC != 0)   // xf_M folds away when 0 (clip-feed)
                        xf_M[pay_idx[4:0]] <= ring_rd_data;
                end
                else case (pay_idx)
                    6'd20: xf_xc       <= ring_rd_data;
                    6'd21: xf_yc       <= ring_rd_data;
                    6'd22: xf_xscale   <= ring_rd_data;
                    6'd23: xf_yscale   <= ring_rd_data;
                    6'd24: xf_nearclip <= ring_rd_data;
                    6'd25: begin
                        // w25: [2:0]=row count, [3]=q29 enable (world), [8:4]=shift
                        xf_rows      <= ring_rd_data[2:0];
                        xf_q29_en    <= ring_rd_data[3];
                        xf_q29_shift <= ring_rd_data[8:4];
                    end
                    default: ;
                endcase
            end
            CMDCLS_XFORM_TRI: begin
                // 0x51: 3 raw verts {x,y,z} Q16.16 + s/t passthrough (parked in
                // the derive's raw-s/t slots) + light.  S_XFORM transforms them.
                case (pay_idx)
                    6'd0: xf_vx[0] <= ring_rd_data; 6'd1: xf_vy[0] <= ring_rd_data;
                    6'd2: xf_vz[0] <= ring_rd_data;
                    6'd3: xf_vx[1] <= ring_rd_data; 6'd4: xf_vy[1] <= ring_rd_data;
                    6'd5: xf_vz[1] <= ring_rd_data;
                    6'd6: xf_vx[2] <= ring_rd_data; 6'd7: xf_vy[2] <= ring_rd_data;
                    6'd8: xf_vz[2] <= ring_rd_data;
                    6'd9:  dv_szi[0] <= ring_rd_data; 6'd10: dv_szi[1] <= ring_rd_data;
                    6'd11: dv_szi[2] <= ring_rd_data;
                    6'd12: dv_tzi[0] <= ring_rd_data; 6'd13: dv_tzi[1] <= ring_rd_data;
                    6'd14: dv_tzi[2] <= ring_rd_data;
                    6'd15: begin
                        vt_lrow[0] <= ring_rd_data[5:0];
                        vt_lrow[1] <= ring_rd_data[11:6];
                        vt_lrow[2] <= ring_rd_data[17:12];
                    end
                    default: ;
                endcase
            end
            CMDCLS_XFORM_RGB_CLIP: begin
                // T1 0x52 / clip-feed 0x4F (identical wire — verts hold clip {x,y,w}
                // for 0x4F): 3 verts {x,y,z} Q16.16 (w0-8) + s/t passthrough
                // (w9-14) — identical to 0x51 — then per-vertex RGB565 (w15-17)
                // in place of 0x51's packed light word.  zi+depth are GPU-computed
                // in S_XFORM (no per-draw zi/depth words).
                case (pay_idx)
                    6'd0: xf_vx[0] <= ring_rd_data; 6'd1: xf_vy[0] <= ring_rd_data;
                    6'd2: xf_vz[0] <= ring_rd_data;
                    6'd3: xf_vx[1] <= ring_rd_data; 6'd4: xf_vy[1] <= ring_rd_data;
                    6'd5: xf_vz[1] <= ring_rd_data;
                    6'd6: xf_vx[2] <= ring_rd_data; 6'd7: xf_vy[2] <= ring_rd_data;
                    6'd8: xf_vz[2] <= ring_rd_data;
                    6'd9:  dv_szi[0] <= ring_rd_data; 6'd10: dv_szi[1] <= ring_rd_data;
                    6'd11: dv_szi[2] <= ring_rd_data;
                    6'd12: dv_tzi[0] <= ring_rd_data; 6'd13: dv_tzi[1] <= ring_rd_data;
                    6'd14: dv_tzi[2] <= ring_rd_data;
                    // RGB565 per vertex: R=[15:11], G(=light slot)=[10:5], B=[4:0]
                    6'd15: begin vt_rrow[0] <= ring_rd_data[15:11];
                                 vt_lrow[0] <= ring_rd_data[10:5];
                                 vt_brow[0] <= ring_rd_data[4:0]; end
                    6'd16: begin vt_rrow[1] <= ring_rd_data[15:11];
                                 vt_lrow[1] <= ring_rd_data[10:5];
                                 vt_brow[1] <= ring_rd_data[4:0]; end
                    6'd17: begin vt_rrow[2] <= ring_rd_data[15:11];
                                 vt_lrow[2] <= ring_rd_data[10:5];
                                 vt_brow[2] <= ring_rd_data[4:0]; end
                    default: ;
                endcase
            end
            CMDCLS_LOAD_VERTS,
            CMDCLS_LOAD_VERT_CLIP: begin
                // T3 0x53/0x56: one vert (raw {x,y,z} for 0x53, clip {x,y,w}
                // for 0x56 -- identical wire) + {s,t} + RGB565 (parked in the
                // transform's vert-0 slots) + destination cache slot.  S_XFORM
                // transforms it and writes the slot (see XF_PROJ_LAUNCH).
                case (pay_idx)
                    6'd0: xf_load_slot <= ring_rd_data[4:0];
                    6'd1: xf_vx[0] <= ring_rd_data;
                    6'd2: xf_vy[0] <= ring_rd_data;
                    6'd3: xf_vz[0] <= ring_rd_data;
                    6'd4: dv_szi[0] <= ring_rd_data;   // raw s (rows==3 passthrough)
                    6'd5: dv_tzi[0] <= ring_rd_data;   // raw t
                    6'd6: begin vt_rrow[0] <= ring_rd_data[15:11];
                                vt_lrow[0] <= ring_rd_data[10:5];
                                vt_brow[0] <= ring_rd_data[4:0]; end
                    // w7 (0x56 only -- 0x53 is 7 words in practice and any
                    // long-form 0x53 word lands here harmlessly): explicit
                    // z-buffer depth.  The GPU-derived depth (= zi) serves
                    // position/perspective well but quantizes far-field z to
                    // ~zi codes; the app computes (1/w)*2^30 in float at full
                    // legacy 0x4E precision and supplies it directly.  Consumed
                    // at the cache write only when xf_clip && xf_to_cache.
                    6'd7: xf_load_depth <= ring_rd_data;
                    default: ;
                endcase
            end
            CMDCLS_INDEXED_TRI: begin
                // T3 0x54: one word = 3 cache indices {i2[14:10],i1[9:5],i0[4:0]}.
                if (pay_idx == 6'd0) begin
                    vc_i0 <= ring_rd_data[4:0];
                    vc_i1 <= ring_rd_data[9:5];
                    vc_i2 <= ring_rd_data[14:10];
                end
            end
            CMDCLS_SET_LIGHT_STATE: begin
                // T4 0x55 sticky: light dir (Q16.16), light colour + ambient
                // (RGB565), enable.  Used by 0x57 lit loads.
                case (pay_idx)
                    6'd0: lt_lx <= ring_rd_data;
                    6'd1: lt_ly <= ring_rd_data;
                    6'd2: lt_lz <= ring_rd_data;
                    6'd3: begin lt_lr <= ring_rd_data[15:11];
                                lt_lg <= ring_rd_data[10:5];
                                lt_lb <= ring_rd_data[4:0]; end
                    6'd4: begin lt_ar <= ring_rd_data[15:11];
                                lt_ag <= ring_rd_data[10:5];
                                lt_ab <= ring_rd_data[4:0]; end
                    6'd5: lt_enable <= ring_rd_data[0];
                    default: ;
                endcase
            end
            CMDCLS_LOAD_VERT_LIT: begin
                // T4 0x57: one raw vert {x,y,z} + object-space normal {nx,ny,nz}
                // + {s,t} + destination cache slot.  S_XFORM transforms position
                // and computes lighting -> RGB565, then writes the cache slot.
                case (pay_idx)
                    6'd0: xf_load_slot <= ring_rd_data[4:0];
                    6'd1: xf_vx[0] <= ring_rd_data;
                    6'd2: xf_vy[0] <= ring_rd_data;
                    6'd3: xf_vz[0] <= ring_rd_data;
                    6'd4: xf_nx <= ring_rd_data;
                    6'd5: xf_ny <= ring_rd_data;
                    6'd6: xf_nz <= ring_rd_data;
                    6'd7: dv_szi[0] <= ring_rd_data;   // raw s
                    6'd8: dv_tzi[0] <= ring_rd_data;   // raw t
                    default: ;
                endcase
            end
            CMDCLS_PARAM_TRI_RECS: begin
                // 0x4D records-only param-tri: per-triangle attr/light planes
                // (w0..w11 -> the identical 0x49 header arms idx 8..19), the
                // q29 shift word (w12 -> idx 30, same validation), and the
                // three vertices (w13..15, same packing as 0x49 w33..35).
                // Deliberately does NOT clear tri_state_valid and does NOT
                // touch the sticky surface/control/clamp/z staging — that is
                // the whole point of the opcode (see the CMD comment block).
                case (pay_idx)
                    6'd0:  load_param_span_list_payload_word(6'd8,  ring_rd_data);
                    6'd1:  load_param_span_list_payload_word(6'd9,  ring_rd_data);
                    6'd2:  load_param_span_list_payload_word(6'd10, ring_rd_data);
                    6'd3:  load_param_span_list_payload_word(6'd11, ring_rd_data);
                    6'd4:  load_param_span_list_payload_word(6'd12, ring_rd_data);
                    6'd5:  load_param_span_list_payload_word(6'd13, ring_rd_data);
                    6'd6:  load_param_span_list_payload_word(6'd14, ring_rd_data);
                    6'd7:  load_param_span_list_payload_word(6'd15, ring_rd_data);
                    6'd8:  load_param_span_list_payload_word(6'd16, ring_rd_data);
                    6'd9:  load_param_span_list_payload_word(6'd17, ring_rd_data);
                    6'd10: load_param_span_list_payload_word(6'd18, ring_rd_data);
                    6'd11: load_param_span_list_payload_word(6'd19, ring_rd_data);
                    6'd12: load_param_span_list_payload_word(6'd30, ring_rd_data);
                    6'd13: begin tri_v0_x <= ring_rd_data[15:0];
                                  tri_v0_y <= ring_rd_data[31:16]; end
                    6'd14: begin tri_v1_x <= ring_rd_data[15:0];
                                  tri_v1_y <= ring_rd_data[31:16]; end
                    6'd15: begin tri_v2_x <= ring_rd_data[15:0];
                                  tri_v2_y <= ring_rd_data[31:16]; end
                    default: ;
                endcase
            end
            // CMDCLS_NONE / CMDCLS_SET_TEXTURE: payload drains word-by-word
            // via pay_remaining with no destination writes — the old chain's
            // fall-through.
            default: ;
            endcase

            if (pay_remaining <= 13'd1) begin
                state <= S_EXECUTE;
            end
            // For long packed-record param lists, execute the current 4-record
            // chunk and return here to consume the next record chunk without
            // requiring a new surface header.
            else if (cmd_is_draw_param_span_list
                  && !spanprod_direct_affine
                  && spanprod_header_supported
                  && spanprod_more_records_w
                  && (pay_idx == 6'd36)) begin
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
            // Parallel case on the S_DECODE-registered class code — same
            // rationale as the S_PAY_DATA dispatch (see the CMDCLS_* block).
            case (cmd_class)
            CMDCLS_FENCE: begin
                // Stall until all outstanding m_wr_* writes commit; then
                // publish the fence token and retire to S_IDLE.
                if (fb_write_drain_complete) begin
                    fence_reached <= pending_fence_token;
                    state         <= S_IDLE;
                end
                // else: stay in S_EXECUTE — m_wr_inflight ticks down via
                // the global counter update below.
            end
            CMDCLS_FLIP: begin
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
            CMDCLS_SET_TEXTURE, CMDCLS_SET_FB: begin
                state <= S_IDLE;
            end
            CMDCLS_CLEAR_RECT: begin
                state <= S_CLEAR_RECT;
            end
            CMDCLS_SPAN_COL: begin
                // Shared spanprod bring-up.  The 0x4C column list lands its
                // (s/sstep-forced-0) direct-affine staging via its own loader,
                // then reuses this EXECUTE / S_SPANPROD path byte-for-byte —
                // there is no column-specific pixel logic.
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
	                       ? S_SPANPROD_SELECT : S_IDLE;
	            end
            CMDCLS_PARAM_TRI: begin
                // Same spanprod bring-up as the span list, but records come
                // from the edge walker via S_TRI_FILL instead of the ring.
                spanprod_idx <= 2'd0;
                spanprod_calc_step <= 3'd0;
                src_done <= 1'b0;
                persp_active      <= 1'b0;
                persp_first_done  <= 1'b0;
                persp_swap_pending <= 1'b0;
                persp_pss         <= PSS_IDLE;
                persp_pass        <= PSS_PASS_ANCHOR;
                sp_seg_left       <= 4'd0;
                spanprod_active <= spanprod_header_supported;
                tri_start       <= spanprod_header_supported;
                tri_fill_idx    <= 3'd0;
                state <= spanprod_header_supported ? S_TRI_FILL : S_IDLE;
            end
            CMDCLS_PARAM_TRI_RECS: begin
                // 0x4D records-only param-tri: same spanprod/walker bring-up
                // as the full 0x49, but the surface/control/clamp/z staging
                // and clip rect come from the 0x4A sticky state.  Without a
                // valid sticky state (no prior 0x4A, soft_reset, or an
                // 0x48/0x49/0x4C overwrote the staging) the command retires
                // as a guarded no-op, exactly like a stale 0x4B.
                // spanprod_header_supported still gates the draw: it carries
                // the sticky control word's validation (set by the 0x4A's
                // idx-7 decode) plus this command's own w12/q29 validation.
                //
                // PRUNE GATE: the INCLUDE_PARAM_TRI_RECS term is redundant
                // with cmd_is_draw_param_tri_recs (constant 0 when the feature
                // is off) but is spelled here AS A CONSTANT — same recipe as
                // the 0x4B arm below — so that when BOTH triangle-extra
                // features are off, tri_state_valid / tri_state_clip_* lose
                // their last consumers at elaboration time and sweep, without
                // depending on the cmd_is_* constant folding first.
                if ((INCLUDE_PARAM_TRI_RECS != 0) && tri_state_valid) begin
                    spanprod_idx <= 2'd0;
                    spanprod_calc_step <= 3'd0;
                    src_done <= 1'b0;
                    persp_active      <= 1'b0;
                    persp_first_done  <= 1'b0;
                    persp_swap_pending <= 1'b0;
                    persp_pss         <= PSS_IDLE;
                    persp_pass        <= PSS_PASS_ANCHOR;
                    sp_seg_left       <= 4'd0;
                    tri_clip_x0 <= tri_state_clip_x0;
                    tri_clip_x1 <= tri_state_clip_x1;
                    tri_clip_y0 <= tri_state_clip_y0;
                    tri_clip_y1 <= tri_state_clip_y1;
                    spanprod_active <= spanprod_header_supported;
                    tri_start       <= spanprod_header_supported;
                    tri_fill_idx    <= 3'd0;
                    state <= spanprod_header_supported ? S_TRI_FILL : S_IDLE;
                end else begin
                    state <= S_IDLE;
                end
            end
            CMDCLS_SET_TRI_STATE: begin
                // 0x4A: the surface/control/clamp/z words are already decoded
                // into the SHARED spanprod staging in S_PAY_DATA (staging-dedup)
                // and the clip rect into tri_state_clip_*.  Just mark the sticky
                // state valid so subsequent 0x4B draws reuse the staging, then
                // retire.
                tri_state_valid <= 1'b1;
                state <= S_IDLE;
            end
            CMDCLS_VERT_TRI, CMDCLS_VERT_TRI_RGB: begin
                // 0x4B / 0x4E: a vert-tri with no valid sticky state (no prior
                // 0x4A, after soft_reset, or after an 0x48/0x49 overwrote the
                // staging) is a no-op — drain already happened, just retire.
                // 0x4E adds per-vertex RGB (spanprod_rgb); both share the derive.
                //
                // PRUNE GATE: the INCLUDE_VERT_TRI term is redundant with
                // cmd_is_draw_vert_tri (constant 0 when the feature is off) but
                // is spelled here AS A CONSTANT so Quartus's FSM extraction sees
                // `state <= S_TRI_DERIVE` as hard-unreachable and drops the
                // S_TRI_DERIVE state + the entire dstate derivation sub-FSM.
                // Without this explicit constant, FSM extraction kept dstate and
                // ~1k ALMs of derivation datapath alive (the cmd_is_* constant
                // folds too late for the state-machine recognizer).
                if ((INCLUDE_VERT_TRI != 0) && tri_state_valid) begin
                    // The spanprod surface/control staging already holds the
                    // 0x4A state (decoded in S_PAY_DATA — no copy needed), and
                    // the szi/tzi products are already in dv_szi/dv_tzi (folded
                    // during payload).  Copy the clip rect, kick the walker, and
                    // run the derivation FSM.  S_TRI_DERIVE blocks the walk
                    // record drain (S_TRI_FILL) until the four planes are staged.
                    spanprod_idx <= 2'd0;
                    spanprod_calc_step <= 3'd0;
                    src_done <= 1'b0;
                    persp_active      <= 1'b0;
                    persp_first_done  <= 1'b0;
                    persp_swap_pending <= 1'b0;
                    persp_pss         <= PSS_IDLE;
                    persp_pass        <= PSS_PASS_ANCHOR;
                    sp_seg_left       <= 4'd0;
                    spanprod_active <= 1'b1;
                    tri_clip_x0 <= tri_state_clip_x0;
                    tri_clip_x1 <= tri_state_clip_x1;
                    tri_clip_y0 <= tri_state_clip_y0;
                    tri_clip_y1 <= tri_state_clip_y1;
                    // route the per-triangle Q29 selection into the same sticky
                    // staging the (frozen) fragment Q29 consume reads.
                    spanprod_attr_q29       <= EFF_Q29 && vt_q29_en;
                    spanprod_q29_attr_shift <= vt_q29_shift;
                    tri_start    <= 1'b1;     // walker runs in parallel with derive
                    tri_fill_idx <= 3'd0;
                    dstate       <= DRV_SORT_A;
                    state        <= S_TRI_DERIVE;
                end else begin
                    state <= S_IDLE;
                end
            end
            CMDCLS_XFORM_TRI, CMDCLS_XFORM_RGB_CLIP: begin
                // 0x51 / 0x52 (T1): run the transform front-end, then the derive.
                // 0x4F (clip-feed): the verts are already M*v clip {x,y,w}, so skip
                // the matrix MAC and enter at XF_CLIP_FEED (recip+project only).
                // Needs the 0x4A sticky surface state (same as 0x4B) — refuse if
                // not armed.  0x52/0x4F additionally derive RGB/depth (sp_rgb).
                if ((INCLUDE_VERT_TRI != 0) && tri_state_valid) begin
                    xf_vtx      <= 2'd0;
                    xf_row      <= 2'd0;
                    xf_idx      <= 2'd0;
                    xf_behind   <= 2'd0;   // T2: near-plane reject accumulator
                    xf_to_cache <= 1'b0;   // T3: draw, not a cache load
                    xf_lit      <= 1'b0;   // T4: explicit RGB, no GPU lighting
                    xf_clip     <= cmd_is_draw_clip_tri;  // 0x4F: skip the MAC
                    xf_last_vtx <= 2'd2;   // 3 verts -> a triangle
                    xf_state    <= cmd_is_draw_clip_tri ? XF_CLIP_FEED : XF_MAC_A;
                    state       <= S_XFORM;
                end else begin
                    state <= S_IDLE;
                end
            end
            CMDCLS_LOAD_VERTS: begin
                // T3 0x53: transform ONE vert through S_XFORM and write the cache
                // slot (XF_PROJ_LAUNCH branches on xf_to_cache).  Needs the 0x4A
                // sticky surface + the 0x50 sticky matrix, same as 0x52.
                if ((INCLUDE_VTX_CACHE != 0) && tri_state_valid) begin
                    xf_vtx      <= 2'd0;
                    xf_row      <= 2'd0;
                    xf_idx      <= 2'd0;
                    xf_behind   <= 2'd0;
                    xf_to_cache <= 1'b1;   // T3: write the cache, do NOT draw
                    xf_lit      <= 1'b0;   // explicit RGB load (no lighting)
                    xf_clip     <= 1'b0;   // matrix path (hygiene: never stale)
                    xf_last_vtx <= 2'd0;   // single vert
                    xf_state    <= XF_MAC_A;
                    state       <= S_XFORM;
                end else begin
                    state <= S_IDLE;
                end
            end
            CMDCLS_LOAD_VERT_CLIP: begin
                // T3 0x56: park ONE pre-transformed clip-space vert in the cache.
                // Same wire as 0x53 but w1-3 are CPU-computed M*v clip {x,y,w};
                // S_XFORM enters at XF_CLIP_FEED (cam.z = clip.w is the divisor)
                // and takes the existing xf_to_cache exit in XF_PROJ_LAUNCH --
                // recip+project only, no MAC, no new states.  s/t are pure
                // payload passthrough (the MAC's s/t rows never run), which is
                // exactly 0x4F's contract; the derive multiplies s/t by zi at
                // 0x54 draw time for every path.  Needs the 0x50 sticky viewport
                // (xc/yc/scales/nearclip -- matrix words are don't-care) + 0x4A.
                if ((INCLUDE_VTX_CACHE != 0) && tri_state_valid) begin
                    xf_vtx      <= 2'd0;
                    xf_row      <= 2'd0;
                    xf_idx      <= 2'd0;
                    xf_behind   <= 2'd0;   // cache exit precedes the behind check
                    xf_to_cache <= 1'b1;
                    xf_lit      <= 1'b0;
                    xf_clip     <= 1'b1;   // multi-vert-safe if last_vtx ever grows
                    xf_last_vtx <= 2'd0;
                    xf_state    <= XF_CLIP_FEED;
                    state       <= S_XFORM;
                end else begin
                    state <= S_IDLE;
                end
            end
            CMDCLS_LOAD_VERT_LIT: begin
                // T4 0x57: transform + light one vert -> cache slot.
                if ((INCLUDE_GPU_LIGHT != 0) && (INCLUDE_VTX_CACHE != 0) && tri_state_valid) begin
                    xf_vtx      <= 2'd0;
                    xf_row      <= 2'd0;
                    xf_idx      <= 2'd0;
                    xf_behind   <= 2'd0;
                    xf_to_cache <= 1'b1;   // write the cache
                    xf_lit      <= 1'b1;   // compute lighting -> RGB565
                    xf_clip     <= 1'b0;
                    xf_last_vtx <= 2'd0;   // single vert
                    xf_state    <= XF_MAC_A;
                    state       <= S_XFORM;
                end else begin
                    state <= S_IDLE;
                end
            end
            CMDCLS_INDEXED_TRI: begin
                // T3 0x54: read 3 cached verts SEQUENTIALLY (single M10K read port)
                // in S_VCREAD, then launch the derive.  Issue the vert-0 read addr
                // here; vc_q reflects it next cycle.
                if ((INCLUDE_VTX_CACHE != 0) && tri_state_valid) begin
                    vc_raddr <= vc_i0;
                    vcr_cnt  <= 2'd0;
                    state    <= S_VCREAD;
                end else begin
                    state <= S_IDLE;
                end
            end
	            // CMDCLS_NONE (unrecognised / wrong-size / feature-gated-out
	            // drain) and the sticky-only classes with no EXECUTE action
	            // (0x50 SET_OBJECT_STATE / 0x55 SET_LIGHT_STATE): retire.
	            default: state <= S_IDLE;
	            endcase
	        end

        // ============================================================
        // Unified span producer — expand compact param records or direct
        // affine lane records into scalar spans for the fragment pipe.
        // ============================================================
	        S_SPANPROD_SELECT: begin
	            spanprod_select_current_record;
	            state <= S_SPANPROD_SETUP;
	        end

        // ============================================================
        // Triangle plane derivation (CMD_DRAW_VERT_TRI / 0x4B)
        // ============================================================
        // Derives the four attribute planes from raw per-vertex {x,y,s,t,zi,
        // light} and lands them in the spanprod staging regs in the SAME format
        // an 0x49 PERSP header would have carried.  The edge walker is already
        // running (started in S_EXECUTE) and computes its (~48-cycle, parallel
        // slope divides) setup concurrently; the walker simply waits in its
        // record handshake if it finishes first.  The state only advances to
        // S_TRI_FILL at DRV_DONE, so the planes are staged before the first
        // record is consumed.  See the FIXED-POINT CONTRACT
        // block above for the exact widths / N / saturation rules; the
        // acceptance C reference mirrors them bit-for-bit.
        S_TRI_DERIVE: begin
            tri_start <= 1'b0;   // start pulse consumed by the walker
            // PRUNE GATE: every writer of dstate and the derivation datapath
            // (dd*/rdet*/dv_*) lives inside this case, except the shared
            // rdet_* divider registers, whose recip-mode writers live in the
            // S_XFORM XF_RECIP states — themselves reachable only through
            // INCLUDE_VERT_TRI/INCLUDE_VTX_CACHE-gated EXECUTE arms, so the
            // shared regs still sweep when the features are off.  Wrapping it in the
            // constant-folding `if (INCLUDE_VERT_TRI != 0)` means that when the
            // feature is off the whole case is dead logic, so Quartus drops the
            // dstate state machine and the ~1k ALMs of derivation arithmetic.
            // (S_TRI_DERIVE is also unreachable — its only entry in S_EXECUTE is
            // gated by the same parameter — so this branch never executes
            // either way; the explicit constant just guarantees the sweep.)
            if (INCLUDE_VERT_TRI != 0) begin
            case (dstate)
                // ---- y-sort: 3 compare-swaps, walker's exact rule.  Only the
                //      order permutation dv_ord[] is swapped (not six attribute
                //      arrays); the raw per-vertex attrs are loaded once in
                //      DRV_SORT_A and read later through dv_ord.  Comparisons are
                //      bit-identical to the array-swapping version: slot s's y is
                //      dvy[dv_ord[s]].  DRV_SORT_A loads + compare-swap (slot0,1);
                //      DRV_SORT_B (slot1,2); DRV_SORT_C (slot0,1).
                DRV_SORT_A: begin : drv_sort_a_blk
                    // Seed the order and apply the first compare-swap in one
                    // cycle.  Staging-dedup: dv_szi/dv_tzi already hold the
                    // s*zi / t*zi products (folded during payload arrival), so
                    // there is NO raw-attr seed here; zi/light are read directly
                    // from vt_zi/vt_lrow later, no copy needed.
                    dv_ord[2] <= 2'd2;
                    if (tri_v1_y < tri_v0_y) begin
                        dv_ord[0] <= 2'd1; dv_ord[1] <= 2'd0;
                    end else begin
                        dv_ord[0] <= 2'd0; dv_ord[1] <= 2'd1;
                    end
                    dstate <= DRV_SORT_B;
                end
                DRV_SORT_B: begin
                    // compare-swap (slot1,slot2)
                    if (dvy[dv_ord[2]] < dvy[dv_ord[1]]) begin
                        dv_ord[1] <= dv_ord[2];
                        dv_ord[2] <= dv_ord[1];
                    end
                    dstate <= DRV_SORT_C;
                end
                DRV_SORT_C: begin
                    // compare-swap (slot0,slot1) — completes the walker's sort.
                    // TIMING DE-FOLD: dv_szi/dv_tzi still hold the RAW s/t parked
                    // in S_PAY_DATA; the szi/tzi products are now formed in the
                    // DRV_PROD_* states (the product fold moved out of payload
                    // arrival).  These run before the edge-delta stage, inside the
                    // walker's idle setup window — free in throughput.
                    if (dvy[dv_ord[1]] < dvy[dv_ord[0]]) begin
                        dv_ord[0] <= dv_ord[1];
                        dv_ord[1] <= dv_ord[0];
                    end
                    dstate <= DRV_PROD_L0;
                end
                // ---- per-vertex szi/tzi numerator products (de-fold) ----
                // dsp_p <= dsp_a*dsp_b is 1-cycle; a product launched in state N
                // is readable in state N+2.  Launch k=0/1/2 in L0/L1/L2C0, capture
                // k=0/1/2 in L2C0/C1/C2.  Products: dv_szi[k]=(s_k*zi_k)>>>16,
                // dv_tzi[k]=(t_k*zi_k)>>>16 (Q16.16, arith trunc toward -inf),
                // bit-identical to the old payload fold.  Read-vs-write slots are
                // distinct each cycle (e.g. write dv_szi[0], read dv_szi[2]), so
                // the in-place overwrite never reads a value it just clobbered.
                DRV_PROD_L0: begin
                    // launch k=0: s0*zi0, t0*zi0
                    dsp_a  <= dv_szi[0];   // raw s0
                    dsp_b  <= vt_zi[0];
                    dsp2_a <= dv_tzi[0];   // raw t0
                    dsp2_b <= vt_zi[0];
                    dstate <= DRV_PROD_L1;
                end
                DRV_PROD_L1: begin
                    // launch k=1: s1*zi1, t1*zi1
                    dsp_a  <= dv_szi[1];   // raw s1
                    dsp_b  <= vt_zi[1];
                    dsp2_a <= dv_tzi[1];   // raw t1
                    dsp2_b <= vt_zi[1];
                    dstate <= DRV_PROD_L2C0;
                end
                DRV_PROD_L2C0: begin
                    // launch k=2 AND capture k=0.  dv_szi[2]/dv_tzi[2] still hold
                    // raw s2/t2; the dv_szi[0]/dv_tzi[0] writes are distinct slots.
                    dsp_a  <= dv_szi[2];   // raw s2
                    dsp_b  <= vt_zi[2];
                    dsp2_a <= dv_tzi[2];   // raw t2
                    dsp2_b <= vt_zi[2];
                    dv_szi[0] <= dsp_p[47:16];
                    dv_tzi[0] <= dsp2_p[47:16];
                    dstate <= DRV_PROD_C1;
                end
                DRV_PROD_C1: begin
                    // capture k=1
                    dv_szi[1] <= dsp_p[47:16];
                    dv_tzi[1] <= dsp2_p[47:16];
                    dstate <= DRV_PROD_C2;
                end
                DRV_PROD_C2: begin
                    // capture k=2 (last product); dv_szi/dv_tzi now hold the
                    // settled s*zi / t*zi products read by DRV_DA_PREP later.
                    dv_szi[2] <= dsp_p[47:16];
                    dv_tzi[2] <= dsp2_p[47:16];
                    dstate <= DRV_DELTA;
                end
                // ---- edge deltas + determinant products ----
                DRV_DELTA: begin : drv_delta_blk
                    // Edge deltas from the anchor (sorted slot 0).  d*x Q12.4
                    // (17b), d*y int (17b).  Sorted x/y are the raw tri_v*
                    // values selected through the order permutation dv_ord.
                    reg signed [16:0] e1x, e2x, e1y, e2y;
                    reg signed [15:0] sx0, sx1, sx2, sy0, sy1, sy2;
                    sx0 = dvx[dv_ord[0]]; sx1 = dvx[dv_ord[1]]; sx2 = dvx[dv_ord[2]];
                    sy0 = dvy[dv_ord[0]]; sy1 = dvy[dv_ord[1]]; sy2 = dvy[dv_ord[2]];
                    e1x = {sx1[15], sx1} - {sx0[15], sx0};
                    e2x = {sx2[15], sx2} - {sx0[15], sx0};
                    e1y = {sy1[15], sy1} - {sy0[15], sy0};
                    e2y = {sy2[15], sy2} - {sy0[15], sy0};
                    dd1x <= e1x; dd2x <= e2x; dd1y <= e1y; dd2y <= e2y;
                    dd_x0px <= {{4{sx0[15]}}, sx0[15:4]}; // x0 Q12.4 >>4
                    // subpix: y0 is Q12.4 here, floor to a scanline (like x0px)
                    // so origin = a0 - du*x0px - dv*y0 anchors at integer (u,v).
                    dd_y0   <= spanprod_subpix_y ? {{4{sy0[15]}}, sy0[15:4]} : sy0;
                    // det = d1x*d2y - d2x*d1y : launch both products.  17-bit
                    // edge deltas sign-extended to the 32-bit signed DSP.
                    dsp_a  <= {{15{e1x[16]}}, e1x};
                    dsp_b  <= {{15{e2y[16]}}, e2y};
                    dsp2_a <= {{15{e2x[16]}}, e2x};
                    dsp2_b <= {{15{e1y[16]}}, e1y};
                    dstate <= DRV_DET_W;
                end
                DRV_DET_W: dstate <= DRV_DET_CAP;
                DRV_DET_CAP: begin
                    // timing: products captured in drv_prod_r/drv2_prod_r the
                    // cycle before; DRV_DET_FORM does the 35-bit subtract + abs
                    // off the DSP-output combinational path.  Pure capture here.
                    drv_prod_r  <= dsp_p;
                    drv2_prod_r <= dsp2_p;
                    dstate <= DRV_DET_FORM;
                end
                DRV_DET_FORM: begin : drv_det_form_blk
                    // det = d1x*d2y - d2x*d1y.  d*x is Q12.4 (17b), d*y int
                    // (17b), product 34b; the difference fits signed 35b.
                    // Operands come from the captured products (drv*_prod_r), not
                    // dsp_p/dsp2_p directly — keeps DSP output off this cone.
                    reg signed [34:0] det_now;
                    det_now = $signed(drv_prod_r[34:0]) - $signed(drv2_prod_r[34:0]);
                    // |det| floored to 1 (collinear -> walker also clips it out)
                    // so the reciprocal divisor is always >=1.  sign tracked
                    // separately and applied to the final du/dv.
                    if (det_now == 35'sd0) begin
                        dd_detabs  <= 35'd1;
                        dd_detsign <= 1'b0;
                    end else if (det_now < 35'sd0) begin
                        dd_detabs  <= (~det_now) + 35'd1;
                        dd_detsign <= 1'b1;
                    end else begin
                        dd_detabs  <= det_now;
                        dd_detsign <= 1'b0;
                    end
                    dstate <= DRV_RDET;
                    rdet_cnt <= 6'd46;   // beat 46 loads operands, 45..1 iterate
                    rdet_q   <= 32'd0;
                    rdet_rem <= 35'd0;
                    rdet_ovf <= 1'b0;
                    rdet_dividend <= 45'd0;
                    rdet_divisor  <= 35'd0;
                end
                // ---- serial restoring divide: rdet = round(2^44/|det|) ----
                DRV_RDET: begin
                    if (rdet_cnt == 6'd46) begin
                        rdet_dividend <= ({10'd0, dd_detabs} >> 1) + (45'd1 << DERIV_N);
                        rdet_divisor  <= dd_detabs;
                        rdet_rem      <= 35'd0;
                        rdet_q        <= 32'd0;
                        rdet_ovf      <= 1'b0;
                        rdet_cnt      <= rdet_cnt - 6'd1;
                    end else begin : drv_rdet_iter
                        // Next quotient value after shifting in this beat's bit.
                        reg [31:0] q_next;
                        q_next = {rdet_q[30:0], rdet_ge};
                        rdet_rem      <= rdet_next;
                        // Saturate the reciprocal at 2^31-1 (fits the signed DSP
                        // operand).  rdet_q[31] catches a 1 about to shift OUT of
                        // the 32-bit register; q_next[31] catches the final
                        // value's MSB (set on the last beat, which would never
                        // shift out) — both mean the rounded reciprocal needs
                        // >=2^31, i.e. a sliver triangle (du/dv then clamp).
                        if (rdet_q[31] || q_next[31])
                            rdet_ovf <= 1'b1;
                        rdet_q        <= q_next;
                        rdet_dividend <= {rdet_dividend[43:0], 1'b0};
                        if (rdet_cnt == 6'd1) begin
                            dstate  <= DRV_DA_SEL;  // capture attr-0 operands
                            dv_attr <= 4'd0;        // attr 0 = szi
                            dv_doing_dv <= 1'b0;
                        end else begin
                            rdet_cnt <= rdet_cnt - 6'd1;
                        end
                    end
                end
                // ---- per-attribute plane terms ----
                // For attr a (selected by dv_attr -> a0/a1/a2 sorted values),
                // compute du then dv: each is sat32(((num*rdet)>>N)*sign).
                // DRV_DA_PREP runs once per attribute (fresh-attr entry only) and
                // forms da1_sat = sat33(a1-a0), da2_sat = sat33(a2-a0).  Both the
                // du and dv numerators reuse these, so the two 33-bit subtract +
                // deriv_sat33 cones are computed ONCE here instead of inside the
                // dsp_a/dsp2_a operand mux on every DRV_PLANE_NUM pass.  da1/da2
                // are differences of two signed 32-bit attributes -> exact in 33
                // bits, saturated to int32 to match the reference.
                // Mux-capture stage: dv_attr was written on the previous
                // edge, so this cycle is pure attr-select + sorted-slot
                // muxing into registers — no arithmetic behind it.
                DRV_DA_SEL: begin
                    deriv_a0_q <= deriv_a0;
                    deriv_a1_q <= deriv_a1;
                    deriv_a2_q <= deriv_a2;
                    dstate     <= DRV_DA_PREP;
                end
                DRV_DA_PREP: begin
                    da1_sat <= deriv_sat33({deriv_a1_q[31], deriv_a1_q}
                                         - {deriv_a0_q[31], deriv_a0_q});
                    da2_sat <= deriv_sat33({deriv_a2_q[31], deriv_a2_q}
                                         - {deriv_a0_q[31], deriv_a0_q});
                    dstate  <= DRV_PLANE_NUM;
                end
                DRV_PLANE_NUM: begin
                    // Launch the two numerator sub-products for du or dv from the
                    // pre-staged clamped differences (da1_sat/da2_sat).
                    // du: num_du = 16*(da1*d2y - da2*d1y)
                    // dv: num_dv =    (da2*d1x - da1*d2x)
                    if (!dv_doing_dv) begin
                        dsp_a  <= da1_sat;
                        dsp_b  <= {{15{dd2y[16]}}, dd2y};
                        dsp2_a <= da2_sat;
                        dsp2_b <= {{15{dd1y[16]}}, dd1y};
                    end else begin
                        dsp_a  <= da2_sat;
                        dsp_b  <= {{15{dd1x[16]}}, dd1x};
                        dsp2_a <= da1_sat;
                        dsp2_b <= {{15{dd2x[16]}}, dd2x};
                    end
                    dstate <= DRV_PLANE_NW;
                end
                DRV_PLANE_NW: dstate <= DRV_PLANE_NC;
                DRV_PLANE_NC: begin
                    // timing: products captured in drv_prod_r/drv2_prod_r the
                    // cycle before.  This was the WNS -1.588 critical state — it
                    // fused dsp_p -> 64-bit subtract -> <<4 -> >>>SPLIT ->
                    // deriv_sat32 -> dsp_a (DSP output straight back into a DSP
                    // operand register).  Now a pure capture; DRV_PLANE_NF below
                    // does the subtract/shift/saturate from the captured regs.
                    drv_prod_r  <= dsp_p;
                    drv2_prod_r <= dsp2_p;
                    dstate <= DRV_PLANE_NF;
                end
                DRV_PLANE_NF: begin : drv_plane_nf_blk
                    // num = product0 - product1, x16 for du.  Each product is a
                    // sat32 numerator (<=2^31) times a 17-bit edge delta, so the
                    // product fits 49 bits, the difference fits 50 bits, and num
                    // (after <<4) fits 54 bits — the subtract/shift cone stays
                    // narrow.  Launch num_hi*rdet in the same cycle, reusing the
                    // 1-launch/1-wait/1-capture DSP cadence; the low SPLIT bits
                    // feed the lo pass next.  Operands are the captured products.
                    reg signed [63:0] num_now;
                    // num_du always x16 (corrects det's Q12.4 x scale).  num_dv
                    // gets x16 too ONLY for subpix: Q12.4 y scales det an extra
                    // 16x, so dv would otherwise be 16x too small.
                    num_now = (!dv_doing_dv)
                            ? (($signed(drv_prod_r) - $signed(drv2_prod_r)) <<< 4)
                            : (spanprod_subpix_y
                                ? (($signed(drv_prod_r) - $signed(drv2_prod_r)) <<< 4)
                                :  ($signed(drv_prod_r) - $signed(drv2_prod_r)));
                    dv_num_lo <= num_now[DERIV_SPLIT-1:0];   // low bits for lo pass
                    // num_hi = num_now >>> SPLIT (<=30-bit signed); deriv_sat32
                    // is the deterministic sliver clamp.
                    dsp_a  <= deriv_sat32(num_now >>> DERIV_SPLIT);
                    dsp_b  <= rdet_operand;
                    dstate <= DRV_SCALE_HW;
                end
                DRV_SCALE_HW: dstate <= DRV_SCALE_HC;   // hi-product DSP latency
                DRV_SCALE_HC: begin
                    // Capture H = num_hi*rdet (signed, <=63 bits) into the shared
                    // 64-bit accumulator; launch num_lo*rdet (unsigned).
                    dv_acc <= dsp_p;
                    dsp_a  <= {{(32-DERIV_SPLIT){1'b0}}, dv_num_lo};   // num_lo (unsigned)
                    dsp_b  <= rdet_operand;
                    dstate <= DRV_SCALE_LW;
                end
                DRV_SCALE_LW: dstate <= DRV_SCALE_LC;   // lo-product DSP latency
                DRV_SCALE_LC: begin
                    // timing: lo product (L = num_lo*rdet) captured in drv_prod_r
                    // the cycle before.  This state used to fuse dsp_p -> 64-bit
                    // add (H + L>>>SPLIT) -> >>> -> negate -> deriv_sat32 ->
                    // dv_du/dv_dv, and dv_du is read into dsp_a next cycle
                    // (DRV_PLANE_ORG), so the long cone reached a DSP operand.
                    // Pure capture; DRV_SCALE_LS + DRV_SCALE_LF recombine.
                    drv_prod_r <= dsp_p;
                    dstate <= DRV_SCALE_LS;
                end
                DRV_SCALE_LS: begin
                    // Recombine split, stage 1 (timing: 182 of the 200 worst
                    // paths on the third OS30 fit ran drv_prod_r -> 64-bit add
                    // -> negate -> sat32 -> dv_du in one cycle — two chained
                    // 64-bit carry chains).  This state registers the sum;
                    // DRV_SCALE_LF keeps the shift/negate/saturate.  The
                    // negate cannot be hoisted into the operands: -(x >>> k)
                    // != (-x) >>> k under floor division, so splitting is the
                    // only bit-exact cut.  +1 cycle per du/dv pass (+8 of
                    // ~127 per triangle, still overlapped with the walker).
                    drv_qsum_r <= dv_acc
                                + $signed({1'b0, drv_prod_r[54:DERIV_SPLIT]});
                    // precompute the final shift here so the barrel shift in
                    // DRV_SCALE_LF reads a register, not a mux off vt_q29_shift.
                    q29_shamt_r <= (vt_q29_en && (dv_attr != 3'd3))
                                 ? (6'd7 + {1'b0, vt_q29_shift})
                                 : (DERIV_N - DERIV_SPLIT);
                    dstate <= DRV_SCALE_LF;
                end
                // Q29 BARREL-SHIFT PIPELINE (fixes the -3.97 dv_du cone — all 400+
                // worst paths funneled here).  The legacy path's shift is a CONSTANT
                // 20 (free wiring); the Q29 path's (7+sh) makes it a VARIABLE 64-bit
                // barrel, which with the negate+sat32 was a 9-level / 13.7ns cone.
                // Split: DRV_SCALE_LF coarse (byte multiple), DRV_SCALE_LF2 fine
                // (0-7), DRV_SCALE_LF3 negate+sat32.  Bit-exact: for signed x,
                // x >>> (8a+b) == (x >>> 8a) >>> b.  +2 cy/du-dv pass, free under
                // the walker.  q29_shamt_r precomputed in DRV_SCALE_LS.
                DRV_SCALE_LF: begin
                    drv_q_coarse <= drv_qsum_r >>> {q29_shamt_r[5:3], 3'b000};
                    dstate <= DRV_SCALE_LF2;
                end
                DRV_SCALE_LF2: begin
                    drv_q_fine <= drv_q_coarse >>> q29_shamt_r[2:0];
                    dstate <= DRV_SCALE_LF3;
                end
                DRV_SCALE_LF3: begin : drv_scale_lf3_blk
                    // sign(det) applied, then deriv_sat32 clamps to int32.
                    reg signed [63:0] qsigned;
                    qsigned = dd_detsign ? -drv_q_fine : drv_q_fine;
                    if (!dv_doing_dv) begin
                        dv_du <= deriv_sat32(qsigned);
                        dv_doing_dv <= 1'b1;
                        dstate <= DRV_PLANE_NUM;   // now compute dv
                    end else begin
                        dv_dv <= deriv_sat32(qsigned);
                        dv_doing_dv <= 1'b0;
                        // both du,dv done -> launch origin offset products.
                        dstate <= DRV_PLANE_ORG;
                    end
                end
                // ---- origin = a0 - du*x0px - dv*y0  (mod 2^32) ----
                DRV_PLANE_ORG: begin
                    dsp_a  <= dv_du;
                    dsp_b  <= {{16{dd_x0px[15]}}, dd_x0px};
                    dsp2_a <= dv_dv;
                    dsp2_b <= {{16{dd_y0[15]}}, dd_y0};
                    dstate <= DRV_ORG_W;
                end
                DRV_ORG_W: dstate <= DRV_ORG_CAP;
                DRV_ORG_CAP: begin
                    // timing: origin products (du*x0px, dv*y0) captured in
                    // drv_prod_r/drv2_prod_r the cycle before.  This state used to
                    // fuse dsp_p/dsp2_p -> subtract -> spanprod_attr*_origin (a
                    // plane-staging write); now a pure capture.  dv_attr is NOT
                    // advanced here, so deriv_a0_q stays stable into DRV_ORG_FORM.
                    drv_prod_r  <= dsp_p;
                    drv2_prod_r <= dsp2_p;
                    // Pre-form the Q29-scaled anchor (a0 << (13-sh), arithmetic,
                    // mod 2^32) here so DRV_ORG_FORM stays a pure subtract.  Same
                    // value the in-line a0_eff produced — deriv_a0_q/vt_q29_shift/
                    // dv_attr don't change before DRV_ORG_FORM consumes it.
                    // Single right barrel: the <<13 is constant wiring (held
                    // exactly in 64 bits), so >>> sh gives the same [31:0] as
                    // the old bidirectional <<(13-sh) / >>>(sh-13) pair.
                    if (vt_q29_en && (dv_attr != 3'd3))
                        a0_eff_r <= $signed({{19{deriv_a0_q[31]}}, deriv_a0_q, 13'b0})
                                    >>> vt_q29_shift;
                    else
                        a0_eff_r <= deriv_a0_q;
                    dstate <= DRV_ORG_FORM;
                end
                DRV_ORG_FORM: begin : drv_org_form_blk
                    // origin = a0_eff - du*x0px - dv*y0, truncated to 32 bits (the
                    // spanprod plane eval wraps mod 2^32, so the large anchor
                    // offset cancels for on-screen records — the "anchoring").
                    // a0_eff (the Q29 anchor scale) was pre-formed in DRV_ORG_CAP,
                    // so this state is just the two captured-product subtracts.
                    reg signed [31:0] org_now;
                    // Pixel-CENTER sampling: the plane is evaluated per-pixel as
                    // origin + x*du + y*dv at INTEGER (x,y) = the pixel CORNER.
                    // Bias the origin by +0.5*du +0.5*dv so it samples the pixel
                    // CENTER (x+0.5, y+0.5) — fixes the sub-texel texture slide on
                    // steep-gradient (small/character) surfaces (SM64 faces). All
                    // attrs (szi/tzi/zi/depth/colour) shift through this one org_now,
                    // so the perspective num+denom and z stay co-sampled. Derive-only
                    // (0x4B/0x4E/xform); the 0x48 CPU-loaded planes bypass this state
                    // and are unaffected. dv_du/dv_dv are this attr's Q16.16 deltas.
                    org_now = a0_eff_r - $signed(drv_prod_r[31:0]) - $signed(drv2_prod_r[31:0])
                            + (dv_du >>> 1) + (dv_dv >>> 1);
                    // store this attr's plane into the MLAB staging bank (B4):
                    // one row per plane, one write per array per pass.  24-bit
                    // planes store the [23:0] truncation SIGN-EXTENDED from
                    // bit 23 — exactly what the old 24-bit reg + read-side
                    // {{8{v[23]}},v} pair produced.  Conditionally-walked
                    // planes' du/dv also land in their flop mirrors (the
                    // EMIT / q29_zstep_op_r readers).
                    case (dv_attr)
                        3'd0: begin
                            spanprod_pl_origin[4'd1] <= org_now;
                            spanprod_pl_du[4'd1]     <= dv_du;
                            spanprod_pl_dv[4'd1]     <= dv_dv;
                        end
                        3'd1: begin
                            spanprod_pl_origin[4'd2] <= org_now;
                            spanprod_pl_du[4'd2]     <= dv_du;
                            spanprod_pl_dv[4'd2]     <= dv_dv;
                        end
                        3'd2: begin
                            spanprod_pl_origin[4'd3] <= org_now;
                            spanprod_pl_du[4'd3]     <= dv_du;
                            spanprod_pl_dv[4'd3]     <= dv_dv;
                            spanprod_attr2_du        <= dv_du;
                            spanprod_attr2_dv        <= dv_dv;
                        end
                        3'd4: begin   // RED plane (truecolor RGB)
                            spanprod_pl_origin[4'd8] <= {{8{org_now[23]}}, org_now[23:0]};
                            spanprod_pl_du[4'd8]     <= {{8{dv_du[23]}}, dv_du[23:0]};
                            spanprod_pl_dv[4'd8]     <= {{8{dv_dv[23]}}, dv_dv[23:0]};
                            spanprod_R_du            <= dv_du[23:0];
                            spanprod_R_dv            <= dv_dv[23:0];
                        end
                        3'd5: begin   // BLUE plane (truecolor RGB)
                            spanprod_pl_origin[4'd9] <= {{8{org_now[23]}}, org_now[23:0]};
                            spanprod_pl_du[4'd9]     <= {{8{dv_du[23]}}, dv_du[23:0]};
                            spanprod_pl_dv[4'd9]     <= {{8{dv_dv[23]}}, dv_dv[23:0]};
                            spanprod_B_du            <= dv_du[23:0];
                            spanprod_B_dv            <= dv_dv[23:0];
                        end
                        3'd6: begin   // decoupled depth plane (0x4E), full 32-bit
                            spanprod_pl_origin[4'd10] <= org_now;
                            spanprod_pl_du[4'd10]     <= dv_du;
                            spanprod_pl_dv[4'd10]     <= dv_dv;
                            spanprod_depth_du         <= dv_du;
                            spanprod_depth_dv         <= dv_dv;
                        end
                        4'd7: begin   // D-red plane (combine)
                            spanprod_pl_origin[4'd11] <= {{8{org_now[23]}}, org_now[23:0]};
                            spanprod_pl_du[4'd11]     <= {{8{dv_du[23]}}, dv_du[23:0]};
                            spanprod_pl_dv[4'd11]     <= {{8{dv_dv[23]}}, dv_dv[23:0]};
                            spanprod_Dr_du            <= dv_du[23:0];
                            spanprod_Dr_dv            <= dv_dv[23:0];
                        end
                        4'd8: begin   // D-green plane (combine)
                            spanprod_pl_origin[4'd12] <= {{8{org_now[23]}}, org_now[23:0]};
                            spanprod_pl_du[4'd12]     <= {{8{dv_du[23]}}, dv_du[23:0]};
                            spanprod_pl_dv[4'd12]     <= {{8{dv_dv[23]}}, dv_dv[23:0]};
                            spanprod_Dg_du            <= dv_du[23:0];
                            spanprod_Dg_dv            <= dv_dv[23:0];
                        end
                        4'd9: begin   // D-blue plane (combine)
                            spanprod_pl_origin[4'd13] <= {{8{org_now[23]}}, org_now[23:0]};
                            spanprod_pl_du[4'd13]     <= {{8{dv_du[23]}}, dv_du[23:0]};
                            spanprod_pl_dv[4'd13]     <= {{8{dv_dv[23]}}, dv_dv[23:0]};
                            spanprod_Db_du            <= dv_du[23:0];
                            spanprod_Db_dv            <= dv_dv[23:0];
                        end
                        default: begin   // attr 3 = light / green
                            spanprod_pl_origin[4'd4] <= {{8{org_now[23]}}, org_now[23:0]};
                            spanprod_pl_du[4'd4]     <= {{8{dv_du[23]}}, dv_du[23:0]};
                            spanprod_pl_dv[4'd4]     <= {{8{dv_dv[23]}}, dv_dv[23:0]};
                            spanprod_light_du        <= dv_du[23:0];
                            spanprod_light_dv        <= dv_dv[23:0];
                        end
                    endcase
                    // Derive depth: combine surfaces derive 10 attrs (..R,B,depth,
                    // Dr,Dg,Db); legacy RGB stops at 7 (..R,B,depth); others at 4.
                    if (dv_attr == (((cmd_is_draw_vert_tri_rgb || cmd_is_draw_xform_tri_rgb
                                      || cmd_is_draw_indexed_tri || cmd_is_draw_clip_tri))
                                    ? (spanprod_cd_combine ? 4'd9 : 4'd6) : 4'd3)) begin
                        dstate <= DRV_DONE;
                    end else begin
                        dv_attr <= dv_attr + 4'd1;
                        dstate  <= DRV_DA_SEL;   // capture next attr's operands
                    end
                end
                default: begin   // DRV_DONE
                    // Planes staged.  Hand off to the record-fill drain; it will
                    // wait on the walker via tri_walker_done if still busy.
                    tri_fill_idx <= 3'd0;
                    state <= S_TRI_FILL;
                end
            endcase
            end // INCLUDE_VERT_TRI
        end

        // ============================================================
        // Transform front-end (CMD_DRAW_XFORM_TRI / 0x51) — per vertex:
        // cam = M*{v,1} (Q16.16), zi = 2^32/cam.z, perspective project to
        // screen Q12.4 x / int y, then hand off to the derive (S_TRI_DERIVE)
        // exactly as 0x4B does.  Single multiplier (dsp_p) on the shared
        // shared operand regs (dsp_a/dsp_b); dsp2 runs stale operands, unread here.
        // ============================================================
        S_XFORM: begin
            case (xf_state)
                // ---- cam[row] = ((M[row][0..2]*v) >>> 16) + M[row][3] ----
                // xf_M is M10K: XF_MAC_A issues the read (xf_rd_addr=row*4+idx),
                // xf_M_q is valid in XF_MAC_L.  idx 0-2 = products, idx 3 = the
                // translate column (captured into xf_transl, no multiply).
                XF_MAC_A: xf_state <= XF_MAC_L;
                XF_MAC_L: begin
                    if (xf_idx == 2'd3) begin
                        xf_transl <= xf_M_q;             // M[row][3]
                        xf_state  <= XF_ROW_DONE;
                    end else begin
                        dsp_a <= xf_M_q;
                        dsp_b <= (xf_idx == 2'd0) ? xf_vx[xf_vtx]
                                  : (xf_idx == 2'd1) ? xf_vy[xf_vtx]
                                                     : xf_vz[xf_vtx];
                        xf_state <= XF_MAC_W;
                    end
                end
                XF_MAC_W: xf_state <= XF_MAC_C;   // DSP latency
                XF_MAC_C: begin
                    xf_acc <= (xf_idx == 2'd0) ? dsp_p : (xf_acc + dsp_p);
                    xf_idx <= xf_idx + 2'd1;             // -> next product, or idx 3 = translate
                    xf_state <= XF_MAC_A;               // issue the next M10K read
                end
                XF_ROW_DONE: begin : xf_row_done_blk
                    reg signed [31:0] camv;
                    camv = (xf_acc >>> 16) + xf_transl;   // captured M[row][3]
                    case (xf_row)
                        3'd0: xf_camx <= camv;
                        3'd1: xf_camy <= camv;
                        3'd2: xf_camz <= camv;
                        3'd3: dv_szi[xf_vtx] <= camv;    // s (N=5 world)
                        default: dv_tzi[xf_vtx] <= camv; // t (N=5 world, row 4)
                    endcase
                    if (xf_row == xf_rows - 3'd1) xf_state <= XF_RECIP_INIT;
                    else begin
                        xf_row <= xf_row + 3'd1; xf_idx <= 2'd0;
                        xf_state <= XF_MAC_A;            // M10K read-issue for next row
                    end
                end
                // ---- clip-feed (0x4F): the CPU already did M*v, so the 3 "verts"
                // ARE clip {x,y,w}.  Load cam{x,y,z} directly and skip to the recip
                // (no matrix MAC).  cam.z = clip.w is the perspective divisor. ----
                XF_CLIP_FEED: begin
                    xf_camx  <= xf_vx[xf_vtx];
                    xf_camy  <= xf_vy[xf_vtx];
                    xf_camz  <= xf_vz[xf_vtx];
                    xf_state <= XF_RECIP_INIT;
                end
                // ---- zi = floor(2^32 / max(cam.z, near_clip)) ----
                // Runs on the SHARED rdet_* divider datapath (see the xf_div_q
                // alias declaration): dividend left-aligned at bit 44, divisor
                // zero-extended, 33 iterate beats.  rdet_ovf is untouched here
                // (derive-mode only; DRV_RDET re-initializes it before use).
                XF_RECIP_INIT: begin
                    rdet_rem      <= 35'd0;
                    rdet_dividend <= {1'b1, 44'd0};   // 2^32 << 12: MSB at the shared bit-44 tap
                    rdet_q        <= 32'd0;
                    rdet_divisor  <= {3'b000, xf_recip_divisor};
                    rdet_cnt      <= 6'd33;
                    // T2: tally verts behind the near plane (same compare as the
                    // divisor floor).  All three behind => trivial-reject at LAUNCH.
                    if (xf_camz < xf_nearclip) xf_behind <= xf_behind + 2'd1;
                    xf_state        <= XF_RECIP_RUN;
                end
                XF_RECIP_RUN: begin
                    rdet_rem      <= rdet_next;
                    rdet_q        <= {rdet_q[30:0], rdet_ge};
                    rdet_dividend <= {rdet_dividend[43:0], 1'b0};
                    if (rdet_cnt == 6'd1) xf_state <= XF_PROJ_XA;
                    else rdet_cnt <= rdet_cnt - 6'd1;
                end
                // ---- screen.x = (xc<<4) + ((xscale*ratio_x) >>> 12) ----
                XF_PROJ_XA: begin
                    dsp_a <= xf_camx;
                    dsp_b <= xf_div_q;          // zi
                    xf_state <= XF_PROJ_XW;
                end
                XF_PROJ_XW: xf_state <= XF_PROJ_XC;
                XF_PROJ_XC: begin
                    dsp_a <= xf_xscale;
                    dsp_b <= dsp_p[47:16];      // ratio_x = (cam.x*zi)>>16
                    xf_state <= XF_PROJ_XW2;
                end
                XF_PROJ_XW2: xf_state <= XF_PROJ_XS;
                // Split the 64-bit add from xf_sat16: dsp_p -> >>>12 -> add ->
                // sat16 -> tri_v was the post-Q29-fix worst cone.  $signed() the
                // xc term — an unsigned operand poisons the add and turns dsp_p>>>12
                // into a LOGICAL shift, corrupting negative screen offsets.
                XF_PROJ_XS: begin
                    xf_sproj_r <= ($signed({{32{xf_xc[31]}}, xf_xc}) <<< 4)
                                + (dsp_p >>> 12);
                    xf_state <= XF_PROJ_XS2;
                end
                XF_PROJ_XS2: begin   // saturate to int16 + store screen.x
                    case (xf_vtx)
                        2'd0:    xf_sx[0] <= xf_sproj_sat;
                        2'd1:    xf_sx[1] <= xf_sproj_sat;
                        default: xf_sx[2] <= xf_sproj_sat;
                    endcase
                    xf_state <= XF_PROJ_YA;
                end
                // ---- screen.y = yc - ((yscale*ratio_y) >>> 16) ----
                XF_PROJ_YA: begin
                    dsp_a <= xf_camy;
                    dsp_b <= xf_div_q;          // zi
                    xf_state <= XF_PROJ_YW;
                end
                XF_PROJ_YW: xf_state <= XF_PROJ_YC;
                XF_PROJ_YC: begin
                    dsp_a <= xf_yscale;
                    dsp_b <= dsp_p[47:16];      // ratio_y
                    xf_state <= XF_PROJ_YW2;
                end
                XF_PROJ_YW2: xf_state <= XF_PROJ_YS;
                XF_PROJ_YS: begin   // register screen.y add (split from sat16)
                    xf_sproj_r <= $signed({{32{xf_yc[31]}}, xf_yc}) - (dsp_p >>> 16);
                    xf_state <= XF_PROJ_YS2;
                end
                XF_PROJ_YS2: begin : xf_projys2_blk  // sat16 + store y/zi; advance
                    // Also stash zi into vt_depth so the 0x52 truecolor path's
                    // depth plane (attr6, read when sp_rgb=1) uses the GPU-computed
                    // zi as the z-buffer value — z_compress is float-like so the
                    // absolute scale is irrelevant, only frame-consistent ordering.
                    // Harmless for 0x51 (sp_rgb=0 never derives attr6/vt_depth).
                    case (xf_vtx)
                        2'd0:    begin xf_sy[0] <= xf_sproj_sat; vt_zi[0] <= xf_div_q; vt_depth[0] <= xf_div_q; end
                        2'd1:    begin xf_sy[1] <= xf_sproj_sat; vt_zi[1] <= xf_div_q; vt_depth[1] <= xf_div_q; end
                        default: begin xf_sy[2] <= xf_sproj_sat; vt_zi[2] <= xf_div_q; vt_depth[2] <= xf_div_q; end
                    endcase
                    if (xf_vtx == xf_last_vtx) begin
                        // last vert projected -> the parallel-load + derive
                        // launch happens in XF_PROJ_LAUNCH (one cycle later) so
                        // the tri_v* writes are plain register copies, not this
                        // saturating cone, keeping them off the critical mux.
                        // (LOAD_VERTS sets xf_last_vtx=0 -> writes the cache there.)
                        // T4: lit loads detour through the lighting MACs first,
                        // computing per-vertex RGB565 before the cache write.
                        if (xf_lit) begin
                            xf_idx   <= 2'd0;
                            xf_state <= XF_LIT_DL;
                        end else begin
                            xf_state <= XF_PROJ_LAUNCH;
                        end
                    end else begin
                        xf_vtx   <= xf_vtx + 2'd1;
                        xf_row   <= 2'd0;
                        xf_idx   <= 2'd0;
                        xf_state <= xf_clip ? XF_CLIP_FEED : XF_MAC_A;
                    end
                end
                XF_PROJ_LAUNCH: begin : xf_proj_launch_blk
                    // T2: trivial near-plane reject — if all three verts are
                    // behind near, the projected tri is degenerate/garbage, so
                    // drop it instead of launching the derive.  Straddlers (1-2
                    // behind) fall through and are clipped on the CPU (hybrid).
                    if (xf_to_cache) begin
                        // T3 0x53/0x57: pack the transformed (+ lit) vert into the
                        // cache slot.  vt_zi[0]/vt_depth[0] set in XF_PROJ_YS2; raw
                        // s/t in dv_szi/dv_tzi[0]; RGB in vt_rrow/lrow/brow[0].
                        // Layout: {b5,g6,r5, depth32, t32, s32, zi32, sy16, sx16}.
                        // Depth field: 0x56 (xf_clip) supplies it explicitly
                        // (w7, full app float precision); matrix loads keep the
                        // divider-derived value (= zi).
                        vc_mem[xf_load_slot] <= { vt_brow[0], vt_lrow[0], vt_rrow[0],
                                                  (xf_clip ? xf_load_depth : vt_depth[0]),
                                                  dv_tzi[0], dv_szi[0],
                                                  vt_zi[0], xf_sy[0], xf_sx[0] };
                        state <= S_IDLE;
                    end else if (xf_behind == 2'd3) begin
                        state <= S_IDLE;
                    end else begin
                    // parallel-load the walker's vertex regs from the held
                    // projection results (register-to-register copy), then
                    // launch the derive (mirror 0x4B).  DRV_SORT_A reads the
                    // tri_v*_y written here on the next edge (NBA), unchanged.
                    tri_v0_x <= xf_sx[0]; tri_v1_x <= xf_sx[1]; tri_v2_x <= xf_sx[2];
                    tri_v0_y <= xf_sy[0]; tri_v1_y <= xf_sy[1]; tri_v2_y <= xf_sy[2];
                    spanprod_idx       <= 2'd0;
                    spanprod_calc_step <= 3'd0;
                    src_done           <= 1'b0;
                    persp_active       <= 1'b0;
                    persp_first_done   <= 1'b0;
                    persp_swap_pending <= 1'b0;
                    persp_pss          <= PSS_IDLE;
                    persp_pass         <= PSS_PASS_ANCHOR;
                    sp_seg_left        <= 4'd0;
                    spanprod_active    <= 1'b1;
                    tri_clip_x0 <= tri_state_clip_x0;
                    tri_clip_x1 <= tri_state_clip_x1;
                    tri_clip_y0 <= tri_state_clip_y0;
                    tri_clip_y1 <= tri_state_clip_y1;
                    // world (N=5) uses Q29 planes (CPU-provided shift); alias
                    // (N=3) keeps Q16.16.  Routes through the verified derive.
                    spanprod_attr_q29       <= EFF_Q29 && xf_q29_en;
                    spanprod_q29_attr_shift <= xf_q29_shift;
                    vt_q29_en    <= xf_q29_en;
                    vt_q29_shift <= xf_q29_shift;
                    tri_start   <= 1'b1;
                    tri_fill_idx <= 3'd0;
                    dstate <= DRV_SORT_A;
                    state  <= S_TRI_DERIVE;
                    end   // else (not all-behind-near)
                end
                // ---- T4: per-vertex lighting (lit cache-load 0x57) ----
                // dot = sum_k normal[k]*lightdir[k] (Q32.32), clamp [0,1.0], then
                // per channel: clamp(ambient + (dot*lightcolor)>>16) -> RGB565.
                XF_LIT_DL: begin   // launch N[idx]*L[idx]
                    dsp_a <= (xf_idx == 2'd0) ? xf_nx : (xf_idx == 2'd1) ? xf_ny : xf_nz;
                    dsp_b <= (xf_idx == 2'd0) ? lt_lx : (xf_idx == 2'd1) ? lt_ly : lt_lz;
                    xf_state <= XF_LIT_DW;
                end
                XF_LIT_DW: xf_state <= XF_LIT_DC;   // DSP latency
                XF_LIT_DC: begin
                    xf_acc <= (xf_idx == 2'd0) ? dsp_p : (xf_acc + dsp_p);  // Q32.32
                    if (xf_idx == 2'd2) xf_state <= XF_LIT_CLAMP;
                    else begin xf_idx <= xf_idx + 2'd1; xf_state <= XF_LIT_DL; end
                end
                XF_LIT_CLAMP: begin : xf_lit_clamp_blk
                    reg signed [63:0] dotf;
                    dotf = xf_acc >>> 16;                 // Q32.32 -> Q16.16
                    if (dotf < 0)               xf_dot <= 32'sd0;
                    else if (dotf > 64'sd65536) xf_dot <= 32'sd65536;   // clamp to 1.0
                    else                        xf_dot <= dotf[31:0];
                    xf_idx   <= 2'd0;
                    xf_state <= XF_LIT_CL;
                end
                XF_LIT_CL: begin   // launch dot * lightcolor[idx]
                    dsp_a <= xf_dot;
                    dsp_b <= (xf_idx == 2'd0) ? {27'd0, lt_lr}
                              : (xf_idx == 2'd1) ? {26'd0, lt_lg}
                                                 : {27'd0, lt_lb};
                    xf_state <= XF_LIT_CW;
                end
                XF_LIT_CW: xf_state <= XF_LIT_CC;   // DSP latency
                XF_LIT_CC: begin : xf_lit_cc_blk
                    // contrib = (dot*lightcolor)>>16 (0..channel); chan=clamp(amb+contrib)
                    reg [7:0] sum8;
                    case (xf_idx)
                        2'd0: begin sum8 = {3'd0, lt_ar} + {1'b0, dsp_p[22:16]};
                                    vt_rrow[0] <= (sum8 > 8'd31) ? 5'd31 : sum8[4:0]; end
                        2'd1: begin sum8 = {2'd0, lt_ag} + {1'b0, dsp_p[22:16]};
                                    vt_lrow[0] <= (sum8 > 8'd63) ? 6'd63 : sum8[5:0]; end
                        default: begin sum8 = {3'd0, lt_ab} + {1'b0, dsp_p[22:16]};
                                    vt_brow[0] <= (sum8 > 8'd31) ? 5'd31 : sum8[4:0]; end
                    endcase
                    if (xf_idx == 2'd2) xf_state <= XF_PROJ_LAUNCH;  // RGB done -> cache write
                    else begin xf_idx <= xf_idx + 2'd1; xf_state <= XF_LIT_CL; end
                end
                default: xf_state <= XF_MAC_A;
            endcase
        end

        // ============================================================
        // T3: sequential 3-vertex cache read for 0x54 DRAW_INDEXED_TRI.
        // vc_q lags vc_raddr by one cycle (registered MLAB read), so cnt0 primes
        // (vc_q not yet valid); cnt1..3 capture verts 0..2; cnt3 launches the
        // derive with the XF_PROJ_LAUNCH bring-up.  Unpack layout matches the
        // write: {b5,g6,r5, depth32, t32, s32, zi32, sy16, sx16}.
        // ============================================================
        S_VCREAD: begin : s_vcread_blk
            case (vcr_cnt)
                2'd0: begin   // prime: vc_q still stale; queue vert-1 read
                    vc_raddr <= vc_i1;
                    vcr_cnt  <= 2'd1;
                end
                2'd1: begin   // vc_q = cache[i0]
                    tri_v0_x <= vc_q[15:0];   tri_v0_y <= vc_q[31:16];
                    vt_zi[0] <= vc_q[63:32];  dv_szi[0] <= vc_q[95:64];
                    dv_tzi[0] <= vc_q[127:96]; vt_depth[0] <= vc_q[159:128];
                    vt_rrow[0] <= vc_q[164:160]; vt_lrow[0] <= vc_q[170:165];
                    vt_brow[0] <= vc_q[175:171];
                    vc_raddr <= vc_i2;
                    vcr_cnt  <= 2'd2;
                end
                2'd2: begin   // vc_q = cache[i1]
                    tri_v1_x <= vc_q[15:0];   tri_v1_y <= vc_q[31:16];
                    vt_zi[1] <= vc_q[63:32];  dv_szi[1] <= vc_q[95:64];
                    dv_tzi[1] <= vc_q[127:96]; vt_depth[1] <= vc_q[159:128];
                    vt_rrow[1] <= vc_q[164:160]; vt_lrow[1] <= vc_q[170:165];
                    vt_brow[1] <= vc_q[175:171];
                    vcr_cnt  <= 2'd3;
                end
                default: begin   // cnt3: vc_q = cache[i2]; capture + launch derive
                    tri_v2_x <= vc_q[15:0];   tri_v2_y <= vc_q[31:16];
                    vt_zi[2] <= vc_q[63:32];  dv_szi[2] <= vc_q[95:64];
                    dv_tzi[2] <= vc_q[127:96]; vt_depth[2] <= vc_q[159:128];
                    vt_rrow[2] <= vc_q[164:160]; vt_lrow[2] <= vc_q[170:165];
                    vt_brow[2] <= vc_q[175:171];
                    spanprod_idx       <= 2'd0;
                    spanprod_calc_step <= 3'd0;
                    src_done           <= 1'b0;
                    persp_active       <= 1'b0;
                    persp_first_done   <= 1'b0;
                    persp_swap_pending <= 1'b0;
                    persp_pss          <= PSS_IDLE;
                    persp_pass         <= PSS_PASS_ANCHOR;
                    sp_seg_left        <= 4'd0;
                    spanprod_active    <= 1'b1;
                    tri_clip_x0 <= tri_state_clip_x0;
                    tri_clip_x1 <= tri_state_clip_x1;
                    tri_clip_y0 <= tri_state_clip_y0;
                    tri_clip_y1 <= tri_state_clip_y1;
                    spanprod_attr_q29       <= 1'b0;   // cache holds legacy Q16.16 planes
                    spanprod_q29_attr_shift <= 5'd0;
                    vt_q29_en    <= 1'b0;
                    vt_q29_shift <= 5'd0;
                    tri_start    <= 1'b1;
                    tri_fill_idx <= 3'd0;
                    dstate <= DRV_SORT_A;
                    state  <= S_TRI_DERIVE;
                end
            endcase
        end

        // ============================================================
        // Triangle record fill — drain the edge walker's record stream
        // into the 4-entry chunk regs, then run the normal spanprod loop.
        // tri_rec_ready is combinational on this state, so each record is
        // captured on the same edge the walker's handshake consumes it.
        // ============================================================
        S_TRI_FILL: begin
            tri_start <= 1'b0;   // start pulse consumed by the walker
            if (tri_rec_valid) begin
                spanprod_u[tri_fill_idx[1:0]]     <= tri_rec_u;
                spanprod_v[tri_fill_idx[1:0]]     <= tri_rec_v;
                spanprod_count[tri_fill_idx[1:0]] <= tri_rec_count;
                spanprod_cnt_valid[tri_fill_idx[1:0]] <= 1'b1;
                if (tri_fill_idx == 3'd3) begin
                    spanprod_record_count <= 3'd4;
                    spanprod_records_left <= 16'd4;
                    spanprod_idx <= 2'd0;
                    spanprod_calc_step <= 3'd0;
                    tri_fill_idx <= 3'd0;
                    state <= S_SPANPROD_SELECT;
                end else begin
                    tri_fill_idx <= tri_fill_idx + 3'd1;
                end
            end else if (tri_walker_done) begin
                if (tri_fill_idx != 3'd0) begin
                    // Dispatch the final partial chunk.
                    spanprod_record_count <= tri_fill_idx;
                    spanprod_records_left <= {13'd0, tri_fill_idx};
                    spanprod_idx <= 2'd0;
                    spanprod_calc_step <= 3'd0;
                    tri_fill_idx <= 3'd0;
                    state <= S_SPANPROD_SELECT;
                end else begin
                    // Nothing (left) to draw: drain accumulators and
                    // retire.  S_FB_FLUSH is idempotent when empty.
                    spanprod_active <= 1'b0;
                    state <= S_FB_FLUSH;
                end
            end
            // else: walker still computing — hold here.
        end

        S_SPANPROD_SETUP: begin
            if (!spanprod_active) begin
                state <= S_IDLE;
            end else if (!spanprod_cur_nonzero) begin
                // FLUSH dedup (audit A2): the z_flush -> z_acc -> fb_acc
                // accumulator drain chain for this state now runs in the
                // ONE shared block ahead of this case statement (gated on
                // exactly this state+condition).  Advance to the next
                // record / retire only on its all-clear — same cycle the
                // final fb_acc push is accepted, identical sequencing to
                // the old in-arm copy (which spelled both the chain and
                // this continuation twice).
                if (acc_drain_done) begin
                    if (spanprod_idx == spanprod_last_idx) begin
                        if (cmd_is_tri_walker) begin
                            if (!tri_walker_done) begin
                                state <= S_TRI_FILL;
                            end else begin
                                spanprod_active <= 1'b0;
                                state <= S_IDLE;
                            end
                        end
                        else if (spanprod_more_records_w
                            && (pay_remaining != 13'd0)) begin
                            spanprod_prepare_next_record_chunk;
                        end else begin
                            spanprod_active <= 1'b0;
                            // Round-2 early-handoff tail safety: after a
                            // relaxed (fastpath) continuation the previous
                            // record's tail may still sit frozen in
                            // p1..p3/fbss when a zero-count record exhausts
                            // the chunk.  Retiring to S_IDLE would strand
                            // those pixels (the pipe only advances inside
                            // S_FRAG_PIPE), so bounce through S_FRAG_PIPE
                            // with src_done armed: the tail drains, then
                            // the full drain detector retires through
                            // S_FB_FLUSH as usual.  With an empty tail this
                            // keeps the original one-cycle S_IDLE retire.
                            // The frozen-tail case only exists downstream of
                            // the sp_fastpath relaxed continuation (compact
                            // direct-affine only — see the early-handoff
                            // comment in S_FRAG_PIPE); every other arrival
                            // here has fully drained, so the wide-OR bounce
                            // is gated with the compact machinery instead of
                            // hoping synthesis proves the pipe empty.
                            if ((INCLUDE_COMPACT_SPAN != 0)
                                && (p0a_valid || p0_valid || p1_valid
                                    || p2_valid || p2b_valid || p3_valid
                                    || (fbss != FBSS_IDLE)
                                    || blend_group_active)) begin
                                src_done <= 1'b1;
                                state <= S_FRAG_PIPE;
                            end else begin
                                state <= S_IDLE;
                            end
                        end
                    end else begin
                        spanprod_idx <= spanprod_idx + 2'd1;
                        state <= S_SPANPROD_SELECT;
                    end
                end
            end else begin
                spanprod_calc_step <= 3'd0;
                if (spanprod_direct_affine) begin
                    state <= S_SPANPROD_EMIT;
                end else begin
                    spanprod_launch_fb_mul;
                    spanprod_launch_step <= spanprod_next_calc(3'd0);
                    state <= S_SPANPROD_MUL_WAIT;
                end
            end
        end


        S_SPANPROD_MUL_WAIT: begin
            // Round-2 pipelining: the former pure DSP-latency dead state
            // now launches the second product (z when enabled, else attr0)
            // while the fb product is still in the multiplier.  Operands
            // changing on consecutive cycles is supported by the registered
            // dsp_a/dsp_b -> dsp_p path (DRV_PROD_* does exactly this).
            spanprod_launch_step_mul(spanprod_launch_step);
            spanprod_launch_step <= spanprod_next_calc(spanprod_launch_step);
            state <= S_SPANPROD_CAPTURE;
        end

        S_SPANPROD_CAPTURE: begin
            // Round-2 pipelining: capture the product launched two cycles
            // ago and launch the next pending product in the SAME cycle.
            // The capture pointer (calc_step) trails the launch pointer by
            // two slots along the same successor chain; both products of a
            // pair (dsp_p/dsp2_p) belong to the capture slot, exactly as in
            // the serial schedule.  Skipped products keep their old zero
            // defaults, written while capturing the predecessor.
            // All arms take the shared spanprod_capture_sum adder (origin
            // muxed by calc_step); narrow destinations slice its low bits.
            spanprod_launch_step_mul(spanprod_launch_step);
            spanprod_launch_step <= spanprod_next_calc(spanprod_launch_step);
            case (spanprod_calc_step)
                3'd0: spanprod_fb_addr_r <= spanprod_capture_sum[GPU_ADDR_W-1:0];
                3'd5: spanprod_z_addr_r <= spanprod_capture_sum[GPU_ADDR_W-1:0];
                3'd1: spanprod_attr0_start_r <= spanprod_capture_sum;
                3'd2: begin
                    spanprod_attr1_start_r <= spanprod_capture_sum;
                    if (!spanprod_attr_persp) begin
                        spanprod_attr2_start_r <= 32'sd0;
                        if (!(spanprod_flags[SPAN_COLORMAP] || spanprod_truecolor))
                            spanprod_light_start_r <= 24'sd0;
                    end
                end
                3'd3: begin
                    spanprod_attr2_start_r <= spanprod_capture_sum;
                    if (!(spanprod_flags[SPAN_COLORMAP] || spanprod_truecolor))
                        spanprod_light_start_r <= 24'sd0;
                end
                SPANPROD_STEP_R: spanprod_R_start_r <= spanprod_capture_sum[23:0];
                SPANPROD_STEP_B: spanprod_B_start_r <= spanprod_capture_sum[23:0];
                SPANPROD_STEP_DEPTH: spanprod_depth_start_r <= spanprod_capture_sum;
                SPANPROD_STEP_DR: spanprod_Dr_start_r <= spanprod_capture_sum[23:0];
                SPANPROD_STEP_DG: spanprod_Dg_start_r <= spanprod_capture_sum[23:0];
                SPANPROD_STEP_DB: spanprod_Db_start_r <= spanprod_capture_sum[23:0];
                default: spanprod_light_start_r <= spanprod_capture_sum[23:0];
            endcase
            if (spanprod_next_calc(spanprod_calc_step) == SPANPROD_STEP_NONE)
                state <= S_SPANPROD_EMIT;
            else
                spanprod_calc_step <= spanprod_next_calc(spanprod_calc_step);
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
            reg        p3_word_match;
            reg        issue_committed;
            reg        p1_to_p2;
            reg        p0a_to_p0;
            reg        p0a_free_after;
            reg        source_pixel_available;
            reg        scalar_source_pixel_available;
            reg        load_p0a;
            reg        load_p0a_z;
            reg        p3_consumed;
            // ZTEST capture dedup (round-2): the 5-field ztest_acc_*
            // capture from p3 was spelled verbatim at three FBSS sites
            // (z_acc hit, z-window hit, read-return fill).  Each site now
            // only selects the 32-bit source word + from_read flavor and
            // raises the fire flag; ONE shared gated capture block after
            // the fbss case applies it (same A2-drain-dedup idiom).
            reg        ztest_cap_fire;
            reg        ztest_cap_from_read;
            reg [31:0] ztest_cap_word;
            reg [GPU_ADDR_W-1:0] source_fb_addr;
            reg [GPU_ADDR_W-1:0] source_tex_base;
            // TEX CLAMP top halves only: every consumer of the clamped
            // coordinate reads [31:16] (the mirror_idx() calls feeding
            // p0a_s_int / p0a_t_y), so the clamp compares and muxes are
            // 16-bit — see the contract/equivalence note at the compute
            // site below.
            reg signed [15:0] source_s_clamped;
            reg signed [15:0] source_t_clamped;
            reg [5:0] source_light;
            reg [4:0] source_R, source_B;   // truecolor RGB red/blue
            reg [4:0] source_Dr, source_Db; // combine D red/blue
            reg [5:0] source_Dg;            // combine D green
            reg [3:0] source_colormap_id;
            reg [GPU_ADDR_W-1:0] source_z_word_addr;
            reg        source_z_hi;
            reg [15:0] source_z_half;
            reg        source_z_stage_ready;
            reg        source_z_ready;
            reg        source_z_write_only_active;
            reg        source_z_advance_active;
            reg [GPU_ADDR_W-1:0] p3_z_word_addr;
            reg        p3_z_hi;
            // PSS slope-commit dedup (see the shared commit block after
            // the persp_pss case): the three commit-capable arms set the
            // fire flag + step values; ONE commit applies them.
            reg        pss_slope_commit_fire;
            reg signed [31:0] pss_commit_s_step;
            reg signed [31:0] pss_commit_t_step;
            // Quadratic 2nd-difference committed alongside the step by the
            // shared pss_slope_commit_fire block.  Set only by the full-16px
            // quadratic arm; 0 elsewhere (pow2 / partial / DIV / constZ / Q29),
            // so those commits leave sp_scc/persp_pend_scc at 0 (byte-exact).
            reg signed [31:0] pss_commit_scc;
            reg signed [31:0] pss_commit_tcc;

            // Was the (combinational) tex_req accepted by the cache this
            // same cycle? Both signals are visible NOW.  p1 is the decoupling
            // slot between cache issue and the tail pipe; using p1 availability
            // instead of fp_pipe_stall keeps p2/p3 feedback out of the cache
            // RAM read-enable/address cone.
            issue_committed = tex_req_valid && tex_req_ready;
            p1_to_p2 = !fp_pipe_stall && p1_valid;
            p3_consumed = 1'b0;
            ztest_cap_fire = 1'b0;
            ztest_cap_from_read = 1'b0;
            ztest_cap_word = 32'd0;
            scalar_span_last_issue = 1'b0;
            pss_slope_commit_fire = 1'b0;
            pss_commit_s_step = 32'sd0;
            pss_commit_t_step = 32'sd0;
            pss_commit_scc = 32'sd0;
            pss_commit_tcc = 32'sd0;

            // Load/refresh p0a only if the one-entry source snapshot will be
            // free after this cycle.  p0 itself remains the cache-issue stage;
            // if it is busy, p0a can still prefetch one source pixel.
            //
            // Promote p0a only when p0 is already empty.  Refilling p0 in the
            // same cycle that tex_req is accepted ties tex_cache hit/ready
            // feedback directly to the texture-row DSP enable.  Taking the
            // one-cycle handoff bubble keeps that ready path out of the DSP.
            // Persp gating:
            //   * !persp_issue_stall: slot A must be loaded (pass 2 done).
            //   * If sp_seg_left == 0 (last px of segment), slot B must be
            //     ready so the swap can fire in the same cycle, unless this
            //     pixel is also the last pixel of the whole span.
            p0a_to_p0 = p0a_valid && !p0_valid;
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
            source_z_word_addr = sp_z_addr & {{(GPU_ADDR_W-2){1'b1}}, 2'b00};
            source_z_hi = sp_z_addr[1];
            // Truecolor surfaces store an N64-style float-encoded depth code
            // (better far precision than the flat linear [16:1] slice); the
            // compress feeds the p0a_z_value register, so it is pipelined many
            // stages ahead of the z compare cone.  sp_truecolor is const 0 when
            // INCLUDE_DIRECT_COLOR=0, so palettized z is byte-exact unchanged.
            source_z_half = sp_q29_z_enable
                          ? sp_q29_z_value[29:14]
                          : (sp_rgb || sp_truecolor)
                            ? zc_s2                        // unified pipelined z_compress (depth for rgb, zi for truecolor)
                            : sp_z_value[16:1];            // palettized: linear (unchanged)
            source_z_stage_ready = !z_flush_valid
                                 && (!z_src_pending_valid
                                     || z_src_pending_consume);
            source_z_ready = !source_z_write_only_active
                           || source_z_stage_ready;
            load_p0a = p0a_free_after && source_pixel_available && source_z_ready
                       && (g_zwarm == 2'd0);   // hold consume while the z pipe warms
            load_p0a_z = p0a_free_after && scalar_source_pixel_available
                       && source_z_ready && source_z_write_only_active
                       && (g_zwarm == 2'd0);
            source_fb_addr = sp_fb_addr;
            source_tex_base = sp_tex_addr;
            // TEX CLAMP — compares narrowed to the top 16 bits.  SDK
            // CONTRACT (of_gpu.h clamp_min/clamp_max): span clamp payload
            // words satisfy min <= max per axis (s and t, signed Q16.16);
            // behavior is UNDEFINED for min > max.  Under that contract,
            //   clamp32(s, min, max)[31:16]
            //     == clamp16(s[31:16], min[31:16], max[31:16])
            // with the same if / else-if priority.  Proof — write
            // f(x) = x >>> 16 (monotonic non-decreasing), and note only
            // [31:16] of the 32-bit result was ever consumed:
            //   * s < min: 32b picks min, top = min16.  Monotonicity gives
            //     s16 <= min16.  If s16 < min16 the 16-bit clamp picks
            //     min16 too.  The boundary s16 == min16 (equal top halves,
            //     differing low bits) falls through the min branch — and
            //     min <= max forces min16 <= max16, so the max branch
            //     stays false — passing s16, which EQUALS min16.
            //   * s > max: 32b picks max, top = max16.  Symmetric: s16 >=
            //     max16, and min16 <= max16 keeps the min branch false;
            //     s16 > max16 picks max16, the s16 == max16 boundary
            //     passes s16 == max16.
            //   * min <= s <= max: 32b passes s, top = s16; monotonicity
            //     gives min16 <= s16 <= max16, both branches false, the
            //     16-bit clamp passes s16.
            // The old 32-bit-only counterexample (min = 0x00018000,
            // max = 0x0000FFFF, s = 0x00017000: 32b top = 1, 16b top = 0)
            // needs min > max and is excluded by the contract.
            source_s_clamped = sp_s[31:16];
            if (sp_clamp_enable[0]) begin
                if (source_s_clamped < $signed(sp_s_clamp_min[31:16]))
                    source_s_clamped = sp_s_clamp_min[31:16];
                else if (source_s_clamped > $signed(sp_s_clamp_max[31:16]))
                    source_s_clamped = sp_s_clamp_max[31:16];
            end
            source_t_clamped = sp_t[31:16];
            if (sp_clamp_enable[1]) begin
                if (source_t_clamped < $signed(sp_t_clamp_min[31:16]))
                    source_t_clamped = sp_t_clamp_min[31:16];
                else if (source_t_clamped > $signed(sp_t_clamp_max[31:16]))
                    source_t_clamped = sp_t_clamp_max[31:16];
            end
            source_light = sp_light;
            source_R = sp_R;
            source_B = sp_B;
            source_Dr = sp_Dr;
            source_Dg = sp_Dg;
            source_Db = sp_Db;
            source_colormap_id = sp_colormap_id;
            p3_z_word_addr = p3_z_addr & {{(GPU_ADDR_W-2){1'b1}}, 2'b00};
            p3_z_hi = p3_z_addr[1];
            // External z reads only reach FBSS_ZTEST_R_WAIT after any dirty
            // z_acc word has either matched the p3 word or been flushed.  The
            // accumulator-hit case is handled by FBSS_ZTEST_ACC_EVAL below, so
            // keep this compare path free of the z_acc_addr equality cone.

            // ----------------------------------------------------------
            // Pipeline shift — only when not stalled
            // ----------------------------------------------------------
            if (cmap_pending_valid && cmap_req_ready_b)
                cmap_pending_valid <= 1'b0;

            if (p1_valid && !p1_tex_ready && tex_resp_valid) begin
                p1_tex_ready <= 1'b1;
                // Capture the RAW texel (RGB565 for truecolor, CI8 byte
                // otherwise).  The truecolor brightness modulate is applied at
                // the p1->p2 shift instead of here: this is the cache-output
                // path and adding the multiplies here failed timing badly
                // (WNS -2.4ns on clk_cpu).  Moving the modulate to the
                // register-to-register p1->p2 stage pipelines it cleanly.
                p1_tex_color <= sp_truecolor
                              ? tex_resp_data[15:0]
                              : {8'b0, tex_resp_data[7:0]};
            end

            if (!fp_pipe_stall) begin
                // p3 <- p2b  (merges cmap result if cmap was used; the
                // response presented by tex_cache port B is the oldest
                // outstanding — p2b's — and cmap_resp_pop_b retires it
                // from the cache's response queue on this same shift)
                p3_valid     <= p2b_valid;
                p3_color     <= (p2b_flags[SPAN_COLORMAP] && !sp_truecolor)
                                ? {8'b0, cmap_rd_data} : p2b_color;
                p3_flags     <= p2b_flags;
                p3_fb_addr   <= p2b_fb_addr;
                // Z-test fold: a fragment whose old z half is resident in
                // z_acc / the z window enters p3 pre-resolved — z_test
                // cleared on pass, discard on fail — and never takes the
                // FBSS detour (see the p2b_zf_* block by z_acc).
                p3_discard   <= p2b_discard || (p2b_zf_fold && !p2b_zf_pass);
                p3_z_test    <= p2b_z_test && !p2b_zf_fold;
                p3_z_write   <= p2b_z_write && !p2b_zf_fold;
                p3_z_addr    <= p2b_z_addr;
                p3_z_value   <= p2b_z_value;
                // Blend fold stage 2: the weighted sums register alongside
                // the pixel (pure reg-to-reg multiplies off p2b_color /
                // p2b_cb_dst).  On a window hit the pixel enters p3
                // blend-resolved and commits through the one-cycle opaque
                // path; a miss dispatches to FBSS_CB_REQ; a stale capture
                // spends one refresh cycle at p3 first.  Garbage sums for
                // non-blend pixels are never consumed (p3_cb_commit_w gates
                // on sp_blend).
                cb_sr <= cbm_sum_r_w;
                cb_sg <= cbm_sum_g_w;
                cb_sb <= cbm_sum_b_w;
                p3_cb_ready <= p2b_valid && sp_truecolor && sp_blend
                            && p2b_cb_hit;
                p3_cb_stale <= p2b_valid && sp_truecolor && sp_blend
                            && p2b_cb_hit
                            && (p2b_cb_stale || cb_stale_p2b_w);
                // Fold the pass-write into the z accumulator at this same
                // edge, so the NEXT pixel's p2b compare (one cycle later)
                // reads it from the registers — the same-word RAW forward.
                // Mirrors ACC_EVAL: merge arm on an acc hit, from_read arm
                // (full-word load, written half dirty) on a window serve.
                if (p2b_zf_fold && p2b_zf_pass && p2b_z_write) begin
                    if (p2b_zf_acc_hit) begin
                        if (p2b_z_addr[1]) begin
                            z_acc_hi        <= p2b_z_value;
                            z_acc_mask[3:2] <= 2'b11;
                        end else begin
                            z_acc_lo        <= p2b_z_value;
                            z_acc_mask[1:0] <= 2'b11;
                        end
                    end else begin
                        z_acc_valid <= 1'b1;
                        z_acc_addr  <= p2b_z_word_addr_w;
                        if (p2b_z_addr[1]) begin
                            z_acc_hi   <= p2b_z_value;
                            z_acc_lo   <= p2b_zf_word[15:0];
                            z_acc_mask <= 4'b1100;
                        end else begin
                            z_acc_hi   <= p2b_zf_word[31:16];
                            z_acc_lo   <= p2b_z_value;
                            z_acc_mask <= 4'b0011;
                        end
                    end
                end

	                // p2b <- p2  (no-op shift, gives cmap port B time to respond)
                p2b_valid   <= p2_valid;
                // Combine gate: when the sticky cd_combine flag is set, take the
                // texel*C+D result (products from p1->p2, +D/clamp here); else the
                // untouched legacy rgb565_gouraud/modulate result in p2_color.
                p2b_color   <= sp_cd_combine
                             ? rgb565_cd_finish(p2_pr, p2_pg, p2_pb, p2_dC_r, p2_dC_g, p2_dC_b)
                             : p2_color;
                p2b_flags   <= p2_flags;
                p2b_fb_addr <= p2_fb_addr;
                p2b_discard <= p2_discard;
                p2b_z_test  <= p2_z_test;
                p2b_z_write <= p2_z_write;
                p2b_z_addr  <= p2_z_addr;
                p2b_z_value <= p2_z_value;
                // Blend fold stage 1: registered dst probe (mux-only cone).
                p2b_cb_dst   <= cbw_p2_dst_w;
                p2b_cb_hit   <= cbw_p2_hit_w;
                p2b_cb_stale <= cb_stale_p2_w;

                // p2 <- p1  (captures tex_resp; stages the cmap request)
                p2_valid   <= p1_valid;
                if (p1_valid) begin
                    // Truecolor brightness modulate, pipelined here (register
                    // inputs p1_tex_color + p1_light -> register p2_color) so
                    // the multiplies are off the cache-output critical path.
                    p2_color   <= sp_rgb
                                ? rgb565_gouraud(p1_tex_color, p1_R, p1_light, p1_B)
                                : (sp_truecolor
                                   ? rgb565_modulate(p1_tex_color, p1_light)
                                   : p1_tex_color);
                    // Combine products texel*C.  C rides the per-vertex RGB planes
                    // as BIASED-UNSIGNED (firmware enc_C5/C6 add bias 16/32) so it
                    // interpolates monotonically like an unsigned colour (zero-extend
                    // in the derive is then correct — fixes the signed-C-as-unsigned
                    // wrap that faceted the head).  Recover signed C by subtracting
                    // the bias here, after interpolation, before the product:
                    //   R/B 5-bit: C = field - 16 ;  G 6-bit: C = field - 32.
                    // texel stays unsigned ({1'b0,...}).
                    p2_pr   <= $signed({1'b0, p1_tex_color[15:11]}) * ($signed({1'b0, p1_R})     - 6'sd16);
                    p2_pg   <= $signed({1'b0, p1_tex_color[10:5]})  * ($signed({1'b0, p1_light}) - 7'sd32);
                    p2_pb   <= $signed({1'b0, p1_tex_color[4:0]})   * ($signed({1'b0, p1_B})     - 6'sd16);
                    // Independent additive D (combine path); zero/unused on legacy.
                    p2_dC_r <= p1_Dr; p2_dC_g <= p1_Dg; p2_dC_b <= p1_Db;
                    p2_flags   <= p1_flags;
                    p2_fb_addr <= p1_fb_addr;
                    p2_discard <= p1_flags[SPAN_SKIP_ZERO]
                               && (sp_truecolor ? (p1_tex_color == 16'h0000)
                                                : (p1_tex_color[7:0] == 8'hFF));
                    p2_z_test  <= p1_z_test;
                    p2_z_write <= p1_z_write;
                    p2_z_addr  <= p1_z_addr;
                    p2_z_value <= p1_z_value;
                    if (p1_flags[SPAN_COLORMAP] && (INCLUDE_PALETTE != 0)) begin
                        // Truecolor-only builds (INCLUDE_PALETTE=0) never issue a
                        // cmap request, so the entire port-B colormap pipe
                        // (cmap_pending/resp, the palookup reads) constant-folds
                        // away — truecolor surfaces take the rgb565_* resolve and
                        // never set SPAN_COLORMAP.
                        // SDRAM byte address for the cmap lookup.  Slot
                        // base + per-pixel (shade × 256 + texel).  The
                        // 16 KB per colormap slot, expressed as a shift here
                        // to avoid a multiplier.
                        // Colormap id is captured per fragment so affine
                        // groups can interleave lanes with different slots.
                        //
                        // Staged HERE (p1→p2) so the request is presented
                        // to tex_cache port B during the fragment's p2
                        // residence and its response is already at the
                        // queue head when the fragment reaches p2b — one
                        // pipe stage earlier than the old p2→p2b staging,
                        // which forced a stall cycle per lit pixel.  The
                        // cmap_pipe_wait staging-overflow term guarantees
                        // the pending slot is free (or freeing via accept
                        // this cycle — the accept-clear above loses to
                        // this set, which is exactly the handoff case)
                        // whenever this shift fires.
                        cmap_pending_valid <= 1'b1;
                        cmap_pending_addr  <= cmap_slot_addr
                                            | {12'b0, p1_light, p1_tex_color[7:0]};
                    end
                end

            end

            if (p1_to_p2) begin
                p1_valid <= 1'b0;
                p1_tex_ready <= 1'b0;
            end

            // p1 <- p0 (issue commit).  Cache accepted our request this
            // cycle.  The OLD p0 metadata becomes p1 even if p2/p3 are stalled;
            // p1 holds the texture response until the tail pipe can shift.
            if (issue_committed) begin
                p1_valid   <= 1'b1;
                p1_light   <= p0_light;
                p1_R       <= p0_R;
                p1_B       <= p0_B;
                p1_Dr      <= p0_Dr;
                p1_Dg      <= p0_Dg;
                p1_Db      <= p0_Db;
                p1_colormap_id <= p0_colormap_id;
                p1_flags   <= p0_flags;
                p1_fb_addr <= p0_fb_addr;
                p1_z_test  <= p0_z_test;
                p1_z_write <= p0_z_write;
                p1_z_addr  <= p0_z_addr;
                p1_z_value <= p0_z_value;
                p1_tex_ready <= 1'b0;
                p1_tex_color <= 16'd0;
                p0_valid <= 1'b0;
            end

            // p0 <- p0a.  This promotion is independent of the tail-pipe
            // shift, so an empty p0 can be preloaded even while p1/p2/p3 are
            // stalled.  tex_req_valid remains blocked by p1_valid, so the
            // cache still sees one in-order request at a time.
            if (p0a_to_p0) begin
                p0_valid     <= 1;
                p0_light     <= p0a_light;
                p0_R         <= p0a_R;
                p0_B         <= p0a_B;
                p0_Dr        <= p0a_Dr;
                p0_Dg        <= p0a_Dg;
                p0_Db        <= p0a_Db;
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
            end

            if (p0a_to_p0 && !load_p0a)
                p0a_valid <= 0;

            // Source snapshot and source-state advance are intentionally outside
            // the tail-pipe shift gate.  When p0a is empty, it can prefetch one
            // source fragment while p1/p2/p3 are stalled on a cache or FB event.
            // This removes a hot `p1_valid -> sp_t/sp_s enable` timing cone and
            // gives the fragment pipe one extra cycle of elasticity.
            // z_compress pipe warm-up at span start: while g_zwarm!=0, load_p0a is
            // held off (above), so advance only the lookahead + 2 compress stages
            // (single cone/cycle) to fill the pipe; after 2 cycles zc_s2 holds
            // z_compress(start) and sp_zc_la is the steady-state +2*step.
            // Direct-color builds only (g_zwarm primed 0 otherwise).
            if (g_zwarm != 2'd0) begin
                sp_zc_la <= sp_zc_la + sp_zc_step;
                zc_s1 <= zc_stage1(sp_zc_la); zc_s2 <= zc_stage2(zc_s1);
                g_zwarm <= g_zwarm - 2'd1;
            end
            if (load_p0a) begin
                p0a_valid     <= 1;
                p0a_light     <= source_light;
                p0a_R         <= source_R;
                p0a_B         <= source_B;
                p0a_Dr        <= source_Dr;
                p0a_Dg        <= source_Dg;
                p0a_Db        <= source_Db;
                p0a_colormap_id <= source_colormap_id;
                p0a_flags     <= sp_flags;
                p0a_fb_addr   <= source_fb_addr;
                p0a_tex_base  <= source_tex_base;
                p0a_tex_width <= sp_tex_width;
                p0a_z_test    <= sp_z_test_enable;
                p0a_z_write   <= sp_z_test_enable && sp_z_write_enable;
                p0a_z_addr    <= sp_z_addr;
                p0a_z_value   <= source_z_half;
                p0a_s_int <= mirror_idx(source_s_clamped, sp_tex_w_mask,
                                        sp_tex_w_octave, sp_mirror_s);
                p0a_t_y   <= mirror_idx(source_t_clamped, sp_tex_h_mask,
                                        sp_tex_h_octave, sp_mirror_t);

                if (load_p0a_z) begin
                    z_src_push      = 1'b1;
                    z_src_push_addr = source_z_word_addr;
                    z_src_push_hi   = source_z_hi;
                    z_src_push_half = source_z_half;
                    sp_z_addr  <= sp_z_addr + sp_z_step;
                    sp_z_value <= sp_z_value + sp_z_value_step;
                    if (sp_q29_z_enable)
                        sp_q29_z_value <= sat_add32(sp_q29_z_value,
                                                    sp_q29_z_value_step);
                    // pipelined z_compress: advance the unified lookahead + both
                    // stages in lockstep (prunes on non-direct-color builds).
                    sp_zc_la <= sp_zc_la + sp_zc_step;
                    zc_s1 <= zc_stage1(sp_zc_la); zc_s2 <= zc_stage2(zc_s1);
                end
                else if (source_z_advance_active) begin
                    sp_z_addr  <= sp_z_addr + sp_z_step;
                    sp_z_value <= sp_z_value + sp_z_value_step;
                    if (sp_q29_z_enable)
                        sp_q29_z_value <= sat_add32(sp_q29_z_value,
                                                    sp_q29_z_value_step);
                    sp_zc_la <= sp_zc_la + sp_zc_step;
                    zc_s1 <= zc_stage1(sp_zc_la); zc_s2 <= zc_stage2(zc_s1);
                end

                sp_fb_addr <= sp_fb_addr + sp_fb_stride;
                sp_count   <= sp_count - 16'd1;
                sp_light_q <= sp_light_q + sp_light_step;
                sp_R_q <= sp_R_q + sp_R_step;   // truecolor RGB red advance
                sp_B_q <= sp_B_q + sp_B_step;   // truecolor RGB blue advance
                sp_Dr_q <= sp_Dr_q + sp_Dr_step; // combine D advance
                sp_Dg_q <= sp_Dg_q + sp_Dg_step;
                sp_Db_q <= sp_Db_q + sp_Db_step;
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
                        // QUADRATIC sub-segment: advance the running step by the
                        // per-pixel 2nd difference (forward-differenced parabola).
                        // sp_scc/sp_tcc are 0 on the affine/palettized path, so
                        // this reduces to sp_sstep <= sp_sstep (a hold) and is
                        // bit-identical to the old constant-step behaviour.  Plain
                        // register reads keep it out of the swap timing cone.
                        sp_sstep    <= sp_sstep + sp_scc;
                        sp_tstep    <= sp_tstep + sp_tcc;
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
                sp_scc               <= persp_pend_scc;   // quadratic 2nd-diff (0 on affine)
                sp_tcc               <= persp_pend_tcc;
                sp_seg_left          <= PERSPECTIVE_SEG_LAST;
                persp_swap_pending   <= 1'b0;
            end

            // ----------------------------------------------------------
            // FB sub-FSM — consumes p3 (drains pipeline tail)
            // ----------------------------------------------------------
            // Shared AR self-clear: every read flow (z fill, CB fill,
            // translucent read) issues on blend_ar* and jumps straight to
            // its R-wait state; the handshake completes here.  AXI orders
            // R strictly after AR acceptance, so the R-wait capture logic
            // can never fire early.
            if (blend_arvalid && blend_arready)
                blend_arvalid <= 1'b0;
            case (fbss)
                FBSS_IDLE: begin
                    // Translucent fragments are grouped by destination word.
                    // As long as consecutive fragments hit different byte
                    // lanes in the same word, keep collecting and let the
                    // pipe advance.  A word change, duplicate lane, or
                    // following opaque pixel flushes the group first so draw
                    // order stays byte-exact.
                    if (p3_valid && !p3_discard && p3_flags[SPAN_TRANSLUC]) begin
                        if (!blend_group_active) begin
                            blend_group_active    <= 1'b1;
                            blend_group_word_addr <= p3_fb_word_addr_w;
                            blend_group_mask      <= p3_fb_lane_mask_w;
                            blend_group_src_data  <= p3_fb_lane_data_w;
                            p3_consumed = 1'b1;
                        end else if (blend_group_word_addr == p3_fb_word_addr_w
                                  && !(|(blend_group_mask & p3_fb_lane_mask_w))) begin
                            blend_group_mask <= blend_group_mask | p3_fb_lane_mask_w;
                            blend_group_src_data <= blend_group_src_data
                                                  | p3_fb_lane_data_w;
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
                                // Detour shortcut (3.2): serve a window hit in
                                // the SAME cycle the mismatched acc flushes.
                                // The arm's own guard says flushed word !=
                                // tested word, so the push (whose snoop lands
                                // on the FLUSHED word next cycle) cannot touch
                                // the captured copy — waiting out the snoop
                                // round trip bought nothing.  Off-window (or
                                // suppressed-snoop) cases retry from IDLE as
                                // before.
                                if ((GPU_Z_READ_WINDOW > 1)
                                    && !zw_snoop_pending
                                    && zw_valid[p3_z_word_addr[3:2]]
                                    && (zw_base == p3_z_word_addr[GPU_ADDR_W-1:4])) begin
                                    ztest_cap_fire = 1'b1;
                                    ztest_cap_from_read = 1'b1;
                                    ztest_cap_word = zw_word[p3_z_word_addr[3:2]];
                                    fbss <= FBSS_ZTEST_ACC_EVAL;
                                end
                            end else begin
                                fbss <= FBSS_FLUSH_W_RSP;
                            end
                        end else if (z_acc_valid && z_acc_addr == p3_z_word_addr) begin
                            // Shared ztest capture (source: z accumulator).
                            ztest_cap_fire = 1'b1;
                            ztest_cap_word = {z_acc_hi, z_acc_lo};
                            fbss               <= FBSS_ZTEST_ACC_EVAL;
                        end else if ((GPU_Z_READ_WINDOW > 1)
                                  && !zw_snoop_pending
                                  && zw_valid[p3_z_word_addr[3:2]]
                                  && (zw_base == p3_z_word_addr[GPU_ADDR_W-1:4])) begin
                            // Z-window hit: serve the old word from the
                            // 4-word read cache — no SDRAM round trip, no
                            // drain barrier.  Same ACC_EVAL inputs the
                            // single-word read produced.
                            //
                            // PRUNE GATE: with GPU_Z_READ_WINDOW==1 this arm
                            // is constant-dead (zw_valid is also never set
                            // non-zero), so the zw_word/zw_base storage and
                            // this whole serve path sweep.
                            // Shared ztest capture (source: z-window word).
                            ztest_cap_fire = 1'b1;
                            ztest_cap_from_read = 1'b1;
                            ztest_cap_word = zw_word[p3_z_word_addr[3:2]];
                            fbss               <= FBSS_ZTEST_ACC_EVAL;
                        end else if (!tex_axi_arvalid && !tex_m0_in_flight
                                  && fb_write_drain_complete) begin
                            // Z-window fill: 4-beat burst read of the whole
                            // 16-byte z line.  The drain barrier is the
                            // conservative fb_write_drain_complete: the whole
                            // fb write queue drains before the z line is read,
                            // so no z-read overlaps any pending write.
                            // GPU_Z_READ_WINDOW==1
                            // degenerates to exactly that old shape: a
                            // single-word read (arlen 0) of the word under
                            // test, nothing cached.
                            blend_arvalid <= 1'b1;
                            blend_araddr  <= (GPU_Z_READ_WINDOW > 1)
                                           ? {p3_z_word_addr[GPU_ADDR_W-1:4],
                                              4'b0}
                                           : p3_z_word_addr;
                            blend_arlen_r <= (GPU_Z_READ_WINDOW > 1) ? 2'd3
                                                                     : 2'd0;
                            zw_base       <= p3_z_word_addr[GPU_ADDR_W-1:4];
                            zw_valid      <= 4'b0;
                            zw_fill_beat  <= 2'd0;
                            fbss          <= FBSS_ZTEST_R_WAIT;
                        end
                    end
                    // Process p3 if it has a non-discard pixel (and no pending depth work)
                    else if (p3_valid && !p3_discard) begin : fb_acc_blk
                        if (sp_truecolor && sp_blend && !p3_cb_ready) begin
                            // Blend pixel whose probe MISSED the dst window:
                            // detour to fill + resolve (the pipe freezes; p3
                            // holds).  A window-HIT blend pixel never reaches
                            // this arm — p3_cb_ready routes it through the
                            // commit below with its lane data muxed to the
                            // repacked blend result.  Const 0 / pruned when
                            // INCLUDE_DIRECT_COLOR is off -> byte-exact
                            // opaque.
                            fbss <= FBSS_CB_REQ;
                        end else if (sp_truecolor && sp_blend && p3_cb_stale) begin
                            // Stale capture: a same-half commit landed at one
                            // of this pixel's capture edges.  Detour one
                            // cycle to re-derive the sums from fb_acc (which
                            // now holds the half) — a state, not an inline
                            // cycle, so the multiplier operand select stays a
                            // registered state compare.
                            fbss <= FBSS_CB_REFRESH;
                        end else begin
                        p3_word_match = fb_acc_p3_word_match;

                        if (!p3_word_match) begin
                            if (fb_write_can_issue) begin
                                fbwq_push_req  = 1'b1;
                                fbwq_push_addr = fb_acc_addr;
                                fbwq_push_data = fb_acc_data;
                                fbwq_push_strb = fb_acc_mask;

                                fb_acc_valid <= 1'b1;
                                fb_acc_addr  <= p3_fb_word_addr_w;
                                fb_acc_data  <= p3_commit_lane_data_w;
                                fb_acc_mask  <= p3_fb_lane_mask_w;
                                p3_consumed = 1'b1;
                            end else begin
                                // The write queue is full.  Park FBSS for one
                                // or more cycles; p3 remains valid because
                                // fbss != IDLE stalls the pipe.
                                fbss <= FBSS_FLUSH_W_RSP;
                            end
                        end else begin
                            fb_acc_valid <= 1'b1;
                            fb_acc_addr  <= p3_fb_word_addr_w;
                            fb_acc_data  <= (fb_acc_data & ~p3_fb_lane_data_mask_w)
                                          | (p3_commit_lane_data_w
                                             & p3_fb_lane_data_mask_w);
                            fb_acc_mask  <= fb_acc_mask | p3_fb_lane_mask_w;
                            p3_consumed = 1'b1;
                        end
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

	                FBSS_ZTEST_R_WAIT: begin
			                    if (blend_rvalid) begin
		                        // Collect all 4 beats of the z-line burst into
		                        // the window; the beat that carries the word
		                        // under test feeds ACC_EVAL exactly as the old
		                        // single-word read did.
		                        //
		                        // PRUNE GATE: with GPU_Z_READ_WINDOW==1 the fill
		                        // is a single beat (arlen 0) carrying exactly the
		                        // word under test, so both per-beat conditions
		                        // fold to constant-true, the zw_* bookkeeping
		                        // goes write-only (unread -> swept), and zw_valid
		                        // stays constant 0.
		                        zw_word[zw_fill_beat] <= blend_rdata;
		                        zw_fill_beat          <= zw_fill_beat + 2'd1;
		                        if ((GPU_Z_READ_WINDOW <= 1)
		                          || (zw_fill_beat == p3_z_word_addr[3:2])) begin
		                            // Shared ztest capture (source: read return).
		                            ztest_cap_fire = 1'b1;
		                            ztest_cap_from_read = 1'b1;
		                            ztest_cap_word = blend_rdata;
		                        end
		                        if ((GPU_Z_READ_WINDOW <= 1)
		                          || (zw_fill_beat == 2'd3)) begin
		                            zw_valid <= (GPU_Z_READ_WINDOW > 1) ? 4'hF
		                                                                : 4'h0;
		                            fbss     <= FBSS_ZTEST_ACC_EVAL;
		                        end
		                    end
		                end

		                FBSS_ZTEST_ACC_EVAL: begin
		                    // The pixel's own operands (value/half-select/
		                    // write/word address) are read from the LIVE p3
		                    // registers: p3 is held valid and unshifted for
		                    // the whole detour (z hold term in
		                    // fp_pipe_shift_blocked), so the old captured
		                    // copies were verbatim duplicates.  Only old_half,
		                    // word and from_read carry information p3 lacks.
		                    if (p3_z_value >= ztest_acc_old_half) begin
		                        if (p3_z_write) begin
		                            if (ztest_acc_from_read) begin
		                                z_acc_valid <= 1'b1;
		                                z_acc_addr  <= p3_z_word_addr;
		                                if (p3_z_hi) begin
		                                    z_acc_hi   <= p3_z_value;
		                                    z_acc_lo   <= ztest_acc_word[15:0];
		                                    z_acc_mask <= 4'b1100;
		                                end else begin
		                                    z_acc_hi   <= ztest_acc_word[31:16];
		                                    z_acc_lo   <= p3_z_value;
		                                    z_acc_mask <= 4'b0011;
		                                end
		                            end else if (p3_z_hi) begin
		                                z_acc_hi <= p3_z_value;
		                                z_acc_mask[3:2] <= 2'b11;
		                            end else begin
		                                z_acc_lo <= p3_z_value;
		                                z_acc_mask[1:0] <= 2'b11;
		                            end
		                        end
		                        p3_z_test <= 1'b0;
	                    end else begin
	                        p3_valid <= 1'b0;
	                    end
	                    fbss <= FBSS_IDLE;
	                end

                // --------------------------------------------------------
                // Truecolor constant-alpha blend MISS path (the fold's slow
                // lane): the p2b probe missed the dst window, so fill it
                // fresh — flush-before-read, full drain barrier — then
                // resolve THIS pixel's sums and hand it back to IDLE, where
                // it commits like an opaque pixel.  Following same-window
                // pixels fold at p2b and never come back here.  Unreachable
                // (pruned) when sp_blend is const 0 (INCLUDE_DIRECT_COLOR off).
                // --------------------------------------------------------
                FBSS_CB_REQ: begin
                    if (fb_acc_valid) begin
                        // Flush any pending accumulator first so the blend read
                        // sees committed pixels (it may target the same word).
                        // Clear the MASK with the valid: the unified IDLE
                        // commit merges into an invalid accumulator (mask-OR),
                        // so a stale mask would adopt garbage lanes from the
                        // flushed word (invariant: !valid => mask == 0).
                        if (fb_write_can_issue) begin
                            fbwq_push_req  = 1'b1;
                            fbwq_push_addr = fb_acc_addr;
                            fbwq_push_data = fb_acc_data;
                            fbwq_push_strb = fb_acc_mask;
                            fb_acc_valid   <= 1'b0;
                            fb_acc_mask    <= 4'b0;
                        end else begin
                            fbss <= FBSS_FLUSH_W_RSP;   // queue full: drain, retry
                        end
                    end else if (!tex_axi_arvalid && !tex_m0_in_flight
                              && fb_write_drain_complete) begin
                        // All writes drained: fill the dst window with one
                        // aligned burst (the requested word feeds this pixel;
                        // the siblings serve the following pixels' p2b
                        // probes).  1-word window: the "line" IS the word —
                        // legacy behavior.
                        blend_arvalid <= 1'b1;
                        blend_araddr  <= {p3_fb_word_addr_w[GPU_ADDR_W-1:CBW_LOW],
                                          {CBW_LOW{1'b0}}};
                        blend_arlen_r <= CBW_ARLEN;
                        cbw_base      <= p3_fb_word_addr_w[GPU_ADDR_W-1:CBW_LOW];
                        cbw_valid     <= 4'b0;
                        cbw_fill_beat <= 2'd0;
                        fbss          <= FBSS_CB_FILLR;
                    end
                end
                FBSS_CB_FILLR: begin
                    if (blend_rvalid) begin
                        // Collect all burst beats into the window; the beat
                        // that carries the word under blend captures cb_dst_r
                        // (the fb_acc bypass is unnecessary here — CB_REQ
                        // flushed the accumulator and drained before the
                        // burst).  1-word window: single beat, no valid set
                        // (the window bookkeeping goes write-only and sweeps).
                        cbw_word[cbw_fill_beat] <= blend_rdata;
                        cbw_fill_beat           <= cbw_fill_beat + 2'd1;
                        if (cbw_fill_beat == cbw_p3_idx)
                            cb_dst_r <= cb_dst_half_w;
                        if (cbw_fill_beat == CBW_ARLEN) begin
                            cbw_valid <= (CBW_WORDS == 4) ? 4'hF
                                       : (CBW_WORDS == 2) ? 4'b0011 : 4'b0000;
                            fbss      <= FBSS_CB_RESOLVE;
                        end
                    end
                end
                FBSS_CB_RESOLVE: begin
                    // One cycle: this pixel's weighted sums through the SAME
                    // shared multipliers the p2b fold uses (operand-muxed to
                    // p3_color / cb_dst_r — the pipe is frozen, so the fold
                    // capture cannot fire concurrently).  Back to IDLE where
                    // the pixel commits through the standard one-cycle path.
                    cb_sr       <= cbm_sum_r_w;
                    cb_sg       <= cbm_sum_g_w;
                    cb_sb       <= cbm_sum_b_w;
                    p3_cb_ready <= 1'b1;
                    // cb_dst_r is fresh post-drain SDRAM content — fresher
                    // than any staleness the capture edges flagged (and the
                    // refresh would read the acc CB_REQ just flushed).
                    p3_cb_stale <= 1'b0;
                    fbss        <= FBSS_IDLE;
                end
                FBSS_CB_REFRESH: begin
                    // One cycle: re-derive the stale pixel's sums from the
                    // fb_acc-resident dst half through the shared
                    // multipliers, then commit normally from IDLE.
                    cb_sr       <= cbm_sum_r_w;
                    cb_sg       <= cbm_sum_g_w;
                    cb_sb       <= cbm_sum_b_w;
                    p3_cb_stale <= 1'b0;
                    fbss        <= FBSS_IDLE;
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
                        blend_arlen_r <= 2'd0;
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
                        blend_p3_match_r <= fb_acc_blend_word_match;
                        blend_lane_iter <= 2'd0;
                        fbss <= FBSS_BLEND_SELECT;
                    end
                end

                FBSS_BLEND_SELECT: begin : fbss_blend_select_blk
                    if (blend_group_mask[blend_lane_iter]) begin
                        transluc_rd_addr <= blend_lut_addr_w;
                        blend_lut_lane   <= blend_lane_iter;
                        fbss             <= FBSS_BLEND_LOOKUP;
                    end else if (blend_lane_iter == 2'd3) begin
                        fbss <= FBSS_BLEND_APPLY;
                    end else begin
                        blend_lane_iter <= blend_lane_iter + 2'd1;
                    end
                end

                FBSS_BLEND_LOOKUP: begin
                    if (transluc_cache_hit) begin
                        // Shared lane-merge cone (audit A1) — byte source
                        // muxes to transluc_cache_byte in this state.
                        blend_result_word <= blend_lut_merged_word_w;
                        if (blend_lut_lane == 2'd3) begin
                            fbss <= FBSS_BLEND_APPLY;
                        end else begin
                            blend_lane_iter <= blend_lut_lane + 2'd1;
                            fbss <= FBSS_BLEND_SELECT;
                        end
                    end else if (transluc_sram_lookup_ready) begin
                        transluc_lookup_fire <= 1'b1;
                        fbss                 <= FBSS_BLEND_LUT_WAIT;
                    end
                end

                FBSS_BLEND_LUT_WAIT: begin
                    if (sram_rdata_valid) begin
                        // Shared lane-merge cone (audit A1) — byte source
                        // muxes to the SRAM LUT byte in this state.
                        blend_result_word <= blend_lut_merged_word_w;
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
            // Shared ztest_acc capture (round-2 dedup, A2 idiom).  The
            // three FBSS sites above only raise ztest_cap_fire and select
            // the 32-bit source word; this ONE copy of the
            // fb_halfword_read mux applies it.  Only old_half, word and
            // from_read are captured — the pixel's own value/half/write/
            // address are read live from p3 in ACC_EVAL (p3 is held
            // unshifted for the whole detour).  The z_acc-hit source
            // never reads ztest_acc_word in ACC_EVAL (from_read=0), so
            // capturing it unconditionally is dont-care for that arm.
            // ----------------------------------------------------------
            if (ztest_cap_fire) begin
                ztest_acc_old_half  <= fb_halfword_read(ztest_cap_word, p3_z_hi);
                ztest_acc_from_read <= ztest_cap_from_read;
                ztest_acc_word      <= ztest_cap_word;
            end

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
            //
            // Free-running pre-decode of the constant-Z launch qualifier —
            // see pss_constz_go_r declaration for the stability argument.
            pss_constz_go_r <= (persp_pass == PSS_PASS_ANCHOR)
                             && sp_zinv_step_zero;
            if (!persp_active) begin
                persp_pss <= PSS_IDLE;
            end else case (persp_pss)
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
                    // Stage 1 of pipelined setup: advance projection-space
                    // counters and register whether this pass needs a
                    // variable-length tail advance.  The following
                    // PSS_ADV_ISSUE stage uses only this registered decision
                    // to load the shared DSP operands; keeping the
                    // future_count compare out of the DSP enable cone fixes
                    // the post-fit sp_seg_left/sp_count → Mult*_ENA path.
                    //
                    // Quake's software rasterizer uses 16-pixel affine
                    // subspans for non-final chunks, but the final chunk uses
                    // endpoint (count-1) and divides by (count-1).  The old
                    // GPU path always advanced by 16 and divided by 16, which
                    // over-projected every short/remainder floor span.
	                    if (!sp_persp_q29_mode) begin
	                        end_advance = PERSPECTIVE_SEG_LEN[4:0];
	                        slope_divisor = PERSPECTIVE_SEG_LEN[4:0];
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
	                        end else if (future_count > 16'd1) begin
	                            end_advance = future_count[4:0] - 5'd1;
	                            slope_divisor = future_count[4:0] - 5'd1;
	                        end else begin
	                            end_advance = 5'd0;
	                            slope_divisor = 5'd1;
	                        end
	                    end

	                    pss_slope_divisor <= slope_divisor;
	                    pss_zinv_prev_r  <= sp_zinv;
	                    pss_zinv_abs_na_r <= persp_zinv_abs_na;
	                    pss_tail_advance <= end_advance;
	                    persp_pss <= PSS_ADV_ISSUE;
	                end

	                PSS_ADV_ISSUE: begin
	                    // Unified advance: full 16-pixel segments and variable
	                    // tails both go through the shared DSP (step * advance).
	                    // dsp_p[31:0] of step*16 is bit-identical to the old
	                    // dedicated (step <<< 4) adders mod 2^32 for every
	                    // operand value (lower product bits are sign-agnostic),
	                    // at +4 cycles per full segment.
	                    dsp_a  <= sp_sZstep;
	                    dsp_b  <= $signed({27'd0, pss_tail_advance});
	                    dsp2_a <= sp_tZstep;
	                    dsp2_b <= $signed({27'd0, pss_tail_advance});
	                    persp_pss <= PSS_ADV_TAIL_ST_WAIT;
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

                PSS_RECIP_SHIFT: begin : pss_recip_shift_blk
                    reg [5:0]  recip_shamt;
                    reg [44:0] recip_funnel;
                    // Compute the reciprocal from LUT mantissa + clz shift
                    // and latch it before the N-R refinement. Normal spans
                    // use Q16.16.  Quake param-span Q29 mode carries a much
                    // larger denominator, so keep four extra fractional bits
                    // in the reciprocal and compensate in the final multiply;
                    // otherwise the reciprocal often has only ~10 integer
                    // levels and texel boundaries drift.
                    //
                    // ONE left funnel replaces the old two-direction shifter
                    // pair: result = recip <<< (shamt-13) or >>> (13-shamt)
                    // == bits [44:13] of (recip << shamt).  recip_rd_data is
                    // 16 bits unsigned (so <<</>>> coincide) and shamt caps
                    // at 35 (persp_clz max 31 + Q29 extra 4), so a 45-bit
                    // container keeps every bit of the consumed slice; bits
                    // above 44 match the original 32-bit truncation.
                    recip_shamt  = sp_persp_q29_mode ? pss_q29_recip_shift
                                                     : {1'b0, persp_clz};
                    recip_funnel = {29'd0, recip_rd_data} << recip_shamt;
                    recip_q16_r <= $signed(recip_funnel[44:13]);
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
                                     + ((sp_zinv_step_zero
                                      && sp_zinv != 32'sh00010000) ? 32'sd1
                                                                   : 32'sd0);
                    end
                    persp_pss <= PSS_MUL;
                end

                PSS_MUL: begin
                    // Kick BOTH multiplies (sZ×recip on dsp, tZ×recip on dsp2)
                    // in parallel using the pre-registered recip_q16_r.
                    // They land together at PSS_FINAL_PROD one DSP cycle later.
                    dsp_a     <= sp_sZ;
                    dsp_b     <= recip_q16_r;
                    dsp2_a    <= sp_tZ;
                    dsp2_b    <= recip_q16_r;
                    persp_pss <= PSS_MUL_W;
                end

                PSS_MUL_W: begin
                    // DSP pipeline delay — dsp_p and dsp2_p both update here.
                    persp_pss <= PSS_FINAL_PROD;
                end

                PSS_FINAL_PROD: begin
                    // T-split stage (STA #1): register the RAW 64-bit endpoint
                    // products.  The multiplier's fabric recombination
                    // terminates in these plain registers; the shared Q29/Q16
                    // rounding adder + shift/slice run next state (PSS_FINAL)
                    // from register outputs.  Values are bit-identical — the
                    // products merely cross one extra FF stage.  The +1 cycle
                    // sits inside the PSS busy window: the issue stage samples
                    // persp_seg_a_ready / persp_seg_b_ready flags (set only by
                    // the commit states), never a fixed cycle count.
                    pss_prod_s_r <= dsp_p;
                    pss_prod_t_r <= dsp2_p;
                    persp_pss    <= PSS_FINAL;
                end

                PSS_FINAL: begin : pss_final_blk
                    reg signed [31:0] s_projected;
                    reg signed [31:0] t_projected;
                    reg signed [63:0] s_round64;
                    reg signed [63:0] t_round64;
                    reg signed [63:0] s_projected64;
                    reg signed [63:0] t_projected64;
                    // pss_prod_s_r = sZ × recip → s_end   (staged in PSS_FINAL_PROD)
                    // pss_prod_t_r = tZ × recip → t_end
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
                    // ONE rounding adder per DSP product (the Q29/Q16
                    // branches add only a different constant): mux the
                    // round constant on the registered mode flag, keep the
                    // per-mode shift/slice after the shared adder.
                    //
                    // T-split (STA #1): the raw products were registered in
                    // PSS_FINAL_PROD, so this cone starts at pss_prod_*_r
                    // FF outputs — the Mult fabric recombination no longer
                    // shares this cycle with the 64-bit rounding carry.
                    s_round64 = pss_prod_s_r + (sp_persp_q29_mode
                                          ? (64'sd1 << (15 + PSS_Q29_RECIP_EXTRA_INT))
                                          : 64'sd32768);
                    t_round64 = pss_prod_t_r + (sp_persp_q29_mode
                                          ? (64'sd1 << (15 + PSS_Q29_RECIP_EXTRA_INT))
                                          : 64'sd32768);
                    if (sp_persp_q29_mode) begin
                        s_projected64 = s_round64 >>> (16 + PSS_Q29_RECIP_EXTRA_INT);
                        t_projected64 = t_round64 >>> (16 + PSS_Q29_RECIP_EXTRA_INT);
                    end else begin
                        s_projected64 = s_round64 >>> 16;
                        t_projected64 = t_round64 >>> 16;
                    end
                    s_projected = s_projected64[31:0];
                    t_projected = t_projected64[31:0];
                    pss_s_end_r <= pss_zinv_clamp_r ? persp_anchor_s
                                                     : s_projected;
                    pss_t_end_r <= pss_zinv_clamp_r ? persp_anchor_t
                                                     : t_projected;
                    // Constant-Z anchor pass: launch the projection-space
                    // minor-step multiplies HERE (one state earlier than the
                    // old PSS_SLOPE launch — all four operands are stable:
                    // recip_q16_r since PSS_NR_CAPTURE, sp_sZstep/sp_tZstep
                    // since span emit).  The dsp2_b operand-select term is
                    // now one state bit AND the pre-decoded pss_constz_go_r
                    // FF instead of the STA #2 PSS_SLOPE-decode + persp_pass
                    // compare + sp_zinv_step_zero cone.  Products land at
                    // the end of PSS_SLOPE; PSS_CONSTZ_STEP_W captures them
                    // raw and PSS_CONSTZ_STEP_CAPTURE rounds/slices — same
                    // total state count as the old launch position, so
                    // constant-Z spans pay only the PSS_FINAL_PROD cycle.
                    if (pss_constz_go_r) begin
                        dsp_a  <= sp_sZstep;
                        dsp_b  <= recip_q16_r;
                        dsp2_a <= sp_tZstep;
                        dsp2_b <= recip_q16_r;
                    end
                    persp_pss   <= PSS_SLOPE;
                end

                PSS_SLOPE: begin
                    persp_pss <= PSS_IDLE;
                    case (persp_pass)
                        PSS_PASS_ANCHOR: begin
                            if (sp_zinv_step_zero) begin
                                // Constant-Z perspective spans (Doom walls):
                                // one reciprocal is enough for the whole
                                // generated span.  Convert to affine by
                                // projecting the start point and multiplying
                                // the projection-space minor steps by the same
                                // reciprocal.  This avoids the 16-pixel PSS
                                // segment loop entirely while preserving the
                                // perspective-correct result for constant 1/Z.
                                // (The step multiplies were launched in
                                // PSS_FINAL — they are in flight now and land
                                // in dsp_p/dsp2_p at the end of this state.)
                                sp_s <= pss_s_end_r;
                                sp_t <= pss_t_end_r;
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
                            // PIPELINE the quadratic curvature ONE cycle early
                            // (registered into persp_pend_scc/tcc) so its 2-sub
                            // cone from the anchors terminates HERE, off the
                            // sp_sstep commit path (STA #7..#24: persp_anchor_s
                            // -> sp_sstep was the GPU limiter at -2.27).  Three
                            // consecutive perspective-exact anchors:
                            //   c = (A_{N+1} - 2*A_N + A_{N-1}) >>> 8
                            // TRUECOLOR-gated; 0 unless a real A_{N-1} exists
                            // (persp_prev_valid) and not Q29.  SLOPE_PREP consumes
                            // persp_pend_scc next cycle for the d0 step.
                            if ((INCLUDE_DIRECT_COLOR != 0) && sp_truecolor
                                && persp_prev_valid && !sp_persp_q29_mode) begin
                                persp_pend_scc <= ($signed(pss_s_end_r)
                                                   - ($signed(persp_anchor_s) <<< 1)
                                                   + $signed(persp_prev_anchor_s)) >>> 8;
                                persp_pend_tcc <= ($signed(pss_t_end_r)
                                                   - ($signed(persp_anchor_t) <<< 1)
                                                   + $signed(persp_prev_anchor_t)) >>> 8;
                            end else begin
                                persp_pend_scc <= 32'sd0;
                                persp_pend_tcc <= 32'sd0;
                            end
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
                        // Shared slope commit — pow2-exact divide step
                        // source.  (Also halves the pss_div_pow2_trunc
                        // barrel-shift cones: the TO_A/TO_B copies used
                        // identical operands.)
                        pss_slope_commit_fire = 1'b1;
                        pss_commit_s_step = pss_div_pow2_trunc(pss_slope_s_delta, pow2_shift);
                        pss_commit_t_step = pss_div_pow2_trunc(pss_slope_t_delta, pow2_shift);
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
                        // Full-segment (divisor==16) step commit.  The per-pixel
                        // 2nd-difference c was computed and REGISTERED one cycle
                        // earlier in PSS_PASS_TO_B (persp_pend_scc/tcc), so the
                        // qc cone stays OFF the sp_sstep commit path.
                        //   d0 = (delta - 120*c) >>> 4   (120c = (c<<7)-(c<<3))
                        // d0 hits both endpoints (f(0)=A_N, f(16)=A_{N+1}) so the
                        // segment boundary stays continuous.
                        // SLOT A (the span's FIRST segment) is always linear (no
                        // A_{N-1}) and commits DIRECTLY to sp_sstep — give it the
                        // plain delta>>4 with NO curvature logic in its cone, so
                        // the only path to sp_sstep is the original affine one.
                        // SLOT B's quadratic d0 commits to persp_pend_sstep (a
                        // register the segment swap copies 1:1 into sp_sstep), so
                        // its deeper cone never reaches sp_sstep directly.  c=0
                        // (palettized / non-truecolor / Q29 -> persp_pend_scc=0)
                        // collapses both branches to the exact original delta>>4.
                        pss_slope_commit_fire = 1'b1;
                        if (persp_pass == PSS_PASS_TO_A) begin
                            pss_commit_s_step = pss_slope_s_delta >>> PERSPECTIVE_SEG_SHIFT;
                            pss_commit_t_step = pss_slope_t_delta >>> PERSPECTIVE_SEG_SHIFT;
                            pss_commit_scc = 32'sd0;
                            pss_commit_tcc = 32'sd0;
                        end else begin
                            pss_commit_scc = persp_pend_scc;   // registered in PASS_TO_B
                            pss_commit_tcc = persp_pend_tcc;
                            pss_commit_s_step = (pss_slope_s_delta
                                                 - (($signed(persp_pend_scc) <<< 7)
                                                    - ($signed(persp_pend_scc) <<< 3)))
                                                >>> PERSPECTIVE_SEG_SHIFT;
                            pss_commit_t_step = (pss_slope_t_delta
                                                 - (($signed(persp_pend_tcc) <<< 7)
                                                    - ($signed(persp_pend_tcc) <<< 3)))
                                                >>> PERSPECTIVE_SEG_SHIFT;
                        end
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

                PSS_SLOPE_DIV_STEP_COMMIT: begin
                    // Shared slope commit — restored-sign divider quotient
                    // step source.
                    persp_pss <= PSS_IDLE;
                    pss_slope_commit_fire = 1'b1;
                    pss_commit_s_step = pss_slope_s_neg ? -$signed(pss_slope_s_quot)
                                                        : $signed(pss_slope_s_quot);
                    pss_commit_t_step = pss_slope_t_neg ? -$signed(pss_slope_t_quot)
                                                        : $signed(pss_slope_t_quot);
                end

                PSS_CONSTZ_STEP_W: begin
                    // T-split stage (STA #1): the step products (launched in
                    // PSS_FINAL, latched into dsp_p/dsp2_p at the end of
                    // PSS_SLOPE) are valid this whole state — register them
                    // RAW so the round/slice/commit into sp_sstep/sp_tstep
                    // next state starts from plain FF outputs, exactly like
                    // the PSS_FINAL_PROD -> PSS_FINAL endpoint split.
                    pss_prod_s_r <= dsp_p;
                    pss_prod_t_r <= dsp2_p;
                    persp_pss <= PSS_CONSTZ_STEP_CAPTURE;
                end

                PSS_CONSTZ_STEP_CAPTURE: begin : pss_constz_step_capture_blk
                    reg signed [63:0] s_round64;
                    reg signed [63:0] t_round64;
                    reg signed [63:0] s_step_projected;
                    reg signed [63:0] t_step_projected;
                    // Shared Q29/Q16 rounding adder — see PSS_FINAL.  Reads
                    // the pss_prod_*_r stage registers (T-split), not dsp_p.
                    s_round64 = pss_prod_s_r + (sp_persp_q29_mode
                                          ? (64'sd1 << (15 + PSS_Q29_RECIP_EXTRA_INT))
                                          : 64'sd32768);
                    t_round64 = pss_prod_t_r + (sp_persp_q29_mode
                                          ? (64'sd1 << (15 + PSS_Q29_RECIP_EXTRA_INT))
                                          : 64'sd32768);
                    if (sp_persp_q29_mode) begin
                        s_step_projected = s_round64 >>> (16 + PSS_Q29_RECIP_EXTRA_INT);
                        t_step_projected = t_round64 >>> (16 + PSS_Q29_RECIP_EXTRA_INT);
                    end else begin
                        s_step_projected = s_round64 >>> 16;
                        t_step_projected = t_round64 >>> 16;
                    end
                    sp_sstep <= s_step_projected[31:0];
                    sp_tstep <= t_step_projected[31:0];
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
            // PSS slope-commit dedup: the {sp_sstep/sp_tstep, seg_left,
            // anchor, a_ready} vs {persp_pend_*, anchor, b_ready} commit
            // pair was spelled verbatim in THREE arms (PSS_SLOPE_PREP
            // pow2-exact, PSS_SLOPE_PREP full-segment >>4 fallback, and
            // PSS_SLOPE_DIV_STEP_COMMIT), differing only in the step
            // value source.  ONE shared commit, sequenced by the fire
            // flag those arms set.  Source position (after the persp_pss
            // case) preserves the original last-writer ordering vs the
            // load_p0a / persp_swap_pending blocks above.
            // ----------------------------------------------------------
            if (pss_slope_commit_fire) begin
                case (persp_pass)
                    PSS_PASS_TO_A: begin
                        sp_sstep          <= pss_commit_s_step;
                        sp_tstep          <= pss_commit_t_step;
                        sp_scc            <= pss_commit_scc;   // quadratic 2nd-diff -> slot A
                        sp_tcc            <= pss_commit_tcc;
                        sp_seg_left       <= PERSPECTIVE_SEG_LAST;
                        // Capture A_{N-1} before persp_anchor advances to A_{N+1},
                        // and arm curvature for the NEXT segment (this first full
                        // segment itself stays linear: persp_prev_valid was 0).
                        persp_prev_anchor_s <= persp_anchor_s;
                        persp_prev_anchor_t <= persp_anchor_t;
                        persp_prev_valid    <= 1'b1;
                        persp_anchor_s    <= pss_s_end_r;
                        persp_anchor_t    <= pss_t_end_r;
                        persp_seg_a_ready <= 1;
                    end
                    PSS_PASS_TO_B: begin
                        persp_pend_sstep  <= pss_commit_s_step;
                        persp_pend_tstep  <= pss_commit_t_step;
                        persp_pend_scc    <= pss_commit_scc;   // quadratic 2nd-diff -> slot B
                        persp_pend_tcc    <= pss_commit_tcc;
                        // Advance anchors: A_{N-1} <- A_N, A_N <- new endpoint.
                        persp_prev_anchor_s <= persp_anchor_s;
                        persp_prev_anchor_t <= persp_anchor_t;
                        persp_prev_valid    <= 1'b1;
                        persp_anchor_s    <= pss_s_end_r;
                        persp_anchor_t    <= pss_t_end_r;
                        persp_seg_b_ready <= 1;
                    end
                    default: ;
                endcase
            end

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
	                        if (cmd_is_tri_walker) begin
	                            if (!tri_walker_done) begin
	                                state <= S_TRI_FILL;
	                            end else begin
	                                spanprod_active <= 1'b0;
	                                state <= S_FB_FLUSH;
	                            end
	                        end
	                        else if (spanprod_more_records_w
	                            && (pay_remaining != 13'd0)) begin
	                            spanprod_prepare_next_record_chunk;
	                        end else begin
	                            spanprod_active <= 1'b0;
	                            state <= S_FB_FLUSH;
	                        end
	                    end else begin
	                        spanprod_idx <= spanprod_idx + 2'd1;
	                        state <= S_SPANPROD_SELECT;
                    end
                end else
                    state    <= S_FB_FLUSH;
            end
            // ----------------------------------------------------------
            // Round-2 early record handoff (cheap B1 subset).  For the
            // opaque non-z direct fastpath ONLY (sp_fastpath: direct-
            // affine, no z write/test, no SPAN_TRANSLUC; persp inactive),
            // the next record's SELECT/SETUP/EMIT reload touches only
            // sp_*/spanprod_cur_* — nothing p1..p3 or FBSS read — and
            // those control states freeze the tail anyway (pipe shift +
            // fbss live only inside this S_FRAG_PIPE arm).  So the
            // continuation may fire as soon as the source is done and the
            // issue-side stages (p0a/p0) are empty, while p1..p3/fbss
            // keep draining; on re-entry the frozen tail retires ahead of
            // the new record's pixels, so fbwq commit order — and the
            // final FB bytes — are unchanged.  Restricted to the
            // intra-chunk SELECT hop: retire and chunk hand-off keep the
            // full drain above (retiring with a frozen tail would strand
            // pixels — the zero-count exhaust case is covered by the
            // tail-safe bounce in S_SPANPROD_SETUP).  A stale blend group
            // from a PRIOR translucent record still blocks, exactly like
            // the full condition.
            // ----------------------------------------------------------
            else if (sp_fastpath && spanprod_active
                  && (spanprod_idx != spanprod_last_idx)
                  && src_done && !p0a_valid && !p0_valid
                  && !blend_group_active && !persp_active) begin
                src_done <= 0;
                spanprod_idx <= spanprod_idx + 2'd1;
                state <= S_SPANPROD_SELECT;
            end
        end

        // ============================================================
        // FB flush — end-of-span or mid-span word boundary
        // ============================================================
        S_FB_FLUSH: begin
            // FLUSH dedup (audit A2): the accumulator drain chain runs in
            // the ONE shared block ahead of this case statement.  Retire
            // the fragment stream on its all-clear — same cycle the final
            // fb_acc push is accepted, exactly like the old in-arm copy.
            if (acc_drain_done) begin
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
                fbwq_push_addr = cr_addr & {{(GPU_ADDR_W-2){1'b1}}, 2'b00};
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
                        cr_row_addr    <= cr_row_addr + {{(GPU_ADDR_W-16){1'b0}}, cr_eff_stride};
                        cr_addr        <= cr_row_addr + {{(GPU_ADDR_W-16){1'b0}}, cr_eff_stride};
                        cr_w_remaining <= cr_w_total;
                        cr_y_remaining <= cr_y_remaining - 16'd1;
                        state          <= S_CLEAR_RECT_WORD;
                    end
                end else begin
                    cr_addr        <= cr_addr + {{(GPU_ADDR_W-3){1'b0}}, cr_bytes_this};
                    cr_w_remaining <= cr_w_remaining - {13'b0, cr_bytes_this};
                    state          <= S_CLEAR_RECT_WORD;
                end
            end
        end


        default: state <= S_IDLE;
        endcase

            if (z_src_pending_consume && !z_flush_set) begin
                z_src_pending_applied = 1'b1;
                if (z_acc_valid && z_acc_addr != z_src_pending_addr) begin
                    z_flush_valid <= 1'b1;
                    z_flush_addr  <= z_acc_addr;
                    z_flush_data  <= z_acc_data;
                    z_flush_strb  <= z_acc_mask;
                    z_flush_set   = 1'b1;
                    z_acc_addr    <= z_src_pending_addr;
                    z_acc_valid   <= 1'b1;
                    if (z_src_pending_hi) begin
                        z_acc_hi   <= z_src_pending_half;
                        z_acc_lo   <= 16'b0;
                        z_acc_mask <= 4'b1100;
                    end else begin
                        z_acc_hi   <= 16'b0;
                        z_acc_lo   <= z_src_pending_half;
                        z_acc_mask <= 4'b0011;
                    end
                end else begin
                    z_acc_valid <= 1'b1;
                    z_acc_addr  <= z_src_pending_addr;
                    if (z_src_pending_hi) begin
                        z_acc_hi <= z_src_pending_half;
                        z_acc_mask[3:2] <= 2'b11;
                        if (!z_acc_valid) begin
                            z_acc_lo <= 16'b0;
                            z_acc_mask[1:0] <= 2'b00;
                        end
                    end else begin
                        z_acc_lo <= z_src_pending_half;
                        z_acc_mask[1:0] <= 2'b11;
                        if (!z_acc_valid) begin
                            z_acc_hi <= 16'b0;
                            z_acc_mask[3:2] <= 2'b00;
                        end
                    end
                end
            end

            if (z_src_push) begin
                z_src_pending_valid <= 1'b1;
                z_src_pending_addr  <= z_src_push_addr;
                z_src_pending_hi    <= z_src_push_hi;
                z_src_pending_half  <= z_src_push_half;
            end else if (z_src_pending_applied) begin
                z_src_pending_valid <= 1'b0;
            end

            if (!fbwq_push_req && z_flush_valid && fb_write_can_issue) begin
                fbwq_push_req  = 1'b1;
                fbwq_push_addr = z_flush_addr;
                fbwq_push_data = z_flush_data;
                fbwq_push_strb = z_flush_strb;
                if (!z_flush_set)
                    z_flush_valid <= 1'b0;
            end

            fbwq_req_links_fifo_tail  = fbwq_req_links_fifo_tail_w;
            fbwq_req_links_stage_tail = fbwq_req_links_stage_tail_w;
            fbwq_req_link_tail = fbwq_stage_drain_now
                               ? fbwq_req_links_stage_tail
                               : fbwq_req_links_fifo_tail;

            // --------------------------------------------------------
            // Central AXI write drain.  The main FSM only enqueues
            // writes into fbwq; this block owns m_wr_* and preserves
            // write order across spans, clears, fences, and translucent
            // read-modify-write barriers.
            // --------------------------------------------------------
            if (fbwq_start_now) begin
                m_wr_awvalid <= 1'b1;
                m_wr_awaddr  <= {{(32-GPU_ADDR_W){1'b0}}, fbwq_addr[fbwq_rd_ptr]};
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
            end else if (fbwq_w_chain_start) begin
                // First W beat of the pre-announced (AW-ahead) burst —
                // back-to-back with the previous burst's last beat.
                m_wr_wvalid  <= 1'b1;
                m_wr_wdata   <= fbwq_data[fbwq_rd_ptr];
                m_wr_wstrb   <= fbwq_strb[fbwq_rd_ptr];
                m_wr_wlast   <= (fbwq_aw_ahead_words == 4'd1);
                fbwq_rd_ptr  <= fbwq_rd_ptr + 1'b1;
                fbwq_burst_remaining <= fbwq_aw_ahead_words - 4'd1;
                fbwq_aw_ahead_valid  <= 1'b0;
            end

            if (fbwq_aw_ahead_issue) begin
                // Pre-announce the next burst's AW while the current run's
                // last W beat drains.  With burst_remaining == 0 the next
                // head is fbwq_rd_ptr, so this is the same MLAB read and
                // the same length cone the idle-start path uses (the two
                // are mutually exclusive: start requires the W channel
                // idle, this requires it busy).
                m_wr_awvalid <= 1'b1;
                m_wr_awaddr  <= {{(32-GPU_ADDR_W){1'b0}}, fbwq_addr[fbwq_rd_ptr]};
                m_wr_awlen   <= {4'b0, fbwq_start_burst_words - 4'd1};
                fbwq_aw_ahead_valid <= 1'b1;
                fbwq_aw_ahead_words <= fbwq_start_burst_words;
            end

            if (fbwq_swap_now) begin
                // Tail-1 link repair: req's chain-extending entry enters
                // the array AHEAD of the staged non-linking entry (which
                // stays put).  The prev-tail link is set unconditionally
                // on the swap condition (req links the array tail); the
                // staged entry's own link is re-derived against its NEW
                // predecessor — req's entry, the new tail.
                fbwq_addr[fbwq_wr_ptr] <= fbwq_req_addr;
                fbwq_data[fbwq_wr_ptr] <= fbwq_req_data;
                fbwq_strb[fbwq_wr_ptr] <= fbwq_req_strb;
                fbwq_tail_addr         <= fbwq_req_addr;
                fbwq_tail_strb         <= fbwq_req_strb;
                fbwq_link_next[fbwq_wr_ptr] <= 1'b0;
                if (fbwq_has_tail_after_pop)
                    fbwq_link_next[fbwq_prev_wr_ptr] <= 1'b1;
                fbwq_wr_ptr            <= fbwq_wr_ptr + 1'b1;
                fbwq_stage_link_tail   <= fbwq_stage_links_req_w;
            end else if (fbwq_stage_drain_now) begin
                fbwq_addr[fbwq_wr_ptr] <= fbwq_stage_addr;
                fbwq_data[fbwq_wr_ptr] <= fbwq_stage_data;
                fbwq_strb[fbwq_wr_ptr] <= fbwq_stage_strb;
                fbwq_tail_addr         <= fbwq_stage_addr;
                fbwq_tail_strb         <= fbwq_stage_strb;
                fbwq_link_next[fbwq_wr_ptr] <= 1'b0;
                if (fbwq_has_tail_after_pop)
                    fbwq_link_next[fbwq_prev_wr_ptr] <= fbwq_stage_link_tail;
                fbwq_wr_ptr            <= fbwq_wr_ptr + 1'b1;
            end

            if (fbwq_req_to_stage_now) begin
                fbwq_stage_valid <= 1'b1;
                fbwq_stage_addr  <= fbwq_req_addr;
                fbwq_stage_data  <= fbwq_req_data;
                fbwq_stage_strb  <= fbwq_req_strb;
                fbwq_stage_link_tail <= fbwq_req_link_tail;
            end else if (fbwq_stage_drain_now) begin
                fbwq_stage_valid <= 1'b0;
                fbwq_stage_link_tail <= 1'b0;
            end

            if (fbwq_push_req && fbwq_can_push) begin
                fbwq_req_valid <= 1'b1;
                fbwq_req_addr  <= fbwq_push_addr;
                fbwq_req_data  <= fbwq_push_data;
                fbwq_req_strb  <= fbwq_push_strb;
            end else if (fbwq_req_to_stage_now || fbwq_swap_now) begin
                fbwq_req_valid <= 1'b0;
            end

            fbwq_count <= fbwq_count
                        + ((fbwq_stage_drain_now || fbwq_swap_now) ? 5'd1 : 5'd0)
                        - fbwq_pop_count;

            // Free-running z-step operand capture for the span EMIT's Q29
            // restore (see q29_zstep_op_r declaration for the timing story).
            q29_zstep_op_r <= spanprod_span_axis ? spanprod_attr2_dv
                                                 : spanprod_attr2_du;

            // Z-window write snoop (item 5 exactness rule 2): every write
            // entering the queue that lands in the window's 16-byte line
            // invalidates that word.  REGISTERED (timing): the compare runs
            // one cycle after push-accept on fbwq_req_addr — the register
            // the queue already loads at accept — instead of on the
            // combinational push-address mux; zw_snoop_pending suppresses
            // window hits for the one in-flight cycle (see the zw_valid
            // declaration comment for the exactness argument).  Placed LAST
            // in the block so the clear wins over any same-cycle zw_valid
            // fill (non-blocking, per-bit) — same win-over property as the
            // old combinational snoop.
            // PRUNE GATE: constant-dead with GPU_Z_READ_WINDOW==1 (nothing
            // is ever cached — zw_valid is constant 0), so the pending bit
            // and zw_base comparator sweep with the rest of the window.
            zw_snoop_pending <= (GPU_Z_READ_WINDOW > 1)
                              && fbwq_push_req && fbwq_can_push;
            if ((GPU_Z_READ_WINDOW > 1) && zw_snoop_pending
                && (fbwq_req_addr[GPU_ADDR_W-1:4] == zw_base))
                zw_valid[fbwq_req_addr[3:2]] <= 1'b0;
            // Blend-dst window snoop: identical contract, own pending bit.
            // Sweeps with the window when INCLUDE_DIRECT_COLOR is absent OR
            // the window is 1 word (cbw_valid is never set either way, so
            // the clear is constant-dead).
            cbw_snoop_pending <= (CBW_WORDS > 1)
                              && fbwq_push_req && fbwq_can_push;
            if ((CBW_WORDS > 1) && cbw_snoop_pending
                && (fbwq_req_addr[GPU_ADDR_W-1:CBW_LOW] == cbw_base))
                cbw_valid[(CBW_LG == 2) ? fbwq_req_addr[3:2]
                         : (CBW_LG == 1) ? {1'b0, fbwq_req_addr[2]}
                         : 2'd0] <= 1'b0;
        end  // closes the housekeeping `begin` introduced for m_wr_inflight + gpu_swap_req auto-clear
    end
end

// Colormap BRAM initialises to zero in Cyclone V M10K.
// CPU uploads data via MMIO before use.

endmodule
