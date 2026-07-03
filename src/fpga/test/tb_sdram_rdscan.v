//------------------------------------------------------------------------------
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileType: SOURCE
// SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
//------------------------------------------------------------------------------
//
// Verilator testbench: MiSTer SDRAM READ-path vs scanout phase hammer.
//
//   axi_sdram_slave
//       -> pulse adapter (byte-identical to the production fabric; copied
//          verbatim from tb_sdram_cont.v)
//       -> io_sdram  (== targets/mister/io_sdram.v, DQ-split for sim)
//       -> sdram_model_full  (cycle-accurate, refresh + row-timing enforced,
//                             combinational backdoor read = live data oracle)
//
// Boot-lottery hunt: this closes the verified coverage hole that NO test
// data-checks word-READ bursts while scanout burst_rd traffic is in flight.
// The slave's AXI port is driven DIRECTLY by the C++ harness (no arbiter,
// no CPU), so read bursts can be golden-checked beat-by-beat while a scanout
// burst_rd is injected at exhaustively swept phase offsets.
//
// TEST-ONLY, ADDITIVE.  No production RTL is modified.
//

`default_nettype none

module tb_sdram_rdscan #(
    parameter BANK_ROW_TRACK = 1
) (
    input  wire        clk,
    input  wire        reset_n,

    // ---- AXI4 slave port (driven directly by the C++ harness) ----
    input  wire        s_axi_arvalid,
    output wire        s_axi_arready,
    input  wire [31:0] s_axi_araddr,
    input  wire [7:0]  s_axi_arlen,

    output wire        s_axi_rvalid,
    input  wire        s_axi_rready,
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

    input  wire        s_axi_wcont,   // drive 0 from C++ (CPU-style writes)

    // ---- Scanout burst_rd injection + data tap ----
    input  wire        burst_rd,
    input  wire [24:0] burst_addr,     // HALFWORD address
    input  wire [10:0] burst_len,      // 32-bit words (burst_32bit=1)
    output wire [31:0] burst_data,
    output wire        burst_data_valid,
    output wire        burst_data_done,

    // ---- sdram_model_full backdoor (preload + live oracle) ----
    input  wire        bd_we,
    input  wire [23:0] bd_word_addr,
    input  wire [31:0] bd_wdata,
    input  wire [23:0] bd_rd_word_addr,
    output wire [31:0] bd_rd_data,

    // ---- Diagnostics ----
    output wire [7:0]  dbg_io,
    output wire [3:0]  dbg_slave_state,
    output wire [31:0] dbg_model_errors
);

// ============================================================
// Slave <-> io_sdram word interface
// ============================================================
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

// io_sdram word interface (pulse adapter outputs)
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

// io_sdram physical interface
wire        phy_cke, phy_clk, phy_cas, phy_ras, phy_we;
wire [1:0]  phy_ba;
wire [12:0] phy_a;
wire [1:0]  phy_dqm;
wire [15:0] ctrl_dq_out;
wire        ctrl_dq_oe;     // unused (split-DQ shim drives port directly)
wire [15:0] model_dq_out;
wire        model_dq_oe;
wire [15:0] dq_to_ctrl = model_dq_oe ? model_dq_out : 16'h0;

// ============================================================
// Slave -> io_sdram pulse adapter
// Byte-identical to core_top.v:2607-2634 (the production fabric).
// Copied VERBATIM from tb_sdram_cont.v.
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
        ram1_word_rd <= 0;
        ram1_word_wr <= 0;
        ram1_word_burst_len <= 4'd0;
        ram1_word_burst_wr_len <= 4'd0;
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
// DUT 1: AXI SDRAM slave (production, unmodified)
// ============================================================
axi_sdram_slave #(
    .SERIALIZE_WRITE_BURSTS(1'b0),
    .MAX_NATIVE_WRITE_BURST_LEN(8'd15)
) slave (
    .clk(clk), .reset_n(reset_n),
    .s_axi_arvalid(s_axi_arvalid), .s_axi_arready(s_axi_arready),
    .s_axi_araddr(s_axi_araddr),   .s_axi_arlen(s_axi_arlen),
    .s_axi_rvalid(s_axi_rvalid),   .s_axi_rready(s_axi_rready),
    .s_axi_rdata(s_axi_rdata),     .s_axi_rresp(s_axi_rresp), .s_axi_rlast(s_axi_rlast),
    .s_axi_awvalid(s_axi_awvalid), .s_axi_awready(s_axi_awready),
    .s_axi_awaddr(s_axi_awaddr),   .s_axi_awlen(s_axi_awlen),
    .s_axi_wvalid(s_axi_wvalid),   .s_axi_wready(s_axi_wready),
    .s_axi_wdata(s_axi_wdata),     .s_axi_wstrb(s_axi_wstrb), .s_axi_wlast(s_axi_wlast),
    .s_axi_bvalid(s_axi_bvalid),   .s_axi_bready(1'b1),       .s_axi_bresp(s_axi_bresp),
    .s_axi_wcont(s_axi_wcont),
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
// DUT 2: io_sdram (MiSTer controller; test variant with split DQ)
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
    .burst_rd(burst_rd), .burst_addr(burst_addr), .burst_len(burst_len), .burst_32bit(1'b1),
    .burst_data(burst_data), .burst_data_valid(burst_data_valid), .burst_data_done(burst_data_done),
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
    .dbg_io(dbg_io)
);

// ============================================================
// Behavioral SDRAM model — ground truth + live combinational oracle.
// ============================================================
sdram_model_full sdram_chip (
    .clk(phy_clk), .cke(phy_cke), .cs_n(1'b0),
    .ras_n(phy_ras), .cas_n(phy_cas), .we_n(phy_we),
    .ba(phy_ba), .a(phy_a),
    .dq_in(ctrl_dq_out),
    .dq_out(model_dq_out), .dq_oe(model_dq_oe),
    .dqm(phy_dqm),
    .bd_we(bd_we), .bd_word_addr(bd_word_addr), .bd_wdata(bd_wdata),
    .bd_rd_word_addr(bd_rd_word_addr), .bd_rd_data(bd_rd_data),
    .wr_evt(), .wr_evt_hw_addr(), .wr_evt_dqm()
);

// ============================================================
// Observability
// ============================================================
assign dbg_slave_state  = slave.state;
assign dbg_model_errors = $unsigned(sdram_chip.errors);

endmodule

`default_nettype wire
