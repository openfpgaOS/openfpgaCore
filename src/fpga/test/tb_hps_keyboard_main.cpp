//------------------------------------------------------------------------------
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileType: SOURCE
// SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
//------------------------------------------------------------------------------
//
// Verilator cosim for hps_keyboard (MiSTer USB keyboard → input-hub slot 2).
// The harness plays hps_io: it composes ps2_key events (bit 10 TOGGLES per
// make/break, [9]=pressed, [8]=extended, [7:0]=set-2 scancode) and checks the
// cont3_* encoding against the Pocket dock's HID boot-report contract:
//   key[31:28] = 4 once a key has been seen, key[7:0] = modifier bitmap,
//   joy[31:24..0] = report_keys[0..3], trig[15:8],[7:0] = report_keys[4..5].
//
// Build/run: make hps-keyboard   (test/Makefile target)
//
#include "Vtb_hps_keyboard.h"
#include "verilated.h"
#include <cstdio>
#include <cstdlib>
#include <cstdint>

static Vtb_hps_keyboard *dut;

static void tick() {
    dut->clk = 0; dut->eval();
    dut->clk = 1; dut->eval();
}

static int toggle_state;

// Compose one hps_io key event.  Bit 10 toggles; it is NOT a strobe.
static void send_key(bool pressed, bool ext, uint8_t code) {
    toggle_state ^= 1;
    dut->ps2_key = ((uint32_t)toggle_state << 10) |
                   ((uint32_t)(pressed ? 1 : 0) << 9) |
                   ((uint32_t)(ext ? 1 : 0) << 8) | code;
    tick();
}

static void make_key(bool ext, uint8_t code)  { send_key(true,  ext, code); }
static void break_key(bool ext, uint8_t code) { send_key(false, ext, code); }

// report_keys[i] out of the packed cont3 buses.
static uint8_t slot(int i) {
    uint32_t joy  = dut->cont3_joy;
    uint16_t trig = dut->cont3_trig;
    switch (i) {
    case 0: return (uint8_t)(joy >> 24);
    case 1: return (uint8_t)(joy >> 16);
    case 2: return (uint8_t)(joy >> 8);
    case 3: return (uint8_t)(joy);
    case 4: return (uint8_t)(trig >> 8);
    default: return (uint8_t)(trig);
    }
}

static uint8_t modifiers() { return (uint8_t)(dut->cont3_key & 0xFFu); }
static uint8_t type_nib()  { return (uint8_t)((dut->cont3_key >> 28) & 0xFu); }

static bool slots_are(uint8_t a, uint8_t b, uint8_t c,
                      uint8_t d, uint8_t e, uint8_t f) {
    return slot(0) == a && slot(1) == b && slot(2) == c &&
           slot(3) == d && slot(4) == e && slot(5) == f;
}

// Set-2 scancodes used below (non-extended unless noted).
enum {
    SC_A = 0x1C, SC_B = 0x32, SC_C = 0x21, SC_D = 0x23, SC_E = 0x24,
    SC_F = 0x2B, SC_G = 0x34, SC_W = 0x1D, SC_ESC = 0x76,
    SC_LSHIFT = 0x12, SC_RSHIFT = 0x59, SC_LCTRL = 0x14, SC_LALT = 0x11,
    SC_KP8 = 0x75, SC_NUMLOCK = 0x77, SC_KPSTAR = 0x7C,
    SC_E_UP = 0x75, SC_E_RCTRL = 0x14, SC_E_RALT = 0x11, SC_E_LGUI = 0x1F,
    SC_E_PAUSE = 0x77, SC_E_PRTSCR = 0x7C, SC_E_LEFT = 0x6B
};

// HID usages the above must translate to.
enum {
    HID_A = 0x04, HID_B = 0x05, HID_C = 0x06, HID_D = 0x07, HID_E = 0x08,
    HID_F = 0x09, HID_G = 0x0A, HID_W = 0x1A, HID_ESC = 0x29,
    HID_KP8 = 0x60, HID_NUMLOCK = 0x53, HID_KPSTAR = 0x55,
    HID_UP = 0x52, HID_PAUSE = 0x48, HID_PRTSCR = 0x46, HID_LEFT = 0x50
};

// HID modifier bitmap: {RGui,RAlt,RShift,RCtrl,LGui,LAlt,LShift,LCtrl}.
enum {
    MOD_LCTRL = 0x01, MOD_LSHIFT = 0x02, MOD_LALT = 0x04, MOD_LGUI = 0x08,
    MOD_RCTRL = 0x10, MOD_RSHIFT = 0x20, MOD_RALT = 0x40, MOD_RGUI = 0x80
};

int main(int argc, char **argv) {
    Verilated::commandArgs(argc, argv);
    dut = new Vtb_hps_keyboard;

    int passed = 0, failed = 0;
    auto CHECK = [&](bool c, const char *m) {
        if (c) { passed++; printf("  OK   %s\n", m); }
        else   { failed++; printf("  FAIL %s\n", m); }
    };

    printf("=== hps_keyboard cosim (USB keyboard -> input-hub slot 2) ===\n\n");

    // Reset.
    dut->reset_n = 0; dut->ps2_key = 0;
    for (int i = 0; i < 4; i++) tick();
    dut->reset_n = 1;
    tick();

    // ---- Idle: absent until the first event ----
    CHECK(dut->cont3_key == 0 && dut->cont3_joy == 0 && dut->cont3_trig == 0,
          "before first event: type nibble 0 (absent), slots and modifiers 0");

    // ---- No toggle, no update ----
    dut->ps2_key = (uint32_t)(toggle_state << 10) | (1u << 9) | SC_A;
    for (int i = 0; i < 8; i++) tick();
    CHECK(dut->cont3_joy == 0 && type_nib() == 0,
          "no output change without a ps2_key[10] toggle");

    // ---- First make: type nibble latches, key lands in slot 0 ----
    make_key(false, SC_A);
    CHECK(type_nib() == 4 && slots_are(HID_A, 0, 0, 0, 0, 0) && modifiers() == 0,
          "make A: type nibble 4, usage 0x04 in slot 0, no modifier set");

    // ---- Typematic repeat must not consume a second slot ----
    make_key(false, SC_A);
    make_key(false, SC_A);
    CHECK(slots_are(HID_A, 0, 0, 0, 0, 0),
          "typematic repeat of a held key does not consume another slot");

    // ---- Break frees the slot ----
    break_key(false, SC_A);
    CHECK(slots_are(0, 0, 0, 0, 0, 0) && type_nib() == 4,
          "break A: slot cleared, keyboard stays present");

    // ---- Modifiers live in the bitmap, never in a rollover slot ----
    make_key(false, SC_LSHIFT);
    CHECK(modifiers() == MOD_LSHIFT && slots_are(0, 0, 0, 0, 0, 0),
          "make LShift: modifier bit set, no rollover slot consumed");

    make_key(false, SC_LCTRL);
    CHECK(modifiers() == (MOD_LSHIFT | MOD_LCTRL) && slots_are(0, 0, 0, 0, 0, 0),
          "make LCtrl: modifiers accumulate independently");

    // Non-extended 0x14 is LCtrl; EXTENDED 0x14 is RCtrl — same scancode.
    make_key(true, SC_E_RCTRL);
    CHECK(modifiers() == (MOD_LSHIFT | MOD_LCTRL | MOD_RCTRL),
          "extended 0x14 is RCtrl, distinct from non-extended 0x14 (LCtrl)");

    break_key(false, SC_LCTRL);
    CHECK(modifiers() == (MOD_LSHIFT | MOD_RCTRL),
          "break LCtrl clears only its bit, RCtrl survives");

    make_key(true, SC_E_RALT);
    make_key(true, SC_E_LGUI);
    CHECK(modifiers() == (MOD_LSHIFT | MOD_RCTRL | MOD_RALT | MOD_LGUI),
          "extended 0x11/0x1F map to RAlt/LGui");

    break_key(false, SC_LSHIFT);
    break_key(true, SC_E_RCTRL);
    break_key(true, SC_E_RALT);
    break_key(true, SC_E_LGUI);
    CHECK(modifiers() == 0, "all modifiers released: bitmap back to 0");

    // ---- Extended vs non-extended scancode collisions ----
    make_key(true, SC_E_UP);              // extended 0x75 = Up
    CHECK(slots_are(HID_UP, 0, 0, 0, 0, 0),
          "extended 0x75 -> Up (0x52), not KP8");
    break_key(true, SC_E_UP);

    make_key(false, SC_KP8);              // non-extended 0x75 = KP8
    CHECK(slots_are(HID_KP8, 0, 0, 0, 0, 0),
          "non-extended 0x75 -> KP8 (0x60), not Up");
    break_key(false, SC_KP8);

    make_key(true, SC_E_PAUSE);           // extended 0x77 = Pause (hps_io fold)
    CHECK(slots_are(HID_PAUSE, 0, 0, 0, 0, 0),
          "extended 0x77 -> Pause (0x48), not NumLock");
    break_key(true, SC_E_PAUSE);

    make_key(false, SC_NUMLOCK);          // non-extended 0x77 = NumLock
    CHECK(slots_are(HID_NUMLOCK, 0, 0, 0, 0, 0),
          "non-extended 0x77 -> NumLock (0x53), not Pause");
    break_key(false, SC_NUMLOCK);

    make_key(true, SC_E_PRTSCR);          // extended 0x7C = PrintScreen
    CHECK(slots_are(HID_PRTSCR, 0, 0, 0, 0, 0),
          "extended 0x7C -> PrintScreen (0x46), not KP*");
    break_key(true, SC_E_PRTSCR);

    make_key(false, SC_KPSTAR);           // non-extended 0x7C = KP*
    CHECK(slots_are(HID_KPSTAR, 0, 0, 0, 0, 0),
          "non-extended 0x7C -> KP* (0x55), not PrintScreen");
    break_key(false, SC_KPSTAR);

    // ---- Unmapped scancodes are ignored entirely ----
    make_key(false, 0xAA);
    CHECK(slots_are(0, 0, 0, 0, 0, 0) && modifiers() == 0,
          "unmapped scancode consumes no slot and sets no modifier");
    break_key(false, 0xAA);

    // ---- 6-key rollover: fill every slot in order ----
    make_key(false, SC_A);
    make_key(false, SC_B);
    make_key(false, SC_C);
    make_key(false, SC_D);
    make_key(false, SC_E);
    make_key(false, SC_F);
    CHECK(slots_are(HID_A, HID_B, HID_C, HID_D, HID_E, HID_F),
          "six simultaneous keys fill slots 0..5 in press order");

    // ---- 7th key is dropped, held keys are never evicted ----
    make_key(false, SC_G);
    CHECK(slots_are(HID_A, HID_B, HID_C, HID_D, HID_E, HID_F),
          "7th simultaneous key is dropped, not swapped in over a held key");

    // ---- Releasing a middle key frees exactly its slot ----
    break_key(false, SC_C);
    CHECK(slots_are(HID_A, HID_B, 0, HID_D, HID_E, HID_F),
          "release of a middle key clears only its own slot");

    // ---- A new key reuses the first free slot ----
    make_key(false, SC_W);
    CHECK(slots_are(HID_A, HID_B, HID_W, HID_D, HID_E, HID_F),
          "next key reuses the freed slot (first-free priority)");

    // ---- Modifiers still independent while the set is full ----
    make_key(false, SC_RSHIFT);
    CHECK(modifiers() == MOD_RSHIFT &&
          slots_are(HID_A, HID_B, HID_W, HID_D, HID_E, HID_F),
          "modifier press with all six slots full still registers");
    break_key(false, SC_RSHIFT);

    // ---- Break of a key that is not held is harmless ----
    break_key(false, SC_ESC);
    CHECK(slots_are(HID_A, HID_B, HID_W, HID_D, HID_E, HID_F),
          "break for a key that is not held leaves the set untouched");

    // ---- Release everything ----
    break_key(false, SC_A);
    break_key(false, SC_B);
    break_key(false, SC_W);
    break_key(false, SC_D);
    break_key(false, SC_E);
    break_key(false, SC_F);
    CHECK(slots_are(0, 0, 0, 0, 0, 0) && modifiers() == 0,
          "all keys released: report empty, keyboard still present");

    // ---- Alt held across a chord (the Doom strafe case) ----
    make_key(false, SC_LALT);
    make_key(false, SC_A);                      // A = strafe-left binding
    CHECK(modifiers() == MOD_LALT && slots_are(HID_A, 0, 0, 0, 0, 0),
          "modifier + key chord reports both simultaneously");
    break_key(false, SC_A);
    break_key(false, SC_LALT);

    // ---- Reset clears everything and drops back to absent ----
    dut->reset_n = 0;
    for (int i = 0; i < 4; i++) tick();
    CHECK(dut->cont3_key == 0 && dut->cont3_joy == 0 && dut->cont3_trig == 0,
          "reset: type nibble 0, slots and modifiers cleared");
    dut->ps2_key = 0; toggle_state = 0;
    dut->reset_n = 1;
    tick();
    make_key(false, SC_ESC);
    CHECK(type_nib() == 4 && slots_are(HID_ESC, 0, 0, 0, 0, 0),
          "post-reset event: fresh report, presence re-latches");

    printf("\n=== Results: %d passed, %d failed ===\n", passed, failed);
    delete dut;
    return failed ? 1 : 0;
}
