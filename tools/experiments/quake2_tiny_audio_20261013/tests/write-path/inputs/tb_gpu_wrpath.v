//------------------------------------------------------------------------------
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileType: SOURCE
// SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
//------------------------------------------------------------------------------
//
// Verilator testbench: REAL gpu_core framebuffer-WRITE path through the REAL
// MiSTer shared-SDRAM stack under multi-master contention.
//
//   gpu_core  (m_rd_* ring/tex/Z reads, m_wr_* FB/clear writes, SRAM scratch)
//        -> axi_sdram_arbiter M0  (GPU read + posted write, the EXACT merge
//                                   core_top uses: gpu_rd_* -> AR, gpu_wr_* -> AW/W)
//        -> axi_sdram_slave
//        -> pulse adapter (byte-identical to core_top.v)
//        -> io_sdram_test  (== targets/mister/io_sdram.v, DQ-split)
//        -> sdram_model_full (10-bit column, holds ring + textures + FB)
//
// M1 (CPU), M2 (bridge), M3 (audio) aggressors hammer the slave so the GPU's
// m_wr_* channel sees REAL W-channel backpressure mid-burst — the condition
// that activates gpu_core's fbwq AW-ahead overlap path (gpu_core.v:2894-2937),
// which the idealized always-fast FB sink in tb_gpu never exercises (and which
// Pocket's light SDRAM load rarely hits → MiSTer-only).
//
// The framebuffer LIVES IN sdram_model_full and is verified by reading the
// model's physical memory back after rendering.  This closes the gap left by
// the arbiter+slave+io_sdram contention test (tb_sdram_cont), which drove the
// arbiter M0 port with an idealized AXI generator rather than the real fbwq.
//
// TEST-ONLY, ADDITIVE.  No production RTL is modified.
//

`default_nettype none

module tb_gpu_wrpath #(
    parameter GPU_HAS_VERT_TRI       = 1,
    parameter GPU_HAS_PARAM_TRI_RECS = 1,
    parameter GPU_Z_READ_WINDOW      = 4,
    parameter GPU_EW_PARALLEL_DIVS   = 1,
    parameter GPU_HAS_COMPACT_SPAN   = 1,
    parameter GPU_HAS_COLUMN_LIST    = 1,
    parameter BANK_ROW_TRACK         = 1
) (
    input  wire        clk,
    input  wire        reset_n,

    // MMIO (driven by C++ harness, mirrors tb_gpu)
    input  wire        reg_wr,
    input  wire [3:0]  reg_addr,
    input  wire [31:0] reg_wdata,
    output wire [31:0] reg_rdata,

    // GPU status
    output wire        busy,
    output wire [31:0] fence_reached,
    output wire [5:0]  dbg_state,

    // ---- Aggressor enable / intensity (from C++) ----
    input  wire        aggr_en,          // master enable for M1/M2/M3 + injection

    // ---- M1/M2/M3 aggressor drive (C++ owns the FSMs; harness wires AXI) ----
    input  wire        m1_arvalid,
    output wire        m1_arready,
    input  wire [31:0] m1_araddr,
    input  wire [7:0]  m1_arlen,
    output wire        m1_rvalid,
    output wire [31:0] m1_rdata,
    output wire        m1_rlast,
    input  wire        m1_rready,
    input  wire        m1_awvalid,
    output wire        m1_awready,
    input  wire [31:0] m1_awaddr,
    input  wire [7:0]  m1_awlen,
    input  wire        m1_wvalid,
    output wire        m1_wready,
    input  wire [31:0] m1_wdata,
    input  wire [3:0]  m1_wstrb,
    input  wire        m1_wlast,
    output wire        m1_bvalid,

    input  wire        m2_arvalid,
    output wire        m2_arready,
    input  wire [31:0] m2_araddr,
    input  wire [7:0]  m2_arlen,
    output wire        m2_rvalid,
    output wire [31:0] m2_rdata,
    output wire        m2_rlast,
    input  wire        m2_rready,
    input  wire        m2_awvalid,
    output wire        m2_awready,
    input  wire [31:0] m2_awaddr,
    input  wire [7:0]  m2_awlen,
    input  wire        m2_wvalid,
    output wire        m2_wready,
    input  wire [31:0] m2_wdata,
    input  wire [3:0]  m2_wstrb,
    input  wire        m2_wlast,
    output wire        m2_bvalid,

    input  wire        m3_arvalid,
    output wire        m3_arready,
    input  wire [31:0] m3_araddr,
    input  wire [7:0]  m3_arlen,
    output wire        m3_rvalid,
    output wire [31:0] m3_rdata,
    output wire        m3_rlast,
    input  wire        m3_rready,

    // ---- Scanout burst injection ----
    input  wire        inj_burst_rd,
    input  wire [24:0] inj_burst_addr,
    input  wire [10:0] inj_burst_len,
    output wire        inj_burst_data_done,

    // ---- SDRAM backdoor (preload ring/textures; verify FB) ----
    input  wire        bd_we,
    input  wire [23:0] bd_addr,    // word address
    input  wire [31:0] bd_wdata,
    input  wire [23:0] bd_rd_addr, // word address
    output wire [31:0] bd_rd_data,

    // ---- Diagnostics ----
    output wire [4:0]  dbg_gpu_wq_count,
    output wire [1:0]  dbg_arb_state,
    output wire [1:0]  dbg_grant,
    // Word-write stream tap (test-only, 2026-09-01): every command the slave
    // hands the controller, so the reference and contention runs can be
    // diffed op-by-op to localize the +1-column FB displacement.
    output wire        tap_wr_fire,
    output wire [23:0] tap_wr_addr,
    output wire [31:0] tap_wr_data,
    output wire [3:0]  tap_wr_strb,
    output wire [3:0]  tap_wr_blen
);

// ============================================================
// gpu_core master channels
// ============================================================
wire        gpu_rd_arvalid, gpu_rd_arready;
wire [31:0] gpu_rd_araddr;
wire [7:0]  gpu_rd_arlen;
wire        gpu_rd_rvalid;
wire [31:0] gpu_rd_rdata;
wire        gpu_rd_rlast;

wire        gpu_wr_awvalid, gpu_wr_awready;
wire [31:0] gpu_wr_awaddr;
wire [7:0]  gpu_wr_awlen;
wire        gpu_wr_wvalid, gpu_wr_wready;
wire [31:0] gpu_wr_wdata;
wire [3:0]  gpu_wr_wstrb;
wire        gpu_wr_wlast;
wire        gpu_wr_bvalid;

// SRAM scratch
wire        gpu_sram_rd, gpu_sram_wr, gpu_sram_rd_half, gpu_sram_rd_hi;
wire [21:0] gpu_sram_addr;
wire [31:0] gpu_sram_wdata;
wire [3:0]  gpu_sram_wstrb;
reg  [31:0] gpu_sram_rdata;
reg         gpu_sram_busy;
reg         gpu_sram_rdata_valid;

wire        gpu_swap_req;
wire [1:0]  gpu_swap_idx;

// ============================================================
// gpu_core
// ============================================================
gpu_core #(
    // Every feature param echoes its INCLUDE_* build macro (variant include
    // list).  OS25 includes NO triangle command (0x49/0x4A-0x4B/0x4D all
    // absent → the shared edge walker constant-folds away) but keeps the 2.5D
    // fastpaths (0x48 compact span groups + 0x4C column lists + translucency).
    // OS30 (Quake2) is the inverse: triangles + fast tex IN, compact/column
    // OUT (Quake2 never emits them).  MiSTer includes all but analogizer +
    // fast tex.  Each INCLUDE_* MUST match the periph's same-named param above.
    .INCLUDE_PARAM_TRI(`ifdef INCLUDE_PARAM_TRI 1 `else 0 `endif),
    .INCLUDE_VERT_TRI(`ifdef INCLUDE_VERT_TRI 1 `else 0 `endif),
    .INCLUDE_PARAM_TRI_RECS(`ifdef INCLUDE_PARAM_TRI_RECS 1 `else 0 `endif),
    .INCLUDE_COMPACT_SPAN(`ifdef INCLUDE_COMPACT_SPAN 1 `else 0 `endif),
    .INCLUDE_COLUMN_LIST(`ifdef INCLUDE_COLUMN_LIST 1 `else 0 `endif),
    .INCLUDE_TEX_MEM(`ifdef INCLUDE_TEX_MEM 1 `else 0 `endif),
    // Dependency pull-up (resolver): combine / xform front-end imply the
    // truecolor RGB565 datapath, so a build can list INCLUDE_COMBINE or
    // INCLUDE_XFORM alone and get truecolor automatically.  See docs/MODULES.md.
    .INCLUDE_DIRECT_COLOR(`ifdef INCLUDE_DIRECT_COLOR 1 `elsif INCLUDE_COMBINE 1 `elsif INCLUDE_XFORM 1 `else 0 `endif),
    // XFORM module (coarse bundle): the whole GPU transform front-end — 0x52
    // xform + 0x50/0x51 matrix-MAC + 0x53/0x54/0x56 vtx-cache + 0x55/0x57
    // lighting + 0x4F clip — is one INCLUDE_XFORM, refined by the EXCLUDE_*
    // opt-outs below.  os30 ships it as of 2026-08-04 (minus lighting/clip);
    // os25 stays off and thus sheds its previously-dead 0x50/0x51/0x4F cones.
    .INCLUDE_XFORM_RGB(`ifdef INCLUDE_XFORM 1 `else 0 `endif),
    // Fine-grained opt-outs inside the XFORM bundle, so a variant can take
    // the vertex cache (0x53 LOAD_VERTS / 0x54 DRAW_INDEXED_TRI, the
    // transform-once/draw-many win) WITHOUT paying for the parts it does
    // not use.  The 0x53 decode gates on VTX_CACHE && XFORM_RGB only -- it
    // does NOT require the matrix MAC -- so a host that already does its
    // own model/view math (os30) can drop the MAC and its xf_M M10K and
    // keep only reciprocal/projection + the cache.
    .INCLUDE_CLIP_TRI(`ifdef INCLUDE_XFORM `ifdef EXCLUDE_CLIP_TRI 0 `else 1 `endif `else 0 `endif),
    .INCLUDE_VTX_CACHE(`ifdef INCLUDE_XFORM 1 `else 0 `endif),
    .INCLUDE_GPU_LIGHT(`ifdef INCLUDE_XFORM `ifdef EXCLUDE_GPU_LIGHT 0 `else 1 `endif `else 0 `endif),
    .INCLUDE_GPU_XFORM_MAC(`ifdef INCLUDE_XFORM `ifdef EXCLUDE_GPU_XFORM_MAC 0 `else 1 `endif `else 0 `endif),
    // Palette / colormap lane (additive): os25/mister list INCLUDE_PALETTE; os30 omits it.
    .INCLUDE_PALETTE(`ifdef INCLUDE_PALETTE 1 `else 0 `endif),
    .INCLUDE_PERSPECTIVE(`ifdef EXCLUDE_GPU_PERSPECTIVE 0 `else 1 `endif),
    .INCLUDE_DEPTH(`ifdef EXCLUDE_GPU_DEPTH 0 `else 1 `endif),
    .INCLUDE_PARAM_SPAN_LIST(`ifdef EXCLUDE_GPU_PARAM_SPAN_LIST 0 `else 1 `endif),
    // Combiner texel*C+D (additive): os30 lists INCLUDE_COMBINE (Mario head).
    .INCLUDE_COMBINE(`ifdef INCLUDE_COMBINE 1 `else 0 `endif),
    // Q29 param-span dynamic-scale precision (additive): os25/mister keep it
    // (Quake perspective); os30 omits it so the Q29 z-step cone + perspective
    // branches fold — removes the GPU's #1 critical path + frees ~150-220 ALM.
    .INCLUDE_PARAM_SPAN_Q29(`ifdef INCLUDE_PARAM_SPAN_Q29 1 `else 0 `endif),
    // Optional CPU command uploads reuse the ring RAM. Status advertises this
    // transport; DMA-free variants require applications that select it.
    .INCLUDE_CPU_RING(`ifdef INCLUDE_CPU_RING 1 `else 0 `endif),
    .INCLUDE_COMMAND_DMA(`ifdef EXCLUDE_GPU_COMMAND_DMA 0 `else 1 `endif),
    // Gather adjacent masked framebuffer words before requesting SDRAM.
    // Keep these independently selectable from the triangle/depth features.
    .GPU_WRITE_COMBINE_FAST_FLUSH(`ifdef INCLUDE_GPU_WRITE_BURSTS 1 `else 0 `endif),
    .GPU_WRITE_GATHER(`ifdef INCLUDE_GPU_WRITE_BURSTS 1 `else 0 `endif),
    .GPU_MASKED_WRITE_BURSTS(`ifdef INCLUDE_GPU_WRITE_BURSTS 1 `else 0 `endif),
    // Numeric tuning (not feature gates).  The z read window is 4 on the
    // triangle-heavy variants and degenerates to 1 on OS25 (single-word z fill,
    // sweeps the window logic).  Keyed off INCLUDE_Z_BURST (NOT INCLUDE_TEX_MEM)
    // so OS30/SM64 keeps the 16-byte z-line burst even with the fast-tex chip
    // disabled — SM64 z-tests every fragment.
    // EW_PARALLEL_DIVS: 1 runs three slope dividers concurrently (~28 beats vs
    // ~87 serial), cutting triangle setup ~30% with bit-identical quotients.
    // BOTH pocket variants currently ship 0 — no variant defines
    // INCLUDE_PARALLEL_DIVS (os30 lists it among its cuts) — on ALM budget.
    // INCLUDE_Z_WINDOW16/8 widen it to a 64/32-byte line (SM64 castle: fewer
    // depth-line refills; pairs with INCLUDE_GPU_SELECTIVE_READ_WAIT).
    .GPU_Z_READ_WINDOW(`ifdef INCLUDE_Z_WINDOW16 16 `elsif INCLUDE_Z_WINDOW8 8 `elsif INCLUDE_Z_BURST 4 `else 1 `endif),
    // Truecolor-blend dst read window: 4-word default; INCLUDE_CB_WINDOW2
    // halves it for ALM-pressed variants; INCLUDE_CB_WINDOW1 compiles the
    // window out (legacy per-pixel blend reads — measurement/fallback).
    // Byte-identical behavior at every size.
    .GPU_CB_READ_WINDOW(`ifdef INCLUDE_CB_WINDOW1 1 `else `ifdef INCLUDE_CB_WINDOW2 2 `else 4 `endif `endif),
    // Additive: only a build that lists INCLUDE_PARALLEL_DIVS gets the concurrent
    // slope dividers.  Neither pocket variant lists it today (both = 0), which is
    // bit-identical to the old EXCLUDE_PARALLEL_DIVS/DIRECT_COLOR derivation and
    // removes that load-bearing ifdef ordering.
    .GPU_EW_PARALLEL_DIVS(`ifdef INCLUDE_PARALLEL_DIVS 1 `else 0 `endif),
    // The render cache gathers and merges framebuffer/depth writes itself;
    // the internal write combiner in front of it only costs time and RAM.
    .GPU_WRITE_COMBINE(`ifdef INCLUDE_GPU_RENDER_CACHE 0 `else 1 `endif)
) gpu (
    .clk(clk), .reset_n(reset_n), .gpu_enable(1'b1),
    .m_rd_arvalid(gpu_rd_arvalid), .m_rd_arready(gpu_rd_arready),
    .m_rd_araddr(gpu_rd_araddr),   .m_rd_arlen(gpu_rd_arlen),
    .m_rd_rvalid(gpu_rd_rvalid),   .m_rd_rdata(gpu_rd_rdata), .m_rd_rlast(gpu_rd_rlast),
    .m_wr_awvalid(gpu_wr_awvalid), .m_wr_awready(gpu_wr_awready),
    .m_wr_awaddr(gpu_wr_awaddr),   .m_wr_awlen(gpu_wr_awlen),
    .m_wr_wvalid(gpu_wr_wvalid),   .m_wr_wready(gpu_wr_wready),
    .m_wr_wdata(gpu_wr_wdata),     .m_wr_wstrb(gpu_wr_wstrb), .m_wr_wlast(gpu_wr_wlast),
    .m_wr_bvalid(gpu_wr_bvalid),
    .sram_rd(gpu_sram_rd), .sram_wr(gpu_sram_wr),
    .sram_rd_half(gpu_sram_rd_half), .sram_rd_hi(gpu_sram_rd_hi),
    .sram_addr(gpu_sram_addr), .sram_wdata(gpu_sram_wdata), .sram_wstrb(gpu_sram_wstrb),
    .sram_rdata(gpu_sram_rdata), .sram_busy(gpu_sram_busy), .sram_rdata_valid(gpu_sram_rdata_valid),
    .gpu_swap_req(gpu_swap_req), .gpu_swap_idx(gpu_swap_idx),
    .memory_barrier_done(gpu_cache_barrier_done), .memory_barrier_req(gpu_cache_barrier_req),
    .cache_clear_bypass(gpu_cache_clear_bypass), .cache_soft_reset(gpu_cache_soft_reset),
    .cache_tex_flush(gpu_cache_tex_flush), .mesh_reset_n(reset_n),
    .mesh_arready(1'b0), .mesh_rvalid(1'b0), .mesh_rdata(32'd0), .mesh_rlast(1'b0),
    .gpu_tex_mem_arready(1'b0), .gpu_tex_mem_rvalid(1'b0),
    .gpu_tex_mem_rdata(32'd0), .gpu_tex_mem_rlast(1'b0), .gpu_tex_mem_up_busy(1'b0),
    .slave_swap_pending(1'b0),
    .reg_wr(reg_wr), .reg_addr(reg_addr), .reg_wdata(reg_wdata), .reg_rdata(gpu_reg_rdata_core),
    .busy(gpu_busy_core), .fence_reached(fence_reached),
    .dbg_state(dbg_state), .dbg_setup_step(), .dbg_aux(), .dbg_frag()
);

// ============================================================
// SRAM model (word-level GPU scratch) — same shape as tb_gpu.
// 1-cycle latency M10K stub.
// ============================================================
reg [31:0] sram_mem [0:65535];
reg        sram_op_read;
reg        sram_half_r, sram_hi_r;
reg [15:0] sram_addr_r;

always @(posedge clk or negedge reset_n) begin
    if (!reset_n) begin
        gpu_sram_busy <= 0; gpu_sram_rdata_valid <= 0; gpu_sram_rdata <= 0;
        sram_op_read <= 0;
    end else begin
        gpu_sram_rdata_valid <= 0;
        gpu_sram_busy <= 0;
        if (gpu_sram_wr) begin
            if (gpu_sram_wstrb[0]) sram_mem[gpu_sram_addr[15:0]][7:0]   <= gpu_sram_wdata[7:0];
            if (gpu_sram_wstrb[1]) sram_mem[gpu_sram_addr[15:0]][15:8]  <= gpu_sram_wdata[15:8];
            if (gpu_sram_wstrb[2]) sram_mem[gpu_sram_addr[15:0]][23:16] <= gpu_sram_wdata[23:16];
            if (gpu_sram_wstrb[3]) sram_mem[gpu_sram_addr[15:0]][31:24] <= gpu_sram_wdata[31:24];
        end
        if (gpu_sram_rd) begin
            gpu_sram_rdata <= sram_mem[gpu_sram_addr[15:0]];
            gpu_sram_rdata_valid <= 1;
        end
    end
end

// ============================================================
// Arbiter <-> Slave wires
// ============================================================
wire        arb_s_arvalid, arb_s_arready;
wire [31:0] arb_s_araddr;
wire [7:0]  arb_s_arlen;
wire        arb_s_rvalid,  arb_s_rready;
wire [31:0] arb_s_rdata;
wire [1:0]  arb_s_rresp;
wire        arb_s_rlast;
wire        arb_s_awvalid, arb_s_awready;
wire [31:0] arb_s_awaddr;
wire [7:0]  arb_s_awlen;
wire        arb_s_wvalid,  arb_s_wready;
wire [31:0] arb_s_wdata;
wire [3:0]  arb_s_wstrb;
wire        arb_s_wlast;
wire        arb_s_bvalid;
wire [1:0]  arb_s_bresp;
wire        arb_s_wcont;

// Slave <-> io_sdram word interface
wire        sdram_slave_rd, sdram_slave_wr;
wire [23:0] sdram_slave_addr;
wire [31:0] sdram_slave_wdata;
wire [3:0]  sdram_slave_wstrb;
wire [3:0]  sdram_slave_burst_len;
wire [3:0]  sdram_slave_burst_wr_len;
wire        sdram_slave_wr_data_next;
wire        sdram_slave_wr_done;
wire [31:0] sdram_slave_next_wdata;
wire [3:0]  sdram_slave_next_wstrb;
wire [31:0] sdram_slave_preload_wdata;
wire [3:0]  sdram_slave_preload_wstrb;

reg         ram1_word_rd, ram1_word_wr;
reg  [23:0] ram1_word_addr;
reg  [31:0] ram1_word_data;
reg  [3:0]  ram1_word_wstrb;
reg  [31:0] ram1_word_data_next;
reg  [3:0]  ram1_word_wstrb_next;
reg  [3:0]  ram1_word_burst_len;
reg  [3:0]  ram1_word_burst_wr_len;
wire [31:0] ram1_word_q;
wire        ram1_word_busy;
wire        ram1_word_q_valid;

wire        phy_cke, phy_clk, phy_cas, phy_ras, phy_we;
wire [1:0]  phy_ba;
wire [12:0] phy_a;
wire [1:0]  phy_dqm;
wire [15:0] ctrl_dq_out;
wire        ctrl_dq_oe;
wire [15:0] model_dq_out;
wire        model_dq_oe;
wire [15:0] dq_to_ctrl = model_dq_oe ? model_dq_out : 16'h0;

// ============================================================
// Slave -> io_sdram pulse adapter (byte-identical to core_top.v)
// ============================================================
reg sdram_accepted_r;
reg sdram_cmd_forwarded;
always @(posedge clk or negedge reset_n) begin
    if (!reset_n) begin
        ram1_word_rd <= 0; ram1_word_wr <= 0;
        ram1_word_addr <= 0; ram1_word_data <= 0; ram1_word_wstrb <= 0;
        ram1_word_data_next <= 0; ram1_word_wstrb_next <= 0;
        ram1_word_burst_len <= 0; ram1_word_burst_wr_len <= 0;
        sdram_accepted_r <= 0; sdram_cmd_forwarded <= 0;
    end else begin
        ram1_word_rd <= 0; ram1_word_wr <= 0;
        ram1_word_burst_len <= 4'd0; ram1_word_burst_wr_len <= 4'd0;
        sdram_accepted_r <= 0;
        if (!sdram_slave_rd && !sdram_slave_wr)
            sdram_cmd_forwarded <= 0;
        if (!ram1_word_busy && !sdram_cmd_forwarded &&
            (sdram_slave_rd || sdram_slave_wr)) begin
            ram1_word_rd <= sdram_slave_rd;
            ram1_word_wr <= sdram_slave_wr;
            ram1_word_addr <= sdram_slave_addr;
            ram1_word_data <= sdram_slave_wdata;
            ram1_word_wstrb <= sdram_slave_wstrb;
            ram1_word_data_next <= sdram_slave_preload_wdata;
            ram1_word_wstrb_next <= sdram_slave_preload_wstrb;
            ram1_word_burst_len <= sdram_slave_burst_len;
            ram1_word_burst_wr_len <= sdram_slave_burst_wr_len;
            sdram_accepted_r <= 1;
            sdram_cmd_forwarded <= 1;
        end
    end
end

// ============================================================
// Arbiter — M0 = gpu_core (rd -> AR, wr -> AW/W), M1/M2/M3 aggressors
// ============================================================
axi_sdram_arbiter #(.GPU_WRITE_QUEUE_DEPTH(16)) sdram_arb (
    .clk(clk), .reset_n(reset_n),

    .m0_arvalid(gpu_mem_rd_arvalid), .m0_arready(gpu_mem_rd_arready),
    .m0_araddr(gpu_mem_rd_araddr),   .m0_arlen(gpu_mem_rd_arlen),
    .m0_rvalid(gpu_mem_rd_rvalid),   .m0_rdata(gpu_mem_rd_rdata),
    .m0_rresp(),                 .m0_rlast(gpu_mem_rd_rlast),
    .m0_awvalid(gpu_mem_wr_awvalid), .m0_awready(gpu_mem_wr_awready),
    .m0_awaddr(gpu_mem_wr_awaddr),   .m0_awlen(gpu_mem_wr_awlen),
    .m0_wvalid(gpu_mem_wr_wvalid),   .m0_wready(gpu_mem_wr_wready),
    .m0_wdata(gpu_mem_wr_wdata),     .m0_wstrb(gpu_mem_wr_wstrb),
    .m0_wlast(gpu_mem_wr_wlast),
    .m0_bvalid(gpu_mem_wr_bvalid),   .m0_bresp(),

    .m1_arvalid(aggr_en & m1_arvalid), .m1_arready(m1_arready),
    .m1_araddr(m1_araddr),   .m1_arlen(m1_arlen),
    .m1_rvalid(m1_rvalid),   .m1_rdata(m1_rdata),
    .m1_rresp(),             .m1_rlast(m1_rlast),
    .m1_rready(m1_rready),
    .m1_awvalid(aggr_en & m1_awvalid), .m1_awready(m1_awready),
    .m1_awaddr(m1_awaddr),   .m1_awlen(m1_awlen),
    .m1_wvalid(aggr_en & m1_wvalid),   .m1_wready(m1_wready),
    .m1_wdata(m1_wdata),     .m1_wstrb(m1_wstrb),
    .m1_wlast(m1_wlast),
    .m1_bvalid(m1_bvalid),   .m1_bresp(),

    .m2_arvalid(aggr_en & m2_arvalid), .m2_arready(m2_arready),
    .m2_araddr(m2_araddr),   .m2_arlen(m2_arlen),
    .m2_rvalid(m2_rvalid),   .m2_rdata(m2_rdata),
    .m2_rresp(),             .m2_rlast(m2_rlast),
    .m2_rready(m2_rready),
    .m2_awvalid(aggr_en & m2_awvalid), .m2_awready(m2_awready),
    .m2_awaddr(m2_awaddr),   .m2_awlen(m2_awlen),
    .m2_wvalid(aggr_en & m2_wvalid),   .m2_wready(m2_wready),
    .m2_wdata(m2_wdata),     .m2_wstrb(m2_wstrb),
    .m2_wlast(m2_wlast),
    .m2_bvalid(m2_bvalid),   .m2_bresp(),

    .m3_arvalid(aggr_en & m3_arvalid), .m3_arready(m3_arready),
    .m3_araddr(m3_araddr),   .m3_arlen(m3_arlen),
    .m3_rvalid(m3_rvalid),   .m3_rdata(m3_rdata),
    .m3_rresp(),             .m3_rlast(m3_rlast),
    .m3_rready(m3_rready),

    .s_arvalid(arb_s_arvalid), .s_arready(arb_s_arready),
    .s_araddr(arb_s_araddr),   .s_arlen(arb_s_arlen),
    .s_rvalid(arb_s_rvalid),   .s_rready(arb_s_rready),
    .s_rdata(arb_s_rdata),
    .s_rresp(arb_s_rresp),     .s_rlast(arb_s_rlast),
    .s_awvalid(arb_s_awvalid), .s_awready(arb_s_awready),
    .s_awaddr(arb_s_awaddr),   .s_awlen(arb_s_awlen),
    .s_wvalid(arb_s_wvalid),   .s_wready(arb_s_wready),
    .s_wdata(arb_s_wdata),     .s_wstrb(arb_s_wstrb),
    .s_wlast(arb_s_wlast),
    .s_bvalid(arb_s_bvalid),   .s_bresp(arb_s_bresp),
    .s_wcont(arb_s_wcont),

    .dbg_arb_state(dbg_arb_state),
    .dbg_cpu_pending(),
    .dbg_grant(dbg_grant)
);

// ============================================================
// Slave
// ============================================================
axi_sdram_slave #(
    .SERIALIZE_WRITE_BURSTS(1'b0),
    .MAX_NATIVE_WRITE_BURST_LEN(8'd15)
) slave (
    .clk(clk), .reset_n(reset_n),
    .s_axi_arvalid(arb_s_arvalid), .s_axi_arready(arb_s_arready),
    .s_axi_araddr(arb_s_araddr),   .s_axi_arlen(arb_s_arlen),
    .s_axi_rvalid(arb_s_rvalid),   .s_axi_rready(arb_s_rready),
    .s_axi_rdata(arb_s_rdata),     .s_axi_rresp(arb_s_rresp), .s_axi_rlast(arb_s_rlast),
    .s_axi_awvalid(arb_s_awvalid), .s_axi_awready(arb_s_awready),
    .s_axi_awaddr(arb_s_awaddr),   .s_axi_awlen(arb_s_awlen),
    .s_axi_wvalid(arb_s_wvalid),   .s_axi_wready(arb_s_wready),
    .s_axi_wdata(arb_s_wdata),     .s_axi_wstrb(arb_s_wstrb), .s_axi_wlast(arb_s_wlast),
    .s_axi_bvalid(arb_s_bvalid),   .s_axi_bready(1'b1),       .s_axi_bresp(arb_s_bresp),
    .s_axi_wcont(arb_s_wcont),
    .sdram_rd(sdram_slave_rd), .sdram_wr(sdram_slave_wr),
    .sdram_addr(sdram_slave_addr), .sdram_wdata(sdram_slave_wdata), .sdram_wstrb(sdram_slave_wstrb),
    .sdram_burst_len(sdram_slave_burst_len), .sdram_burst_wr_len(sdram_slave_burst_wr_len),
    .sdram_rdata(ram1_word_q), .sdram_busy(ram1_word_busy),
    .sdram_accepted(sdram_accepted_r), .sdram_rdata_valid(ram1_word_q_valid),
    .sdram_wr_data_next(sdram_slave_wr_data_next),
    .sdram_wr_done(sdram_slave_wr_done),
    .sdram_next_wdata(sdram_slave_next_wdata),
    .sdram_next_wstrb(sdram_slave_next_wstrb),
    .sdram_preload_wdata(sdram_slave_preload_wdata),
    .sdram_preload_wstrb(sdram_slave_preload_wstrb)
);

// ============================================================
// io_sdram (MiSTer controller; split-DQ test variant)
// ============================================================
io_sdram #(
    .BANK_ROW_TRACK(BANK_ROW_TRACK)
) sdram_ctrl (
    .controller_clk(clk), .chip_clk(clk), .clk_90(clk), .reset_n(reset_n),
    .phy_cke(phy_cke), .phy_clk(phy_clk),
    .phy_cas(phy_cas), .phy_ras(phy_ras), .phy_we(phy_we),
    .phy_ba(phy_ba), .phy_a(phy_a),
    .phy_dq_in(dq_to_ctrl),
    .phy_dq_out_port(ctrl_dq_out),
    .phy_dq_oe_port(ctrl_dq_oe),
    .phy_dqm(phy_dqm),
    .burst_rd(inj_burst_rd), .burst_addr(inj_burst_addr), .burst_len(inj_burst_len), .burst_32bit(1'b1),
    .burst_data(), .burst_data_valid(), .burst_data_done(inj_burst_data_done),
    .burstwr(1'b0), .burstwr_addr(25'b0), .burstwr_ready(),
    .burstwr_strobe(1'b0), .burstwr_data(16'b0), .burstwr_done(1'b0),
    .word_rd(ram1_word_rd), .word_wr(ram1_word_wr),
    .word_addr(ram1_word_addr), .word_data(ram1_word_data), .word_wstrb(ram1_word_wstrb),
    .word_data_next(ram1_word_data_next), .word_wstrb_next(ram1_word_wstrb_next),
    .word_burst_len(ram1_word_burst_len), .word_burst_wr_len(ram1_word_burst_wr_len),
    .word_q(ram1_word_q), .word_busy(ram1_word_busy), .word_q_valid(ram1_word_q_valid),
    .word_wr_data_next(sdram_slave_wr_data_next),
    .word_wr_done(sdram_slave_wr_done),
    .burst_wr_direct_data(sdram_slave_next_wdata),
    .burst_wr_direct_strb(sdram_slave_next_wstrb),
    .dbg_io()
);

// ============================================================
// SDRAM model (full 10-bit column) — holds ring + textures + FB.
// Backdoor preload from C++ via bd_*.
// ============================================================
sdram_model_full sdram_chip (
    .clk(phy_clk), .cke(phy_cke), .cs_n(1'b0),
    .ras_n(phy_ras), .cas_n(phy_cas), .we_n(phy_we),
    .ba(phy_ba), .a(phy_a),
    .dq_in(ctrl_dq_out),
    .dq_out(model_dq_out), .dq_oe(model_dq_oe),
    .dqm(phy_dqm),
    .bd_we(bd_we), .bd_word_addr(bd_addr), .bd_wdata(bd_wdata),
    .bd_rd_word_addr(bd_rd_addr), .bd_rd_data(bd_rd_data)
);

assign tap_wr_fire = ram1_word_wr;
assign tap_wr_addr = ram1_word_addr;
assign tap_wr_data = ram1_word_data;
assign tap_wr_strb = ram1_word_wstrb;
assign tap_wr_blen = ram1_word_burst_wr_len;

assign dbg_gpu_wq_count = sdram_arb.gpu_wq_count;

wire        gpu_mem_rd_arvalid, gpu_mem_rd_arready, gpu_mem_rd_rvalid, gpu_mem_rd_rlast;
wire [31:0] gpu_mem_rd_araddr, gpu_mem_rd_rdata;
wire [7:0]  gpu_mem_rd_arlen;
wire        gpu_mem_wr_awvalid, gpu_mem_wr_awready, gpu_mem_wr_wvalid, gpu_mem_wr_wready;
wire        gpu_mem_wr_wlast, gpu_mem_wr_bvalid;
wire [31:0] gpu_mem_wr_awaddr, gpu_mem_wr_wdata;
wire [7:0]  gpu_mem_wr_awlen;
wire [3:0]  gpu_mem_wr_wstrb;
wire        gpu_busy_core;
wire [31:0] gpu_reg_rdata_core;
wire        gpu_cache_barrier_done, gpu_cache_barrier_req, gpu_cache_clear_bypass;
wire        gpu_cache_soft_reset, gpu_cache_tex_flush;
wire        gpu_cache_busy, gpu_cache_has_lines, gpu_cache_flush_done;
wire [25:0] gpu_cache_m_araddr, gpu_cache_m_awaddr;
reg         gpu_cache_idle_flush;
reg         gpu_cache_tex_flush_pending;
always @(posedge clk) begin
    if (!reset_n || gpu_cache_soft_reset) begin
        gpu_cache_idle_flush <= 1'b0;
        gpu_cache_tex_flush_pending <= 1'b0;
    end else begin
        if (gpu_cache_flush_done) gpu_cache_idle_flush <= 1'b0;
        else if (!gpu_busy_core && gpu_cache_has_lines) gpu_cache_idle_flush <= 1'b1;
        if (gpu_cache_flush_done) gpu_cache_tex_flush_pending <= 1'b0;
        else if (gpu_cache_tex_flush && gpu_cache_has_lines) gpu_cache_tex_flush_pending <= 1'b1;
    end
end
assign gpu_cache_barrier_done = gpu_cache_flush_done || (!gpu_cache_has_lines && !gpu_cache_busy);
assign busy = gpu_busy_core || gpu_cache_busy || gpu_cache_has_lines || gpu_cache_idle_flush;
assign reg_rdata = gpu_reg_rdata_core
                     | ((reg_addr == 4'd5 && busy) ? 32'd1 : 32'd0);
assign gpu_mem_rd_araddr = {6'd0, gpu_cache_m_araddr};
assign gpu_mem_wr_awaddr = {6'd0, gpu_cache_m_awaddr};
gpu_color_depth_cache #(
    .ADDR_W(26), .CACHE_ALL(1), .WAYS(2), .SET_BITS(8), .WORD_BITS(4),
    .REGISTER_READ_LOOKUP(1), .REGISTER_FILL_HAZARD(1), .SPECULATE_WRITE_HOT0(1)
) gpu_render_cache (
    .clk(clk), .reset_n(reset_n && !gpu_cache_soft_reset), .bus_reset_n(reset_n),
    .range0_lo(26'd0), .range0_hi(26'd0), .range1_lo(26'd0), .range1_hi(26'd0),
    .write_no_allocate(gpu_cache_clear_bypass),
    .flush_req((gpu_cache_barrier_req && gpu_cache_has_lines) || gpu_cache_idle_flush
               || gpu_cache_tex_flush_pending),
    .flush_done(gpu_cache_flush_done), .busy(gpu_cache_busy), .has_lines(gpu_cache_has_lines),
    .s_arvalid(gpu_rd_arvalid), .s_arready(gpu_rd_arready), .s_araddr(gpu_rd_araddr[25:0]),
    .s_arlen(gpu_rd_arlen), .s_rvalid(gpu_rd_rvalid), .s_rdata(gpu_rd_rdata), .s_rlast(gpu_rd_rlast),
    .s_awvalid(gpu_wr_awvalid), .s_awready(gpu_wr_awready), .s_awaddr(gpu_wr_awaddr[25:0]),
    .s_awlen(gpu_wr_awlen), .s_wvalid(gpu_wr_wvalid), .s_wready(gpu_wr_wready),
    .s_wdata(gpu_wr_wdata), .s_wstrb(gpu_wr_wstrb), .s_wlast(gpu_wr_wlast), .s_bvalid(gpu_wr_bvalid),
    .m_arvalid(gpu_mem_rd_arvalid), .m_arready(gpu_mem_rd_arready), .m_araddr(gpu_cache_m_araddr),
    .m_arlen(gpu_mem_rd_arlen), .m_rvalid(gpu_mem_rd_rvalid), .m_rdata(gpu_mem_rd_rdata),
    .m_rlast(gpu_mem_rd_rlast),
    .m_awvalid(gpu_mem_wr_awvalid), .m_awready(gpu_mem_wr_awready), .m_awaddr(gpu_cache_m_awaddr),
    .m_awlen(gpu_mem_wr_awlen), .m_wvalid(gpu_mem_wr_wvalid), .m_wready(gpu_mem_wr_wready),
    .m_wdata(gpu_mem_wr_wdata), .m_wstrb(gpu_mem_wr_wstrb), .m_wlast(gpu_mem_wr_wlast),
    .m_bvalid(gpu_mem_wr_bvalid),
    .hits(), .misses(), .writebacks(), .protocol_error()
);

endmodule

`default_nettype wire
