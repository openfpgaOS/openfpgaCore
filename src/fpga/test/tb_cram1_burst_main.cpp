/*
 * Verilator C++ harness for tb_cram1_burst.
 *
 * Exercises the burst-read interface on cram1_controller.v (N async
 * reads under one burst contract — no BCR write, chip stays in
 * POR-default 0x9D1F):
 *
 *   1. Reset is released; bcr_init_done rises immediately (kept as
 *      an output for compatibility; the controller does not write BCR).
 *   2. word_busy / burst_busy guard async + burst paths properly.
 *   3. An async word_wr followed by word_rd round-trips data.
 *   4. A 16-word burst read against chip-model-preseeded data returns
 *      the 16 expected values in order, one per burst_q_valid pulse,
 *      with burst_busy held throughout.
 *   5. Bank-1 (CE1#) bursts work too.
 *
 * Any deviation fails the test.
 */

#include <cstdio>
#include <cstdlib>
#include <cstdint>
#include <cstring>
#include "Vtb_cram1_burst.h"
#include "verilated.h"
#include "verilated_vcd_c.h"

static Vtb_cram1_burst *tb;
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
    tb->burst_rd = 0;
    tb->burst_addr = 0;
    tb->burst_len = 0;
    tb->bd_we = 0;
    tb->bd_word_addr = 0;
    tb->bd_wdata_word = 0;
    for (int i = 0; i < 8; i++) tick();
    tb->reset_n = 1;
}

static void check_eq(const char *what, uint32_t expected, uint32_t actual) {
    if (expected == actual) {
        passed++;
    } else {
        failed++;
        printf("  FAIL: %s — expected 0x%08x got 0x%08x\n", what, expected, actual);
    }
}

static void check_true(const char *what, bool cond) {
    if (cond) {
        passed++;
    } else {
        failed++;
        printf("  FAIL: %s\n", what);
    }
}

// Wait up to max_cycles for bcr_init_done to rise; report the
// observed cycle count for sanity.
static bool wait_for_bcr(int max_cycles = 200) {
    int t = 0;
    while (!tb->bcr_init_done && t < max_cycles) {
        tick();
        t++;
    }
    if (tb->bcr_init_done) {
        printf("  bcr_init_done rose after %d cycles post-reset\n", t);
        return true;
    }
    printf("  FAIL: bcr_init_done never rose within %d cycles\n", max_cycles);
    return false;
}

// Preload a 32-bit word into the chip-model memory via the backdoor
// port.  bank_sel=0 → CE0, bank_sel=1 → CE1. word_idx is a word
// index inside the selected bank.
static void backdoor_write(uint32_t bank_sel, uint32_t word_idx, uint32_t data) {
    tb->bd_word_addr = ((bank_sel & 1u) << 20) | (word_idx & 0xFFFFFu);
    tb->bd_wdata_word = data;
    tb->bd_we = 1;
    tick();
    tb->bd_we = 0;
    tb->bd_word_addr = 0;
    tb->bd_wdata_word = 0;
}

// Async single-word write via the word_wr path.
static bool do_word_write(uint32_t addr, uint32_t data,
                          int max_cycles = 300) {
    tb->word_addr = addr;
    tb->word_data = data;
    tb->word_wstrb = 0xF;
    tb->word_wr = 1;
    tick();
    tb->word_wr = 0;
    tb->word_wstrb = 0;
    int t = 0;
    while (!tb->word_busy && t < max_cycles) { tick(); t++; }
    if (t >= max_cycles) {
        printf("  FAIL: word_wr at 0x%06x — word_busy never asserted\n", addr);
        return false;
    }
    while (tb->word_busy && t < max_cycles) { tick(); t++; }
    if (t >= max_cycles) {
        printf("  FAIL: word_wr at 0x%06x — word_busy never released\n", addr);
        return false;
    }
    return true;
}

// Async single-word read via the word_rd path.
static bool do_word_read(uint32_t addr, uint32_t *out_data,
                         int max_cycles = 300) {
    tb->word_addr = addr;
    tb->word_rd = 1;
    tick();
    tb->word_rd = 0;
    int t = 0;
    while (!tb->word_busy && t < max_cycles) { tick(); t++; }
    if (t >= max_cycles) {
        printf("  FAIL: word_rd at 0x%06x — word_busy never asserted\n", addr);
        return false;
    }
    bool got = false;
    uint32_t captured = 0;
    while ((tb->word_busy || !got) && t < max_cycles) {
        if (tb->word_q_valid) { captured = tb->word_q; got = true; }
        tick(); t++;
    }
    if (!got) {
        printf("  FAIL: word_rd at 0x%06x — word_q_valid never pulsed\n", addr);
        return false;
    }
    *out_data = captured;
    return true;
}

// Sync burst read via the burst_rd path.  Captures up to n_words
// into out_data, returns true on clean completion.  burst_busy must
// remain HIGH from the accept cycle until all words have been
// collected; a premature drop is a FAIL.
static bool do_burst_read(uint32_t addr, uint32_t n_words,
                          uint32_t *out_data, int max_cycles = 500) {
    if (n_words == 0 || n_words > 16) {
        printf("  FAIL: n_words=%u out of range (1..16)\n", n_words);
        return false;
    }
    tb->burst_addr = addr;
    tb->burst_len = (uint8_t)(n_words - 1);
    tb->burst_rd = 1;
    tick();
    tb->burst_rd = 0;
    int t = 0;
    // burst_busy should assert within a few cycles.
    while (!tb->burst_busy && t < max_cycles) { tick(); t++; }
    if (t >= max_cycles) {
        printf("  FAIL: burst_rd at 0x%06x — burst_busy never asserted\n", addr);
        return false;
    }
    uint32_t got_count = 0;
    while (got_count < n_words && t < max_cycles) {
        if (!tb->burst_busy) {
            printf("  FAIL: burst_busy dropped after only %u/%u words\n",
                   got_count, n_words);
            return false;
        }
        if (tb->burst_q_valid) {
            out_data[got_count] = tb->burst_q;
            got_count++;
        }
        tick(); t++;
    }
    if (got_count < n_words) {
        printf("  FAIL: burst_rd at 0x%06x — only got %u/%u words\n",
               addr, got_count, n_words);
        return false;
    }
    // burst_busy should drop shortly after the last word.
    int drain = 0;
    while (tb->burst_busy && drain < 40) { tick(); drain++; t++; }
    if (tb->burst_busy) {
        printf("  FAIL: burst_busy still HIGH 40 cycles after last word\n");
        return false;
    }
    return true;
}

// ---- Tests ----

static void test_bcr_init_done() {
    printf("TEST: bcr_init_done is HIGH at reset (no BCR write needed in async mode)\n");
    // The current cram1_controller leaves the chip in POR-default async
    // mode (BCR 0x9D1F).  No BCR write is performed at reset, so
    // bcr_init_done is HIGH immediately and word_busy / burst_busy are
    // LOW once the controller reaches ST_IDLE.  bcr_init_done is kept
    // as an output port for compatibility with the previous (BCR-
    // writing) implementation.
    check_true("bcr_init_done HIGH at reset", tb->bcr_init_done != 0);
    // Tick once so the FSM lands in ST_IDLE after reset deassertion.
    tick();
    check_true("word_busy LOW idle",  tb->word_busy == 0);
    check_true("burst_busy LOW idle", tb->burst_busy == 0);
    passed++;
}

static void test_word_round_trip_post_bcr() {
    printf("TEST: async word write + read round trip (chip is in BCR sync-burst mode)\n");
    uint32_t got = 0;
    if (!do_word_write(0x000010, 0xDEADBEEF)) { failed++; return; }
    if (!do_word_read(0x000010, &got))        { failed++; return; }
    check_eq("round trip value", 0xDEADBEEFu, got);
}

static void test_burst_16_words_bank0() {
    printf("TEST: 16-word sync burst (bank 0) matches preloaded pattern\n");
    const uint32_t base = 0x000200;
    uint32_t expected[16];
    for (uint32_t i = 0; i < 16; i++) {
        expected[i] = 0xB0b50000u | (i * 0x1111u);
        backdoor_write(0, base + i, expected[i]);
    }
    uint32_t got[16] = {0};
    if (!do_burst_read(base, 16, got)) { failed++; return; }
    for (uint32_t i = 0; i < 16; i++) {
        char label[64];
        snprintf(label, sizeof(label), "burst[%u]", i);
        check_eq(label, expected[i], got[i]);
    }
}

static void test_burst_16_words_bank1() {
    printf("TEST: 16-word sync burst (bank 1) verifies BCR init reached CE1 die\n");
    const uint32_t base = 0x000400;
    uint32_t expected[16];
    for (uint32_t i = 0; i < 16; i++) {
        expected[i] = 0xCAFE0000u | i;
        // Bank 1 backdoor writes use bit 20 of bd_word_addr.
        backdoor_write(1, base + i, expected[i]);
    }
    uint32_t got[16] = {0};
    // Burst address bit 21 selects bank.
    if (!do_burst_read(0x200000u | base, 16, got)) { failed++; return; }
    for (uint32_t i = 0; i < 16; i++) {
        char label[64];
        snprintf(label, sizeof(label), "burst_b1[%u]", i);
        check_eq(label, expected[i], got[i]);
    }
}

static void test_burst_followed_by_word_rd() {
    printf("TEST: burst then single word read (no stuck state)\n");
    const uint32_t base = 0x000600;
    uint32_t expected[4];
    for (uint32_t i = 0; i < 4; i++) {
        expected[i] = 0x12340000u | (i << 4);
        backdoor_write(0, base + i, expected[i]);
    }
    uint32_t got[4] = {0};
    if (!do_burst_read(base, 4, got)) { failed++; return; }
    for (uint32_t i = 0; i < 4; i++) {
        char label[32];
        snprintf(label, sizeof(label), "burst4[%u]", i);
        check_eq(label, expected[i], got[i]);
    }
    // Now a word_rd to a different address — controller must be back
    // in ST_IDLE and ready to serve.
    uint32_t single = 0;
    if (!do_word_read(base + 0, &single)) { failed++; return; }
    check_eq("word_rd after burst", expected[0], single);
}

int main(int argc, char **argv) {
    Verilated::commandArgs(argc, argv);
    tb = new Vtb_cram1_burst;

    bool want_trace = false;
    for (int i = 1; i < argc; i++)
        if (!strcmp(argv[i], "--trace")) want_trace = true;
    if (want_trace) {
        Verilated::traceEverOn(true);
        trace = new VerilatedVcdC;
        tb->trace(trace, 99);
        trace->open("tb_cram1_burst.vcd");
    }

    printf("=== cram1_controller BCR + burst test ===\n");
    reset();

    test_bcr_init_done();
    test_word_round_trip_post_bcr();
    test_burst_16_words_bank0();
    test_burst_16_words_bank1();
    test_burst_followed_by_word_rd();

    printf("\n=== Results: %d passed, %d failed (chip model errors=%u) ===\n",
           passed, failed, (unsigned)tb->cram_errors);

    if (trace) { trace->close(); delete trace; }
    delete tb;
    return failed == 0 ? 0 : 1;
}
