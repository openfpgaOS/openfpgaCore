//------------------------------------------------------------------------------
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileType: SOURCE
// SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
//------------------------------------------------------------------------------

// Verilator driver for tb_audio_pipeline.v — audio_output.v integration test.

#include "Vtb_audio_pipeline.h"
#include "Vtb_audio_pipeline___024root.h"
#include <verilated.h>
#include <verilated_vcd_c.h>

#include <cstdio>
#include <cstdlib>
#include <cstring>

using DUT = Vtb_audio_pipeline;

static vluint64_t g_time = 0;

static void tick_sys(DUT* dut) {
    // 100 MHz sys clock: 10 ns period, toggle every 5 ns
    dut->eval();
    dut->clk_sys = 0;
    for (int k = 0; k < 5; k++) {
        dut->eval();
        g_time++;
        // Advance clk_audio ~every 8 sys ticks so 48 kHz pop fires
        if ((g_time & 0x7) == 0) dut->clk_audio = !dut->clk_audio;
    }
    dut->clk_sys = 1;
    for (int k = 0; k < 5; k++) {
        dut->eval();
        g_time++;
        if ((g_time & 0x7) == 0) dut->clk_audio = !dut->clk_audio;
    }
}

static int fails = 0;
static int passes = 0;

static void check(bool cond, const char* name) {
    if (cond) { passes++; printf("  OK   %s\n", name); }
    else      { fails++;  printf("  FAIL %s\n", name); }
}

static void push_sample(DUT* dut, int16_t l, int16_t r) {
    uint32_t word = ((uint32_t)(uint16_t)l << 16) | (uint32_t)(uint16_t)r;
    dut->sample_data = word;
    dut->sample_wr   = 1;
    tick_sys(dut);
    dut->sample_wr = 0;
    tick_sys(dut);
}

static uint16_t active_l(DUT* dut) {
    return (uint16_t)dut->rootp->tb_audio_pipeline__DOT__dut__DOT__active_l;
}

static uint16_t active_r(DUT* dut) {
    return (uint16_t)dut->rootp->tb_audio_pipeline__DOT__dut__DOT__active_r;
}

static bool wait_for_active(DUT* dut, uint16_t l, uint16_t r) {
    for (int i = 0; i < 100000; i++) {
        tick_sys(dut);
        if (active_l(dut) == l && active_r(dut) == r)
            return true;
    }
    return false;
}

// --- I2S channel-order decode ---------------------------------------------
// The staging checks above only prove active_l/active_r carry the right
// 16-bit words; they do NOT prove the SERIALIZER emits them on the correct
// physical channel.  The Analogue Pocket convention (Analogue dev docs +
// shipping stereo cores e.g. nullobject/openfpga-tecmo) is:
//     audio_lrck LOW  = LEFT channel
//     audio_lrck HIGH = RIGHT channel
// Decode the real audio_dac/audio_lrck stream: keep the FIFO fed with one
// constant stereo pair, then count how many '1' DAC bits land in each LRCK
// phase.  A hard-left pair (left!=0, right==0) must put all its energy in
// the LRCK-LOW window; a hard-right pair in the LRCK-HIGH window.  Samples
// taken exactly on an lrck transition are skipped to avoid boundary
// straddle.  Returns ones-counted-while-low / ones-counted-while-high.
static void reset_dut(DUT* dut) {
    dut->reset_n   = 0;
    dut->sample_wr = 0;
    for (int i = 0; i < 20; i++) tick_sys(dut);
    dut->reset_n = 1;
    for (int i = 0; i < 20; i++) tick_sys(dut);
}

static void measure_channel_ones(DUT* dut, int16_t l, int16_t r,
                                 long* ones_low, long* ones_high) {
    // Clean slate each call: the dcfifo (aclr) and serializer are flushed so
    // a previous vector still queued in the FIFO can't bleed into this one.
    reset_dut(dut);
    // Top the FIFO up so the serializer emits this pair without underrun.
    for (int i = 0; i < 1100 && !dut->fifo_full; i++) push_sample(dut, l, r);
    wait_for_active(dut, (uint16_t)l, (uint16_t)r);

    long lo = 0, hi = 0;
    int prev_lrck = dut->audio_lrck;
    for (int i = 0; i < 80000; i++) {
        // Keep it fed (all identical, so active_l/active_r stay put).
        if (!dut->fifo_full && (i & 0x3F) == 0) push_sample(dut, l, r);
        tick_sys(dut);
        int lrck = dut->audio_lrck;
        if (lrck == prev_lrck && dut->audio_dac) {
            if (lrck) hi++; else lo++;
        }
        prev_lrck = lrck;
    }
    *ones_low = lo;
    *ones_high = hi;
}

int main(int argc, char** argv) {
    Verilated::commandArgs(argc, argv);
    auto* dut = new DUT;

    printf("\n=== audio pipeline (audio_output + dcfifo stub) ===\n");

    // Reset
    dut->reset_n     = 0;
    dut->sample_wr   = 0;
    dut->sample_data = 0;
    dut->clk_sys     = 0;
    dut->clk_audio   = 0;
    for (int i = 0; i < 20; i++) tick_sys(dut);
    dut->reset_n = 1;
    for (int i = 0; i < 20; i++) tick_sys(dut);

    // Initial state: FIFO empty, not full
    check(dut->fifo_level == 0, "fifo empty at reset");
    check(dut->fifo_full  == 0, "not full at reset");

    push_sample(dut, 0x1111, 0x2222);
    check(wait_for_active(dut, 0x1111, 0x2222),
          "stereo center sample reaches output staging");

    push_sample(dut, 0x4000, 0x0000);
    check(wait_for_active(dut, 0x4000, 0x0000),
          "hard-left sample reaches output staging");

    push_sample(dut, 0x0000, 0x4000);
    check(wait_for_active(dut, 0x0000, 0x4000),
          "hard-right sample reaches output staging");

    // --- I2S channel order (Analogue Pocket: LRCK low = LEFT) -------------
    // Decode the serialized stream, not just the staging registers.
    {
        long lo = 0, hi = 0;
        measure_channel_ones(dut, 0x7F00, 0x0000, &lo, &hi);  // hard LEFT
        printf("    hard-left  : ones(lrck=low)=%ld  ones(lrck=high)=%ld\n", lo, hi);
        check(lo > 50 && lo > 10 * (hi + 1),
              "hard-left sample serializes on LRCK-low (physical left)");

        measure_channel_ones(dut, 0x0000, 0x7F00, &lo, &hi);  // hard RIGHT
        printf("    hard-right : ones(lrck=low)=%ld  ones(lrck=high)=%ld\n", lo, hi);
        check(hi > 50 && hi > 10 * (lo + 1),
              "hard-right sample serializes on LRCK-high (physical right)");

        // Restore the empty-FIFO precondition the downstream checks assume.
        reset_dut(dut);
    }

    // Push 100 samples, check level rises
    for (int i = 0; i < 100; i++) {
        push_sample(dut, (int16_t)(i * 100), (int16_t)(-i * 100));
    }
    check(dut->fifo_level >= 80, "fifo_level rose after 100 pushes");

    // Let the audio drain for a while — eventually level should drop
    uint32_t start_level = dut->fifo_level;
    for (int i = 0; i < 50000; i++) tick_sys(dut);
    check(dut->fifo_level < start_level,
          "fifo_level drops as audio clock pops samples");

    // Stress: push up to 1024, confirm wrfull asserts near capacity
    int pushes = 0;
    while (!dut->fifo_full && pushes < 2000) {
        push_sample(dut, 0x1234, 0x5678);
        pushes++;
    }
    check(dut->fifo_full, "fifo_full asserts when full");

    // Drain fully: stop writing, let the audio clock pop everything
    dut->sample_wr = 0;
    for (int i = 0; i < 400000; i++) tick_sys(dut);

    // Underrun: dcfifo stub empties, audio_output holds last sample and
    // decays.  We just check the serializer keeps ticking (lrck toggles
    // over a long window) and the CPU hasn't locked up.
    int lrck_toggles = 0;
    int prev_lrck = dut->audio_lrck;
    for (int i = 0; i < 200000; i++) {
        tick_sys(dut);
        if (dut->audio_lrck != prev_lrck) {
            lrck_toggles++;
            prev_lrck = dut->audio_lrck;
        }
    }
    check(lrck_toggles > 10, "lrck keeps toggling during underrun (no lockup)");

    // mclk should always pass through clk_audio
    int mclk_toggles = 0;
    int prev_mclk = dut->audio_mclk;
    for (int i = 0; i < 100; i++) {
        tick_sys(dut);
        if (dut->audio_mclk != prev_mclk) {
            mclk_toggles++;
            prev_mclk = dut->audio_mclk;
        }
    }
    check(mclk_toggles > 0, "audio_mclk passthrough toggling");

    printf("\n=== Audio Pipeline: %d passed, %d failed ===\n", passes, fails);

    dut->final();
    delete dut;
    return fails == 0 ? 0 : 1;
}
