//
// Verilator testbench for the new burst-read interface on
// cram1_controller.v.  Wires the real DUT to the behavioral
// cram_chip_model and exposes word + burst interfaces at the top so a
// C++ harness can:
//   1. Observe the internal BCR init FSM run to completion
//      (bcr_init_done rises, both dies configured).
//   2. Pre-seed the chip with a known pattern via the chip model's
//      backdoor port.
//   3. Issue a 16-word burst_rd and verify burst_q / burst_q_valid
//      deliver the expected values in order, with burst_busy held
//      throughout.
//   4. Verify the unchanged async word_rd / word_wr paths still work
//      with the chip in BCR sync-burst mode (that's the cram0 trick —
//      async ADV#/OE# reads still return valid data).
//

`default_nettype none

module tb_cram1_burst (
    input wire clk,
    input wire reset_n,

    // Word-level interface
    input  wire        word_rd,
    input  wire        word_wr,
    input  wire [21:0] word_addr,
    input  wire [31:0] word_data,
    input  wire [3:0]  word_wstrb,
    output wire [31:0] word_q,
    output wire        word_busy,
    output wire        word_q_valid,

    // Burst-read interface
    input  wire        burst_rd,
    input  wire [21:0] burst_addr,
    input  wire [4:0]  burst_len,
    output wire [31:0] burst_q,
    output wire        burst_q_valid,
    output wire        burst_busy,

    // Init status (for BCR-init observation)
    output wire        bcr_init_done,

    // Backdoor write into the chip model (C++ harness preloads test
    // vectors before issuing bursts).
    input  wire        bd_we,
    input  wire [20:0] bd_word_addr,
    input  wire [31:0] bd_wdata_word,

    // Error count from the chip model (pre-existing assertion hooks).
    output wire [15:0] cram_errors
);

wire [21:16] cram_a;
wire [15:0]  cram_ctrl_dq_out;
wire         cram_ctrl_dq_oe;
wire [15:0]  cram_chip_dq_out;
wire [15:0]  cram_dq_to_ctrl = cram_ctrl_dq_oe ? cram_ctrl_dq_out : cram_chip_dq_out;

wire cram_wait;
wire cram_clk;
wire cram_adv_n, cram_cre;
wire cram_ce0_n, cram_ce1_n;
wire cram_oe_n, cram_we_n;
wire cram_ub_n, cram_lb_n;

cram1_controller #(
    .CLOCK_SPEED(100.0)
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

    .burst_rd(burst_rd),
    .burst_addr(burst_addr),
    .burst_len(burst_len),
    .burst_q(burst_q),
    .burst_q_valid(burst_q_valid),
    .burst_busy(burst_busy),

    .bcr_init_done(bcr_init_done),

    .cram_a(cram_a),
    .cram_dq_out(cram_ctrl_dq_out),
    .cram_dq_oe(cram_ctrl_dq_oe),
    .cram_dq_in(cram_dq_to_ctrl),
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

// cram_clk tied to clk — controller drives async paths and sync burst
// state machine off the same edge; phase shift is irrelevant in sim.
// POWERUP_CYCLES kept short so the chip-model powerup window closes
// before the controller's BCR init starts pulsing config_en (otherwise
// the pre-existing adv_n-vs-cram_cre sampling race in the chip model
// fires cosmetic "command before power-up stable" warnings).
cram_chip_model #(
    .POWERUP_CYCLES(8'd4)
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
    .bd_we(bd_we),
    .bd_word_addr(bd_word_addr),
    .bd_wdata_word(bd_wdata_word),
    .error_count(cram_errors)
);

endmodule
