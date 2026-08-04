`timescale 1ns/10ps

// 90 MHz variant of mf_pllram_133 for reduced-clock CPU/RAM variants
// (INCLUDE_CLK90 — see variants/os20.mk; 2026-08-02: os20 moved 96→90 MHz to match os25's near-closure margin — dual-issue wall needs 11.22 ns → ~90.7 MHz true closure).  Same ports and instance role;
// only the output frequencies and the SDRAM chip-clock phase change.
// Phase shifts preserve the DEGREE relationship of the 100 MHz tuning:
//   outclk_1: 243.0° = 6750 ps @ 100 MHz -> 7031 ps @ 96 MHz (10.4167 ns)
//   outclk_2: 198.0° = 5500 ps @ 100 MHz -> 5729 ps @ 96 MHz (unconnected
//             since the v2 memory arch; scaled for consistency only)
// The SDRAM refresh divider is CLOCK-SCALED for this wrapper: core_top
// passes REFRESH_INTERVAL=660 to io_sdram under INCLUDE_CLK90
// (660 x 11.111 ns = 7.33 us < tREFI 7.8125 us, margin 6.1%).  The
// default 736 would violate below ~94 MHz (736 x 11.111 = 8.18 us).

module mf_pllram_90(
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
        .output_clock_frequency0("90.000000 MHz"),
        .phase_shift0("0 ps"),
        .duty_cycle0(50),
        .output_clock_frequency1("90.000000 MHz"),
        .phase_shift1("6667 ps"),  // 216.0 deg (was 243), VCO/8-quantized: -0.8 ns chip phase -- trades write-launch surplus (+1.28) into the DQ read-capture window (-0.38 floor); see sta_dq.tcl + 2026-08-02 forensics
        .duty_cycle1(50),
        .output_clock_frequency2("90.000000 MHz"),
        .phase_shift2("6111 ps"),
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
