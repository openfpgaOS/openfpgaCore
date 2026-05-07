#include "Vtb_cram0_prefetch.h"
#include <verilated.h>
#include <cstdio>
#include <cstdint>

static Vtb_cram0_prefetch *tb;
static vluint64_t sim_time = 0;
static int passes = 0;
static int fails = 0;

static int feed_remaining = 0;
static uint32_t feed_word = 0;
static uint32_t words_fed = 0;

static uint32_t word_data(uint32_t word_addr) {
    return 0xA5000000u | (word_addr & 0x000FFFFFu);
}

static void check_eq(const char *tag, uint32_t got, uint32_t expect) {
    if (got == expect) {
        passes++;
        printf("  OK  %s\n", tag);
    } else {
        fails++;
        printf("  FAIL %s got=0x%08x exp=0x%08x\n", tag, got, expect);
    }
}

static void drive_fetch_inputs() {
    tb->burst_rdata_valid = (feed_remaining > 0);
    tb->ctrl_busy = (feed_remaining > 0);
    tb->burst_rdata = word_data(feed_word);
}

static void tick() {
    drive_fetch_inputs();
    tb->clk = 0;
    tb->eval();
    sim_time++;
    tb->clk = 1;
    tb->eval();
    sim_time++;

    if (tb->burst_rdata_valid) {
        feed_remaining--;
        feed_word++;
        words_fed++;
    }

    if (tb->burst_rd) {
        feed_word = tb->burst_addr;
        feed_remaining = (uint32_t)tb->burst_len + 1u;
    }
}

static void reset_sequence() {
    tb->reset_n = 0;
    tb->start = 0;
    tb->start_bridge_addr = 0;
    tb->start_length = 0;
    tb->bridge_owner = 1;
    tb->bridge_rd_pulse = 0;
    tb->bridge_addr = 0;
    tb->burst_rdata = 0;
    tb->burst_rdata_valid = 0;
    tb->ctrl_busy = 0;
    feed_remaining = 0;
    feed_word = 0;
    words_fed = 0;
    for (int i = 0; i < 6; i++) tick();
    tb->reset_n = 1;
    for (int i = 0; i < 4; i++) tick();
}

static void start_transfer(uint32_t bridge_addr, uint32_t length) {
    tb->start_bridge_addr = bridge_addr;
    tb->start_length = length;
    tb->start = 1;
    tick();
    tb->start = 0;
}

static bool wait_for_words(uint32_t words, int max_cycles = 20000) {
    while (words_fed < words && max_cycles-- > 0)
        tick();
    return words_fed >= words;
}

static bool wait_for_ready(int max_cycles = 20000) {
    while (!tb->ready && max_cycles-- > 0)
        tick();
    return tb->ready;
}

static void test_short_transfer_ready_and_data() {
    printf("test_short_transfer_ready_and_data:\n");
    reset_sequence();
    const uint32_t base = 0x20100000u;
    start_transfer(base, 16);

    check_eq("short-ready-initial", tb->ready, 0u);
    if (!wait_for_words(1))
        check_eq("short-first-word-arrived", 0u, 1u);
    check_eq("short-ready-after-one-word", tb->ready, 0u);
    check_eq("short-ready-eventually", wait_for_ready() ? 1u : 0u, 1u);
    check_eq("short-prefetched-word-count", words_fed, 4u);

    for (uint32_t i = 0; i < 4; i++) {
        tb->bridge_addr = base + i * 4u;
        tb->bridge_rd_pulse = 1;
        tb->eval();
        char tag[64];
        std::snprintf(tag, sizeof(tag), "short-hit-%u", i);
        check_eq(tag, tb->bridge_hit, 1u);
        std::snprintf(tag, sizeof(tag), "short-data-%u", i);
        check_eq(tag, tb->bridge_rd_data, word_data((base >> 2) + i));
        tick();
        tb->bridge_rd_pulse = 0;
        tick();
    }

    check_eq("short-inactive-after-last", tb->active, 0u);
}

static void test_full_chunk_ready_waits_for_fifo() {
    printf("test_full_chunk_ready_waits_for_fifo:\n");
    reset_sequence();
    start_transfer(0x20102000u, 8192);

    if (!wait_for_words(32))
        check_eq("full-first-burst-arrived", 0u, 1u);
    check_eq("full-ready-after-first-burst", tb->ready, 0u);
    check_eq("full-ready-eventually", wait_for_ready(100000) ? 1u : 0u, 1u);
    check_eq("full-ready-at-depth", words_fed, 2048u);
}

static void test_large_transfer_ready_at_full_fifo() {
    printf("test_large_transfer_ready_at_full_fifo:\n");
    reset_sequence();
    start_transfer(0x20104000u, 12288);

    check_eq("large-ready-eventually", wait_for_ready(100000) ? 1u : 0u, 1u);
    check_eq("large-prefetch-stops-at-depth", words_fed, 2048u);
}

int main(int argc, char **argv) {
    Verilated::commandArgs(argc, argv);
    tb = new Vtb_cram0_prefetch;

    printf("=== cram0_bridge_prefetch test ===\n");
    test_short_transfer_ready_and_data();
    test_full_chunk_ready_waits_for_fifo();
    test_large_transfer_ready_at_full_fifo();

    printf("\n=== Results: %d passed, %d failed ===\n", passes, fails);
    delete tb;
    return fails == 0 ? 0 : 1;
}
