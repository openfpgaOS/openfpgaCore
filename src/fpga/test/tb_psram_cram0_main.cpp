/*
 * Verilator C++ harness for tb_psram_cram0.
 *
 * Drives the word-level interface of psram_cram0 (the CRAM0 wrapper
 * actually instantiated in core_top.v) and verifies that sequential
 * writes followed by reads round-trip through the real CRAM driver
 * and the behavioral cram_chip_model. Catches FSM bugs in
 * psram_cram0.v before paying for a Quartus build to discover them
 * on the Pocket.
 *
 * Coverage:
 *   1. Basic single-word write + read-back (all four byte strobes hot)
 *   2. Sequential streaming write then sequential streaming read of N
 *      consecutive words — exercises the LO→HI inline path
 *   3. Byte-strobe writes (partial wstrb) — verifies LO/HI half skip
 *      logic and read-modify-style behaviour stays correct
 *   4. Both CRAM0 banks (word_addr[21] = 0 and 1) — verifies bank_sel
 *      latching survives wrapper edits
 *
 * Counts pass/fail and exits non-zero on any failure.
 */

#include <cstdio>
#include <cstdlib>
#include <cstdint>
#include <cstring>
#include "Vtb_psram_cram0.h"
#include "verilated.h"
#include "verilated_vcd_c.h"

static Vtb_psram_cram0 *tb;
static VerilatedVcdC *trace = nullptr;
static uint64_t sim_time = 0;
static uint64_t cycles = 0;
static int passed = 0;
static int failed = 0;

static void tick() {
    tb->clk = 0;
    tb->eval();
    if (trace) trace->dump(sim_time);
    sim_time++;
    tb->clk = 1;
    tb->eval();
    if (trace) trace->dump(sim_time);
    sim_time++;
    cycles++;
}

static void reset() {
    tb->reset_n = 0;
    tb->word_rd = 0;
    tb->word_wr = 0;
    tb->word_addr = 0;
    tb->word_data = 0;
    tb->word_wstrb = 0;
    for (int i = 0; i < 8; i++) tick();
    tb->reset_n = 1;
    // Let the chip model finish its power-up sequence (POWERUP_CYCLES=16
    // in tb_psram_cram0.v) before issuing any commands.
    for (int i = 0; i < 32; i++) tick();
}

// Issue a word write and wait for word_busy to go high then low.
// max_cycles guards against an FSM hang — the harness fails the
// test instead of looping forever (which would freeze the gate).
static bool do_write(uint32_t addr, uint32_t data, uint8_t wstrb,
                     int max_cycles = 200) {
    tb->word_addr = addr;
    tb->word_data = data;
    tb->word_wstrb = wstrb;
    tb->word_wr = 1;
    tick();
    tb->word_wr = 0;
    tb->word_wstrb = 0;
    int t = 0;
    // Wait for busy to assert
    while (!tb->word_busy && t < max_cycles) { tick(); t++; }
    if (t >= max_cycles) {
        printf("  FAIL: write to 0x%06x — word_busy never asserted\n", addr);
        return false;
    }
    // Wait for busy to deassert
    while (tb->word_busy && t < max_cycles) { tick(); t++; }
    if (t >= max_cycles) {
        printf("  FAIL: write to 0x%06x — word_busy never released\n", addr);
        return false;
    }
    return true;
}

// Issue a word read; on success, *out_data is the captured word_q at
// the cycle word_q_valid pulses.
static bool do_read(uint32_t addr, uint32_t *out_data,
                    int max_cycles = 200) {
    tb->word_addr = addr;
    tb->word_data = 0;
    tb->word_wstrb = 0;
    tb->word_rd = 1;
    tick();
    tb->word_rd = 0;
    int t = 0;
    while (!tb->word_busy && t < max_cycles) { tick(); t++; }
    if (t >= max_cycles) {
        printf("  FAIL: read from 0x%06x — word_busy never asserted\n", addr);
        return false;
    }
    // Drain the read; capture the word the cycle q_valid pulses.
    bool got = false;
    uint32_t captured = 0;
    while ((tb->word_busy || !got) && t < max_cycles) {
        if (tb->word_q_valid) { captured = tb->word_q; got = true; }
        tick(); t++;
    }
    if (!got) {
        printf("  FAIL: read from 0x%06x — word_q_valid never pulsed\n", addr);
        return false;
    }
    *out_data = captured;
    return true;
}

static void check_eq(const char *what, uint32_t expected, uint32_t actual) {
    if (expected == actual) {
        passed++;
    } else {
        failed++;
        printf("  FAIL: %s — expected 0x%08x got 0x%08x\n", what, expected, actual);
    }
}

// ---- Test bodies ----

static void test_basic_round_trip() {
    printf("TEST: basic single-word write + read-back\n");
    uint32_t got = 0;
    if (!do_write(0x000010, 0xDEADBEEF, 0xF)) { failed++; return; }
    if (!do_read (0x000010, &got)) { failed++; return; }
    check_eq("0xDEADBEEF round trip", 0xDEADBEEF, got);
}

static void test_sequential_stream() {
    printf("TEST: sequential 16-word write then read (LO->HI inline path)\n");
    const int N = 16;
    const uint32_t base = 0x000100;
    for (int i = 0; i < N; i++) {
        uint32_t v = 0xA5A50000u | i;
        if (!do_write(base + i, v, 0xF)) { failed++; return; }
    }
    for (int i = 0; i < N; i++) {
        uint32_t got = 0;
        if (!do_read(base + i, &got)) { failed++; return; }
        char label[64];
        snprintf(label, sizeof(label), "seq[%d]", i);
        check_eq(label, 0xA5A50000u | i, got);
    }
}

static void test_byte_strobes() {
    printf("TEST: partial byte strobes (LO-only / HI-only writes)\n");
    uint32_t got = 0;
    // Prime the location with a known full pattern.
    if (!do_write(0x000200, 0x11223344, 0xF)) { failed++; return; }
    // Overwrite only the LOW halfword.
    if (!do_write(0x000200, 0xDEADBEEF, 0x3)) { failed++; return; }
    if (!do_read (0x000200, &got)) { failed++; return; }
    check_eq("LO-only write keeps HI=0x1122", 0x1122BEEFu, got);
    // Overwrite only the HIGH halfword (use a fresh address to avoid
    // depending on chip-model write-merge of the previous step).
    if (!do_write(0x000201, 0x11223344, 0xF)) { failed++; return; }
    if (!do_write(0x000201, 0xCAFEBABE, 0xC)) { failed++; return; }
    if (!do_read (0x000201, &got)) { failed++; return; }
    check_eq("HI-only write keeps LO=0x3344", 0xCAFE3344u, got);
}

static void test_both_banks() {
    printf("TEST: both CRAM0 banks (word_addr[21] = 0 and 1)\n");
    uint32_t got = 0;
    if (!do_write(0x000300, 0x10101010, 0xF)) { failed++; return; }
    if (!do_write(0x200300, 0x20202020, 0xF)) { failed++; return; }
    if (!do_read (0x000300, &got)) { failed++; return; }
    check_eq("bank0 readback", 0x10101010u, got);
    if (!do_read (0x200300, &got)) { failed++; return; }
    check_eq("bank1 readback", 0x20202020u, got);
}

static void test_back_to_back() {
    printf("TEST: back-to-back accesses without idle gap\n");
    // Write 8 words then immediately read them in the opposite order.
    // This stresses the ST_HI_WAIT → ST_IDLE → next-request path.
    const int N = 8;
    const uint32_t base = 0x000400;
    for (int i = 0; i < N; i++) {
        uint32_t v = 0xC0DE0000u | (i << 4);
        if (!do_write(base + i, v, 0xF)) { failed++; return; }
    }
    for (int i = N - 1; i >= 0; i--) {
        uint32_t got = 0;
        if (!do_read(base + i, &got)) { failed++; return; }
        char label[64];
        snprintf(label, sizeof(label), "b2b[%d]", i);
        check_eq(label, 0xC0DE0000u | (i << 4), got);
    }
}

int main(int argc, char **argv) {
    Verilated::commandArgs(argc, argv);
    tb = new Vtb_psram_cram0;

    // Optional VCD trace via --trace flag.
    bool want_trace = false;
    for (int i = 1; i < argc; i++)
        if (!strcmp(argv[i], "--trace")) want_trace = true;
    if (want_trace) {
        Verilated::traceEverOn(true);
        trace = new VerilatedVcdC;
        tb->trace(trace, 99);
        trace->open("tb_psram_cram0.vcd");
    }

    printf("=== psram_cram0 wrapper test ===\n");
    reset();

    test_basic_round_trip();
    test_sequential_stream();
    test_byte_strobes();
    test_both_banks();
    test_back_to_back();

    printf("\n=== Results: %d passed, %d failed ===\n", passed, failed);

    if (trace) { trace->close(); delete trace; }
    delete tb;
    return failed == 0 ? 0 : 1;
}
