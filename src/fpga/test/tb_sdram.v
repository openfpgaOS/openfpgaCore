//
// Verilator Testbench: SDRAM Controller Stack
//
// Instantiates: word_sdram_arbiter → io_sdram_test → sdram_model
// io_sdram_test is a patched copy with split DQ (no inout) for Verilator.
// Test harness drives M2 (CPU) port on the arbiter; M0/M1/M3 tied off.
//

`default_nettype none

module tb_sdram (
    input  wire        clk,
    input  wire        reset_n,

    // Word-level master interface (drives arbiter M2/CPU port)
    input  wire        m_rd,
    input  wire        m_wr,
    input  wire [23:0] m_addr,
    input  wire [31:0] m_wdata,
    input  wire [3:0]  m_wstrb,
    input  wire [3:0]  m_burst_len,
    input  wire [3:0]  m_burst_wr_len,
    output wire [31:0] m_rdata,
    output wire        m_busy,
    output wire        m_accepted,
    output wire        m_rdata_valid,
    output wire        m_wr_data_next
);

// io_sdram physical interface
wire        phy_cke, phy_clk, phy_cas, phy_ras, phy_we;
wire [1:0]  phy_ba;
wire [12:0] phy_a;
wire [1:0]  phy_dqm;

// Split DQ bus (no inout needed)
wire [15:0] ctrl_dq_out;
wire        ctrl_dq_oe;
wire [15:0] model_dq_out;
wire        model_dq_oe;
wire [15:0] dq_to_ctrl = model_dq_oe ? model_dq_out : 16'h0;

// Arbiter → io_sdram word signals
wire        arb_sdram_rd, arb_sdram_wr;
wire [23:0] arb_sdram_addr;
wire [31:0] arb_sdram_wdata;
wire [3:0]  arb_sdram_wstrb;
wire [31:0] arb_sdram_wdata_direct;
wire [3:0]  arb_sdram_wstrb_direct;
wire [3:0]  arb_sdram_burst_len;
wire [3:0]  arb_sdram_burst_wr_len;
wire [31:0] word_q;
wire        word_busy;
wire        word_q_valid;
wire        word_wr_data_next;

// Word-level SDRAM arbiter (only M2/CPU active)
word_sdram_arbiter sdram_arb (
    .clk(clk), .reset_n(reset_n),
    // M0: tied off
    .m0_rd(1'b0), .m0_addr(24'b0), .m0_burst_len(4'b0),
    .m0_rdata(), .m0_busy(), .m0_accepted(), .m0_rdata_valid(),
    // M1: tied off
    .m1_rd(1'b0), .m1_wr(1'b0), .m1_addr(24'b0), .m1_wdata(32'b0),
    .m1_wstrb(4'b0), .m1_burst_len(4'b0), .m1_burst_wr_len(4'b0),
    .m1_rdata(), .m1_busy(), .m1_accepted(), .m1_rdata_valid(), .m1_wr_data_next(),
    // M2: test harness (CPU port)
    .m2_rd(m_rd), .m2_wr(m_wr),
    .m2_addr(m_addr), .m2_wdata(m_wdata), .m2_wstrb(m_wstrb),
    .m2_burst_len(m_burst_len), .m2_burst_wr_len(m_burst_wr_len),
    .m2_rdata(m_rdata), .m2_busy(m_busy),
    .m2_accepted(m_accepted), .m2_rdata_valid(m_rdata_valid),
    .m2_wr_data_next(m_wr_data_next),
    // M3: tied off
    .m3_rd(1'b0), .m3_wr(1'b0), .m3_addr(24'b0), .m3_wdata(32'b0),
    .m3_wstrb(4'b0), .m3_burst_len(4'b0), .m3_burst_wr_len(4'b0),
    .m3_rdata(), .m3_busy(), .m3_accepted(), .m3_rdata_valid(), .m3_wr_data_next(),
    // io_sdram word interface
    .sdram_rd(arb_sdram_rd), .sdram_wr(arb_sdram_wr),
    .sdram_addr(arb_sdram_addr), .sdram_wdata(arb_sdram_wdata),
    .sdram_wstrb(arb_sdram_wstrb),
    .sdram_burst_len(arb_sdram_burst_len),
    .sdram_burst_wr_len(arb_sdram_burst_wr_len),
    .sdram_rdata(word_q), .sdram_busy(word_busy),
    .sdram_rdata_valid(word_q_valid),
    .sdram_wr_data_next(word_wr_data_next),
    .sdram_wdata_direct(arb_sdram_wdata_direct),
    .sdram_wstrb_direct(arb_sdram_wstrb_direct)
);

// io_sdram (test variant with split DQ)
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
    .word_rd(arb_sdram_rd), .word_wr(arb_sdram_wr),
    .word_addr(arb_sdram_addr), .word_data(arb_sdram_wdata),
    .word_wstrb(arb_sdram_wstrb),
    .word_burst_len(arb_sdram_burst_len),
    .word_burst_wr_len(arb_sdram_burst_wr_len),
    .word_q(word_q), .word_busy(word_busy), .word_q_valid(word_q_valid),
    .word_wr_data_next(word_wr_data_next),
    .burst_wr_direct_data(arb_sdram_wdata_direct),
    .burst_wr_direct_strb(arb_sdram_wstrb_direct)
);

// Behavioral SDRAM model
sdram_model sdram_chip (
    .clk(phy_clk), .cke(phy_cke), .cs_n(1'b0),
    .ras_n(phy_ras), .cas_n(phy_cas), .we_n(phy_we),
    .ba(phy_ba), .a(phy_a),
    .dq_in(ctrl_dq_out),
    .dq_out(model_dq_out), .dq_oe(model_dq_oe),
    .dqm(phy_dqm),
    .bd_we(1'b0), .bd_word_addr(24'b0), .bd_wdata(32'b0)
);

endmodule
