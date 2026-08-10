//------------------------------------------------------------------------------
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileType: SOURCE
// SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
//------------------------------------------------------------------------------

//
// hps_keyboard — MiSTer USB keyboard → APF input-hub slot 2 (cont3_*).
//
// Consumes hps_io's ps2_key event stream (same clk_sys = clk_cpu domain, so no
// CDC here; axi_periph_slave 2FF-syncs every cont bus internally) and rebuilds
// a USB HID BOOT KEYBOARD REPORT in the exact shape the Pocket dock delivers,
// so firmware decodes both targets with one code path:
//
//   cont3_key [31:28] = 4 (OF_INPUT_TYPE_KEYBOARD) once a key has been seen
//   cont3_key [7:0]   = HID modifier bitmap
//                       {RGui,RAlt,RShift,RCtrl,LGui,LAlt,LShift,LCtrl}
//   cont3_joy [31:24] = report_keys[0]   cont3_joy [23:16] = report_keys[1]
//   cont3_joy [15:8]  = report_keys[2]   cont3_joy [7:0]   = report_keys[3]
//   cont3_trig[15:8]  = report_keys[4]   cont3_trig[7:0]   = report_keys[5]
//
// WHY THE TRANSLATION LIVES HERE, NOT IN FIRMWARE: slot 2 is a SHARED register
// contract carrying HID usage IDs (of_input.h hands apps `usage` directly, and
// of_keyboard_key() indexes a 256-bit map by it).  Publishing raw PS/2 set-2
// scancodes into that slot would make the same register mean two different
// things per target.  Keeping the RTL honest to the contract is what lets
// targets/mister/input.c reuse the pocket decoder verbatim -- and it needs no
// new sysreg, so axi_periph_slave.v (shared with Pocket, where os30 has ~300
// spare ALMs) is untouched by keyboard support.
//
// hps_io event contract: ps2_key[10] TOGGLES once per make/break -- it is not
// a strobe, so edges are tracked, not levels.  [9] = pressed, [8] = extended
// (E0 prefix), [7:0] = set-2 scancode.  hps_io pre-folds the two multi-byte
// oddballs into that shape: PrintScreen arrives as extended 0x7C and Pause as
// extended 0x77 (hps_io.sv:307-309).
//
// Keys are held in a 6-slot rollover set, exactly like a real boot keyboard:
// a make lands in the first free slot (ignored if already held, so the PS/2
// typematic repeat stream does not consume slots), a break clears its slot.
// A 7th simultaneous key is dropped rather than evicting a held one.
//
// Modifiers do NOT occupy rollover slots -- they live in the separate bitmap,
// per the HID boot report.
//
`default_nettype none

module hps_keyboard (
    input  wire        clk,
    input  wire        reset_n,

    // ── hps_io key stream: {toggle, pressed, extended, scancode[7:0]} ──
    input  wire [10:0] ps2_key,

    // ── APF cont3 (input-hub slot 2) ────────────────────────────────
    output wire [31:0] cont3_key,
    output wire [31:0] cont3_joy,
    output wire [15:0] cont3_trig
);

wire       ev_pressed = ps2_key[9];
wire       ev_ext     = ps2_key[8];
wire [7:0] ev_code    = ps2_key[7:0];

// ── PS/2 set-2 → HID usage (non-modifier keys; 0 = unmapped) ────────
function [7:0] ps2_to_hid;
    input       ext;
    input [7:0] code;
    begin
        ps2_to_hid = 8'h00;
        if (!ext) begin
            case (code)
                // letters
                8'h1C: ps2_to_hid = 8'h04; // A
                8'h32: ps2_to_hid = 8'h05; // B
                8'h21: ps2_to_hid = 8'h06; // C
                8'h23: ps2_to_hid = 8'h07; // D
                8'h24: ps2_to_hid = 8'h08; // E
                8'h2B: ps2_to_hid = 8'h09; // F
                8'h34: ps2_to_hid = 8'h0A; // G
                8'h33: ps2_to_hid = 8'h0B; // H
                8'h43: ps2_to_hid = 8'h0C; // I
                8'h3B: ps2_to_hid = 8'h0D; // J
                8'h42: ps2_to_hid = 8'h0E; // K
                8'h4B: ps2_to_hid = 8'h0F; // L
                8'h3A: ps2_to_hid = 8'h10; // M
                8'h31: ps2_to_hid = 8'h11; // N
                8'h44: ps2_to_hid = 8'h12; // O
                8'h4D: ps2_to_hid = 8'h13; // P
                8'h15: ps2_to_hid = 8'h14; // Q
                8'h2D: ps2_to_hid = 8'h15; // R
                8'h1B: ps2_to_hid = 8'h16; // S
                8'h2C: ps2_to_hid = 8'h17; // T
                8'h3C: ps2_to_hid = 8'h18; // U
                8'h2A: ps2_to_hid = 8'h19; // V
                8'h1D: ps2_to_hid = 8'h1A; // W
                8'h22: ps2_to_hid = 8'h1B; // X
                8'h35: ps2_to_hid = 8'h1C; // Y
                8'h1A: ps2_to_hid = 8'h1D; // Z
                // digits row
                8'h16: ps2_to_hid = 8'h1E; // 1
                8'h1E: ps2_to_hid = 8'h1F; // 2
                8'h26: ps2_to_hid = 8'h20; // 3
                8'h25: ps2_to_hid = 8'h21; // 4
                8'h2E: ps2_to_hid = 8'h22; // 5
                8'h36: ps2_to_hid = 8'h23; // 6
                8'h3D: ps2_to_hid = 8'h24; // 7
                8'h3E: ps2_to_hid = 8'h25; // 8
                8'h46: ps2_to_hid = 8'h26; // 9
                8'h45: ps2_to_hid = 8'h27; // 0
                // control / punctuation
                8'h5A: ps2_to_hid = 8'h28; // Enter
                8'h76: ps2_to_hid = 8'h29; // Escape
                8'h66: ps2_to_hid = 8'h2A; // Backspace
                8'h0D: ps2_to_hid = 8'h2B; // Tab
                8'h29: ps2_to_hid = 8'h2C; // Space
                8'h4E: ps2_to_hid = 8'h2D; // - _
                8'h55: ps2_to_hid = 8'h2E; // = +
                8'h54: ps2_to_hid = 8'h2F; // [ {
                8'h5B: ps2_to_hid = 8'h30; // ] }
                8'h5D: ps2_to_hid = 8'h31; // \ |
                8'h4C: ps2_to_hid = 8'h33; // ; :
                8'h52: ps2_to_hid = 8'h34; // ' "
                8'h0E: ps2_to_hid = 8'h35; // ` ~
                8'h41: ps2_to_hid = 8'h36; // , <
                8'h49: ps2_to_hid = 8'h37; // . >
                8'h4A: ps2_to_hid = 8'h38; // / ?
                8'h58: ps2_to_hid = 8'h39; // CapsLock
                // function row
                8'h05: ps2_to_hid = 8'h3A; // F1
                8'h06: ps2_to_hid = 8'h3B; // F2
                8'h04: ps2_to_hid = 8'h3C; // F3
                8'h0C: ps2_to_hid = 8'h3D; // F4
                8'h03: ps2_to_hid = 8'h3E; // F5
                8'h0B: ps2_to_hid = 8'h3F; // F6
                8'h83: ps2_to_hid = 8'h40; // F7
                8'h0A: ps2_to_hid = 8'h41; // F8
                8'h01: ps2_to_hid = 8'h42; // F9
                8'h09: ps2_to_hid = 8'h43; // F10
                8'h78: ps2_to_hid = 8'h44; // F11
                8'h07: ps2_to_hid = 8'h45; // F12
                8'h7E: ps2_to_hid = 8'h47; // ScrollLock
                // keypad
                8'h77: ps2_to_hid = 8'h53; // NumLock
                8'h7C: ps2_to_hid = 8'h55; // KP *
                8'h7B: ps2_to_hid = 8'h56; // KP -
                8'h79: ps2_to_hid = 8'h57; // KP +
                8'h69: ps2_to_hid = 8'h59; // KP 1
                8'h72: ps2_to_hid = 8'h5A; // KP 2
                8'h7A: ps2_to_hid = 8'h5B; // KP 3
                8'h6B: ps2_to_hid = 8'h5C; // KP 4
                8'h73: ps2_to_hid = 8'h5D; // KP 5
                8'h74: ps2_to_hid = 8'h5E; // KP 6
                8'h6C: ps2_to_hid = 8'h5F; // KP 7
                8'h75: ps2_to_hid = 8'h60; // KP 8
                8'h7D: ps2_to_hid = 8'h61; // KP 9
                8'h70: ps2_to_hid = 8'h62; // KP 0
                8'h71: ps2_to_hid = 8'h63; // KP .
                8'h61: ps2_to_hid = 8'h64; // ISO \ (102nd key)
                default: ps2_to_hid = 8'h00;
            endcase
        end else begin
            case (code)
                8'h5A: ps2_to_hid = 8'h58; // KP Enter
                8'h4A: ps2_to_hid = 8'h54; // KP /
                8'h7C: ps2_to_hid = 8'h46; // PrintScreen (hps_io-folded)
                8'h77: ps2_to_hid = 8'h48; // Pause       (hps_io-folded)
                8'h70: ps2_to_hid = 8'h49; // Insert
                8'h6C: ps2_to_hid = 8'h4A; // Home
                8'h7D: ps2_to_hid = 8'h4B; // PageUp
                8'h71: ps2_to_hid = 8'h4C; // Delete
                8'h69: ps2_to_hid = 8'h4D; // End
                8'h7A: ps2_to_hid = 8'h4E; // PageDown
                8'h74: ps2_to_hid = 8'h4F; // Right
                8'h6B: ps2_to_hid = 8'h50; // Left
                8'h72: ps2_to_hid = 8'h51; // Down
                8'h75: ps2_to_hid = 8'h52; // Up
                8'h2F: ps2_to_hid = 8'h65; // Application / Menu
                default: ps2_to_hid = 8'h00;
            endcase
        end
    end
endfunction

// ── PS/2 set-2 → one-hot HID modifier mask (0 = not a modifier) ─────
function [7:0] ps2_to_mod;
    input       ext;
    input [7:0] code;
    begin
        ps2_to_mod = 8'h00;
        if (!ext) begin
            case (code)
                8'h14: ps2_to_mod = 8'h01; // Left Ctrl
                8'h12: ps2_to_mod = 8'h02; // Left Shift
                8'h11: ps2_to_mod = 8'h04; // Left Alt
                8'h59: ps2_to_mod = 8'h20; // Right Shift
                default: ps2_to_mod = 8'h00;
            endcase
        end else begin
            case (code)
                8'h1F: ps2_to_mod = 8'h08; // Left Gui
                8'h14: ps2_to_mod = 8'h10; // Right Ctrl
                8'h11: ps2_to_mod = 8'h40; // Right Alt
                8'h27: ps2_to_mod = 8'h80; // Right Gui
                default: ps2_to_mod = 8'h00;
            endcase
        end
    end
endfunction

wire [7:0] ev_usage = ps2_to_hid(ev_ext, ev_code);
wire [7:0] ev_mod   = ps2_to_mod(ev_ext, ev_code);

reg       stb_prev;
reg       seen;
reg [7:0] modifiers;
reg [7:0] k0, k1, k2, k3, k4, k5;

wire ev_new = (ps2_key[10] != stb_prev);

// Already held?  A make for a held key is a typematic repeat -- drop it.
wire hit0 = (k0 == ev_usage);
wire hit1 = (k1 == ev_usage);
wire hit2 = (k2 == ev_usage);
wire hit3 = (k3 == ev_usage);
wire hit4 = (k4 == ev_usage);
wire hit5 = (k5 == ev_usage);
wire held = hit0 | hit1 | hit2 | hit3 | hit4 | hit5;

// First free slot (priority encode; all-full drops the key).
wire free0 = (k0 == 8'h00);
wire free1 = (k1 == 8'h00);
wire free2 = (k2 == 8'h00);
wire free3 = (k3 == 8'h00);
wire free4 = (k4 == 8'h00);
wire free5 = (k5 == 8'h00);
wire put0 =  free0;
wire put1 = !free0 &  free1;
wire put2 = !free0 & !free1 &  free2;
wire put3 = !free0 & !free1 & !free2 &  free3;
wire put4 = !free0 & !free1 & !free2 & !free3 &  free4;
wire put5 = !free0 & !free1 & !free2 & !free3 & !free4 & free5;

wire do_make  = ev_new &&  ev_pressed && (ev_usage != 8'h00) && !held;
wire do_break = ev_new && !ev_pressed && (ev_usage != 8'h00);

always @(posedge clk or negedge reset_n) begin
    if (!reset_n) begin
        stb_prev  <= 1'b0;
        seen      <= 1'b0;
        modifiers <= 8'h00;
        k0        <= 8'h00;
        k1        <= 8'h00;
        k2        <= 8'h00;
        k3        <= 8'h00;
        k4        <= 8'h00;
        k5        <= 8'h00;
    end else begin
        stb_prev <= ps2_key[10];

        if (ev_new) begin
            seen <= 1'b1;

            if (ev_mod != 8'h00)
                modifiers <= ev_pressed ? (modifiers |  ev_mod)
                                        : (modifiers & ~ev_mod);
        end

        if (do_make) begin
            if (put0) k0 <= ev_usage;
            if (put1) k1 <= ev_usage;
            if (put2) k2 <= ev_usage;
            if (put3) k3 <= ev_usage;
            if (put4) k4 <= ev_usage;
            if (put5) k5 <= ev_usage;
        end

        if (do_break) begin
            if (hit0) k0 <= 8'h00;
            if (hit1) k1 <= 8'h00;
            if (hit2) k2 <= 8'h00;
            if (hit3) k3 <= 8'h00;
            if (hit4) k4 <= 8'h00;
            if (hit5) k5 <= 8'h00;
        end
    end
end

assign cont3_key  = {seen ? 4'h4 : 4'h0, 12'h000, 8'h00, modifiers};
assign cont3_joy  = {k0, k1, k2, k3};
assign cont3_trig = {k4, k5};

endmodule
