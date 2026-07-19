//------------------------------------------------------------------------------
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileType: SOURCE
// SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
//------------------------------------------------------------------------------

/*
 * MiSTer hps_mouse Verilator testbench.
 *
 * Thin wrapper: the unit under test is hps_mouse alone (no sys/ framework);
 * the C++ harness plays hps_io by composing ps2_mouse packets (bit 24
 * toggle, PS/2 flags/dx/dy bytes) and checks the APF cont4_* encoding
 * against a software model.  Coverage (see tb_hps_mouse_main.cpp):
 * toggle-gated updates, signed/overflow delta decode with Y inversion,
 * free-running 16-bit wraparound accumulation, per-packet report counter,
 * button mapping (incl. ext buttons), type nibble latch, reset.
 */

`timescale 1ns/1ps
`default_nettype none

module tb_hps_mouse (
    input  wire        clk,
    input  wire        reset_n,

    input  wire [24:0] ps2_mouse,
    input  wire [15:0] ps2_mouse_ext,

    output wire [31:0] cont4_key,
    output wire [31:0] cont4_joy,
    output wire [15:0] cont4_trig
);

hps_mouse dut (
    .clk           (clk),
    .reset_n       (reset_n),
    .ps2_mouse     (ps2_mouse),
    .ps2_mouse_ext (ps2_mouse_ext),
    .cont4_key     (cont4_key),
    .cont4_joy     (cont4_joy),
    .cont4_trig    (cont4_trig)
);

endmodule
