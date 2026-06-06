//------------------------------------------------------------------------------
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileType: SOURCE
// SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
//------------------------------------------------------------------------------

// Core PLLs for the MiSTer target (refclk = FPGA_CLK1_50 via sys_top).
//
// Two PLLs because 100 MHz and 24.576 MHz share no legal VCO on one
// (24.576/100 = 768/3125 — the common multiple is far outside the
// Cyclone V VCO range; the Pocket likewise sources video from its own
// PLL).  pll_sys is integer-mode; pll_vid is fractional.
//
//   pll_sys outclk_0  100.000 MHz @ 0 ps     clk_cpu / clk_ram_controller
//   pll_sys outclk_1  100.000 MHz @ 6750 ps  clk_ram_chip (SDRAM CK — the
//                                            Pocket's shipping phase;
//                                            re-tune on hardware if read
//                                            capture is marginal)
//   pll_vid outclk_0   24.576 MHz @ 0 ps     clk_vid (scanout pixel clock)
//
// The pll_sys hierarchy deliberately matches MiSTer's generated pll
// wrappers (pll → pll_inst → altera_pll_i): sys_top.sdc's exclusive
// clock-group wildcard `*|pll|pll_inst|altera_pll_i|*[*].*|divclk` must
// match these clocks.  Instantiate pll_sys with the name `pll`; pll_vid
// gets its own async group in mister.sdc.

`timescale 1ns/10ps

module pll_sys (
    input  wire refclk,
    input  wire rst,
    output wire outclk_0,
    output wire outclk_1,
    output wire locked
);

    pll_sys_0002 pll_inst (
        .refclk   (refclk),
        .rst      (rst),
        .outclk_0 (outclk_0),
        .outclk_1 (outclk_1),
        .locked   (locked)
    );

endmodule

module pll_sys_0002 (
    input  wire refclk,
    input  wire rst,
    output wire outclk_0,
    output wire outclk_1,
    output wire locked
);

    wire unused_outclk2;
    wire unused_outclk3;
    wire unused_outclk4;

    altera_pll #(
        .fractional_vco_multiplier("false"),
        .reference_clock_frequency("50.0 MHz"),
        .operation_mode("normal"),
        .number_of_clocks(5),
        .output_clock_frequency0("100.000000 MHz"),
        .phase_shift0("0 ps"),
        .duty_cycle0(50),
        .output_clock_frequency1("100.000000 MHz"),
        .phase_shift1("6750 ps"),
        .duty_cycle1(50),
        .output_clock_frequency2("0 MHz"),
        .phase_shift2("0 ps"),
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
        .outclk ({unused_outclk4, unused_outclk3, unused_outclk2, outclk_1, outclk_0}),
        .locked (locked),
        .fboutclk (),
        .fbclk  (1'b0),
        .refclk (refclk)
    );

endmodule

module pll_vid (
    input  wire refclk,
    input  wire rst,
    output wire outclk_0,
    output wire locked
);

    pll_vid_0002 pll_inst (
        .refclk   (refclk),
        .rst      (rst),
        .outclk_0 (outclk_0),
        .locked   (locked)
    );

endmodule

module pll_vid_0002 (
    input  wire refclk,
    input  wire rst,
    output wire outclk_0,
    output wire locked
);

    wire unused_outclk1;
    wire unused_outclk2;
    wire unused_outclk3;
    wire unused_outclk4;

    altera_pll #(
        .fractional_vco_multiplier("true"),
        .reference_clock_frequency("50.0 MHz"),
        .operation_mode("normal"),
        .number_of_clocks(5),
        .output_clock_frequency0("24.576000 MHz"),
        .phase_shift0("0 ps"),
        .duty_cycle0(50),
        .output_clock_frequency1("0 MHz"),
        .phase_shift1("0 ps"),
        .duty_cycle1(50),
        .output_clock_frequency2("0 MHz"),
        .phase_shift2("0 ps"),
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
        .outclk ({unused_outclk4, unused_outclk3, unused_outclk2, unused_outclk1, outclk_0}),
        .locked (locked),
        .fboutclk (),
        .fbclk  (1'b0),
        .refclk (refclk)
    );

endmodule
