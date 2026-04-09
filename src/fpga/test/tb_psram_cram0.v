//
// Verilator testbench for psram_cram0.v (CRAM0 wrapper FSM).
//
// Wires the CRAM0 wrapper to a real psram_cram0_drv driver and to the
// behavioral cram_chip_model — exactly the chain used in core_top.v
// for the CPU's main PSRAM access path. Exposes the word-level
// interface at the top so the C++ harness can drive sequential
// reads/writes and verify FSM correctness.
//
// None of the previously existing tb_psram* tests instantiate
// psram_cram0.v itself — they cover axi_psram_slave (tb_psram) and
// psram_controller (tb_psram_full), but not the wrapper that
// core_top actually uses on the FPGA build. Edits to psram_cram0.v
// or its driver should run this test first to catch FSM bugs before
// burning a Quartus build + Pocket round trip.
//

`default_nettype none

module tb_psram_cram0 (
    input wire clk,
    input wire reset_n,

    // Word-level interface (driven by the C++ harness)
    input  wire        word_rd,
    input  wire        word_wr,
    input  wire [21:0] word_addr,
    input  wire [31:0] word_data,
    input  wire [3:0]  word_wstrb,
    output wire [31:0] word_q,
    output wire        word_busy,
    output wire        word_q_valid,

    // Error / status from the chip model
    output wire [15:0] cram_errors
);

// CRAM0 physical pins (split DQ for Verilator)
wire [21:16] cram_a;
wire [15:0]  cram_ctrl_dq_out;   // controller → chip
wire         cram_ctrl_dq_oe;
wire [15:0]  cram_chip_dq_out;   // chip → controller
wire [15:0]  cram_dq_to_ctrl;    // resolved DQ presented to the controller

wire         cram_wait;
wire         cram_clk;
wire         cram_adv_n, cram_cre;
wire         cram_ce0_n, cram_ce1_n;
wire         cram_oe_n, cram_we_n;
wire         cram_ub_n, cram_lb_n;

// DQ bus resolution: controller drives when oe=1, chip drives otherwise.
assign cram_dq_to_ctrl = cram_ctrl_dq_oe ? cram_ctrl_dq_out : cram_chip_dq_out;

// CRAM0 wrapper under test (sed-wrapped variant with split DQ).
psram_cram0_test #(
    .CLOCK_SPEED(48.0)
) dut (
    .clk(clk),
    .reset_n(reset_n),

    .word_rd(word_rd),
    .word_wr(word_wr),
    .word_addr(word_addr),
    .word_data(word_data),
    .word_wstrb(word_wstrb),
    .word_q(word_q),
    .word_busy(word_busy),
    .word_q_valid(word_q_valid),

    .cram_a(cram_a),
    .cram_dq_in(cram_dq_to_ctrl),
    .cram_dq_out(cram_ctrl_dq_out),
    .cram_dq_oe(cram_ctrl_dq_oe),
    .cram_wait(cram_wait),
    .cram_clk(cram_clk),
    .cram_adv_n(cram_adv_n),
    .cram_cre(cram_cre),
    .cram_ce0_n(cram_ce0_n),
    .cram_ce1_n(cram_ce1_n),
    .cram_oe_n(cram_oe_n),
    .cram_we_n(cram_we_n),
    .cram_ub_n(cram_ub_n),
    .cram_lb_n(cram_lb_n)
);

// Behavioral CRAM chip model. cram_clk feeds the same edge as clk —
// psram_cram0 only does async ops, so the sync-burst phase offset is
// irrelevant for this test.
cram_chip_model #(
    .POWERUP_CYCLES(8'd16)
) chip (
    .clk(clk),
    .cram_clk(clk),
    .reset_n(reset_n),
    .cram_a(cram_a),
    .cram_dq_in(cram_ctrl_dq_oe ? cram_ctrl_dq_out : 16'h0),
    .cram_dq_out(cram_chip_dq_out),
    .cram_dq_oe(cram_ctrl_dq_oe),
    .cram_wait_out(cram_wait),
    .cram_adv_n(cram_adv_n),
    .cram_cre(cram_cre),
    .cram_ce0_n(cram_ce0_n),
    .cram_ce1_n(cram_ce1_n),
    .cram_oe_n(cram_oe_n),
    .cram_we_n(cram_we_n),
    .cram_ub_n(cram_ub_n),
    .cram_lb_n(cram_lb_n),
    .cram_wr_strobe(1'b0),
    .error_count(cram_errors)
);

endmodule
