//
// tb_system.v — full CPU + SDRAM + CRAM0 + BRAM sim for v2 architecture.
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

    // UART TX observation — driven by axi_periph_slave
    output wire        uart_tx_dv,
    output wire [7:0]  uart_tx_byte,

    // CRAM0 backdoor (bridge-native side of the mux).  The harness uses
    // this to preload os.bin before releasing reset_n.
    input  wire        bd_cram0_wr,
    input  wire [21:0] bd_cram0_addr,
    input  wire [31:0] bd_cram0_wdata,

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

axi_sdram_arbiter sdram_arb (
    .clk(clk_cpu), .reset_n(reset_n),

    // M0 — GPU (tied off)
    .m0_arvalid(1'b0), .m0_arready(),
    .m0_araddr (32'b0), .m0_arlen(8'b0),
    .m0_rvalid (), .m0_rdata(), .m0_rresp(), .m0_rlast(),
    .m0_awvalid(1'b0), .m0_awready(),
    .m0_awaddr (32'b0), .m0_awlen(8'b0),
    .m0_wvalid (1'b0), .m0_wready(),
    .m0_wdata  (32'b0), .m0_wstrb(4'b0), .m0_wlast(1'b0),
    .m0_bvalid (), .m0_bresp(),

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

    // M2 — Audio (tied off)
    .m2_arvalid(1'b0), .m2_arready(),
    .m2_araddr (32'b0), .m2_arlen(8'b0),
    .m2_rvalid (), .m2_rdata(), .m2_rresp(), .m2_rlast(),

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
    .s_bvalid (arb_bvalid),  .s_bresp  (arb_bresp)
);

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

// Pulse adapter (mirror of core_top.v lines 1713–1746)
reg         cmd_forwarded;
reg         accepted_r;
reg         wr_data_fwd_d1;
reg         word_rd_sd;
reg         word_wr_sd;
reg  [23:0] word_addr_sd;
reg  [31:0] word_data_sd;
reg  [3:0]  word_wstrb_sd;
reg  [3:0]  word_burst_len_sd;
reg  [3:0]  word_burst_wr_len_sd;
wire [31:0] word_q_sd;
wire        word_busy_sd;
wire        word_q_valid_sd;
wire        word_wr_data_next_sd;

always @(posedge clk_cpu or negedge reset_n) begin
    if (!reset_n) begin
        word_rd_sd <= 0; word_wr_sd <= 0;
        word_addr_sd <= 0; word_data_sd <= 0; word_wstrb_sd <= 0;
        word_burst_len_sd <= 0; word_burst_wr_len_sd <= 0;
        cmd_forwarded <= 0; accepted_r <= 0; wr_data_fwd_d1 <= 0;
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
            word_burst_len_sd <= sdram_burst_len;
            word_burst_wr_len_sd <= sdram_burst_wr_len;
            accepted_r <= 1;
            cmd_forwarded <= 1;
        end

        wr_data_fwd_d1 <= word_wr_data_next_sd;
        if (wr_data_fwd_d1) begin
            word_data_sd <= sdram_next_wdata;
            word_wstrb_sd <= sdram_next_wstrb;
        end
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
    .sdram_next_wdata  (sdram_next_wdata),
    .sdram_next_wstrb  (sdram_next_wstrb)
);

sdram_fast_model sdram_mem (
    .clk (clk_cpu),
    .reset_n(reset_n),
    .word_rd            (word_rd_sd),
    .word_wr            (word_wr_sd),
    .word_addr          (word_addr_sd),
    .word_data          (word_data_sd),
    .word_wstrb         (word_wstrb_sd),
    .word_burst_len     (word_burst_len_sd),
    .word_burst_wr_len  (word_burst_wr_len_sd),
    .word_q             (word_q_sd),
    .word_busy          (word_busy_sd),
    .word_q_valid       (word_q_valid_sd),
    .word_wr_data_next  (word_wr_data_next_sd),
    .burst_wr_direct_data(sdram_next_wdata),
    .burst_wr_direct_strb(sdram_next_wstrb)
);

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
wire [31:0] gpu_reg_rdata = 32'b0;
wire link_irq = 1'b0;
wire bridge_wr_idle = 1'b1;
wire dataslot_allcomplete = 1'b1;
wire vsync = 1'b0;
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
    .cont1_key           (cont1_key),
    .cont1_joy           (cont1_joy),
    .cont1_trig          (cont1_trig),
    .cont2_key           (cont2_key),
    .cont2_joy           (cont2_joy),
    .cont2_trig          (cont2_trig),
    .target_dataslot_ack (target_dataslot_ack),
    .target_dataslot_done(target_dataslot_done),
    .target_dataslot_err (target_dataslot_err),
    .bridge_wr_idle      (bridge_wr_idle),

    .color_mode     (),
    .fb_display_addr(),

    .pal_wr  (), .pal_addr(), .pal_data(),

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

    .snac_pin_out(),
    .snac_pin_dir(),
    .snac_pin_in (snac_pin_in),
    .snac_enable (),

    .gpu_reg_wr   (),
    .gpu_reg_addr (),
    .gpu_reg_wdata(),
    .gpu_reg_rdata(gpu_reg_rdata)
);

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
assign dbg_sdram_model_state = sdram_mem.state;
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

endmodule
