//------------------------------------------------------------------------------
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileType: SOURCE
// SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
//------------------------------------------------------------------------------
//
// Verilator cosim for hps_mouse (MiSTer framework mouse → input-hub slot 3).
// The harness plays hps_io: it composes ps2_mouse packets (bit 24 toggle,
// PS/2 flags/dx/dy) and checks the cont4_* encoding against a software model
// of the unified mouse contract:
//   key[31:28] = 5 once seen, key[15:0] = report counter,
//   joy[31:16] = buttons {ext, M, R, L}, joy[15:0] = acc_x (positive = right),
//   trig[15:0] = acc_y (positive = DOWN — PS/2 dy is negated),
//   accumulators free-running with 16-bit wraparound.
//
// Build/run: make hps-mouse   (test/Makefile target)
//
#include "Vtb_hps_mouse.h"
#include "verilated.h"
#include <cstdio>
#include <cstdlib>
#include <cstdint>

static Vtb_hps_mouse *dut;

static void tick() {
    dut->clk = 0; dut->eval();
    dut->clk = 1; dut->eval();
}

// PS/2 flags byte: bit0=L, bit1=R, bit2=M, bit3=always-1, bit4=X sign,
// bit5=Y sign, bit6=X overflow, bit7=Y overflow.
enum {
    F_L = 0x01, F_R = 0x02, F_M = 0x04, F_ONE = 0x08,
    F_XS = 0x10, F_YS = 0x20, F_XO = 0x40, F_YO = 0x80
};

// Software model of the contract (drives the CHECKs).
static uint16_t m_acc_x, m_acc_y, m_cnt, m_buttons;
static bool     m_seen;
static int      toggle_state;

static void send_packet(uint8_t flags, uint8_t dx, uint8_t dy, uint8_t ext = 0) {
    toggle_state ^= 1;
    dut->ps2_mouse     = ((uint32_t)toggle_state << 24) |
                         ((uint32_t)dy << 16) | ((uint32_t)dx << 8) | flags;
    dut->ps2_mouse_ext = (uint16_t)(ext & 0xF) << 8;
    tick();

    // 9-bit sign-extend from {sign flag, byte}, overflow clamps to ±255.
    int sdx = (flags & F_XS) ? (int)dx - 256 : (int)dx;
    int sdy = (flags & F_YS) ? (int)dy - 256 : (int)dy;
    if (flags & F_XO) sdx = (flags & F_XS) ? -255 : 255;
    if (flags & F_YO) sdy = (flags & F_YS) ? -255 : 255;
    m_acc_x   = (uint16_t)(m_acc_x + sdx);
    m_acc_y   = (uint16_t)(m_acc_y - sdy);   // PS/2 up-positive → down-positive
    m_cnt     = (uint16_t)(m_cnt + 1);
    m_buttons = (uint16_t)(((ext & 0xF) << 3) | (flags & 0x7));
    m_seen    = true;
}

static bool outputs_match() {
    uint32_t exp_key  = (m_seen ? 0x50000000u : 0u) | m_cnt;
    uint32_t exp_joy  = ((uint32_t)m_buttons << 16) | m_acc_x;
    uint16_t exp_trig = m_acc_y;
    return dut->cont4_key == exp_key && dut->cont4_joy == exp_joy &&
           dut->cont4_trig == exp_trig;
}

int main(int argc, char **argv) {
    Verilated::commandArgs(argc, argv);
    dut = new Vtb_hps_mouse;

    int passed = 0, failed = 0;
    auto CHECK = [&](bool c, const char *m) {
        if (c) { passed++; printf("  OK   %s\n", m); }
        else   { failed++; printf("  FAIL %s\n", m); }
    };

    printf("=== hps_mouse cosim (framework mouse -> input-hub slot 3) ===\n\n");

    // Reset.
    dut->reset_n = 0; dut->ps2_mouse = 0; dut->ps2_mouse_ext = 0;
    for (int i = 0; i < 4; i++) tick();
    dut->reset_n = 1;
    tick();

    // ---- Idle: type nibble 0 and everything cleared before any packet ----
    CHECK(dut->cont4_key == 0 && dut->cont4_joy == 0 && dut->cont4_trig == 0,
          "before first packet: type nibble 0, counter/buttons/accumulators 0");

    // ---- No toggle, no update: payload wiggles must be ignored ----
    dut->ps2_mouse     = ((uint32_t)toggle_state << 24) | 0x7F3F00u | (F_ONE | F_L);
    dut->ps2_mouse_ext = 0x0F00;
    for (int i = 0; i < 8; i++) tick();
    CHECK(outputs_match(), "no output change without a ps2_mouse[24] toggle");

    // ---- Single packet: positive deltas, Y inversion ----
    send_packet(F_ONE, 5, 3);            // PS/2 dy=+3 is UP
    CHECK(outputs_match() && dut->cont4_key == 0x50000001u &&
          (uint16_t)dut->cont4_joy == 5 && dut->cont4_trig == 0xFFFD,
          "packet(+5,+3up): type=5, counter=1, acc_x=+5, acc_y=-3 (Y inverted)");

    // ---- Negative deltas via the sign flags ----
    send_packet(F_ONE | F_XS | F_YS, 0xFB, 0xFD);   // dx=-5, dy=-3 (DOWN)
    CHECK(outputs_match() && (uint16_t)dut->cont4_joy == 0 && dut->cont4_trig == 0,
          "packet(-5,-3up): sign flags decode, accumulators return to 0");

    // ---- Overflow flags clamp magnitude to 255 ----
    send_packet(F_ONE | F_XO, 0x12, 0);             // X overflow, positive
    CHECK(outputs_match() && (uint16_t)dut->cont4_joy == 255,
          "X overflow, positive: clamps to +255 (magnitude byte ignored)");
    send_packet(F_ONE | F_XO | F_XS, 0x34, 0);      // X overflow, negative
    CHECK(outputs_match() && (uint16_t)dut->cont4_joy == 0,
          "X overflow, negative: clamps to -255");
    send_packet(F_ONE | F_YO, 0, 0x56);             // Y overflow, positive (UP)
    CHECK(outputs_match() && dut->cont4_trig == (uint16_t)-255,
          "Y overflow, positive-up: clamps to -255 after inversion");
    send_packet(F_ONE | F_YO | F_YS, 0, 0x78);      // Y overflow, negative (DOWN)
    CHECK(outputs_match() && dut->cont4_trig == 0,
          "Y overflow, negative-up: clamps to +255 after inversion");

    // ---- Button mapping: L/R/M on bits 0-2, ext buttons on bits 3-6 ----
    send_packet(F_ONE | F_L, 0, 0);
    CHECK(outputs_match() && (dut->cont4_joy >> 16) == 0x0001, "button L -> joy[16]");
    send_packet(F_ONE | F_R, 0, 0);
    CHECK(outputs_match() && (dut->cont4_joy >> 16) == 0x0002, "button R -> joy[17]");
    send_packet(F_ONE | F_M, 0, 0);
    CHECK(outputs_match() && (dut->cont4_joy >> 16) == 0x0004, "button M -> joy[18]");
    send_packet(F_ONE | F_L | F_R | F_M, 0, 0, 0xF);
    CHECK(outputs_match() && (dut->cont4_joy >> 16) == 0x007F,
          "all buttons + ext[11:8] -> joy[22:19]");
    send_packet(F_ONE, 0, 0);
    CHECK(outputs_match() && (dut->cont4_joy >> 16) == 0,
          "button release: buttons track each packet's flags");

    // ---- Randomized multi-packet accumulation + wraparound cosim ----
    // 70000 packets: wraps acc_x/acc_y many times AND the 16-bit report
    // counter once (65536), proving free-running modulo-2^16 arithmetic
    // everywhere (the firmware differencing contract).
    srand(0xC0FFEE);
    bool cosim_ok = true;
    for (int i = 0; i < 70000; i++) {
        uint8_t flags = F_ONE | (rand() & 0x7) |
                        ((rand() & 0x3) << 4) |        // random signs
                        ((rand() % 16 == 0) ? (rand() & 0xC0) : 0);  // rare overflow
        send_packet(flags, rand() & 0xFF, rand() & 0xFF, rand() & 0xF);
        if (!outputs_match()) { cosim_ok = false; break; }
    }
    CHECK(cosim_ok, "70000-packet random cosim: accumulation, counter, wraparound");
    CHECK(m_cnt == (uint16_t)(70000 + 11) && (uint16_t)dut->cont4_key == m_cnt,
          "report counter incremented once per packet (wrapped past 65536)");

    // ---- Reset clears everything (type nibble drops back to 0) ----
    dut->reset_n = 0;
    for (int i = 0; i < 4; i++) tick();
    CHECK(dut->cont4_key == 0 && dut->cont4_joy == 0 && dut->cont4_trig == 0,
          "reset: type nibble 0, counter/buttons/accumulators cleared");
    dut->ps2_mouse = 0; dut->ps2_mouse_ext = 0; toggle_state = 0;
    m_acc_x = m_acc_y = m_cnt = m_buttons = 0; m_seen = false;
    dut->reset_n = 1;
    tick();
    send_packet(F_ONE | F_XS, 0xF6, 10);            // dx=-10, dy=+10 (UP)
    CHECK(outputs_match() && dut->cont4_key == 0x50000001u &&
          (uint16_t)dut->cont4_joy == 0xFFF6 && dut->cont4_trig == 0xFFF6,
          "post-reset packet: fresh accumulation from 0, counter restarts at 1");

    printf("\n=== Results: %d passed, %d failed ===\n", passed, failed);
    delete dut;
    return failed ? 1 : 0;
}
