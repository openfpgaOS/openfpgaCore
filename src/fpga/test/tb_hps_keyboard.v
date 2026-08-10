//------------------------------------------------------------------------------
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileType: SOURCE
// SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
//------------------------------------------------------------------------------

/*
 * MiSTer hps_keyboard Verilator testbench.
 *
 * Thin wrapper: the unit under test is hps_keyboard alone (no sys/ framework);
 * the C++ harness plays hps_io by composing ps2_key events (bit 10 toggle,
 * pressed/extended flags, set-2 scancode) and checks the APF cont3_* HID boot
 * report against a software model.  Coverage (see tb_hps_keyboard_main.cpp):
 * toggle-gated updates, set-2 → HID usage translation, extended/non-extended
 * scancode collisions (0x14 Ctrl, 0x75 Up vs KP8, 0x77 Pause vs NumLock),
 * modifier bitmap vs rollover separation, 6-key rollover fill/free/drop,
 * typematic-repeat suppression, unmapped-key rejection, reset.
 */

`timescale 1ns/1ps
`default_nettype none

module tb_hps_keyboard (
    input  wire        clk,
    input  wire        reset_n,

    input  wire [10:0] ps2_key,

    output wire [31:0] cont3_key,
    output wire [31:0] cont3_joy,
    output wire [15:0] cont3_trig
);

hps_keyboard dut (
    .clk        (clk),
    .reset_n    (reset_n),
    .ps2_key    (ps2_key),
    .cont3_key  (cont3_key),
    .cont3_joy  (cont3_joy),
    .cont3_trig (cont3_trig)
);

endmodule

`default_nettype wire
