//
// Verilator System Testbench: VexiiRiscv CPU + Memory Stack
//
// Full CPU system running real firmware in simulation:
//   VexiiRiscv → cpu_system → { BRAM (I-fetch + local), SDRAM stack }
//
// The C++ harness loads firmware into BRAM via backdoor, releases reset,
// and monitors a result register at a known BRAM address.
//
// Result protocol:
//   BRAM[0x3FF00] = test status (0 = running, 1 = pass, 2 = fail)
//   BRAM[0x3FF04] = test result value
//   BRAM[0x3FF08] = cycle counter (written by firmware)
//

`default_nettype none

module tb_system (
    input wire clk,
    input wire reset_n,

    // Backdoor BRAM access (for firmware loading and result checking)
    input  wire        bd_we,
    input  wire [15:0] bd_addr,
    input  wire [31:0] bd_wdata,
    output wire [31:0] bd_rdata,

    // UART output (from CPU writes to 0x4F000004)
    output wire        uart_tx_valid,
    output wire [7:0]  uart_tx_byte,

    // SDRAM backdoor (for OS binary preload)
    input  wire        sd_bd_we,
    input  wire [23:0] sd_bd_addr,    // 24-bit word address
    input  wire [31:0] sd_bd_wdata,

    // PSRAM/CRAM0 backdoor (for OS binary preload)
    input  wire        ps_bd_we,
    input  wire [23:0] ps_bd_addr,
    input  wire [31:0] ps_bd_wdata,

    // Debug: expose fetch and LSU addresses for PC tracing
    output wire [31:0] dbg_fetch_addr,
    output wire        dbg_fetch_valid,
    output wire [31:0] dbg_lsu_addr,
    output wire        dbg_lsu_valid
);

// ============================================================
// CPU system (VexiiRiscv + address decode + bus routing)
// ============================================================

// SDRAM AXI4 bus
wire        sdram_arvalid, sdram_arready;
wire [31:0] sdram_araddr;
wire [7:0]  sdram_arlen;
wire        sdram_rvalid, sdram_rlast;
wire [31:0] sdram_rdata;
wire [1:0]  sdram_rresp;
wire        sdram_awvalid, sdram_awready;
wire [31:0] sdram_awaddr;
wire [7:0]  sdram_awlen;
wire        sdram_wvalid, sdram_wready, sdram_wlast;
wire [31:0] sdram_wdata;
wire [3:0]  sdram_wstrb;
wire        sdram_bvalid;
wire [1:0]  sdram_bresp;
// Per-target R-channel back-pressure from cpu_system (new plumbing).
wire sdram_rready, cram0_rready, cram1_rready, sram_rready, local_rready;

// CRAM0 / CRAM1 / SRAM AXI4 buses — per-chip fan-out from cpu_system.
// We only give CRAM0 a real behavioral model (so preloaded OS / app
// binaries can be read back); CRAM1 and SRAM are minimal ack stubs.
wire        cram0_arvalid, cram0_arready;
wire [31:0] cram0_araddr;
wire [7:0]  cram0_arlen;
wire        cram0_rvalid, cram0_rlast;
wire [31:0] cram0_rdata;
wire [1:0]  cram0_rresp;
wire        cram0_awvalid, cram0_awready;
wire [31:0] cram0_awaddr;
wire [7:0]  cram0_awlen;
wire        cram0_wvalid, cram0_wready, cram0_wlast;
wire [31:0] cram0_wdata;
wire [3:0]  cram0_wstrb;
wire        cram0_bvalid;
wire [1:0]  cram0_bresp;

// CRAM1 / SRAM ack-only stubs — declare master-driven signals and
// slave-driven ready/resp signals both up front.
wire        cram1_arvalid, cram1_awvalid, cram1_wvalid;
wire [31:0] cram1_araddr, cram1_awaddr, cram1_wdata;
wire [7:0]  cram1_arlen,  cram1_awlen;
wire [3:0]  cram1_wstrb;
wire        cram1_wlast;
wire        cram1_arready, cram1_awready;

wire        sram_arvalid, sram_awvalid, sram_wvalid;
wire [31:0] sram_araddr, sram_awaddr, sram_wdata;
wire [7:0]  sram_arlen, sram_awlen;
wire [3:0]  sram_wstrb;
wire        sram_wlast;
wire        sram_arready, sram_awready;

// ============================================================
// CRAM0: REAL stack — axi_cram0_slave → psram_cram0_test →
// psram_cram0_drv → cram_chip_model.  This replaces the behavioral
// cram0_mem[] from earlier and makes boot simulation actually
// exercise the sync-burst RTL that runs on hardware.
//
// ps_bd_we / ps_bd_addr / ps_bd_wdata write the OS binary through
// cram_chip_model's backdoor (32-bit word wide, bank-bit in addr[21]).
// Harness also drives bcr_config_en via `bcr_config_pulse` below to
// sequence the BCR init that core_top.v's BCR_ST_* FSM would do on
// real hardware.
// ============================================================

// psram_cram0 → axi_cram0_slave wires
wire        c0_psram_rd, c0_psram_wr;
wire [25:0] c0_psram_addr;
wire [31:0] c0_psram_wdata;
wire [3:0]  c0_psram_wstrb;
wire [31:0] c0_psram_rdata;
wire        c0_psram_busy, c0_psram_rdata_valid;
wire        c0_psram_burst_rd;
wire [5:0]  c0_psram_burst_len;
wire [31:0] c0_psram_burst_rdata;
wire        c0_psram_burst_rdata_valid;

// CRAM0 physical pins
wire [21:16] cram_a;
wire [15:0]  cram_ctrl_dq_out;
wire         cram_ctrl_dq_oe;
wire [15:0]  cram_chip_dq_out;
wire [15:0]  cram_dq_to_ctrl;
wire         cram_wait, cram_clk_unused;
wire         cram_adv_n, cram_cre;
wire         cram_ce0_n, cram_ce1_n;
wire         cram_oe_n, cram_we_n;
wire         cram_ub_n, cram_lb_n;

assign cram_dq_to_ctrl = cram_ctrl_dq_oe ? cram_ctrl_dq_out : cram_chip_dq_out;

// BCR init FSM — replicates the core_top.v BCR_ST_* sequence.  Runs
// immediately after reset_n rises, issues BCR config pulses to both
// chip banks, then sets bcr_init_done.  CPU reset is gated below so
// VexiiRiscv doesn't start fetching until the chip is in sync burst
// mode.
reg [2:0]  bcr_init_state;
reg        bcr_config_en;
reg        bcr_config_bank;
reg        bcr_init_done;
localparam [2:0] BCR_IDLE   = 3'd0;
localparam [2:0] BCR_PULSE0 = 3'd1;
localparam [2:0] BCR_WAIT0  = 3'd2;
localparam [2:0] BCR_PULSE1 = 3'd3;
localparam [2:0] BCR_WAIT1  = 3'd4;
localparam [2:0] BCR_DONE   = 3'd5;
reg        bcr_saw_busy;
wire       cram_raw_busy;  // from psram_cram0_test

always @(posedge clk or negedge reset_n) begin
    if (!reset_n) begin
        bcr_init_state  <= BCR_IDLE;
        bcr_config_en   <= 1'b0;
        bcr_config_bank <= 1'b0;
        bcr_init_done   <= 1'b0;
        bcr_saw_busy    <= 1'b0;
    end else begin
        bcr_config_en <= 1'b0;
        case (bcr_init_state)
            BCR_IDLE: bcr_init_state <= BCR_PULSE0;
            BCR_PULSE0: begin
                bcr_config_bank <= 1'b0;
                bcr_config_en   <= 1'b1;
                bcr_saw_busy    <= 1'b0;
                bcr_init_state  <= BCR_WAIT0;
            end
            BCR_WAIT0: begin
                if (cram_raw_busy) bcr_saw_busy <= 1'b1;
                else if (bcr_saw_busy) bcr_init_state <= BCR_PULSE1;
            end
            BCR_PULSE1: begin
                bcr_config_bank <= 1'b1;
                bcr_config_en   <= 1'b1;
                bcr_saw_busy    <= 1'b0;
                bcr_init_state  <= BCR_WAIT1;
            end
            BCR_WAIT1: begin
                if (cram_raw_busy) bcr_saw_busy <= 1'b1;
                else if (bcr_saw_busy) bcr_init_state <= BCR_DONE;
            end
            BCR_DONE: bcr_init_done <= 1'b1;
        endcase
    end
end

// CRAM0 AXI slave — DUT for the whole burst read path
axi_cram0_slave cram0_axi (
    .clk    (clk),
    .reset_n(reset_n),
    .s_axi_arvalid(cram0_arvalid), .s_axi_arready(cram0_arready),
    .s_axi_araddr (cram0_araddr),  .s_axi_arlen  (cram0_arlen),
    .s_axi_rvalid (cram0_rvalid),  .s_axi_rready (cram0_rready),
    .s_axi_rdata  (cram0_rdata),
    .s_axi_rresp  (cram0_rresp),   .s_axi_rlast  (cram0_rlast),
    .s_axi_awvalid(cram0_awvalid), .s_axi_awready(cram0_awready),
    .s_axi_awaddr (cram0_awaddr),  .s_axi_awlen  (cram0_awlen),
    .s_axi_wvalid (cram0_wvalid),  .s_axi_wready (cram0_wready),
    .s_axi_wdata  (cram0_wdata),   .s_axi_wstrb  (cram0_wstrb),
    .s_axi_wlast  (cram0_wlast),
    .s_axi_bvalid (cram0_bvalid),  .s_axi_bready (1'b1),
    .s_axi_bresp  (cram0_bresp),

    .psram_rd         (c0_psram_rd),
    .psram_wr         (c0_psram_wr),
    .psram_addr       (c0_psram_addr),
    .psram_wdata      (c0_psram_wdata),
    .psram_wstrb      (c0_psram_wstrb),
    .psram_rdata      (c0_psram_rdata),
    .psram_busy       (c0_psram_busy),
    .psram_rdata_valid(c0_psram_rdata_valid),

    .psram_burst_rd         (c0_psram_burst_rd),
    .psram_burst_len        (c0_psram_burst_len),
    .psram_burst_rdata      (c0_psram_burst_rdata),
    .psram_burst_rdata_valid(c0_psram_burst_rdata_valid)
);

psram_cram0_test #(.CLOCK_SPEED(100.0)) cram0_wrap (
    .clk    (clk),
    .reset_n(reset_n),
    .word_rd   (c0_psram_rd),
    .word_wr   (c0_psram_wr),
    .word_addr (c0_psram_addr[21:0]),
    .word_data (c0_psram_wdata),
    .word_wstrb(c0_psram_wstrb),
    .word_q    (c0_psram_rdata),
    .word_busy (c0_psram_busy),
    .word_q_valid(c0_psram_rdata_valid),
    .burst_rd          (c0_psram_burst_rd),
    .burst_len         (c0_psram_burst_len),
    .burst_rdata_valid (c0_psram_burst_rdata_valid),
    .burst_rdata       (c0_psram_burst_rdata),
    .config_en      (bcr_config_en),
    .config_data    (16'h641F),
    .config_bank_sel(bcr_config_bank),
    .raw_busy       (cram_raw_busy),
    .cram_a    (cram_a),
    .cram_dq_in(cram_dq_to_ctrl),
    .cram_dq_out(cram_ctrl_dq_out),
    .cram_dq_oe(cram_ctrl_dq_oe),
    .cram_wait (cram_wait),
    .cram_clk  (cram_clk_unused),
    .cram_adv_n(cram_adv_n), .cram_cre(cram_cre),
    .cram_ce0_n(cram_ce0_n), .cram_ce1_n(cram_ce1_n),
    .cram_oe_n (cram_oe_n),  .cram_we_n (cram_we_n),
    .cram_ub_n (cram_ub_n),  .cram_lb_n (cram_lb_n)
);

cram_chip_model #(.POWERUP_CYCLES(8'd16)) cram_chip (
    .clk         (clk),
    .cram_clk    (clk),
    .reset_n     (reset_n),
    .cram_a      (cram_a),
    .cram_dq_in  (cram_ctrl_dq_oe ? cram_ctrl_dq_out : 16'h0),
    .cram_dq_out (cram_chip_dq_out),
    .cram_dq_oe  (cram_ctrl_dq_oe),
    .cram_wait_out(cram_wait),
    .cram_adv_n  (cram_adv_n),
    .cram_cre    (cram_cre),
    .cram_ce0_n  (cram_ce0_n),
    .cram_ce1_n  (cram_ce1_n),
    .cram_oe_n   (cram_oe_n),
    .cram_we_n   (cram_we_n),
    .cram_ub_n   (cram_ub_n),
    .cram_lb_n   (cram_lb_n),
    .cram_wr_strobe(1'b0),
    // Backdoor: 32-bit word writes from C++ harness during reset
    .bd_we        (ps_bd_we),
    .bd_word_addr (ps_bd_addr[20:0]),
    .bd_wdata_word(ps_bd_wdata),
    .error_count (/* unused */)
);

// CRAM1 stub — ack everything with zero data
assign cram1_arready = 1'b1;
assign cram1_awready = 1'b1;
wire        cram1_rvalid = 1'b0;
wire [31:0] cram1_rdata  = 32'b0;
wire [1:0]  cram1_rresp  = 2'b00;
wire        cram1_rlast  = 1'b0;
wire        cram1_wready = 1'b1;
wire        cram1_bvalid = 1'b0;
wire [1:0]  cram1_bresp  = 2'b00;

// SRAM stub — ack everything with zero data
assign sram_arready = 1'b1;
assign sram_awready = 1'b1;
wire        sram_rvalid  = 1'b0;
wire [31:0] sram_rdata   = 32'b0;
wire [1:0]  sram_rresp   = 2'b00;
wire        sram_rlast   = 1'b0;
wire        sram_wready  = 1'b1;
wire        sram_bvalid  = 1'b0;
wire [1:0]  sram_bresp   = 2'b00;

// Local AXI4 bus (BRAM)
wire        local_arvalid, local_arready;
wire [31:0] local_araddr;
wire [7:0]  local_arlen;
wire        local_rvalid, local_rlast;
wire [31:0] local_rdata;
wire [1:0]  local_rresp;
wire        local_awvalid, local_awready;
wire [31:0] local_awaddr;
wire [7:0]  local_awlen;
wire        local_wvalid, local_wready, local_wlast;
wire [31:0] local_wdata;
wire [3:0]  local_wstrb;
wire        local_bvalid;
wire [1:0]  local_bresp;

// I-fetch AXI4 bus (from cpu_system's fetch port → BRAM)
wire        fetch_arvalid, fetch_arready;
wire [31:0] fetch_araddr;
wire [7:0]  fetch_arlen;
wire        fetch_rvalid, fetch_rlast;
wire [31:0] fetch_rdata;
wire [1:0]  fetch_rresp;

// Gate CPU reset on BCR init done — matches core_top.v's
//   wire reset_n = reset_n_apf & bcr_init_done;
wire cpu_reset_n = reset_n & bcr_init_done;
cpu_system cpu (
    .clk(clk), .reset_n(cpu_reset_n),
    // SDRAM
    .m_sdram_arvalid(sdram_arvalid), .m_sdram_arready(sdram_arready),
    .m_sdram_araddr(sdram_araddr), .m_sdram_arlen(sdram_arlen),
    .m_sdram_rvalid(sdram_rvalid), .m_sdram_rready(sdram_rready),
    .m_sdram_rdata(sdram_rdata),
    .m_sdram_rresp(sdram_rresp), .m_sdram_rlast(sdram_rlast),
    .m_sdram_awvalid(sdram_awvalid), .m_sdram_awready(sdram_awready),
    .m_sdram_awaddr(sdram_awaddr), .m_sdram_awlen(sdram_awlen),
    .m_sdram_wvalid(sdram_wvalid), .m_sdram_wready(sdram_wready),
    .m_sdram_wdata(sdram_wdata), .m_sdram_wstrb(sdram_wstrb),
    .m_sdram_wlast(sdram_wlast),
    .m_sdram_bvalid(sdram_bvalid), .m_sdram_bresp(sdram_bresp),
    // CRAM0 (backed by cram0_mem[])
    .m_cram0_arvalid(cram0_arvalid), .m_cram0_arready(cram0_arready),
    .m_cram0_araddr(cram0_araddr), .m_cram0_arlen(cram0_arlen),
    .m_cram0_rvalid(cram0_rvalid), .m_cram0_rready(cram0_rready),
    .m_cram0_rdata(cram0_rdata),
    .m_cram0_rresp(cram0_rresp), .m_cram0_rlast(cram0_rlast),
    .m_cram0_awvalid(cram0_awvalid), .m_cram0_awready(cram0_awready),
    .m_cram0_awaddr(cram0_awaddr), .m_cram0_awlen(cram0_awlen),
    .m_cram0_wvalid(cram0_wvalid), .m_cram0_wready(cram0_wready),
    .m_cram0_wdata(cram0_wdata), .m_cram0_wstrb(cram0_wstrb),
    .m_cram0_wlast(cram0_wlast),
    .m_cram0_bvalid(cram0_bvalid), .m_cram0_bresp(cram0_bresp),
    // CRAM1 (stub ack)
    .m_cram1_arvalid(cram1_arvalid), .m_cram1_arready(cram1_arready),
    .m_cram1_araddr(cram1_araddr), .m_cram1_arlen(cram1_arlen),
    .m_cram1_rvalid(cram1_rvalid), .m_cram1_rready(cram1_rready),
    .m_cram1_rdata(cram1_rdata),
    .m_cram1_rresp(cram1_rresp), .m_cram1_rlast(cram1_rlast),
    .m_cram1_awvalid(cram1_awvalid), .m_cram1_awready(cram1_awready),
    .m_cram1_awaddr(cram1_awaddr), .m_cram1_awlen(cram1_awlen),
    .m_cram1_wvalid(cram1_wvalid), .m_cram1_wready(cram1_wready),
    .m_cram1_wdata(cram1_wdata), .m_cram1_wstrb(cram1_wstrb),
    .m_cram1_wlast(cram1_wlast),
    .m_cram1_bvalid(cram1_bvalid), .m_cram1_bresp(cram1_bresp),
    // SRAM (stub ack)
    .m_sram_arvalid(sram_arvalid), .m_sram_arready(sram_arready),
    .m_sram_araddr(sram_araddr), .m_sram_arlen(sram_arlen),
    .m_sram_rvalid(sram_rvalid), .m_sram_rready(sram_rready),
    .m_sram_rdata(sram_rdata),
    .m_sram_rresp(sram_rresp), .m_sram_rlast(sram_rlast),
    .m_sram_awvalid(sram_awvalid), .m_sram_awready(sram_awready),
    .m_sram_awaddr(sram_awaddr), .m_sram_awlen(sram_awlen),
    .m_sram_wvalid(sram_wvalid), .m_sram_wready(sram_wready),
    .m_sram_wdata(sram_wdata), .m_sram_wstrb(sram_wstrb),
    .m_sram_wlast(sram_wlast),
    .m_sram_bvalid(sram_bvalid), .m_sram_bresp(sram_bresp),
    // Local
    .m_local_arvalid(local_arvalid), .m_local_arready(local_arready),
    .m_local_araddr(local_araddr), .m_local_arlen(local_arlen),
    .m_local_rvalid(local_rvalid), .m_local_rready(local_rready),
    .m_local_rdata(local_rdata),
    .m_local_rresp(local_rresp), .m_local_rlast(local_rlast),
    .m_local_awvalid(local_awvalid), .m_local_awready(local_awready),
    .m_local_awaddr(local_awaddr), .m_local_awlen(local_awlen),
    .m_local_wvalid(local_wvalid), .m_local_wready(local_wready),
    .m_local_wdata(local_wdata), .m_local_wstrb(local_wstrb),
    .m_local_wlast(local_wlast),
    .m_local_bvalid(local_bvalid), .m_local_bresp(local_bresp),
    .int_m_external(1'b0),
    .int_m_timer(1'b0)
);

// ============================================================
// BRAM: serves both I-fetch and local (uncached) access
// ============================================================
bram_model #(.ADDR_BITS(16)) bram (
    .clk(clk), .reset_n(reset_n),
    // I-fetch port
    .fetch_ar_valid(fetch_arvalid), .fetch_ar_ready(fetch_arready),
    .fetch_ar_addr(fetch_araddr), .fetch_ar_len(fetch_arlen),
    .fetch_r_valid(fetch_rvalid), .fetch_r_ready(fetch_rready),
    .fetch_r_data(fetch_rdata), .fetch_r_resp(fetch_rresp),
    .fetch_r_last(fetch_rlast),
    // Local port
    .local_ar_valid(local_arvalid), .local_ar_ready(local_arready),
    .local_ar_addr(local_araddr), .local_ar_len(local_arlen),
    .local_r_valid(local_rvalid), .local_r_ready(local_rready),
    .local_r_data(local_rdata), .local_r_resp(local_rresp),
    .local_r_last(local_rlast),
    .local_aw_valid(local_awvalid), .local_aw_ready(local_awready),
    .local_aw_addr(local_awaddr), .local_aw_len(local_awlen),
    .local_w_valid(local_wvalid), .local_w_ready(local_wready),
    .local_w_data(local_wdata), .local_w_strb(local_wstrb),
    .local_w_last(local_wlast),
    .local_b_valid(local_bvalid), .local_b_ready(1'b1),
    .local_b_resp(local_bresp),
    // Backdoor
    .bd_we(bd_we), .bd_addr(bd_addr), .bd_wdata(bd_wdata), .bd_rdata(bd_rdata),
    // UART
    .uart_tx_valid(uart_tx_valid), .uart_tx_byte(uart_tx_byte)
);

// ============================================================
// SDRAM: fast behavioral model (~4 cycle latency)
// ============================================================
sdram_fast_model sdram_fast (
    .clk(clk), .reset_n(reset_n),
    .s_axi_arvalid(sdram_arvalid), .s_axi_arready(sdram_arready),
    .s_axi_araddr(sdram_araddr), .s_axi_arlen(sdram_arlen),
    .s_axi_rvalid(sdram_rvalid), .s_axi_rready(sdram_rready),
    .s_axi_rdata(sdram_rdata), .s_axi_rresp(sdram_rresp), .s_axi_rlast(sdram_rlast),
    .s_axi_awvalid(sdram_awvalid), .s_axi_awready(sdram_awready),
    .s_axi_awaddr(sdram_awaddr), .s_axi_awlen(sdram_awlen),
    .s_axi_wvalid(sdram_wvalid), .s_axi_wready(sdram_wready),
    .s_axi_wdata(sdram_wdata), .s_axi_wstrb(sdram_wstrb), .s_axi_wlast(sdram_wlast),
    .s_axi_bvalid(sdram_bvalid), .s_axi_bready(1'b1), .s_axi_bresp(sdram_bresp),
    .bd_we(sd_bd_we), .bd_word_addr(sd_bd_addr), .bd_wdata(sd_bd_wdata)
);

// ============================================================
// I-fetch routing: cpu_system exposes fetch bus, route to BRAM
// ============================================================
// cpu_system internally routes FetchL1Axi4 to the local bus for
// address range 0x00000000-0x0003FFFF. The BRAM's fetch port
// handles these requests separately from local R/W.

// Note: cpu_system routes fetch to the local bus (same as BRAM).
// The BRAM model has separate fetch and local ports.
// We need to check if cpu_system exposes the fetch bus separately
// or merges it with local. Let me check...

// Actually, cpu_system merges fetch into the local bus based on
// address decode. The BRAM's local port handles both fetch and
// local accesses. The separate fetch port on bram_model is not
// used in this configuration — tie it off.

// For now, tie off the BRAM fetch port (cpu_system handles routing)
assign fetch_arvalid = 0;

// Debug: expose bus activity for PC tracing.
// PSRAM is included so CRAM0 (OS code at 0x30000000) instruction
// fetches are visible — without this, anything running in CRAM0
// looks like a hang because the read goes through the psram bus.
assign dbg_fetch_addr = local_arvalid ? local_araddr :
                         sdram_arvalid ? sdram_araddr :
                         cram0_arvalid ? cram0_araddr :
                         cram1_arvalid ? cram1_araddr :
                         sram_arvalid  ? sram_araddr  : 32'h0;
assign dbg_fetch_valid = local_arvalid | sdram_arvalid | cram0_arvalid | cram1_arvalid | sram_arvalid;
assign dbg_lsu_addr = local_awvalid ? local_awaddr :
                       sdram_awvalid ? sdram_awaddr :
                       cram0_awvalid ? cram0_awaddr :
                       cram1_awvalid ? cram1_awaddr :
                       sram_awvalid  ? sram_awaddr  : 32'h0;
assign dbg_lsu_valid = local_awvalid | sdram_awvalid | cram0_awvalid | cram1_awvalid | sram_awvalid;
assign fetch_araddr = 0;
assign fetch_arlen = 0;
wire fetch_rready = 1;

endmodule
