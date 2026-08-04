//------------------------------------------------------------------------------
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileType: SOURCE
// SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
//------------------------------------------------------------------------------
//
// Verilator testbench: FIELD-FAULT reproduction rig for the Pocket os25
// "+8-byte shifted .text" SDRAM corruption.
//
//   axi_sdram_arbiter  (4 masters: M0 GPU, M1 CPU, M2 bridge, M3 audio)
//        -> axi_sdram_slave
//        -> pulse adapter (byte-identical to core_top.v fabric)
//        -> io_sdram_test  (== targets/pocket/io_sdram.v, DQ-split for sim)
//        -> sdram_model_full (cycle-accurate, refresh + row-timing enforced)
//
// Field traffic mix (OS init window on Pocket):
//   * M1 (CPU) cache-line eviction WRITE bursts (8/16 beats, wcont=0 ->
//     the slave's S_WR_FILL buffered path -> ONE native io_sdram burst),
//     back-to-back, plus cache-refill read bursts.
//   * Scanout burst_rd injection (hard-real-time terminal fetch) preempting
//     at randomized phases against in-flight write bursts.
//   * M2 occasional single-word reads/writes; M3 audio-mixer-style short
//     read bursts.  Refresh fires autonomously inside io_sdram.
//
// The C++ harness scoreboards every committed byte against the physical
// sdram_model memory and SPECIFICALLY hunts the field signature
// mem[addr] == expected[addr-8] (data stream lagging its address by one
// 8-byte unit), plus wedges (no completion progress for N cycles).
//
// TEST-ONLY, ADDITIVE.  No production RTL is modified.  Structure is a
// twin of tb_sdram_cont.v (new file so existing tests stay untouched).
//

`default_nettype none

module tb_sdram_stress #(
    parameter BANK_ROW_TRACK = 1
) (
    input  wire        clk,
    input  wire        reset_n,

    // ---- M0: GPU (merged read + posted write) ----
    input  wire        m0_arvalid,
    output wire        m0_arready,
    input  wire [31:0] m0_araddr,
    input  wire [7:0]  m0_arlen,
    output wire        m0_rvalid,
    output wire [31:0] m0_rdata,
    output wire        m0_rlast,
    input  wire        m0_awvalid,
    output wire        m0_awready,
    input  wire [31:0] m0_awaddr,
    input  wire [7:0]  m0_awlen,
    input  wire        m0_wvalid,
    output wire        m0_wready,
    input  wire [31:0] m0_wdata,
    input  wire [3:0]  m0_wstrb,
    input  wire        m0_wlast,
    output wire        m0_bvalid,

    // ---- M1: CPU (read + write) ----
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
    output wire [1:0]  m1_bresp,

    // ---- M2: bridge file DMA (read + write) ----
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

    // ---- M3: Audio mixer (read-only) ----
    input  wire        m3_arvalid,
    output wire        m3_arready,
    input  wire [31:0] m3_araddr,
    input  wire [7:0]  m3_arlen,
    output wire        m3_rvalid,
    output wire [31:0] m3_rdata,
    output wire        m3_rlast,
    input  wire        m3_rready,

    // ---- Scanout burst_rd injection (hard-real-time video fetch) ----
    input  wire        inj_burst_rd,
    input  wire [24:0] inj_burst_addr,
    input  wire [10:0] inj_burst_len,
    output wire        inj_burst_data_done,

    // ---- Diagnostics ----
    output wire [4:0]  dbg_gpu_wq_count,
    output wire        dbg_gpu_wq_full,
    output wire [1:0]  dbg_arb_state,
    output wire [1:0]  dbg_grant,
    output wire [7:0]  dbg_io,
    output wire        dbg_busy,
    output wire [3:0]  dbg_slave_state,
    output wire [3:0]  dbg_wbuf_wptr,
    output wire [3:0]  dbg_wbuf_rptr,
    output wire        dbg_wr_data_next,
    output wire        dbg_wr_done
);

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
wire        ram1_word_queue_pending;
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

assign dbg_busy = ram1_word_busy;
assign dbg_slave_state = slave.state;
assign dbg_wbuf_wptr = slave.wbuf_wptr;
assign dbg_wbuf_rptr = slave.wbuf_rptr;
assign dbg_wr_data_next = sdram_slave_wr_data_next;
assign dbg_wr_done = sdram_slave_wr_done;

// ============================================================
// Slave -> io_sdram pulse adapter
// Byte-identical to core_top.v (the production fabric).
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

        if (!ram1_word_queue_pending && !sdram_cmd_forwarded &&
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
// DUT 1: AXI SDRAM arbiter (production, unmodified)
// ============================================================
axi_sdram_arbiter sdram_arb (
    .clk(clk),
    .reset_n(reset_n),

    .m0_arvalid(m0_arvalid), .m0_arready(m0_arready),
    .m0_araddr(m0_araddr),   .m0_arlen(m0_arlen),
    .m0_rvalid(m0_rvalid),   .m0_rdata(m0_rdata),
    .m0_rresp(),             .m0_rlast(m0_rlast),
    .m0_awvalid(m0_awvalid), .m0_awready(m0_awready),
    .m0_awaddr(m0_awaddr),   .m0_awlen(m0_awlen),
    .m0_wvalid(m0_wvalid),   .m0_wready(m0_wready),
    .m0_wdata(m0_wdata),     .m0_wstrb(m0_wstrb),
    .m0_wlast(m0_wlast),
    .m0_bvalid(m0_bvalid),   .m0_bresp(),

    .m1_arvalid(m1_arvalid), .m1_arready(m1_arready),
    .m1_araddr(m1_araddr),   .m1_arlen(m1_arlen),
    .m1_rvalid(m1_rvalid),   .m1_rdata(m1_rdata),
    .m1_rresp(),             .m1_rlast(m1_rlast),
    .m1_rready(m1_rready),
    .m1_awvalid(m1_awvalid), .m1_awready(m1_awready),
    .m1_awaddr(m1_awaddr),   .m1_awlen(m1_awlen),
    .m1_wvalid(m1_wvalid),   .m1_wready(m1_wready),
    .m1_wdata(m1_wdata),     .m1_wstrb(m1_wstrb),
    .m1_wlast(m1_wlast),
    .m1_bvalid(m1_bvalid),   .m1_bresp(m1_bresp),

    .m2_arvalid(m2_arvalid), .m2_arready(m2_arready),
    .m2_araddr(m2_araddr),   .m2_arlen(m2_arlen),
    .m2_rvalid(m2_rvalid),   .m2_rdata(m2_rdata),
    .m2_rresp(),             .m2_rlast(m2_rlast),
    .m2_rready(m2_rready),
    .m2_awvalid(m2_awvalid), .m2_awready(m2_awready),
    .m2_awaddr(m2_awaddr),   .m2_awlen(m2_awlen),
    .m2_wvalid(m2_wvalid),   .m2_wready(m2_wready),
    .m2_wdata(m2_wdata),     .m2_wstrb(m2_wstrb),
    .m2_wlast(m2_wlast),
    .m2_bvalid(m2_bvalid),   .m2_bresp(),

    .m3_arvalid(m3_arvalid), .m3_arready(m3_arready),
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
// DUT 2: AXI SDRAM slave (production, unmodified; params == core_top.v)
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
    .sdram_rdata(ram1_word_q), .sdram_busy(ram1_word_queue_pending),
    .sdram_accepted(sdram_accepted_r), .sdram_rdata_valid(ram1_word_q_valid),
    .sdram_wr_data_next(sdram_slave_wr_data_next),
    .sdram_wr_done(sdram_slave_wr_done),
    .sdram_next_wdata(sdram_slave_next_wdata),
    .sdram_next_wstrb(sdram_slave_next_wstrb),
    .sdram_preload_wdata(sdram_slave_preload_wdata),
    .sdram_preload_wstrb(sdram_slave_preload_wstrb)
);

// ============================================================
// DUT 3: io_sdram (Pocket controller; test variant with split DQ)
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
    .word_q(ram1_word_q), .word_busy(ram1_word_busy),
    .word_queue_pending ( ram1_word_queue_pending ), .word_q_valid(ram1_word_q_valid),
    .word_wr_data_next(sdram_slave_wr_data_next),
    .word_wr_done(sdram_slave_wr_done),
    .burst_wr_direct_data(sdram_slave_next_wdata),
    .burst_wr_direct_strb(sdram_slave_next_wstrb),
    .dbg_io(dbg_io)
);

// ============================================================
// Behavioral SDRAM model — ground truth of what was physically written.
// ============================================================
sdram_model_full sdram_chip (
    .clk(phy_clk), .cke(phy_cke), .cs_n(1'b0),
    .ras_n(phy_ras), .cas_n(phy_cas), .we_n(phy_we),
    .ba(phy_ba), .a(phy_a),
    .dq_in(ctrl_dq_out),
    .dq_out(model_dq_out), .dq_oe(model_dq_oe),
    .dqm(phy_dqm),
    .bd_we(1'b0), .bd_word_addr(24'b0), .bd_wdata(32'b0)
);

// ============================================================
// Observability
// ============================================================
assign dbg_gpu_wq_count = sdram_arb.gpu_wq_count;
assign dbg_gpu_wq_full   = sdram_arb.gpu_wq_full;

endmodule

`default_nettype wire
