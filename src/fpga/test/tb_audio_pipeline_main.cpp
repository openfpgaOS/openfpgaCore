// Verilator driver for tb_audio_pipeline.v — audio_output.v integration test.

#include "Vtb_audio_pipeline.h"
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
