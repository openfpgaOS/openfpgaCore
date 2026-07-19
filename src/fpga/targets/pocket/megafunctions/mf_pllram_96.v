`timescale 1ns/10ps

// 96 MHz variant of mf_pllram_133 for reduced-clock CPU/RAM variants
// (INCLUDE_CLK96 — see variants/os20.mk).  Same ports and instance role;
// only the output frequencies and the SDRAM chip-clock phase change.
// Phase shifts preserve the DEGREE relationship of the 100 MHz tuning:
//   outclk_1: 243.0° = 6750 ps @ 100 MHz -> 7031 ps @ 96 MHz (10.4167 ns)
//   outclk_2: 198.0° = 5500 ps @ 100 MHz -> 5729 ps @ 96 MHz (unconnected
//             since the v2 memory arch; scaled for consistency only)
// The SDRAM refresh divider (io_sdram.v, 736 cycles = 7.36 us @ 100 MHz)
// stays legal at 96 MHz: 736 x 10.4167 ns = 7.67 us < tREFI 7.8125 us
// (margin 5.8% -> 1.9%).  Below ~94 MHz it would violate — rescale it
// before any deeper clock drop.

module mf_pllram_96(
    input  wire refclk,
    input  wire rst,
    output wire outclk_0,
    output wire outclk_1,
    output wire outclk_2,
    output wire locked
);

    wire unused_outclk3;
    wire unused_outclk4;

    altera_pll #(
        .fractional_vco_multiplier("true"),
        .reference_clock_frequency("74.25 MHz"),
        .operation_mode("normal"),
        .number_of_clocks(5),
        .output_clock_frequency0("96.000000 MHz"),
        .phase_shift0("0 ps"),
        .duty_cycle0(50),
        .output_clock_frequency1("96.000000 MHz"),
        .phase_shift1("7031 ps"),
        .duty_cycle1(50),
        .output_clock_frequency2("96.000000 MHz"),
        .phase_shift2("5729 ps"),
        .duty_cycle2(50),
        .output_clock_frequency3("0 MHz"),
        .phase_shift3("0 ps"),
        .duty_cycle3(50),
        .output_clock_frequency4("0 MHz"),
        .phase_shift4("0 ps"),
        .duty_cycle4(50),
        .pll_type("General"),
        .pll_subtype("General")
    ) altera_pll_i (
        .rst    (rst),
        .outclk ({unused_outclk4, unused_outclk3, outclk_2, outclk_1, outclk_0}),
        .locked (locked),
        .fboutclk (),
        .fbclk  (1'b0),
        .refclk (refclk)
    );

endmodule
