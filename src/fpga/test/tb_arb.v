//
// Verilator Testbench: Multi-Master SDRAM Arbiter Stress Test
//
// Exercises word_sdram_arbiter with all 4 masters active simultaneously:
//   M0 (Audio DMA): single-beat reads
//   M1 (DMA engine): burst reads + burst writes
//   M2 (CPU): burst reads + single writes
//   M3 (Bridge): single-beat writes
//
// Tests: priority enforcement, data integrity under contention,
//        back-to-back transactions, burst interleaving.
//

`default_nettype none

module tb_arb (
    input wire clk,
    input wire reset_n,

    // M0: Audio DMA (read-only, single-beat)
    input  wire        m0_rd,
    input  wire [23:0] m0_addr,
    input  wire [3:0]  m0_burst_len,
    output wire [31:0] m0_rdata,
    output wire        m0_busy,
    output wire        m0_accepted,
    output wire        m0_rdata_valid,

    // M1: DMA engine (read + write, burst)
    input  wire        m1_rd,
    input  wire        m1_wr,
    input  wire [23:0] m1_addr,
    input  wire [31:0] m1_wdata,
    input  wire [3:0]  m1_wstrb,
    input  wire [3:0]  m1_burst_len,
    input  wire [3:0]  m1_burst_wr_len,
    output wire [31:0] m1_rdata,
    output wire        m1_busy,
    output wire        m1_accepted,
    output wire        m1_rdata_valid,
    output wire        m1_wr_data_next,

    // M2: CPU (read + write, burst)
    input  wire        m2_rd,
    input  wire        m2_wr,
    input  wire [23:0] m2_addr,
    input  wire [31:0] m2_wdata,
    input  wire [3:0]  m2_wstrb,
    input  wire [3:0]  m2_burst_len,
    input  wire [3:0]  m2_burst_wr_len,
    output wire [31:0] m2_rdata,
    output wire        m2_busy,
    output wire        m2_accepted,
    output wire        m2_rdata_valid,
    output wire        m2_wr_data_next,

    // M3: Bridge (write-only, single-beat)
    input  wire        m3_wr,
    input  wire [23:0] m3_addr,
    input  wire [31:0] m3_wdata,
    input  wire [3:0]  m3_wstrb,
    output wire        m3_busy,
    output wire        m3_accepted,

    // SDRAM backdoor
    input  wire        bd_we,
    input  wire [23:0] bd_addr,
    input  wire [31:0] bd_wdata
);

// Arbiter → SDRAM model
wire        arb_rd, arb_wr;
wire [23:0] arb_addr;
wire [31:0] arb_wdata;
wire [3:0]  arb_wstrb;
wire [3:0]  arb_burst_len, arb_burst_wr_len;
wire [31:0] sdram_q;
wire        sdram_busy, sdram_q_valid, sdram_wr_data_next;
wire [31:0] arb_wdata_direct;
wire [3:0]  arb_wstrb_direct;

word_sdram_arbiter arb (
    .clk(clk), .reset_n(reset_n),
    // M0
    .m0_rd(m0_rd), .m0_addr(m0_addr), .m0_burst_len(m0_burst_len),
    .m0_rdata(m0_rdata), .m0_busy(m0_busy),
    .m0_accepted(m0_accepted), .m0_rdata_valid(m0_rdata_valid),
    // M1
    .m1_rd(m1_rd), .m1_wr(m1_wr),
    .m1_addr(m1_addr), .m1_wdata(m1_wdata), .m1_wstrb(m1_wstrb),
    .m1_burst_len(m1_burst_len), .m1_burst_wr_len(m1_burst_wr_len),
    .m1_rdata(m1_rdata), .m1_busy(m1_busy),
    .m1_accepted(m1_accepted), .m1_rdata_valid(m1_rdata_valid),
    .m1_wr_data_next(m1_wr_data_next),
    // M2
    .m2_rd(m2_rd), .m2_wr(m2_wr),
    .m2_addr(m2_addr), .m2_wdata(m2_wdata), .m2_wstrb(m2_wstrb),
    .m2_burst_len(m2_burst_len), .m2_burst_wr_len(m2_burst_wr_len),
    .m2_rdata(m2_rdata), .m2_busy(m2_busy),
    .m2_accepted(m2_accepted), .m2_rdata_valid(m2_rdata_valid),
    .m2_wr_data_next(m2_wr_data_next),
    // M3
    .m3_rd(1'b0), .m3_wr(m3_wr),
    .m3_addr(m3_addr), .m3_wdata(m3_wdata), .m3_wstrb(m3_wstrb),
    .m3_burst_len(4'b0), .m3_burst_wr_len(4'b0),
    .m3_rdata(), .m3_busy(m3_busy),
    .m3_accepted(m3_accepted), .m3_rdata_valid(),
    .m3_wr_data_next(),
    // SDRAM
    .sdram_rd(arb_rd), .sdram_wr(arb_wr),
    .sdram_addr(arb_addr), .sdram_wdata(arb_wdata),
    .sdram_wstrb(arb_wstrb),
    .sdram_burst_len(arb_burst_len),
    .sdram_burst_wr_len(arb_burst_wr_len),
    .sdram_rdata(sdram_q), .sdram_busy(sdram_busy),
    .sdram_rdata_valid(sdram_q_valid),
    .sdram_wr_data_next(sdram_wr_data_next),
    .sdram_wdata_direct(arb_wdata_direct),
    .sdram_wstrb_direct(arb_wstrb_direct)
);

sdram_word_model sdram (
    .clk(clk), .reset_n(reset_n),
    .word_rd(arb_rd), .word_wr(arb_wr),
    .word_addr(arb_addr), .word_data(arb_wdata),
    .word_wstrb(arb_wstrb),
    .word_burst_len(arb_burst_len),
    .word_burst_wr_len(arb_burst_wr_len),
    .word_q(sdram_q), .word_busy(sdram_busy),
    .word_q_valid(sdram_q_valid),
    .word_wr_data_next(sdram_wr_data_next),
    .burst_wr_direct_data(arb_wdata_direct),
    .burst_wr_direct_strb(arb_wstrb_direct),
    .bd_we(bd_we), .bd_word_addr(bd_addr), .bd_wdata(bd_wdata)
);

endmodule
