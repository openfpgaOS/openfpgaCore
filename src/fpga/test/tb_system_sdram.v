//------------------------------------------------------------------------------
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileType: SOURCE
// SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
//------------------------------------------------------------------------------

//
// tb_system_sdram.v — tb_system with the REAL MiSTer SDRAM back-end.
//
// Same top-level as tb_system.v (module name kept: tb_system) except the
// idealized sdram_fast_model is replaced by the real chain:
//     axi_sdram_slave → pulse adapter → io_sdram (mister copy, DQ-split)
//         → sdram_model_full (cycle-accurate chip, refresh-enforced)
// plus a scanout burst_rd injection port at io_sdram (the MiSTer video
// scanout enters BELOW the AXI fabric on hardware) and observation taps
// for a C++ R-beat data checker.  Purpose: reproduce the MiSTer
// kernel-layout boot lottery (suspected i-fetch corruption/hang under
// concurrent scanout) in simulation.
//
// Brings up:
//   - OpenFpgaVexii CPU            (clk_cpu)
//   - cpu_system 3-master router   (clk_cpu)
//   - axi_sdram_arbiter + slave    (clk_cpu)
//   - sdram_fast_model             (clk_cpu)
//   - cram0_cdc (s_axi → word-if)  (clk_cpu ↔ clk_74a)
//   - cram0_sim_model              (clk_74a)
//   - axi_periph_slave             (clk_cpu)
//       └── altsyncram (BRAM, 32KB) via altsyncram stub (bram_model.v)
//
// The mux from core_top.v between bridge-native and CPU-native word
// interfaces is replicated below; the bridge side exposes a backdoor so
// the C++ harness can preload os.bin into CRAM0 before reset is
// released.  Once reset_n deasserts, the mux tracks cram0_mode exactly
// as it does on hardware.
//
// Outputs surfaced to the C++ harness:
//   uart_tx_dv    — 1-cycle pulse when axi_periph_slave accepts a byte
//   uart_tx_byte  — the accepted byte
//   dbg_pc        — CPU fetch program counter hoist (best-effort)
//

`default_nettype none

module tb_system (
    input  wire        clk_cpu,
    input  wire        clk_74a,
    input  wire        reset_n,
    // Vsync — driven by C++ harness as a periodic 1-cycle pulse so the
    // periph slave's fb_swap_pending actually clears the way it does
    // on real hardware.
    input  wire        vsync,

    // UART TX observation — driven by axi_periph_slave
    output wire        uart_tx_dv,
    output wire [7:0]  uart_tx_byte,

    // CRAM0 backdoor (bridge-native side of the mux).  The harness uses
    // this to preload os.bin before releasing reset_n.
    input  wire        bd_cram0_wr,
    input  wire [21:0] bd_cram0_addr,
    input  wire [31:0] bd_cram0_wdata,

    // SDRAM chip-model backdoor: clocked preload writes + combinational
    // live peek (the C++ checker compares every delivered R beat against
    // the model's current content through this port).
    input  wire        bd_sdram_we,
    input  wire [23:0] bd_sdram_word_addr,
    input  wire [31:0] bd_sdram_wdata,
    input  wire [23:0] bd_sdram_rd_word_addr,
    output wire [31:0] bd_sdram_rd_data,

    // Scanout burst injection — drives io_sdram's burst_rd port at the
    // real video cadence (C++-driven).  burst_addr is a HALFWORD address,
    // burst_len counts 32-bit words (burst_32bit=1).
    input  wire        inj_burst_rd,
    input  wire [24:0] inj_burst_addr,
    input  wire [10:0] inj_burst_len,
    output wire [31:0] inj_burst_data,
    output wire        inj_burst_data_valid,
    output wire        inj_burst_data_done,

    // M1 (CPU→arbiter) AXI observation taps for the R-beat checker
    output wire        dbg_m1_arvalid,
    output wire        dbg_m1_arready,
    output wire [31:0] dbg_m1_araddr,
    output wire [7:0]  dbg_m1_arlen,
    output wire        dbg_m1_rvalid,
    output wire        dbg_m1_rready,
    output wire [31:0] dbg_m1_rdata,
    output wire        dbg_m1_rlast,
    output wire        dbg_m1_awvalid,
    output wire        dbg_m1_awready,
    output wire [31:0] dbg_m1_awaddr,

    // Chip-model write-beat events (harness written-address bitmap)
    output wire        dbg_model_wr_evt,
    output wire [24:0] dbg_model_wr_hw_addr,
    output wire [1:0]  dbg_model_wr_dqm,

    // CPU-boundary per-master taps (i/mem/per as the CPU sees them) —
    // catch response mis-routing that is invisible at M1 (data valid for
    // the address presented downstream but delivered to the wrong master
    // or wrong refill slot).
    output wire        dbg_i_arvalid,
    output wire        dbg_i_arready,
    output wire [31:0] dbg_i_araddr,
    output wire        dbg_i_arid,
    output wire [7:0]  dbg_i_arlen,
    output wire        dbg_i_rvalid,
    output wire        dbg_i_rready,
    output wire [31:0] dbg_i_rdata,
    output wire        dbg_i_rid,
    output wire        dbg_i_rlast,
    output wire        dbg_mem_arvalid,
    output wire        dbg_mem_arready,
    output wire [31:0] dbg_mem_araddr,
    output wire [1:0]  dbg_mem_arid,
    output wire [7:0]  dbg_mem_arlen,
    output wire        dbg_mem_rvalid,
    output wire        dbg_mem_rready,
    output wire [31:0] dbg_mem_rdata,
    output wire [1:0]  dbg_mem_rid,
    output wire        dbg_mem_rlast,
    output wire        dbg_per_arvalid,
    output wire        dbg_per_arready,
    output wire [31:0] dbg_per_araddr,
    output wire [7:0]  dbg_per_arlen,
    output wire        dbg_per_rvalid,
    output wire        dbg_per_rready,
    output wire [31:0] dbg_per_rdata,
    output wire        dbg_per_rlast,
    output wire        dbg_slave_rd,
    output wire        dbg_slave_wr,

    // Arbiter→slave write-channel taps + chip write-beat data (ordered
    // write-stream conformance checking in the harness)
    output wire        dbg_aw_valid,
    output wire        dbg_aw_ready,
    output wire [31:0] dbg_aw_addr,
    output wire [7:0]  dbg_aw_len,
    output wire        dbg_w_valid,
    output wire        dbg_w_ready,
    output wire [31:0] dbg_w_data,
    output wire [3:0]  dbg_w_strb,
    output wire        dbg_w_last,
    output wire        dbg_w_cont,
    output wire [15:0] dbg_model_wr_data,

    // CPU debug hoists
    output wire [31:0] dbg_periph_araddr,
    output wire        dbg_periph_arvalid,
    output wire [31:0] dbg_sdram_araddr,
    output wire        dbg_sdram_arvalid,
    output wire [31:0] dbg_cram0_araddr,
    output wire        dbg_cram0_arvalid,
    output wire [31:0] dbg_periph_awaddr,
    output wire        dbg_periph_awvalid,
    output wire [31:0] dbg_sdram_awaddr,
    output wire        dbg_sdram_awvalid,
    output wire [2:0]  dbg_periph_state,
    output wire        dbg_cram0_mode,
    output wire [3:0]  dbg_sdram_slave_state,
    output wire [3:0]  dbg_sdram_model_state,
    output wire        dbg_sdram_model_busy,
    output wire        dbg_arb_grant_cpu,
    output wire [1:0]  dbg_arb_state,
    output wire        dbg_cmd_forwarded,
    output wire        dbg_accepted,
    output wire        dbg_word_rd_sd,
    output wire        dbg_word_wr_sd,
    output wire [23:0] dbg_word_addr_sd,
    output wire [3:0]  dbg_word_burst_len_sd,
    output wire        dbg_word_q_valid_sd
);

// ============================================================
// CPU master → cpu_system busses
// ============================================================
wire        cpu_sdram_arvalid, cpu_sdram_arready;
wire [31:0] cpu_sdram_araddr;
wire [7:0]  cpu_sdram_arlen;
wire        cpu_sdram_rvalid,  cpu_sdram_rready;
wire [31:0] cpu_sdram_rdata;
wire [1:0]  cpu_sdram_rresp;
wire        cpu_sdram_rlast;
wire        cpu_sdram_awvalid, cpu_sdram_awready;
wire [31:0] cpu_sdram_awaddr;
wire [7:0]  cpu_sdram_awlen;
wire        cpu_sdram_wvalid,  cpu_sdram_wready;
wire [31:0] cpu_sdram_wdata;
wire [3:0]  cpu_sdram_wstrb;
wire        cpu_sdram_wlast;
wire        cpu_sdram_bvalid;
wire [1:0]  cpu_sdram_bresp;

wire        cpu_cram0_arvalid, cpu_cram0_arready;
wire [31:0] cpu_cram0_araddr;
wire [7:0]  cpu_cram0_arlen;
wire        cpu_cram0_rvalid,  cpu_cram0_rready;
wire [31:0] cpu_cram0_rdata;
wire [1:0]  cpu_cram0_rresp;
wire        cpu_cram0_rlast;
wire        cpu_cram0_awvalid, cpu_cram0_awready;
wire [31:0] cpu_cram0_awaddr;
wire [7:0]  cpu_cram0_awlen;
wire        cpu_cram0_wvalid,  cpu_cram0_wready;
wire [31:0] cpu_cram0_wdata;
wire [3:0]  cpu_cram0_wstrb;
wire        cpu_cram0_wlast;
wire        cpu_cram0_bvalid;
wire [1:0]  cpu_cram0_bresp;

wire        cpu_local_arvalid, cpu_local_arready;
wire [31:0] cpu_local_araddr;
wire [7:0]  cpu_local_arlen;
wire        cpu_local_rvalid,  cpu_local_rready;
wire [31:0] cpu_local_rdata;
wire [1:0]  cpu_local_rresp;
wire        cpu_local_rlast;
wire        cpu_local_awvalid, cpu_local_awready;
wire [31:0] cpu_local_awaddr;
wire [7:0]  cpu_local_awlen;
wire [1:0]  cpu_local_awburst;
wire        cpu_local_wvalid,  cpu_local_wready;
wire [31:0] cpu_local_wdata;
wire [3:0]  cpu_local_wstrb;
wire        cpu_local_wlast;
wire        cpu_local_bvalid;
wire [1:0]  cpu_local_bresp;

// ============================================================
// cpu_system — 3-master CPU router
// ============================================================
cpu_system cpu_sys (
    .clk    (clk_cpu),
    .reset_n(reset_n),

    // SDRAM master
    .m_sdram_arvalid(cpu_sdram_arvalid), .m_sdram_arready(cpu_sdram_arready),
    .m_sdram_araddr (cpu_sdram_araddr),  .m_sdram_arlen  (cpu_sdram_arlen),
    .m_sdram_rvalid (cpu_sdram_rvalid),  .m_sdram_rready (cpu_sdram_rready),
    .m_sdram_rdata  (cpu_sdram_rdata),   .m_sdram_rresp  (cpu_sdram_rresp),
    .m_sdram_rlast  (cpu_sdram_rlast),
    .m_sdram_awvalid(cpu_sdram_awvalid), .m_sdram_awready(cpu_sdram_awready),
    .m_sdram_awaddr (cpu_sdram_awaddr),  .m_sdram_awlen  (cpu_sdram_awlen),
    .m_sdram_wvalid (cpu_sdram_wvalid),  .m_sdram_wready (cpu_sdram_wready),
    .m_sdram_wdata  (cpu_sdram_wdata),   .m_sdram_wstrb  (cpu_sdram_wstrb),
    .m_sdram_wlast  (cpu_sdram_wlast),
    .m_sdram_bvalid (cpu_sdram_bvalid),  .m_sdram_bresp  (cpu_sdram_bresp),

    // CRAM0 master
    .m_cram0_arvalid(cpu_cram0_arvalid), .m_cram0_arready(cpu_cram0_arready),
    .m_cram0_araddr (cpu_cram0_araddr),  .m_cram0_arlen  (cpu_cram0_arlen),
    .m_cram0_rvalid (cpu_cram0_rvalid),  .m_cram0_rready (cpu_cram0_rready),
    .m_cram0_rdata  (cpu_cram0_rdata),   .m_cram0_rresp  (cpu_cram0_rresp),
    .m_cram0_rlast  (cpu_cram0_rlast),
    .m_cram0_awvalid(cpu_cram0_awvalid), .m_cram0_awready(cpu_cram0_awready),
    .m_cram0_awaddr (cpu_cram0_awaddr),  .m_cram0_awlen  (cpu_cram0_awlen),
    .m_cram0_wvalid (cpu_cram0_wvalid),  .m_cram0_wready (cpu_cram0_wready),
    .m_cram0_wdata  (cpu_cram0_wdata),   .m_cram0_wstrb  (cpu_cram0_wstrb),
    .m_cram0_wlast  (cpu_cram0_wlast),
    .m_cram0_bvalid (cpu_cram0_bvalid),  .m_cram0_bresp  (cpu_cram0_bresp),

    // Local master
    .m_local_arvalid(cpu_local_arvalid), .m_local_arready(cpu_local_arready),
    .m_local_araddr (cpu_local_araddr),  .m_local_arlen  (cpu_local_arlen),
    .m_local_rvalid (cpu_local_rvalid),  .m_local_rready (cpu_local_rready),
    .m_local_rdata  (cpu_local_rdata),   .m_local_rresp  (cpu_local_rresp),
    .m_local_rlast  (cpu_local_rlast),
    .m_local_awvalid(cpu_local_awvalid), .m_local_awready(cpu_local_awready),
    .m_local_awaddr (cpu_local_awaddr),  .m_local_awlen  (cpu_local_awlen),
    .m_local_awburst(cpu_local_awburst),
    .m_local_wvalid (cpu_local_wvalid),  .m_local_wready (cpu_local_wready),
    .m_local_wdata  (cpu_local_wdata),   .m_local_wstrb  (cpu_local_wstrb),
    .m_local_wlast  (cpu_local_wlast),
    .m_local_bvalid (cpu_local_bvalid),  .m_local_bresp  (cpu_local_bresp),

    .int_m_external(1'b0),
    .int_m_timer   (1'b0)
);

// ============================================================
// SDRAM arbiter + slave + sim model
// ============================================================
// GPU and audio masters are tied off: only the CPU port goes live.
wire        arb_arvalid, arb_arready;
wire [31:0] arb_araddr;
wire [7:0]  arb_arlen;
wire        arb_rvalid,  arb_rready;
wire [31:0] arb_rdata;
wire [1:0]  arb_rresp;
wire        arb_rlast;
wire        arb_awvalid, arb_awready;
wire [31:0] arb_awaddr;
wire [7:0]  arb_awlen;
wire        arb_wvalid,  arb_wready;
wire [31:0] arb_wdata;
wire [3:0]  arb_wstrb;
wire        arb_wlast;
wire        arb_bvalid;
wire [1:0]  arb_bresp;
wire        arb_wcont;

// ─── GPU bus signals (live: gpu_core instance below) ───
wire        gpu_rd_arvalid, gpu_rd_arready;
wire [31:0] gpu_rd_araddr;
wire [7:0]  gpu_rd_arlen;
wire        gpu_rd_rvalid;
wire [31:0] gpu_rd_rdata;
wire [1:0]  gpu_rd_rresp;
wire        gpu_rd_rlast;
wire        gpu_wr_awvalid, gpu_wr_awready;
wire [31:0] gpu_wr_awaddr;
wire [7:0]  gpu_wr_awlen;
wire        gpu_wr_wvalid,  gpu_wr_wready;
wire [31:0] gpu_wr_wdata;
wire [3:0]  gpu_wr_wstrb;
wire        gpu_wr_wlast;
wire        gpu_wr_bvalid;
wire [1:0]  gpu_wr_bresp;
wire        gpu_sram_rd;
wire        gpu_sram_wr;
wire        gpu_sram_rd_half;
wire        gpu_sram_rd_hi;
wire [21:0] gpu_sram_addr;
wire [31:0] gpu_sram_wdata;
wire [3:0]  gpu_sram_wstrb;
wire [31:0] gpu_sram_rdata;
wire        gpu_sram_busy;
wire        gpu_sram_rdata_valid;
wire        gpu_swap_req;
wire [1:0]  gpu_swap_idx;
wire        slave_swap_pending;
wire [31:0] gpu_reg_rdata;
wire        gpu_reg_wr;
wire [3:0]  gpu_reg_addr;
wire [31:0] gpu_reg_wdata;
wire        bridge_awready_unused;
wire        bridge_wready_unused;
wire        bridge_bvalid_unused;
wire [1:0]  bridge_bresp_unused;

axi_sdram_arbiter sdram_arb (
    .clk(clk_cpu), .reset_n(reset_n),

    // M0 — GPU (live; reads + writes both go here)
    .m0_arvalid(gpu_rd_arvalid), .m0_arready(gpu_rd_arready),
    .m0_araddr (gpu_rd_araddr),  .m0_arlen  (gpu_rd_arlen),
    .m0_rvalid (gpu_rd_rvalid),  .m0_rdata  (gpu_rd_rdata),
    .m0_rresp  (gpu_rd_rresp),   .m0_rlast  (gpu_rd_rlast),
    .m0_awvalid(gpu_wr_awvalid), .m0_awready(gpu_wr_awready),
    .m0_awaddr (gpu_wr_awaddr),  .m0_awlen  (gpu_wr_awlen),
    .m0_wvalid (gpu_wr_wvalid),  .m0_wready (gpu_wr_wready),
    .m0_wdata  (gpu_wr_wdata),   .m0_wstrb  (gpu_wr_wstrb),
    .m0_wlast  (gpu_wr_wlast),
    .m0_bvalid (gpu_wr_bvalid),  .m0_bresp  (gpu_wr_bresp),

    // M1 — CPU (live)
    .m1_arvalid(cpu_sdram_arvalid), .m1_arready(cpu_sdram_arready),
    .m1_araddr (cpu_sdram_araddr),  .m1_arlen  (cpu_sdram_arlen),
    .m1_rvalid (cpu_sdram_rvalid),  .m1_rdata  (cpu_sdram_rdata),
    .m1_rresp  (cpu_sdram_rresp),   .m1_rlast  (cpu_sdram_rlast),
    .m1_rready (cpu_sdram_rready),
    .m1_awvalid(cpu_sdram_awvalid), .m1_awready(cpu_sdram_awready),
    .m1_awaddr (cpu_sdram_awaddr),  .m1_awlen  (cpu_sdram_awlen),
    .m1_wvalid (cpu_sdram_wvalid),  .m1_wready (cpu_sdram_wready),
    .m1_wdata  (cpu_sdram_wdata),   .m1_wstrb  (cpu_sdram_wstrb),
    .m1_wlast  (cpu_sdram_wlast),
    .m1_bvalid (cpu_sdram_bvalid),  .m1_bresp  (cpu_sdram_bresp),

    // M2 — bridge DMA unused in this system test
    .m2_arvalid(1'b0), .m2_arready(),
    .m2_araddr (32'd0), .m2_arlen(8'd0),
    .m2_rvalid (), .m2_rdata(), .m2_rresp(), .m2_rlast(),
    .m2_rready (1'b1),
    .m2_awvalid(1'b0), .m2_awready(bridge_awready_unused),
    .m2_awaddr (32'd0), .m2_awlen(8'd0),
    .m2_wvalid (1'b0), .m2_wready(bridge_wready_unused),
    .m2_wdata  (32'd0), .m2_wstrb(4'd0),
    .m2_wlast  (1'b0),
    .m2_bvalid (bridge_bvalid_unused), .m2_bresp(bridge_bresp_unused),

    // Slave side
    .s_arvalid(arb_arvalid), .s_arready(arb_arready),
    .s_araddr (arb_araddr),  .s_arlen  (arb_arlen),
    .s_rvalid (arb_rvalid),  .s_rready (arb_rready),
    .s_rdata  (arb_rdata),   .s_rresp  (arb_rresp),
    .s_rlast  (arb_rlast),
    .s_awvalid(arb_awvalid), .s_awready(arb_awready),
    .s_awaddr (arb_awaddr),  .s_awlen  (arb_awlen),
    .s_wvalid (arb_wvalid),  .s_wready (arb_wready),
    .s_wdata  (arb_wdata),   .s_wstrb  (arb_wstrb),
    .s_wlast  (arb_wlast),
    .s_bvalid (arb_bvalid),  .s_bresp  (arb_bresp),
    .s_wcont  (arb_wcont),

    // Debug taps kept for the harness.
    .dbg_arb_state(tb_arb_state_dbg),
    .dbg_cpu_pending(tb_arb_cpu_pending_dbg),
    .dbg_grant(tb_arb_grant_dbg)
);

wire [1:0] tb_arb_state_dbg;
wire       tb_arb_cpu_pending_dbg;
wire [1:0] tb_arb_grant_dbg;

// ─── axi_sdram_slave + pulse adapter ───
wire        sdram_rd, sdram_wr;
wire [23:0] sdram_addr;
wire [31:0] sdram_wdata;
wire [3:0]  sdram_wstrb;
wire [3:0]  sdram_burst_len;
wire [3:0]  sdram_burst_wr_len;
wire        sdram_wr_data_next;
wire [31:0] sdram_next_wdata;
wire [3:0]  sdram_next_wstrb;
wire [31:0] sdram_preload_wdata;
wire [3:0]  sdram_preload_wstrb;

// Pulse adapter (mirror of core_top.v lines 1713–1746)
reg         cmd_forwarded;
reg         accepted_r;
reg         word_rd_sd;
reg         word_wr_sd;
reg  [23:0] word_addr_sd;
reg  [31:0] word_data_sd;
reg  [3:0]  word_wstrb_sd;
reg  [31:0] word_data_next_sd;
reg  [3:0]  word_wstrb_next_sd;
reg  [3:0]  word_burst_len_sd;
reg  [3:0]  word_burst_wr_len_sd;
wire [31:0] word_q_sd;
wire        word_busy_sd;
wire        word_q_valid_sd;
wire        word_wr_data_next_sd;
wire        word_wr_done_sd;

always @(posedge clk_cpu or negedge reset_n) begin
    if (!reset_n) begin
        word_rd_sd <= 0; word_wr_sd <= 0;
        word_addr_sd <= 0; word_data_sd <= 0; word_wstrb_sd <= 0;
        word_data_next_sd <= 0; word_wstrb_next_sd <= 0;
        word_burst_len_sd <= 0; word_burst_wr_len_sd <= 0;
        cmd_forwarded <= 0; accepted_r <= 0;
    end else begin
        word_rd_sd <= 0; word_wr_sd <= 0;
        word_burst_len_sd <= 0; word_burst_wr_len_sd <= 0;
        accepted_r <= 0;

        if (!sdram_rd && !sdram_wr)
            cmd_forwarded <= 0;

        if (!word_busy_sd && !cmd_forwarded && (sdram_rd || sdram_wr)) begin
            word_rd_sd <= sdram_rd;
            word_wr_sd <= sdram_wr;
            word_addr_sd <= sdram_addr;
            word_data_sd <= sdram_wdata;
            word_wstrb_sd <= sdram_wstrb;
            word_data_next_sd <= sdram_preload_wdata;
            word_wstrb_next_sd <= sdram_preload_wstrb;
            word_burst_len_sd <= sdram_burst_len;
            word_burst_wr_len_sd <= sdram_burst_wr_len;
            accepted_r <= 1;
            cmd_forwarded <= 1;
        end
        // (The old wr_data_fwd_d1 refresh of word_data_sd is gone: native
        // burst continuation beats now flow over the direct
        // sdram_next_wdata/strb bus, matching io_sdram on hardware.)
    end
end

axi_sdram_slave sdram_axi_slave (
    .clk(clk_cpu), .reset_n(reset_n),
    .s_axi_arvalid(arb_arvalid), .s_axi_arready(arb_arready),
    .s_axi_araddr (arb_araddr),  .s_axi_arlen  (arb_arlen),
    .s_axi_rvalid (arb_rvalid),  .s_axi_rready (arb_rready),
    .s_axi_rdata  (arb_rdata),   .s_axi_rresp  (arb_rresp),
    .s_axi_rlast  (arb_rlast),
    .s_axi_awvalid(arb_awvalid), .s_axi_awready(arb_awready),
    .s_axi_awaddr (arb_awaddr),  .s_axi_awlen  (arb_awlen),
    .s_axi_wvalid (arb_wvalid),  .s_axi_wready (arb_wready),
    .s_axi_wdata  (arb_wdata),   .s_axi_wstrb  (arb_wstrb),
    .s_axi_wlast  (arb_wlast),
    .s_axi_bvalid (arb_bvalid),  .s_axi_bready (1'b1),
    .s_axi_bresp  (arb_bresp),
    .s_axi_wcont  (arb_wcont),
    .sdram_rd          (sdram_rd),
    .sdram_wr          (sdram_wr),
    .sdram_addr        (sdram_addr),
    .sdram_wdata       (sdram_wdata),
    .sdram_wstrb       (sdram_wstrb),
    .sdram_burst_len   (sdram_burst_len),
    .sdram_burst_wr_len(sdram_burst_wr_len),
    .sdram_rdata       (word_q_sd),
    .sdram_busy        (word_busy_sd),
    .sdram_accepted    (accepted_r),
    .sdram_rdata_valid (word_q_valid_sd),
    .sdram_wr_data_next(word_wr_data_next_sd),
    .sdram_wr_done     (word_wr_done_sd),
    .sdram_next_wdata  (sdram_next_wdata),
    .sdram_next_wstrb  (sdram_next_wstrb),
    .sdram_preload_wdata(sdram_preload_wdata),
    .sdram_preload_wstrb(sdram_preload_wstrb)
);

// ============================================================
// Real MiSTer SDRAM back-end (replaces sdram_fast_model): the slave's
// word ops cross the real io_sdram FSM — bank/row management, CAS
// pipeline, refresh, and scanout burst arbitration — against a
// cycle-accurate chip model, exactly as on hardware.
// ============================================================
wire        phy_cke, phy_clk, phy_cas, phy_ras, phy_we;
wire [1:0]  phy_ba;
wire [12:0] phy_a;
wire [1:0]  phy_dqm;
wire [15:0] ctrl_dq_out;
wire        ctrl_dq_oe;     // unused (split-DQ shim drives port directly)
wire [15:0] model_dq_out;
wire        model_dq_oe;
wire [15:0] dq_to_ctrl = model_dq_oe ? model_dq_out : 16'h0;
wire [7:0]  dbg_io_sd;

io_sdram sdram_ctrl (
    .controller_clk(clk_cpu), .chip_clk(clk_cpu), .clk_90(clk_cpu),
    .reset_n(reset_n),
    .phy_cke(phy_cke), .phy_clk(phy_clk),
    .phy_cas(phy_cas), .phy_ras(phy_ras), .phy_we(phy_we),
    .phy_ba(phy_ba), .phy_a(phy_a),
    .phy_dq_in(dq_to_ctrl),
    .phy_dq_out_port(ctrl_dq_out),
    .phy_dq_oe_port(ctrl_dq_oe),
    .phy_dqm(phy_dqm),
    .burst_rd(inj_burst_rd), .burst_addr(inj_burst_addr),
    .burst_len(inj_burst_len), .burst_32bit(1'b1),
    .burst_data(inj_burst_data), .burst_data_valid(inj_burst_data_valid),
    .burst_data_done(inj_burst_data_done),
    .burstwr(1'b0), .burstwr_addr(25'b0), .burstwr_ready(),
    .burstwr_strobe(1'b0), .burstwr_data(16'b0), .burstwr_done(1'b0),
    .word_rd(word_rd_sd), .word_wr(word_wr_sd),
    .word_addr(word_addr_sd), .word_data(word_data_sd), .word_wstrb(word_wstrb_sd),
    .word_data_next(word_data_next_sd), .word_wstrb_next(word_wstrb_next_sd),
    .word_burst_len(word_burst_len_sd), .word_burst_wr_len(word_burst_wr_len_sd),
    .word_q(word_q_sd), .word_busy(word_busy_sd), .word_q_valid(word_q_valid_sd),
    .word_wr_data_next(word_wr_data_next_sd),
    .word_wr_done(word_wr_done_sd),
    .burst_wr_direct_data(sdram_next_wdata),
    .burst_wr_direct_strb(sdram_next_wstrb),
    .dbg_io(dbg_io_sd)
);

sdram_model_full sdram_chip (
    .clk(phy_clk), .cke(phy_cke), .cs_n(1'b0),
    .ras_n(phy_ras), .cas_n(phy_cas), .we_n(phy_we),
    .ba(phy_ba), .a(phy_a),
    .dq_in(ctrl_dq_out),
    .dq_out(model_dq_out), .dq_oe(model_dq_oe),
    .dqm(phy_dqm),
    .bd_we(bd_sdram_we), .bd_word_addr(bd_sdram_word_addr),
    .bd_wdata(bd_sdram_wdata),
    .bd_rd_word_addr(bd_sdram_rd_word_addr), .bd_rd_data(bd_sdram_rd_data),
    .wr_evt(dbg_model_wr_evt), .wr_evt_hw_addr(dbg_model_wr_hw_addr),
    .wr_evt_dqm(dbg_model_wr_dqm), .wr_evt_data(dbg_model_wr_data)
);

assign dbg_aw_valid = arb_awvalid;
assign dbg_aw_ready = arb_awready;
assign dbg_aw_addr  = arb_awaddr;
assign dbg_aw_len   = arb_awlen;
assign dbg_w_valid  = arb_wvalid;
assign dbg_w_ready  = arb_wready;
assign dbg_w_data   = arb_wdata;
assign dbg_w_strb   = arb_wstrb;
assign dbg_w_last   = arb_wlast;
assign dbg_w_cont   = arb_wcont;

// M1 observation taps (CPU master port into the SDRAM arbiter)
assign dbg_m1_arvalid = cpu_sdram_arvalid;
assign dbg_m1_arready = cpu_sdram_arready;
assign dbg_m1_araddr  = cpu_sdram_araddr;
assign dbg_m1_arlen   = cpu_sdram_arlen;
assign dbg_m1_rvalid  = cpu_sdram_rvalid;
assign dbg_m1_rready  = cpu_sdram_rready;
assign dbg_m1_rdata   = cpu_sdram_rdata;
assign dbg_m1_rlast   = cpu_sdram_rlast;
assign dbg_m1_awvalid = cpu_sdram_awvalid;
assign dbg_m1_awready = cpu_sdram_awready;
assign dbg_m1_awaddr  = cpu_sdram_awaddr;

// ============================================================
// CRAM0 — CDC from clk_cpu to clk_74a, then word interface mux to
//         either the backdoor (bridge-native) or the CDC output
//         depending on cram0_mode (driven by axi_periph_slave).
// ============================================================
wire        cdc_word_rd;
wire        cdc_word_wr;
wire [21:0] cdc_word_addr;
wire [31:0] cdc_word_wdata;
wire [3:0]  cdc_word_wstrb;
wire [31:0] cram0_word_rdata;
wire        cram0_word_busy;
wire        cram0_word_q_valid;

cram0_cdc cpu_cram0_axi (
    .clk_cpu     (clk_cpu),
    .reset_n_cpu (reset_n),

    .s_axi_arvalid (cpu_cram0_arvalid), .s_axi_arready (cpu_cram0_arready),
    .s_axi_araddr  (cpu_cram0_araddr),  .s_axi_arlen   (cpu_cram0_arlen),
    .s_axi_rvalid  (cpu_cram0_rvalid),  .s_axi_rready  (cpu_cram0_rready),
    .s_axi_rdata   (cpu_cram0_rdata),   .s_axi_rresp   (cpu_cram0_rresp),
    .s_axi_rlast   (cpu_cram0_rlast),
    .s_axi_awvalid (cpu_cram0_awvalid), .s_axi_awready (cpu_cram0_awready),
    .s_axi_awaddr  (cpu_cram0_awaddr),  .s_axi_awlen   (cpu_cram0_awlen),
    .s_axi_wvalid  (cpu_cram0_wvalid),  .s_axi_wready  (cpu_cram0_wready),
    .s_axi_wdata   (cpu_cram0_wdata),   .s_axi_wstrb   (cpu_cram0_wstrb),
    .s_axi_wlast   (cpu_cram0_wlast),
    .s_axi_bvalid  (cpu_cram0_bvalid),  .s_axi_bready  (1'b1),
    .s_axi_bresp   (cpu_cram0_bresp),

    .clk_bridge    (clk_74a),
    .reset_n_bridge(reset_n),
    .b_word_rd         (cdc_word_rd),
    .b_word_wr         (cdc_word_wr),
    .b_word_addr       (cdc_word_addr),
    .b_word_wdata      (cdc_word_wdata),
    .b_word_wstrb      (cdc_word_wstrb),
    .b_word_rdata      (cram0_word_rdata),
    .b_word_busy       (cram0_word_busy),
    .b_word_rdata_valid(cram0_word_q_valid)
);

// CDC of cram0_mode from clk_cpu → clk_74a (same pattern as core_top.v).
wire cram0_mode_cpu;
reg  [2:0] cram0_mode_sync;
always @(posedge clk_74a or negedge reset_n) begin
    if (!reset_n) cram0_mode_sync <= 3'b0;
    else          cram0_mode_sync <= {cram0_mode_sync[1:0], cram0_mode_cpu};
end
wire cram0_mode_74a = cram0_mode_sync[2];
assign dbg_cram0_mode = cram0_mode_74a;

// Mode mux: bridge-native backdoor vs CDC-from-CPU.
wire        mux_word_rd    = cram0_mode_74a ? cdc_word_rd    : 1'b0;
wire        mux_word_wr    = cram0_mode_74a ? cdc_word_wr    : bd_cram0_wr;
wire [21:0] mux_word_addr  = cram0_mode_74a ? cdc_word_addr  : bd_cram0_addr;
wire [31:0] mux_word_wdata = cram0_mode_74a ? cdc_word_wdata : bd_cram0_wdata;
wire [3:0]  mux_word_wstrb = cram0_mode_74a ? cdc_word_wstrb : 4'b1111;

cram0_sim_model cram0_mem (
    .clk    (clk_74a),
    .reset_n(reset_n),
    .word_rd  (mux_word_rd),
    .word_wr  (mux_word_wr),
    .word_addr(mux_word_addr),
    .word_data(mux_word_wdata),
    .word_wstrb(mux_word_wstrb),
    .word_q   (cram0_word_rdata),
    .word_busy(cram0_word_busy),
    .word_q_valid(cram0_word_q_valid)
);

// ============================================================
// axi_periph_slave — LOCAL target (BRAM 32KB + MMIO + UART)
// ============================================================
// Tie-offs for peripheral inputs we don't exercise.
wire target_dataslot_ack   = 1'b0;
wire target_dataslot_done  = 1'b0;
wire [2:0] target_dataslot_err = 3'b0;
wire [31:0] link_reg_rdata = 32'b0;
wire [9:0] audio_fifo_level = 10'b0;
wire audio_fifo_full = 1'b0;
wire uart_tx_active = 1'b0;   // UART always idle in sim → TX FIFO always ready
wire uart_rx_dv = 1'b0;
wire [7:0] uart_rx_byte = 8'b0;
wire shutdown_pending = 1'b0;
wire [31:0] dt_query_data = 32'b0;
wire dt_query_valid = 1'b0;
wire [7:0] snac_pin_in = 8'b0;
wire link_irq = 1'b0;
wire bridge_wr_idle = 1'b1;
wire dataslot_allcomplete = 1'b1;
wire [31:0] cont1_key = 32'b0;
wire [31:0] cont1_joy = 32'b0;
wire [15:0] cont1_trig = 16'b0;
wire [31:0] cont2_key = 32'b0;
wire [31:0] cont2_joy = 32'b0;
wire [15:0] cont2_trig = 16'b0;
wire [31:0] app_id = 32'b0;

axi_periph_slave periph (
    .clk    (clk_cpu),
    .reset_n(reset_n),

    .s_axi_arvalid(cpu_local_arvalid), .s_axi_arready(cpu_local_arready),
    .s_axi_araddr (cpu_local_araddr),  .s_axi_arlen  (cpu_local_arlen),
    .s_axi_rvalid (cpu_local_rvalid),  .s_axi_rready (cpu_local_rready),
    .s_axi_rdata  (cpu_local_rdata),   .s_axi_rresp  (cpu_local_rresp),
    .s_axi_rlast  (cpu_local_rlast),
    .s_axi_awvalid(cpu_local_awvalid), .s_axi_awready(cpu_local_awready),
    .s_axi_awaddr (cpu_local_awaddr),  .s_axi_awlen  (cpu_local_awlen),
    .s_axi_awburst(cpu_local_awburst),
    .s_axi_wvalid (cpu_local_wvalid),  .s_axi_wready (cpu_local_wready),
    .s_axi_wdata  (cpu_local_wdata),   .s_axi_wstrb  (cpu_local_wstrb),
    .s_axi_wlast  (cpu_local_wlast),
    .s_axi_bvalid (cpu_local_bvalid),  .s_axi_bready (1'b1),
    .s_axi_bresp  (cpu_local_bresp),

    .dataslot_allcomplete(dataslot_allcomplete),
    .vsync               (vsync),
    .early_vblank        (1'b0),
    .os_inmenu           (1'b0),
    .cont1_key           (cont1_key),
    .cont1_joy           (cont1_joy),
    .cont1_trig          (cont1_trig),
    .cont2_key           (cont2_key),
    .cont2_joy           (cont2_joy),
    .cont2_trig          (cont2_trig),
    .cont3_key           (32'b0),
    .cont3_joy           (32'b0),
    .cont3_trig          (16'b0),
    .cont4_key           (32'b0),
    .cont4_joy           (32'b0),
    .cont4_trig          (16'b0),
    .target_dataslot_ack (target_dataslot_ack),
    .target_dataslot_done(target_dataslot_done),
    .target_dataslot_err (target_dataslot_err),
    .hps_status          (32'd0),
    .hps_img_size        (64'd0),
    .hps_boot_len        (32'd0),
    .hps_img1_size       (64'd0),
    .hps_img2_size       (64'd0),
    .bridge_wr_idle      (bridge_wr_idle),
    .bridge_dbg_wcnt     (32'd0),

    .color_mode     (),
    .fb_display_addr(),
    .fb_width       (),
    .fb_height      (),
    .fb_stride      (),

    .pal_wr  (), .pal_addr(), .pal_data(), .pal_commit(), .pal_busy(1'b0),

    .target_dataslot_read      (),
    .target_dataslot_write     (),
    .target_dataslot_openfile  (),
    .target_dataslot_getfile   (),
    .target_dataslot_id        (),
    .target_dataslot_slotoffset(),
    .target_dataslot_bridgeaddr(),
    .target_dataslot_length    (),
    .target_buffer_param_struct(),
    .target_buffer_resp_struct (),

    .audio_fifo_level (audio_fifo_level),
    .audio_fifo_full  (audio_fifo_full),

    .cram0_mode(cram0_mode_cpu),

    .link_reg_wr   (),
    .link_reg_rd   (),
    .link_reg_addr (),
    .link_reg_wdata(),
    .link_reg_rdata(link_reg_rdata),

    .save_dt_slot   (),
    .save_dt_size   (),
    .save_dt_commit (),

    .uart_tx_dv    (uart_tx_dv),
    .uart_tx_byte  (uart_tx_byte),
    .uart_tx_active(uart_tx_active),
    .uart_rx_dv    (uart_rx_dv),
    .uart_rx_byte  (uart_rx_byte),

    .app_id         (app_id),
    .analogizer_settings(32'b0),
    .analogizer_hoffset (32'b0),
    .analogizer_voffset (32'b0),
    .analogizer_cpu_wr_toggle(),
    .analogizer_cpu_wr_settings(),
    .analogizer_cpu_wr_hoffset(),
    .analogizer_cpu_wr_voffset(),
    .shutdown_pending(shutdown_pending),
    .shutdown_ack   (),

    .timer_irq  (),
    .uart_rx_irq(),

    .link_irq        (link_irq),
    .ext_irq         (),

    .dt_query_addr  (),
    .dt_query_toggle(),
    .dt_query_data  (dt_query_data),
    .dt_query_valid (dt_query_valid),

    .vrr_v_total(),
    .analogizer_enabled(1'b0),

    .snac_pin_out(),
    .snac_pin_dir(),
    .snac_pin_in (snac_pin_in),
    .snac_enable (),

    .gpu_reg_wr   (gpu_reg_wr),
    .gpu_reg_addr (gpu_reg_addr),
    .gpu_reg_wdata(gpu_reg_wdata),
    .gpu_reg_rdata(gpu_reg_rdata),
    // CMD_FLIP side-port from gpu_core
    .gpu_swap_req (gpu_swap_req),
    .gpu_swap_idx (gpu_swap_idx),
    .fb_swap_pending_o (slave_swap_pending)
);

// ============================================================
// gpu_core — full GPU instance (matches core_top.v wiring)
// ============================================================
gpu_core gpu (
    .clk        (clk_cpu),
    .reset_n    (reset_n),
    .gpu_enable (1'b1),
    // AXI4 read master (ring fetch + texture)
    .m_rd_arvalid(gpu_rd_arvalid), .m_rd_arready(gpu_rd_arready),
    .m_rd_araddr (gpu_rd_araddr),  .m_rd_arlen  (gpu_rd_arlen),
    .m_rd_rvalid (gpu_rd_rvalid),  .m_rd_rdata  (gpu_rd_rdata),
    .m_rd_rlast  (gpu_rd_rlast),
    // AXI4 write master (FB writes + clear)
    .m_wr_awvalid(gpu_wr_awvalid), .m_wr_awready(gpu_wr_awready),
    .m_wr_awaddr (gpu_wr_awaddr),  .m_wr_awlen  (gpu_wr_awlen),
    .m_wr_wvalid (gpu_wr_wvalid),  .m_wr_wready (gpu_wr_wready),
    .m_wr_wdata  (gpu_wr_wdata),   .m_wr_wstrb  (gpu_wr_wstrb),
    .m_wr_wlast  (gpu_wr_wlast),
    .m_wr_bvalid (gpu_wr_bvalid),
    // SRAM scratch
    .sram_rd     (gpu_sram_rd),
    .sram_wr     (gpu_sram_wr),
    .sram_rd_half(gpu_sram_rd_half),
    .sram_rd_hi  (gpu_sram_rd_hi),
    .sram_addr   (gpu_sram_addr),
    .sram_wdata  (gpu_sram_wdata),
    .sram_wstrb  (gpu_sram_wstrb),
    .sram_rdata  (gpu_sram_rdata),
    .sram_busy   (gpu_sram_busy),
    .sram_rdata_valid(gpu_sram_rdata_valid),
    // MMIO from periph slave
    .reg_wr      (gpu_reg_wr),
    .reg_addr    (gpu_reg_addr),
    .reg_wdata   (gpu_reg_wdata),
    .reg_rdata   (gpu_reg_rdata),
    // CMD_FLIP side-port → periph slave
    .gpu_swap_req(gpu_swap_req),
    .gpu_swap_idx(gpu_swap_idx),
    .slave_swap_pending(slave_swap_pending),
    // Status
    .busy        (),
    .fence_reached()
);

// ============================================================
// SRAM Model (word-level GPU scratch)
// ============================================================
reg [31:0] gpu_sram_mem [0:65535];
reg        gpu_sram_busy_r;
reg        gpu_sram_rvalid_r;
reg [1:0]  gpu_sram_delay;
reg        gpu_sram_op_read;
reg        gpu_sram_half_r;
reg        gpu_sram_hi_r;
reg [15:0] gpu_sram_addr_r;
reg [31:0] gpu_sram_wdata_r;
reg [3:0]  gpu_sram_wstrb_r;

assign gpu_sram_busy = gpu_sram_busy_r;
assign gpu_sram_rdata_valid = gpu_sram_rvalid_r;
assign gpu_sram_rdata = gpu_sram_half_r
                      ? (gpu_sram_hi_r
                         ? {gpu_sram_mem[gpu_sram_addr_r][31:16], 16'd0}
                         : {16'd0, gpu_sram_mem[gpu_sram_addr_r][15:0]})
                      : gpu_sram_mem[gpu_sram_addr_r];

always @(posedge clk_cpu) begin
    if (!reset_n) begin
        gpu_sram_busy_r   <= 1'b0;
        gpu_sram_rvalid_r <= 1'b0;
        gpu_sram_delay    <= 2'd0;
        gpu_sram_op_read  <= 1'b0;
        gpu_sram_half_r   <= 1'b0;
        gpu_sram_hi_r     <= 1'b0;
        gpu_sram_addr_r   <= 16'd0;
        gpu_sram_wdata_r  <= 32'd0;
        gpu_sram_wstrb_r  <= 4'd0;
    end else begin
        gpu_sram_rvalid_r <= 1'b0;
        if (!gpu_sram_busy_r && (gpu_sram_rd || gpu_sram_wr)) begin
            gpu_sram_busy_r  <= 1'b1;
            gpu_sram_delay   <= 2'd2;
            gpu_sram_op_read <= gpu_sram_rd;
            gpu_sram_half_r  <= gpu_sram_rd && gpu_sram_rd_half;
            gpu_sram_hi_r    <= gpu_sram_rd_hi;
            gpu_sram_addr_r  <= gpu_sram_addr[15:0];
            gpu_sram_wdata_r <= gpu_sram_wdata;
            gpu_sram_wstrb_r <= gpu_sram_wstrb;
        end else if (gpu_sram_busy_r) begin
            if (gpu_sram_delay != 2'd0) begin
                gpu_sram_delay <= gpu_sram_delay - 2'd1;
            end else begin
                if (gpu_sram_op_read) begin
                    gpu_sram_rvalid_r <= 1'b1;
                end else begin
                    if (gpu_sram_wstrb_r[0]) gpu_sram_mem[gpu_sram_addr_r][7:0]   <= gpu_sram_wdata_r[7:0];
                    if (gpu_sram_wstrb_r[1]) gpu_sram_mem[gpu_sram_addr_r][15:8]  <= gpu_sram_wdata_r[15:8];
                    if (gpu_sram_wstrb_r[2]) gpu_sram_mem[gpu_sram_addr_r][23:16] <= gpu_sram_wdata_r[23:16];
                    if (gpu_sram_wstrb_r[3]) gpu_sram_mem[gpu_sram_addr_r][31:24] <= gpu_sram_wdata_r[31:24];
                end
                gpu_sram_busy_r <= 1'b0;
            end
        end
    end
end

// ============================================================
// Debug hoists for the C++ harness
// ============================================================
assign dbg_periph_araddr  = cpu_local_araddr;
assign dbg_periph_arvalid = cpu_local_arvalid;
assign dbg_sdram_araddr   = cpu_sdram_araddr;
assign dbg_sdram_arvalid  = cpu_sdram_arvalid;
assign dbg_cram0_araddr   = cpu_cram0_araddr;
assign dbg_cram0_arvalid  = cpu_cram0_arvalid;
assign dbg_periph_awaddr  = cpu_local_awaddr;
assign dbg_periph_awvalid = cpu_local_awvalid;
assign dbg_sdram_awaddr   = cpu_sdram_awaddr;
assign dbg_sdram_awvalid  = cpu_sdram_awvalid;
assign dbg_periph_state   = periph.state;
assign dbg_sdram_slave_state = sdram_axi_slave.state;
assign dbg_sdram_model_state = dbg_io_sd[3:0];
assign dbg_sdram_model_busy  = word_busy_sd;
assign dbg_arb_grant_cpu     = (sdram_arb.grant == 2'd1);
assign dbg_arb_state         = sdram_arb.arb_state;
assign dbg_cmd_forwarded     = cmd_forwarded;
assign dbg_accepted          = accepted_r;
assign dbg_word_rd_sd        = word_rd_sd;
assign dbg_word_wr_sd        = word_wr_sd;
assign dbg_word_addr_sd      = word_addr_sd;
assign dbg_word_burst_len_sd = word_burst_len_sd;
assign dbg_word_q_valid_sd   = word_q_valid_sd;

// CPU-boundary taps (hierarchical refs into cpu_system internals)
assign dbg_i_arvalid   = cpu_sys.i_arvalid_cpu;
assign dbg_i_arready   = cpu_sys.i_arready_cpu;
assign dbg_i_araddr    = cpu_sys.i_araddr_cpu;
assign dbg_i_arid      = cpu_sys.i_arid_cpu;
assign dbg_i_arlen     = cpu_sys.i_arlen_cpu;
assign dbg_i_rvalid    = cpu_sys.i_rvalid_cpu;
assign dbg_i_rready    = cpu_sys.i_rready_cpu;
assign dbg_i_rdata     = cpu_sys.i_rdata_cpu;
assign dbg_i_rid       = cpu_sys.i_rid_cpu;
assign dbg_i_rlast     = cpu_sys.i_rlast_cpu;
assign dbg_mem_arvalid = cpu_sys.mem_arvalid_cpu;
assign dbg_mem_arready = cpu_sys.mem_arready_cpu;
assign dbg_mem_araddr  = cpu_sys.mem_araddr_cpu;
assign dbg_mem_arid    = cpu_sys.mem_arid_cpu;
assign dbg_mem_arlen   = cpu_sys.mem_arlen_cpu;
assign dbg_mem_rvalid  = cpu_sys.mem_rvalid_cpu;
assign dbg_mem_rready  = cpu_sys.mem_rready_cpu;
assign dbg_mem_rdata   = cpu_sys.mem_rdata_cpu;
assign dbg_mem_rid     = cpu_sys.mem_rid_cpu;
assign dbg_mem_rlast   = cpu_sys.mem_rlast_cpu;
assign dbg_per_arvalid = cpu_sys.per_arvalid_cpu;
assign dbg_per_arready = cpu_sys.per_arready_cpu;
assign dbg_per_araddr  = cpu_sys.per_araddr_cpu;
assign dbg_per_arlen   = cpu_sys.per_arlen_cpu;
assign dbg_per_rvalid  = cpu_sys.per_rvalid_cpu;
assign dbg_per_rready  = cpu_sys.per_rready_cpu;
assign dbg_per_rdata   = cpu_sys.per_rdata_cpu;
assign dbg_per_rlast   = cpu_sys.per_rlast_cpu;
assign dbg_slave_rd    = sdram_rd;
assign dbg_slave_wr    = sdram_wr;

endmodule
