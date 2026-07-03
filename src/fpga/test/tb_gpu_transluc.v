//------------------------------------------------------------------------------
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileType: SOURCE
// SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
//------------------------------------------------------------------------------
//
// Verilator testbench: MiSTer TRANSLUCENT-COLUMN repro rig.
//
// Full-stack GPU pipeline with INCLUDE_TRANSLUC on the REAL MiSTer SDRAM
// stack, parameterized exactly like targets/mister/emu.sv:
//
//   gpu_core (INCLUDE_TRANSLUC, MiSTer param set: PARAM_TRI/VERT_TRI/
//             PARAM_TRI_RECS/COMPACT_SPAN/COLUMN_LIST = 1, TEX_MEM = 0)
//        -> axi_sdram_arbiter M0
//        -> axi_sdram_slave
//        -> pulse adapter (byte-identical to core_top/emu)
//        -> io_sdram_mister_test (== targets/mister/io_sdram.v, DQ-split)
//        -> sdram_model_full
//
// The workload (driven from C++) is native 0x4C translucent column lists —
// the exact Doom II fireball path that drops spans on MiSTer hardware —
// under scanout burst injection + M1/M2/M3 aggressor traffic, verified
// byte-exact against a CPU reference model.
//
// Debug taps (hierarchical, test-only):
//   * slave drop-arm: a read beat arrives while s_axi_rvalid && !rready and
//     both skid slots are full -> the beat is LOST (axi_sdram_slave.v:405+).
//   * doorbell-DMA starvation override / R-channel ownership overlap events
//     (DMA in S_R while the blend unit waits for its dest-read beat, or
//     while a tex fill is in flight) — the suspected ring-desync mechanism.
//   * decoder header visibility (state/cmd_type/payload words) so the C++
//     side can histogram opcodes and catch a shifted/desynced ring stream.
//
// TEST-ONLY, ADDITIVE.  No production RTL is modified.
//

`default_nettype none

module tb_gpu_transluc #(
    parameter GPU_Z_READ_WINDOW      = 4,
    parameter GPU_EW_PARALLEL_DIVS   = 1,
    parameter BANK_ROW_TRACK         = 1
) (
    input  wire        clk,
    input  wire        reset_n,

    // MMIO (driven by C++ harness)
    input  wire        reg_wr,
    input  wire [3:0]  reg_addr,
    input  wire [31:0] reg_wdata,
    output wire [31:0] reg_rdata,

    // GPU status
    output wire        busy,
    output wire [31:0] fence_reached,
    output wire [5:0]  dbg_state,

    // ---- Aggressor enable ----
    input  wire        aggr_en,

    // ---- SS1 DQM convention ----
    // SuperStation One's integrated 128MB module wires the chip's DQM pins
    // from A12/A11 (MiSTer pin-saving convention) instead of the dedicated
    // DQM lines.  With this set, the SDRAM model takes its write mask from
    // {phy_a[12], phy_a[11]} — end-to-end validation of io_sdram's
    // ST_WRITE_2/3 mask mirror.
    input  wire        ss1_dqm_mode,

    // ---- SS1 READ-side DQM masking (chip semantics, applied tb-level) ----
    // On SDR SDRAM, DQM also gates READ data: DQM sampled at pin-cycle T
    // masks (Hi-Z's) the chip's DQ output at T+2.  On SS1 the chip's DQM
    // pins ARE A12/A11 — continuously — so whatever the address bus holds
    // two pin-cycles before a read beat masks that beat's byte lanes.
    // sdram_model_full does not model read masking; it is applied HERE on
    // the DQ bus between model and controller (functionally identical to
    // the chip tri-stating its drivers; masked lanes read as 0xBA poison).
    //
    // ALIGNMENT (why the model-time delay is 1, not 2): the model runs
    // CAS_LATENCY=2 instead of silicon's CL=3, explicitly to compensate
    // io_sdram's registered command output (+1 cmd->chip) and its
    // phy_dq_latched input register (+1 chip->capture) — see
    // sdram_model_full.v:80-84.  Relative to the SHARED pin-time command/
    // address stream the model emits every read beat exactly ONE cycle
    // earlier than the silicon DQ bus would.  Silicon rule: beat@X masked
    // by {A12,A11}@(X-2); the same logical beat leaves the model at X-1,
    // so the model-time mask source is {A12,A11} delayed by ONE cycle.
    // ss1_read_delay selects the delay (silicon-equivalent = 1; 0/2/3
    // provided to bracket the alignment empirically).
    input  wire        ss1_read_dqm,
    input  wire [1:0]  ss1_read_delay,

    // ---- M1/M2/M3 aggressor drive (C++ owns the FSMs) ----
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

    // ---- SDRAM backdoor ----
    input  wire        bd_we,
    input  wire [23:0] bd_addr,    // word address
    input  wire [31:0] bd_wdata,
    input  wire [23:0] bd_rd_addr, // word address
    output wire [31:0] bd_rd_data,

    // ---- Diagnostics / event taps ----
    output wire [4:0]  dbg_gpu_wq_count,
    output wire [1:0]  dbg_arb_state,
    output wire [1:0]  dbg_grant,

    output wire [5:0]  dbg_gpu_state,     // gpu.state (decoder FSM)
    output wire [7:0]  dbg_cmd_type,      // gpu.cmd_type (valid in S_DECODE)
    output wire [12:0] dbg_cmd_words,     // gpu.cmd_payload_words
    output wire [4:0]  dbg_fbss,          // gpu.fbss
    output wire [1:0]  dbg_dma_state,     // gpu.dma_state
    output wire [9:0]  dbg_dma_starve,    // gpu.dma_starve_count
    output wire [15:0] dbg_ring_wrptr_b,  // ring wrptr (bytes)
    output wire [15:0] dbg_ring_rdptr_b,  // ring rdptr (bytes)

    // Event wires (single-cycle-accurate; count in C++ pre-edge)
    output wire        evt_drop_arm,          // slave 3-deep R chain overflow arm
    output wire        evt_dma_forced,        // starvation override threshold hit
    output wire        evt_dma_hijack_blend,  // DMA owns R while blend waits its beat
    output wire        evt_dma_hijack_tex,    // DMA owns R while tex fill in flight
    output wire        evt_ring_capture,      // one ring word captured from R this cycle

    // Wedge-diagnosis taps
    output wire [3:0]  dbg_slave_state,       // slave.state
    output wire        dbg_word_busy,         // io_sdram word port busy
    output wire        dbg_arb_active_rd,     // arbiter read grant live
    output wire        dbg_m0_arvalid,        // GPU AR pending at arbiter

    // M0 beat-ownership adjudication taps: the C++ side queues each accepted
    // M0 AR with its owner (DMA/BLEND/TEX from the AR-mux selects) and
    // classifies every returned R beat — a TEX-owned beat presented while
    // dma_owns_r masks tex_axi_rvalid would be a genuinely SWALLOWED beat.
    output wire [7:0]  dbg_m0_arlen,
    output wire        dbg_m0_arready,
    output wire        dbg_m0_rvalid,
    output wire        dbg_m0_rlast,
    output wire        dbg_dma_owns_ar,
    output wire        dbg_dma_owns_r,
    output wire        dbg_blend_owns,
    output wire        dbg_tex_inflight,
    output wire        dbg_tex_arvalid,
    output wire [2:0]  dbg_tex_state,         // tex cache fill FSM

    // SS1 read-DQM exposure taps (counted regardless of ss1_read_dqm):
    // a read beat is EXPOSED at delay k when the model drives DQ while the
    // k-cycle-delayed A[12:11] is nonzero — i.e. silicon-with-A-wired-DQM
    // would mask those byte lanes.  evt_read_masked = poison actually
    // applied this cycle (ss1_read_dqm set).
    output wire        evt_rd_expose_d1,
    output wire        evt_rd_expose_d2,
    output wire        evt_read_masked,
    // phy-level view for the exposure trace
    output wire [2:0]  dbg_phy_cmd,           // {ras_n, cas_n, we_n}
    output wire [1:0]  dbg_phy_ba,
    output wire [12:0] dbg_phy_a,
    output wire [1:0]  dbg_phy_dqm,
    output wire        dbg_dq_oe,
    output wire [15:0] dbg_dq_bus             // post-mask DQ as the controller sees it
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

// SRAM scratch (holds the 32KB transluc LUT)
wire        gpu_sram_rd, gpu_sram_wr, gpu_sram_rd_half, gpu_sram_rd_hi;
wire [21:0] gpu_sram_addr;
wire [31:0] gpu_sram_wdata;
wire [3:0]  gpu_sram_wstrb;
wire [31:0] gpu_sram_rdata;
wire        gpu_sram_busy;
wire        gpu_sram_rdata_valid;

wire        gpu_swap_req;
wire [1:0]  gpu_swap_idx;

// ============================================================
// gpu_core — EXACT MiSTer parameterization (emu.sv:1219-1229).
// INCLUDE_TRANSLUC comes from the build (+define+INCLUDE_TRANSLUC).
// Only the six params emu.sv overrides are overridden here; all other
// INCLUDE_* stay at their gpu_core defaults, same as the mister fit.
// ============================================================
gpu_core #(
    .INCLUDE_PARAM_TRI(1),
    .INCLUDE_VERT_TRI(1),
    .INCLUDE_PARAM_TRI_RECS(1),
    .INCLUDE_COMPACT_SPAN(1),
    .INCLUDE_COLUMN_LIST(1),
    .INCLUDE_TEX_MEM(0),
    .GPU_Z_READ_WINDOW(GPU_Z_READ_WINDOW),
    .GPU_EW_PARALLEL_DIVS(GPU_EW_PARALLEL_DIVS)
) gpu (
    .clk(clk), .reset_n(reset_n), .gpu_enable(1'b1),
    .m_rd_arvalid(gpu_rd_arvalid), .m_rd_arready(gpu_rd_arready),
    .m_rd_araddr(gpu_rd_araddr),   .m_rd_arlen(gpu_rd_arlen),
    .m_rd_rvalid(gpu_rd_rvalid),   .m_rd_rdata(gpu_rd_rdata), .m_rd_rlast(gpu_rd_rlast),
    // Fast texture memory absent on MiSTer: tie fill-master inputs idle
    // exactly as emu.sv does.
    .gpu_tex_mem_arready(1'b0),
    .gpu_tex_mem_rvalid(1'b0),
    .gpu_tex_mem_rdata(32'b0),
    .gpu_tex_mem_rlast(1'b0),
    .gpu_tex_mem_up_busy(1'b0),
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
    .slave_swap_pending(1'b0),
    .reg_wr(reg_wr), .reg_addr(reg_addr), .reg_wdata(reg_wdata), .reg_rdata(reg_rdata),
    .busy(busy), .fence_reached(fence_reached),
    .dbg_state(dbg_state), .dbg_setup_step(), .dbg_aux(), .dbg_frag()
);

// ============================================================
// SRAM model — MUST assert busy + delayed rdata_valid: the transluc LUT
// upload FSM (gpu_core LUTSRAM_WRITE_WAIT) is saw-busy gated and WEDGES
// against a never-busy model.  This is the acceptance tb_gpu.v model
// verbatim (2-cycle latency M10K-like).
// ============================================================
reg [31:0] sram_mem [0:65535];
reg        sram_busy_r;
reg        sram_rvalid_r;
reg [1:0]  sram_delay;
reg        sram_op_read;
reg        sram_half_r;
reg        sram_hi_r;
reg [15:0] sram_addr_r;
reg [31:0] sram_wdata_r;
reg [3:0]  sram_wstrb_r;

assign gpu_sram_busy = sram_busy_r;
assign gpu_sram_rdata_valid = sram_rvalid_r;
assign gpu_sram_rdata = sram_half_r
                       ? (sram_hi_r
                          ? {sram_mem[sram_addr_r][31:16], 16'd0}
                          : {16'd0, sram_mem[sram_addr_r][15:0]})
                       : sram_mem[sram_addr_r];

always @(posedge clk) begin
    if (!reset_n) begin
        sram_busy_r   <= 1'b0;
        sram_rvalid_r <= 1'b0;
        sram_delay    <= 2'd0;
        sram_op_read  <= 1'b0;
        sram_half_r   <= 1'b0;
        sram_hi_r     <= 1'b0;
        sram_addr_r   <= 16'd0;
        sram_wdata_r  <= 32'd0;
        sram_wstrb_r  <= 4'd0;
    end else begin
        sram_rvalid_r <= 1'b0;
        if (!sram_busy_r && (gpu_sram_rd || gpu_sram_wr)) begin
            sram_busy_r  <= 1'b1;
            sram_delay   <= 2'd2;
            sram_op_read <= gpu_sram_rd;
            sram_half_r  <= gpu_sram_rd && gpu_sram_rd_half;
            sram_hi_r    <= gpu_sram_rd_hi;
            sram_addr_r  <= gpu_sram_addr[15:0];
            sram_wdata_r <= gpu_sram_wdata;
            sram_wstrb_r <= gpu_sram_wstrb;
        end else if (sram_busy_r) begin
            if (sram_delay != 2'd0) begin
                sram_delay <= sram_delay - 2'd1;
            end else begin
                if (sram_op_read) begin
                    sram_rvalid_r <= 1'b1;
                end else begin
                    if (sram_wstrb_r[0]) sram_mem[sram_addr_r][7:0]   <= sram_wdata_r[7:0];
                    if (sram_wstrb_r[1]) sram_mem[sram_addr_r][15:8]  <= sram_wdata_r[15:8];
                    if (sram_wstrb_r[2]) sram_mem[sram_addr_r][23:16] <= sram_wdata_r[23:16];
                    if (sram_wstrb_r[3]) sram_mem[sram_addr_r][31:24] <= sram_wdata_r[31:24];
                end
                sram_busy_r <= 1'b0;
            end
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

// SS1 read-side DQM: delay line on the address-bus DQM view (pin time).
// a1211_d[k] = {phy_a[12], phy_a[11]} as it was k+1 cycles ago.
reg [1:0] a1211_d [0:3];
always @(posedge clk or negedge reset_n) begin
    if (!reset_n) begin
        a1211_d[0] <= 2'b00; a1211_d[1] <= 2'b00;
        a1211_d[2] <= 2'b00; a1211_d[3] <= 2'b00;
    end else begin
        a1211_d[0] <= {phy_a[12], phy_a[11]};
        a1211_d[1] <= a1211_d[0];
        a1211_d[2] <= a1211_d[1];
        a1211_d[3] <= a1211_d[2];
    end
end
wire [1:0] dqm_rd_eff = (ss1_read_delay == 2'd0) ? {phy_a[12], phy_a[11]}
                                                 : a1211_d[ss1_read_delay - 2'd1];
wire [15:0] dq_masked = { dqm_rd_eff[1] ? 8'hBA : model_dq_out[15:8],
                          dqm_rd_eff[0] ? 8'hBA : model_dq_out[7:0] };
wire [15:0] dq_to_ctrl = !model_dq_oe   ? 16'h0
                       : ss1_read_dqm   ? dq_masked
                       :                  model_dq_out;

// ============================================================
// Slave -> io_sdram pulse adapter (byte-identical to core_top/emu)
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
// Arbiter — M0 = gpu_core, M1/M2/M3 aggressors
// ============================================================
axi_sdram_arbiter sdram_arb (
    .clk(clk), .reset_n(reset_n),

    .m0_arvalid(gpu_rd_arvalid), .m0_arready(gpu_rd_arready),
    .m0_araddr(gpu_rd_araddr),   .m0_arlen(gpu_rd_arlen),
    .m0_rvalid(gpu_rd_rvalid),   .m0_rdata(gpu_rd_rdata),
    .m0_rresp(),                 .m0_rlast(gpu_rd_rlast),
    .m0_awvalid(gpu_wr_awvalid), .m0_awready(gpu_wr_awready),
    .m0_awaddr(gpu_wr_awaddr),   .m0_awlen(gpu_wr_awlen),
    .m0_wvalid(gpu_wr_wvalid),   .m0_wready(gpu_wr_wready),
    .m0_wdata(gpu_wr_wdata),     .m0_wstrb(gpu_wr_wstrb),
    .m0_wlast(gpu_wr_wlast),
    .m0_bvalid(gpu_wr_bvalid),   .m0_bresp(),

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
// io_sdram — the SHIPPING MiSTer controller (split-DQ test variant of
// targets/mister/io_sdram.v, incl. the SS1 A[12:11] DQM mirror)
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
// SDRAM model (full 10-bit column)
// ============================================================
sdram_model_full sdram_chip (
    .clk(phy_clk), .cke(phy_cke), .cs_n(1'b0),
    .ras_n(phy_ras), .cas_n(phy_cas), .we_n(phy_we),
    .ba(phy_ba), .a(phy_a),
    .dq_in(ctrl_dq_out),
    .dq_out(model_dq_out), .dq_oe(model_dq_oe),
    // {DQMH,DQML}: dedicated pins (DE10-class) or A[12:11] (SS1 modules)
    .dqm(ss1_dqm_mode ? {phy_a[12], phy_a[11]} : phy_dqm),
    .bd_we(bd_we), .bd_word_addr(bd_addr), .bd_wdata(bd_wdata),
    .bd_rd_word_addr(bd_rd_addr), .bd_rd_data(bd_rd_data),
    .wr_evt(), .wr_evt_hw_addr(), .wr_evt_dqm(), .wr_evt_data()
);

assign dbg_gpu_wq_count = sdram_arb.gpu_wq_count;

// ============================================================
// Hierarchical debug taps (test-only)
// ============================================================
assign dbg_gpu_state    = gpu.state;
assign dbg_cmd_type     = gpu.cmd_type;
assign dbg_cmd_words    = gpu.cmd_payload_words;
assign dbg_fbss         = gpu.fbss;
assign dbg_dma_state    = gpu.dma_state;
assign dbg_dma_starve   = gpu.dma_starve_count;
assign dbg_ring_wrptr_b = gpu.ring_wrptr_bytes;
assign dbg_ring_rdptr_b = gpu.ring_rdptr_bytes;

// Slave R-chain drop-arm: a fresh read beat arrives from io_sdram while the
// AXI R output is stalled AND both skid slots already hold beats -> the beat
// has nowhere to go and is silently dropped (axi_sdram_slave.v "all three
// full" else-branch).  Must NEVER fire while the GPU is the read owner
// (its rready is hardwired 1 in the arbiter).
assign evt_drop_arm = ram1_word_q_valid
                   && arb_s_rvalid && !arb_s_rready
                   && slave.rskid_valid && slave.rskid2_valid;

// Doorbell-DMA starvation override: the counter hits the 512-cycle threshold
// (it resets on any idle/assert cycle, so ==THRESHOLD marks each fire).
assign evt_dma_forced = (gpu.dma_state == 2'd1) && !gpu.dma_arvalid
                     && (gpu.dma_starve_count >= 10'd512);

// R-channel ownership overlap: DMA is in its data phase (masks/absorbs every
// M0 R beat) while the blend unit is parked in FBSS_BLEND_R_WAIT expecting
// its single-beat dest read, or while a tex-cache fill is in flight.  Either
// means R beats can be routed to the wrong consumer -> ring desync.
assign evt_dma_hijack_blend = gpu.dma_owns_r && (gpu.fbss == 5'd8);
assign evt_dma_hijack_tex   = gpu.dma_owns_r && gpu.tex_m0_in_flight;

// One command-stream word captured into ring BRAM from the R channel.
assign evt_ring_capture = gpu.dma_ring_wr_raw;

// Wedge diagnosis
assign dbg_slave_state  = slave.state;
assign dbg_word_busy    = ram1_word_busy;
assign dbg_arb_active_rd = sdram_arb.active_rd;
assign dbg_m0_arvalid   = gpu_rd_arvalid;

// M0 beat-ownership adjudication
assign dbg_m0_arlen     = gpu_rd_arlen;
assign dbg_m0_arready   = gpu_rd_arready;
assign dbg_m0_rvalid    = gpu_rd_rvalid;
assign dbg_m0_rlast     = gpu_rd_rlast;
assign dbg_dma_owns_ar  = gpu.dma_owns_ar;
assign dbg_dma_owns_r   = gpu.dma_owns_r;
assign dbg_blend_owns   = gpu.blend_owns_m0;
assign dbg_tex_inflight = gpu.tex_m0_in_flight;
assign dbg_tex_arvalid  = gpu.tex_axi_arvalid;
assign dbg_tex_state    = gpu.tex_cache.state;

// SS1 read-DQM exposure
assign evt_rd_expose_d1 = model_dq_oe && (a1211_d[0] != 2'b00);
assign evt_rd_expose_d2 = model_dq_oe && (a1211_d[1] != 2'b00);
assign evt_read_masked  = ss1_read_dqm && model_dq_oe && (dqm_rd_eff != 2'b00);
assign dbg_phy_cmd      = {phy_ras, phy_cas, phy_we};  // model's cmd decode order
assign dbg_phy_ba       = phy_ba;
assign dbg_phy_a        = phy_a;
assign dbg_phy_dqm      = phy_dqm;
assign dbg_dq_oe        = model_dq_oe;
assign dbg_dq_bus       = dq_to_ctrl;

endmodule

`default_nettype wire
