//
// Verilator Testbench: SRAM Controller
//
// Instantiates: sram_controller -> sram_chip_model
// Tests the async SRAM timing FSM with real RTL controller
// and behavioral chip model. No inout splitting needed —
// sram_controller already has separate dq_in/dq_out/dq_oe ports.
//

`default_nettype none

module tb_sram (
    input  wire        clk,
    input  wire        reset_n,

    // Word interface (driven from C++)
    input  wire        word_rd,
    input  wire        word_wr,
    input  wire        word_rd_half,
    input  wire        word_rd_hi,
    input  wire [21:0] word_addr,
    input  wire [31:0] word_data,
    input  wire [3:0]  word_wstrb,
    output wire [31:0] word_q,
    output wire        word_busy,
    output wire        word_q_valid
);

// Physical SRAM signals
wire [16:0] sram_a;
wire [15:0] sram_dq_out;  // controller -> chip (writes)
wire [15:0] sram_dq_in;   // chip -> controller (reads)
wire        sram_dq_oe;
wire        sram_oe_n;
wire        sram_we_n;
wire        sram_ub_n;
wire        sram_lb_n;

// SRAM controller (real RTL)
sram_controller #(
    .WAIT_CYCLES(6)
) ctrl (
    .clk(clk), .reset_n(reset_n),
    .word_rd(word_rd), .word_wr(word_wr),
    .word_rd_half(word_rd_half), .word_rd_hi(word_rd_hi),
    .word_addr(word_addr), .word_data(word_data), .word_wstrb(word_wstrb),
    .word_q(word_q), .word_busy(word_busy), .word_q_valid(word_q_valid),
    .sram_a(sram_a),
    .sram_dq_out(sram_dq_out),
    .sram_dq_in(sram_dq_in),
    .sram_dq_oe(sram_dq_oe),
    .sram_oe_n(sram_oe_n),
    .sram_we_n(sram_we_n),
    .sram_ub_n(sram_ub_n),
    .sram_lb_n(sram_lb_n)
);

// Behavioral SRAM chip model
sram_chip_model chip (
    .clk(clk),
    .sram_a(sram_a),
    .sram_dq_in(sram_dq_out),   // controller write data -> model input
    .sram_dq_out(sram_dq_in),   // model output -> controller read data
    .sram_oe_n(sram_oe_n),
    .sram_we_n(sram_we_n),
    .sram_ub_n(sram_ub_n),
    .sram_lb_n(sram_lb_n)
);

endmodule
