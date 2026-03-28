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
    output wire [7:0]  uart_tx_byte
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

// PSRAM AXI4 bus (active but not connected to memory for now — just ack)
wire        psram_arvalid, psram_arready;
wire [31:0] psram_araddr;
wire [7:0]  psram_arlen;
wire        psram_rvalid, psram_rlast;
wire [31:0] psram_rdata;
wire [1:0]  psram_rresp;
wire        psram_awvalid, psram_awready;
wire [31:0] psram_awaddr;
wire [7:0]  psram_awlen;
wire        psram_wvalid, psram_wready, psram_wlast;
wire [31:0] psram_wdata;
wire [3:0]  psram_wstrb;
wire        psram_bvalid;
wire [1:0]  psram_bresp;

// Tie off PSRAM (no memory behind it for initial test)
assign psram_arready = 0;
assign psram_rvalid = 0;
assign psram_rdata = 0;
assign psram_rresp = 0;
assign psram_rlast = 0;
assign psram_awready = 0;
assign psram_wready = 0;
assign psram_bvalid = 0;
assign psram_bresp = 0;

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
wire        local_rready = 1'b1;  // Always accept read data

// I-fetch AXI4 bus (from cpu_system's fetch port → BRAM)
wire        fetch_arvalid, fetch_arready;
wire [31:0] fetch_araddr;
wire [7:0]  fetch_arlen;
wire        fetch_rvalid, fetch_rlast;
wire [31:0] fetch_rdata;
wire [1:0]  fetch_rresp;

cpu_system cpu (
    .clk(clk), .reset_n(reset_n),
    // SDRAM
    .m_sdram_arvalid(sdram_arvalid), .m_sdram_arready(sdram_arready),
    .m_sdram_araddr(sdram_araddr), .m_sdram_arlen(sdram_arlen),
    .m_sdram_rvalid(sdram_rvalid), .m_sdram_rdata(sdram_rdata),
    .m_sdram_rresp(sdram_rresp), .m_sdram_rlast(sdram_rlast),
    .m_sdram_awvalid(sdram_awvalid), .m_sdram_awready(sdram_awready),
    .m_sdram_awaddr(sdram_awaddr), .m_sdram_awlen(sdram_awlen),
    .m_sdram_wvalid(sdram_wvalid), .m_sdram_wready(sdram_wready),
    .m_sdram_wdata(sdram_wdata), .m_sdram_wstrb(sdram_wstrb),
    .m_sdram_wlast(sdram_wlast),
    .m_sdram_bvalid(sdram_bvalid), .m_sdram_bresp(sdram_bresp),
    // PSRAM
    .m_psram_arvalid(psram_arvalid), .m_psram_arready(psram_arready),
    .m_psram_araddr(psram_araddr), .m_psram_arlen(psram_arlen),
    .m_psram_rvalid(psram_rvalid), .m_psram_rdata(psram_rdata),
    .m_psram_rresp(psram_rresp), .m_psram_rlast(psram_rlast),
    .m_psram_awvalid(psram_awvalid), .m_psram_awready(psram_awready),
    .m_psram_awaddr(psram_awaddr), .m_psram_awlen(psram_awlen),
    .m_psram_wvalid(psram_wvalid), .m_psram_wready(psram_wready),
    .m_psram_wdata(psram_wdata), .m_psram_wstrb(psram_wstrb),
    .m_psram_wlast(psram_wlast),
    .m_psram_bvalid(psram_bvalid), .m_psram_bresp(psram_bresp),
    // Local
    .m_local_arvalid(local_arvalid), .m_local_arready(local_arready),
    .m_local_araddr(local_araddr), .m_local_arlen(local_arlen),
    .m_local_rvalid(local_rvalid), .m_local_rdata(local_rdata),
    .m_local_rresp(local_rresp), .m_local_rlast(local_rlast),
    .m_local_awvalid(local_awvalid), .m_local_awready(local_awready),
    .m_local_awaddr(local_awaddr), .m_local_awlen(local_awlen),
    .m_local_wvalid(local_wvalid), .m_local_wready(local_wready),
    .m_local_wdata(local_wdata), .m_local_wstrb(local_wstrb),
    .m_local_wlast(local_wlast),
    .m_local_bvalid(local_bvalid), .m_local_bresp(local_bresp)
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
    .local_r_valid(local_rvalid), .local_r_ready(1'b1),
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
// SDRAM stack: axi_sdram_slave → io_sdram_test → sdram_model
// (Reuse existing SDRAM test stack from tb_sdram.v)
// ============================================================

// AXI slave ↔ word interface
wire        sdram_rd, sdram_wr;
wire [23:0] sdram_addr_w;
wire [31:0] sdram_wdata_w;
wire [3:0]  sdram_wstrb_w;
wire [3:0]  sdram_burst_len;
wire [3:0]  sdram_burst_wr_len;
wire        sdram_wr_data_next;
wire [31:0] sdram_next_wdata;
wire [3:0]  sdram_next_wstrb;

// io_sdram physical signals
wire        phy_cke, phy_clk, phy_cas, phy_ras, phy_we;
wire [1:0]  phy_ba;
wire [12:0] phy_a;
wire [1:0]  phy_dqm;
wire [15:0] ctrl_dq_out, model_dq_out;
wire        ctrl_dq_oe, model_dq_oe;
wire [15:0] dq_to_ctrl = model_dq_oe ? model_dq_out : 16'h0;

// Word interface
reg         word_rd, word_wr;
reg  [23:0] word_addr;
reg  [31:0] word_data;
reg  [3:0]  word_wstrb;
reg  [3:0]  word_burst_len;
reg  [3:0]  word_burst_wr_len;
wire [31:0] word_q;
wire        word_busy;
wire        word_q_valid;
wire        word_wr_data_next;

// Pulse adapter (from tb_sdram.v)
reg         cmd_forwarded;
reg         accepted_r;
reg         wr_data_fwd_d1;

always @(posedge clk or negedge reset_n) begin
    if (!reset_n) begin
        word_rd <= 0; word_wr <= 0;
        word_addr <= 0; word_data <= 0; word_wstrb <= 0;
        word_burst_len <= 0; word_burst_wr_len <= 0;
        cmd_forwarded <= 0; accepted_r <= 0; wr_data_fwd_d1 <= 0;
    end else begin
        word_rd <= 0; word_wr <= 0;
        word_burst_len <= 0; word_burst_wr_len <= 0;
        accepted_r <= 0;

        if (!sdram_rd && !sdram_wr)
            cmd_forwarded <= 0;

        if (!word_busy && !cmd_forwarded && (sdram_rd || sdram_wr)) begin
            word_rd <= sdram_rd;
            word_wr <= sdram_wr;
            word_addr <= sdram_addr_w;
            word_data <= sdram_wdata_w;
            word_wstrb <= sdram_wstrb_w;
            word_burst_len <= sdram_burst_len;
            word_burst_wr_len <= sdram_burst_wr_len;
            accepted_r <= 1;
            cmd_forwarded <= 1;
        end

        wr_data_fwd_d1 <= word_wr_data_next;
        if (wr_data_fwd_d1) begin
            word_data <= sdram_wdata_w;
            word_wstrb <= sdram_wstrb_w;
        end
    end
end

axi_sdram_slave sdram_slave (
    .clk(clk), .reset_n(reset_n),
    .s_axi_arvalid(sdram_arvalid), .s_axi_arready(sdram_arready),
    .s_axi_araddr(sdram_araddr), .s_axi_arlen(sdram_arlen),
    .s_axi_rvalid(sdram_rvalid), .s_axi_rready(1'b1),
    .s_axi_rdata(sdram_rdata), .s_axi_rresp(sdram_rresp), .s_axi_rlast(sdram_rlast),
    .s_axi_awvalid(sdram_awvalid), .s_axi_awready(sdram_awready),
    .s_axi_awaddr(sdram_awaddr), .s_axi_awlen(sdram_awlen),
    .s_axi_wvalid(sdram_wvalid), .s_axi_wready(sdram_wready),
    .s_axi_wdata(sdram_wdata), .s_axi_wstrb(sdram_wstrb), .s_axi_wlast(sdram_wlast),
    .s_axi_bvalid(sdram_bvalid), .s_axi_bready(1'b1), .s_axi_bresp(sdram_bresp),
    .sdram_rd(sdram_rd), .sdram_wr(sdram_wr),
    .sdram_addr(sdram_addr_w), .sdram_wdata(sdram_wdata_w), .sdram_wstrb(sdram_wstrb_w),
    .sdram_burst_len(sdram_burst_len), .sdram_burst_wr_len(sdram_burst_wr_len),
    .sdram_rdata(word_q), .sdram_busy(word_busy),
    .sdram_accepted(accepted_r), .sdram_rdata_valid(word_q_valid),
    .sdram_wr_data_next(sdram_wr_data_next),
    .sdram_next_wdata(sdram_next_wdata),
    .sdram_next_wstrb(sdram_next_wstrb)
);

io_sdram sdram_ctrl (
    .controller_clk(clk), .chip_clk(clk), .clk_90(clk), .reset_n(reset_n),
    .phy_cke(phy_cke), .phy_clk(phy_clk),
    .phy_cas(phy_cas), .phy_ras(phy_ras), .phy_we(phy_we),
    .phy_ba(phy_ba), .phy_a(phy_a),
    .phy_dq_in(dq_to_ctrl),
    .phy_dq_out_port(ctrl_dq_out),
    .phy_dq_oe_port(ctrl_dq_oe),
    .phy_dqm(phy_dqm),
    .burst_rd(1'b0), .burst_addr(25'b0), .burst_len(11'b0), .burst_32bit(1'b1),
    .burst_data(), .burst_data_valid(), .burst_data_done(),
    .burstwr(1'b0), .burstwr_addr(25'b0), .burstwr_ready(),
    .burstwr_strobe(1'b0), .burstwr_data(16'b0), .burstwr_done(1'b0),
    .word_rd(word_rd), .word_wr(word_wr),
    .word_addr(word_addr), .word_data(word_data), .word_wstrb(word_wstrb),
    .word_burst_len(word_burst_len), .word_burst_wr_len(word_burst_wr_len),
    .word_q(word_q), .word_busy(word_busy), .word_q_valid(word_q_valid),
    .word_wr_data_next(word_wr_data_next),
    .burst_wr_direct_data(sdram_wdata_w),
    .burst_wr_direct_strb(sdram_wstrb_w)
);

sdram_model sdram_chip (
    .clk(phy_clk), .cke(phy_cke), .cs_n(1'b0),
    .ras_n(phy_ras), .cas_n(phy_cas), .we_n(phy_we),
    .ba(phy_ba), .a(phy_a),
    .dq_in(ctrl_dq_out),
    .dq_out(model_dq_out), .dq_oe(model_dq_oe),
    .dqm(phy_dqm)
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
assign fetch_araddr = 0;
assign fetch_arlen = 0;
wire fetch_rready = 1;

endmodule
