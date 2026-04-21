// tb_cram1_burst_mmio_main.cpp — self-checking C++ harness for
// cram1_burst_mmio.v.  Drives the MMIO interface the way the SW mixer's
// swmixer.c does: write BURST_ADDR to trigger, spin on BURST_STATUS.busy,
// then read BURST_DATA eight times.  Verifies that the words come out
// in the expected order and that the DUT correctly signals busy/ready.

#include "Vtb_cram1_burst_mmio.h"
#include <cstdio>
#include <cstdint>
#include <cstdlib>

static Vtb_cram1_burst_mmio *dut;
static uint64_t g_cycles;

static void tick(void) {
    dut->clk = 0; dut->eval();
    dut->clk = 1; dut->eval();
    g_cycles++;
}

static void reset(void) {
    dut->reset_n = 0;
    dut->mmio_addr_wr_pulse = 0;
    dut->mmio_addr_wdata    = 0;
    dut->mmio_data_rd_pulse = 0;
    for (int i = 0; i < 5; i++) tick();
    dut->reset_n = 1;
    for (int i = 0; i < 3; i++) tick();
}

static void fire_burst(uint32_t addr) {
    dut->mmio_addr_wr_pulse = 1;
    dut->mmio_addr_wdata    = addr & 0x3FFFFFu;
    tick();
    dut->mmio_addr_wr_pulse = 0;
}

// Drain one word from the buffer, with a timeout.  Returns true if the
// word was observed in mmio_data_q during the pulse cycle.
static bool read_word(uint32_t *out) {
    // The DUT should have returned to ST_IDLE by now; mmio_data_q is
    // always the current head, and pulsing mmio_data_rd_pulse advances
    // the read pointer on the same cycle.
    *out = dut->mmio_data_q;
    dut->mmio_data_rd_pulse = 1;
    tick();
    dut->mmio_data_rd_pulse = 0;
    return true;
}

static bool spin_for_idle(int max_cycles) {
    for (int i = 0; i < max_cycles; i++) {
        if (!dut->mmio_busy) return true;
        tick();
    }
    return false;
}

static int fails = 0;

static void check(const char *name, uint32_t got, uint32_t want) {
    if (got == want) {
        printf("  [OK ] %s: got 0x%08x\n", name, got);
    } else {
        printf("  [FAIL] %s: got 0x%08x, expected 0x%08x\n", name, got, want);
        fails++;
    }
}

int main(int argc, char **argv) {
    (void)argc; (void)argv;
    dut = new Vtb_cram1_burst_mmio;
    g_cycles = 0;

    printf("\n=== cram1_burst_mmio self-check ===\n\n");

    reset();

    // Test 1: single burst starting at a 8-word-aligned address.
    printf("Test 1: burst at word_addr=0x000100 (aligned)\n");
    fire_burst(0x000100);
    if (!spin_for_idle(200)) {
        printf("  [FAIL] burst did not complete within 200 cycles\n");
        fails++;
    }
    // DUT should expose the 8 words in buf0..buf7.  Stub pushed
    // {addr + 0, addr + 1, ..., addr + 7}.
    for (uint32_t i = 0; i < 8; i++) {
        char buf[64];
        snprintf(buf, sizeof(buf), "burst[%u]", i);
        uint32_t got;
        read_word(&got);
        check(buf, got, 0x000100u + i);
    }

    // Test 2: second burst at a different address — verifies the
    // buffer and read pointer are reset correctly between bursts.
    printf("\nTest 2: burst at word_addr=0x003F80 (new range)\n");
    fire_burst(0x003F80);
    if (!spin_for_idle(200)) {
        printf("  [FAIL] burst did not complete within 200 cycles\n");
        fails++;
    }
    for (uint32_t i = 0; i < 8; i++) {
        char buf[64];
        snprintf(buf, sizeof(buf), "burst[%u]", i);
        uint32_t got;
        read_word(&got);
        check(buf, got, 0x003F80u + i);
    }

    // Test 3: after draining all 8 words, an extra read should clamp
    // at buf7 per the module's contract.
    printf("\nTest 3: overdraw returns buf7 (no wrap)\n");
    {
        uint32_t got;
        read_word(&got);
        check("overdraw", got, 0x003F87u);   // last word of previous burst
    }

    // Test 4: busy stays HIGH while the DUT is servicing a burst.
    printf("\nTest 4: mmio_busy asserted during a live burst\n");
    fire_burst(0x000200);
    int busy_seen = 0;
    for (int i = 0; i < 50; i++) {
        if (dut->mmio_busy) busy_seen = 1;
        tick();
    }
    if (busy_seen) {
        printf("  [OK ] busy asserted during burst\n");
    } else {
        printf("  [FAIL] busy never asserted\n");
        fails++;
    }
    spin_for_idle(200);

    // Test 5: back-to-back bursts without intervening delay work.
    printf("\nTest 5: back-to-back bursts\n");
    for (int t = 0; t < 4; t++) {
        uint32_t base = 0x001000u + (uint32_t)t * 0x40u;
        fire_burst(base);
        if (!spin_for_idle(200)) {
            printf("  [FAIL] burst %d did not complete\n", t);
            fails++;
            break;
        }
        for (uint32_t i = 0; i < 8; i++) {
            uint32_t got;
            read_word(&got);
            if (got != base + i) {
                printf("  [FAIL] burst %d[%u]: got 0x%08x, expected 0x%08x\n",
                       t, i, got, base + i);
                fails++;
            }
        }
    }
    if (fails == 0)
        printf("  [OK ] back-to-back bursts clean\n");

    printf("\n=== Total: %d failures, %lu cycles ===\n", fails, (unsigned long)g_cycles);
    delete dut;
    return fails ? 1 : 0;
}
