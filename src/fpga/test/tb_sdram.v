//------------------------------------------------------------------------------
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileType: SOURCE
// SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
//------------------------------------------------------------------------------

//
// Verilator Testbench: SDRAM Controller Stack
//
// Instantiates: axi_sdram_slave → io_sdram_test → sdram_model
// io_sdram_test is a patched copy with split DQ (no inout) for Verilator.
//

`default_nettype none

module tb_sdram #(
    parameter SERIALIZE_WRITE_BURSTS = 1'b0,
    parameter [7:0] MAX_NATIVE_WRITE_BURST_LEN = 8'd15
) (
    input  wire        clk,
    input  wire        reset_n,

    // AXI4 Slave interface
    input  wire        s_axi_arvalid,
    output wire        s_axi_arready,
    input  wire [31:0] s_axi_araddr,
    input  wire [7:0]  s_axi_arlen,

    output wire        s_axi_rvalid,
    output wire [31:0] s_axi_rdata,
    output wire [1:0]  s_axi_rresp,
    output wire        s_axi_rlast,

    input  wire        s_axi_awvalid,
    output wire        s_axi_awready,
    input  wire [31:0] s_axi_awaddr,
    input  wire [7:0]  s_axi_awlen,

    input  wire        s_axi_wvalid,
    output wire        s_axi_wready,
    input  wire [31:0] s_axi_wdata,
    input  wire [3:0]  s_axi_wstrb,
    input  wire        s_axi_wlast,

    output wire        s_axi_bvalid,
    output wire [1:0]  s_axi_bresp,

    // Optional burst_rd injection — drives io_sdram's burst_rd port
    // directly so a regression test can keep word_busy high across the
    // slave's write completion (mirrors scanout's burst_rd contention
    // on hardware).  Default tie-offs at the C++ side are 0, preserving
    // pre-existing test behaviour.
    input  wire        inj_burst_rd,
    input  wire [24:0] inj_burst_addr,
    input  wire [10:0] inj_burst_len,

    output wire        dbg_word_wr_data_next,
    output wire        dbg_serialize_write_bursts,

    output wire        busy
);

// AXI slave ↔ word interface signals
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

// io_sdram physical interface
wire        phy_cke, phy_clk, phy_cas, phy_ras, phy_we;
wire [1:0]  phy_ba;
wire [12:0] phy_a;
wire [1:0]  phy_dqm;

// Split DQ bus (no inout needed)
wire [15:0] ctrl_dq_out;   // io_sdram → SDRAM chip (writes)
wire        ctrl_dq_oe;    // io_sdram driving
wire [15:0] model_dq_out;  // SDRAM chip → io_sdram (reads)
wire        model_dq_oe;   // model driving

// Resolved DQ: feed to io_sdram's input
wire [15:0] dq_to_ctrl = model_dq_oe ? model_dq_out : 16'h0;

// Word interface
reg         word_rd, word_wr;
reg  [23:0] word_addr;
reg  [31:0] word_data;
reg  [3:0]  word_wstrb;
reg  [31:0] word_data_next;
reg  [3:0]  word_wstrb_next;
reg  [3:0]  word_burst_len;
reg  [3:0]  word_burst_wr_len;
wire [31:0] word_q;
wire        word_busy;
wire        word_q_valid;
wire        word_wr_data_next;
wire        word_wr_done;

// Pulse adapter (mirrors core_top.v)
reg         cmd_forwarded;
reg         accepted_r;

always @(posedge clk or negedge reset_n) begin
    if (!reset_n) begin
        word_rd <= 0; word_wr <= 0;
        word_addr <= 0; word_data <= 0; word_wstrb <= 0;
        word_data_next <= 0; word_wstrb_next <= 0;
        word_burst_len <= 0; word_burst_wr_len <= 0;
        cmd_forwarded <= 0; accepted_r <= 0;
    end else begin
        word_rd <= 0; word_wr <= 0;
        word_burst_len <= 0; word_burst_wr_len <= 0;
        accepted_r <= 0;

        if (!sdram_rd && !sdram_wr)
            cmd_forwarded <= 0;

        if (!word_busy && !cmd_forwarded && (sdram_rd || sdram_wr)) begin
            word_rd <= sdram_rd;
            word_wr <= sdram_wr;
            word_addr <= sdram_addr;
            word_data <= sdram_wdata;
            word_wstrb <= sdram_wstrb;
            word_data_next <= sdram_preload_wdata;
            word_wstrb_next <= sdram_preload_wstrb;
            word_burst_len <= sdram_burst_len;
            word_burst_wr_len <= sdram_burst_wr_len;
            accepted_r <= 1;
            cmd_forwarded <= 1;
        end
    end
end

assign busy = word_busy;
assign dbg_word_wr_data_next = word_wr_data_next;
assign dbg_serialize_write_bursts = SERIALIZE_WRITE_BURSTS;

// AXI SDRAM Slave
axi_sdram_slave #(
    .SERIALIZE_WRITE_BURSTS(SERIALIZE_WRITE_BURSTS),
    .MAX_NATIVE_WRITE_BURST_LEN(MAX_NATIVE_WRITE_BURST_LEN)
) slave (
    .clk(clk), .reset_n(reset_n),
    .s_axi_arvalid(s_axi_arvalid), .s_axi_arready(s_axi_arready),
    .s_axi_araddr(s_axi_araddr), .s_axi_arlen(s_axi_arlen),
    .s_axi_rvalid(s_axi_rvalid), .s_axi_rready(1'b1),
    .s_axi_rdata(s_axi_rdata), .s_axi_rresp(s_axi_rresp), .s_axi_rlast(s_axi_rlast),
    .s_axi_awvalid(s_axi_awvalid), .s_axi_awready(s_axi_awready),
    .s_axi_awaddr(s_axi_awaddr), .s_axi_awlen(s_axi_awlen),
    .s_axi_wvalid(s_axi_wvalid), .s_axi_wready(s_axi_wready),
    .s_axi_wdata(s_axi_wdata), .s_axi_wstrb(s_axi_wstrb), .s_axi_wlast(s_axi_wlast),
    .s_axi_bvalid(s_axi_bvalid), .s_axi_bready(1'b1), .s_axi_bresp(s_axi_bresp),
    .s_axi_wcont(1'b1),  // tb holds WVALID continuously → exercise the native burst path
    .sdram_rd(sdram_rd), .sdram_wr(sdram_wr),
    .sdram_addr(sdram_addr), .sdram_wdata(sdram_wdata), .sdram_wstrb(sdram_wstrb),
    .sdram_burst_len(sdram_burst_len), .sdram_burst_wr_len(sdram_burst_wr_len),
    .sdram_rdata(word_q), .sdram_busy(word_busy),
    .sdram_accepted(accepted_r), .sdram_rdata_valid(word_q_valid),
    .sdram_wr_data_next(word_wr_data_next),
    .sdram_wr_done(word_wr_done),
    .sdram_next_wdata(sdram_next_wdata),
    .sdram_next_wstrb(sdram_next_wstrb),
    .sdram_preload_wdata(sdram_preload_wdata),
    .sdram_preload_wstrb(sdram_preload_wstrb)
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
    .burst_rd(inj_burst_rd), .burst_addr(inj_burst_addr), .burst_len(inj_burst_len), .burst_32bit(1'b1),
    .burst_data(), .burst_data_valid(), .burst_data_done(),
    .burstwr(1'b0), .burstwr_addr(25'b0), .burstwr_ready(),
    .burstwr_strobe(1'b0), .burstwr_data(16'b0), .burstwr_done(1'b0),
    .word_rd(word_rd), .word_wr(word_wr),
    .word_addr(word_addr), .word_data(word_data), .word_wstrb(word_wstrb),
    .word_data_next(word_data_next), .word_wstrb_next(word_wstrb_next),
    .word_burst_len(word_burst_len), .word_burst_wr_len(word_burst_wr_len),
    .word_q(word_q), .word_busy(word_busy), .word_q_valid(word_q_valid),
    .word_wr_data_next(word_wr_data_next),
    .word_wr_done(word_wr_done),
    .burst_wr_direct_data(sdram_next_wdata),
    .burst_wr_direct_strb(sdram_next_wstrb)
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
