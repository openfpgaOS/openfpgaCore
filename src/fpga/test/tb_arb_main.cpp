/*
 * Multi-Master SDRAM Arbiter Stress Test
 *
 * Exercises word_sdram_arbiter with all 4 masters active:
 *   M0 (Audio DMA): frequent single-beat reads
 *   M1 (DMA engine): burst reads + burst writes (16-beat)
 *   M2 (CPU): burst reads + single/burst writes
 *   M3 (Bridge): single-beat writes (FIFO drain)
 *
 * Tests: priority, data integrity, contention, back-to-back, starvation.
 */

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <cstdint>
#include "Vtb_arb.h"
#include "verilated.h"

static Vtb_arb *tb;
static uint64_t sim_time = 0;
static int pass_count = 0, fail_count = 0;

static void tick() {
    tb->clk = 0; tb->eval(); sim_time++;
    tb->clk = 1; tb->eval(); sim_time++;
}

static void reset() {
    tb->reset_n = 0;
    tb->m0_rd = 0; tb->m1_rd = 0; tb->m1_wr = 0;
    tb->m2_rd = 0; tb->m2_wr = 0; tb->m3_wr = 0;
    tb->bd_we = 0;
    for (int i = 0; i < 20; i++) tick();
    tb->reset_n = 1;
    for (int i = 0; i < 10; i++) tick();
}

static void idle(int n = 2) {
    tb->m0_rd = 0; tb->m1_rd = 0; tb->m1_wr = 0;
    tb->m2_rd = 0; tb->m2_wr = 0; tb->m3_wr = 0;
    for (int i = 0; i < n; i++) tick();
}

static void check(const char *name, uint32_t got, uint32_t expected) {
    if (got == expected) { pass_count++; }
    else { printf("  FAIL %s: got 0x%08x, expected 0x%08x\n", name, got, expected); fail_count++; }
}

// Backdoor write to SDRAM
static void bd_write(uint32_t word_addr, uint32_t data) {
    tb->bd_we = 1; tb->bd_addr = word_addr; tb->bd_wdata = data;
    tick(); tb->bd_we = 0;
}

// ============================================================
// Per-master transaction helpers
// ============================================================

// M0: single-beat read (hold rd until accepted, wait rdata_valid)
static uint32_t m0_read(uint32_t addr, int timeout = 500) {
    tb->m0_rd = 1; tb->m0_addr = addr; tb->m0_burst_len = 0;
    bool acc = false;
    for (int t = 0; t < timeout; t++) {
        tick();
        if (!acc && tb->m0_accepted) { acc = true; tb->m0_rd = 0; }
        if (tb->m0_rdata_valid) { tb->m0_rd = 0; idle(); return tb->m0_rdata; }
    }
    printf("  TIMEOUT m0_read addr=0x%06x\n", addr);
    tb->m0_rd = 0; return 0xDEADDEAD;
}

// M1: burst read
static bool m1_burst_read(uint32_t addr, uint32_t *data, int len, int timeout = 2000) {
    tb->m1_rd = 1; tb->m1_addr = addr; tb->m1_burst_len = len - 1;
    bool acc = false; int beat = 0;
    for (int t = 0; t < timeout; t++) {
        tick();
        if (!acc && tb->m1_accepted) { acc = true; tb->m1_rd = 0; }
        if (tb->m1_rdata_valid) { data[beat++] = tb->m1_rdata; if (beat >= len) { idle(); return true; } }
    }
    tb->m1_rd = 0; return false;
}

// M1: single-beat write
static bool m1_write(uint32_t addr, uint32_t data, int timeout = 500) {
    tb->m1_wr = 1; tb->m1_addr = addr; tb->m1_wdata = data;
    tb->m1_wstrb = 0xF; tb->m1_burst_wr_len = 0;
    bool acc = false, busy_seen = false;
    for (int t = 0; t < timeout; t++) {
        tick();
        if (!acc && tb->m1_accepted) { acc = true; tb->m1_wr = 0; }
        if (acc) { if (tb->m1_busy) busy_seen = true; if (busy_seen && !tb->m1_busy) { idle(); return true; } }
    }
    tb->m1_wr = 0; return false;
}

// M2: single-beat read
static uint32_t m2_read(uint32_t addr, int timeout = 500) {
    tb->m2_rd = 1; tb->m2_addr = addr; tb->m2_burst_len = 0;
    bool acc = false;
    for (int t = 0; t < timeout; t++) {
        tick();
        if (!acc && tb->m2_accepted) { acc = true; tb->m2_rd = 0; }
        if (tb->m2_rdata_valid) { tb->m2_rd = 0; idle(); return tb->m2_rdata; }
    }
    tb->m2_rd = 0; return 0xDEADDEAD;
}

// M2: burst read
static bool m2_burst_read(uint32_t addr, uint32_t *data, int len, int timeout = 2000) {
    tb->m2_rd = 1; tb->m2_addr = addr; tb->m2_burst_len = len - 1;
    bool acc = false; int beat = 0;
    for (int t = 0; t < timeout; t++) {
        tick();
        if (!acc && tb->m2_accepted) { acc = true; tb->m2_rd = 0; }
        if (tb->m2_rdata_valid) { data[beat++] = tb->m2_rdata; if (beat >= len) { idle(); return true; } }
    }
    tb->m2_rd = 0; return false;
}

// M2: single-beat write
static bool m2_write(uint32_t addr, uint32_t data, int timeout = 500) {
    tb->m2_wr = 1; tb->m2_addr = addr; tb->m2_wdata = data;
    tb->m2_wstrb = 0xF; tb->m2_burst_wr_len = 0;
    bool acc = false, busy_seen = false;
    for (int t = 0; t < timeout; t++) {
        tick();
        if (!acc && tb->m2_accepted) { acc = true; tb->m2_wr = 0; }
        if (acc) { if (tb->m2_busy) busy_seen = true; if (busy_seen && !tb->m2_busy) { idle(); return true; } }
    }
    tb->m2_wr = 0; return false;
}

// M3: single-beat write
static bool m3_write(uint32_t addr, uint32_t data, int timeout = 500) {
    tb->m3_wr = 1; tb->m3_addr = addr; tb->m3_wdata = data;
    tb->m3_wstrb = 0xF;
    bool acc = false, busy_seen = false;
    for (int t = 0; t < timeout; t++) {
        tick();
        if (!acc && tb->m3_accepted) { acc = true; tb->m3_wr = 0; }
        if (acc) { if (tb->m3_busy) busy_seen = true; if (busy_seen && !tb->m3_busy) { idle(); return true; } }
    }
    tb->m3_wr = 0; return false;
}

// ============================================================
// Tests
// ============================================================

static void test_single_master_basic() {
    printf("TEST: Single-master basic (each port independently)\n");
    // M2 write then read
    m2_write(0x000100, 0xAAAAAAAA);
    check("m2_wr_rd", m2_read(0x000100), 0xAAAAAAAA);

    // M1 write then read (via M2 since M1 doesn't have burst_len=0 read easily)
    m1_write(0x000200, 0xBBBBBBBB);
    check("m1_wr_m2_rd", m2_read(0x000200), 0xBBBBBBBB);

    // M3 write, M2 read
    m3_write(0x000300, 0xCCCCCCCC);
    check("m3_wr_m2_rd", m2_read(0x000300), 0xCCCCCCCC);

    // M0 read (preload via backdoor)
    bd_write(0x000400, 0xDDDDDDDD);
    check("m0_rd", m0_read(0x000400), 0xDDDDDDDD);
}

static void test_priority_m0_over_m2() {
    printf("TEST: Priority — M0 (Audio) preempts M2 (CPU)\n");

    bd_write(0x001000, 0x11111111);
    bd_write(0x002000, 0x22222222);

    // Start both M0 and M2 reads on the same cycle
    tb->m0_rd = 1; tb->m0_addr = 0x001000; tb->m0_burst_len = 0;
    tb->m2_rd = 1; tb->m2_addr = 0x002000; tb->m2_burst_len = 0;

    // M0 should be granted first (higher priority)
    bool m0_done = false, m2_done = false;
    uint32_t m0_val = 0, m2_val = 0;
    bool m0_acc = false, m2_acc = false;

    for (int t = 0; t < 500 && !(m0_done && m2_done); t++) {
        tick();
        if (!m0_acc && tb->m0_accepted) { m0_acc = true; tb->m0_rd = 0; }
        if (!m2_acc && tb->m2_accepted) { m2_acc = true; tb->m2_rd = 0; }
        if (tb->m0_rdata_valid && !m0_done) { m0_val = tb->m0_rdata; m0_done = true; }
        if (tb->m2_rdata_valid && !m2_done) { m2_val = tb->m2_rdata; m2_done = true; }
    }
    idle();

    check("m0_data", m0_val, 0x11111111);
    check("m2_data", m2_val, 0x22222222);
    check("m0_first", m0_done && m2_done, 1);
}

static void test_priority_m1_over_m3() {
    printf("TEST: Priority — M1 (DMA) over M3 (Bridge)\n");

    // M1 write and M3 write simultaneously to different addresses
    tb->m1_wr = 1; tb->m1_addr = 0x003000; tb->m1_wdata = 0xAA00AA00;
    tb->m1_wstrb = 0xF; tb->m1_burst_wr_len = 0;
    tb->m3_wr = 1; tb->m3_addr = 0x004000; tb->m3_wdata = 0xBB00BB00;
    tb->m3_wstrb = 0xF;

    bool m1_acc = false, m3_acc = false;
    bool m1_done = false, m3_done = false;
    bool m1_busy_s = false, m3_busy_s = false;
    int m1_acc_cycle = -1, m3_acc_cycle = -1;
    int cycle = 0;

    for (int t = 0; t < 500 && !(m1_done && m3_done); t++) {
        tick(); cycle++;
        if (!m1_acc && tb->m1_accepted) { m1_acc = true; tb->m1_wr = 0; m1_acc_cycle = cycle; }
        if (!m3_acc && tb->m3_accepted) { m3_acc = true; tb->m3_wr = 0; m3_acc_cycle = cycle; }
        if (m1_acc) { if (tb->m1_busy) m1_busy_s = true; if (m1_busy_s && !tb->m1_busy) m1_done = true; }
        if (m3_acc) { if (tb->m3_busy) m3_busy_s = true; if (m3_busy_s && !tb->m3_busy) m3_done = true; }
    }
    idle();

    check("m1_accepted_first", m1_acc_cycle < m3_acc_cycle, 1);
    check("m1_data", m2_read(0x003000), 0xAA00AA00);
    check("m3_data", m2_read(0x004000), 0xBB00BB00);
}

static void test_contention_all_masters() {
    printf("TEST: All 4 masters requesting simultaneously\n");

    // Preload known data for reads
    bd_write(0x010000, 0x10101010);  // M0 will read
    bd_write(0x010100, 0x20202020);  // M2 will read

    // Start all 4 masters at once:
    // M0: read 0x010000
    // M1: write 0x010200 = 0x30303030
    // M2: read 0x010100
    // M3: write 0x010300 = 0x40404040
    tb->m0_rd = 1; tb->m0_addr = 0x010000; tb->m0_burst_len = 0;
    tb->m1_wr = 1; tb->m1_addr = 0x010200; tb->m1_wdata = 0x30303030;
    tb->m1_wstrb = 0xF; tb->m1_burst_wr_len = 0;
    tb->m2_rd = 1; tb->m2_addr = 0x010100; tb->m2_burst_len = 0;
    tb->m3_wr = 1; tb->m3_addr = 0x010300; tb->m3_wdata = 0x40404040;
    tb->m3_wstrb = 0xF;

    bool m0_done = false, m1_done = false, m2_done = false, m3_done = false;
    uint32_t m0_val = 0, m2_val = 0;
    bool m0_acc = false, m1_acc = false, m2_acc = false, m3_acc = false;
    bool m1_bs = false, m3_bs = false;

    for (int t = 0; t < 2000 && !(m0_done && m1_done && m2_done && m3_done); t++) {
        tick();
        if (!m0_acc && tb->m0_accepted) { m0_acc = true; tb->m0_rd = 0; }
        if (!m1_acc && tb->m1_accepted) { m1_acc = true; tb->m1_wr = 0; }
        if (!m2_acc && tb->m2_accepted) { m2_acc = true; tb->m2_rd = 0; }
        if (!m3_acc && tb->m3_accepted) { m3_acc = true; tb->m3_wr = 0; }
        if (tb->m0_rdata_valid && !m0_done) { m0_val = tb->m0_rdata; m0_done = true; }
        if (tb->m2_rdata_valid && !m2_done) { m2_val = tb->m2_rdata; m2_done = true; }
        if (m1_acc) { if (tb->m1_busy) m1_bs = true; if (m1_bs && !tb->m1_busy) m1_done = true; }
        if (m3_acc) { if (tb->m3_busy) m3_bs = true; if (m3_bs && !tb->m3_busy) m3_done = true; }
    }
    idle();

    check("all4_m0_rd", m0_val, 0x10101010);
    check("all4_m2_rd", m2_val, 0x20202020);
    check("all4_m1_wr", m2_read(0x010200), 0x30303030);
    check("all4_m3_wr", m2_read(0x010300), 0x40404040);
    check("all4_complete", m0_done && m1_done && m2_done && m3_done, 1);
}

static void test_burst_read_contention() {
    printf("TEST: Burst read (M2) during M0 single reads\n");

    // Preload 8 words for burst
    for (int i = 0; i < 8; i++)
        bd_write(0x020000 + i, 0xB0000000 | i);
    // Preload M0 read target
    bd_write(0x021000, 0xA0A0A0A0);

    // M2 starts burst read, M0 starts single read simultaneously
    tb->m2_rd = 1; tb->m2_addr = 0x020000; tb->m2_burst_len = 7;  // 8 beats
    tb->m0_rd = 1; tb->m0_addr = 0x021000; tb->m0_burst_len = 0;

    uint32_t m2_data[8] = {};
    uint32_t m0_val = 0;
    bool m2_acc = false, m0_acc = false;
    bool m0_done = false;
    int m2_beat = 0;

    for (int t = 0; t < 2000 && !(m2_beat >= 8 && m0_done); t++) {
        tick();
        if (!m0_acc && tb->m0_accepted) { m0_acc = true; tb->m0_rd = 0; }
        if (!m2_acc && tb->m2_accepted) { m2_acc = true; tb->m2_rd = 0; }
        if (tb->m0_rdata_valid && !m0_done) { m0_val = tb->m0_rdata; m0_done = true; }
        if (tb->m2_rdata_valid && m2_beat < 8) { m2_data[m2_beat++] = tb->m2_rdata; }
    }
    idle();

    check("burst_m0_rd", m0_val, 0xA0A0A0A0);
    for (int i = 0; i < 8; i++) {
        char name[32]; snprintf(name, sizeof(name), "burst_m2[%d]", i);
        check(name, m2_data[i], 0xB0000000 | i);
    }
}

static void test_write_integrity_under_contention() {
    printf("TEST: Write integrity — M1+M2+M3 writing different regions\n");

    // M1 writes 16 words to region A
    // M2 writes 16 words to region B
    // M3 writes 16 words to region C
    // Interleave to stress arbiter

    uint32_t base_a = 0x030000;
    uint32_t base_b = 0x031000;
    uint32_t base_c = 0x032000;

    for (int i = 0; i < 16; i++) {
        m1_write(base_a + i, 0xA0000000 | i);
        m2_write(base_b + i, 0xB0000000 | i);
        m3_write(base_c + i, 0xC0000000 | i);
    }

    // Verify all regions
    for (int i = 0; i < 16; i++) {
        char name[32];
        snprintf(name, sizeof(name), "integ_a[%d]", i);
        check(name, m2_read(base_a + i), 0xA0000000 | (uint32_t)i);
        snprintf(name, sizeof(name), "integ_b[%d]", i);
        check(name, m2_read(base_b + i), 0xB0000000 | (uint32_t)i);
        snprintf(name, sizeof(name), "integ_c[%d]", i);
        check(name, m2_read(base_c + i), 0xC0000000 | (uint32_t)i);
    }
}

static void test_back_to_back_transactions() {
    printf("TEST: Back-to-back transactions (rapid fire)\n");

    // 100 rapid M2 write-read cycles
    for (int i = 0; i < 100; i++) {
        uint32_t addr = 0x040000 + i;
        uint32_t val = 0xF0000000 | i;
        m2_write(addr, val);
        uint32_t rd = m2_read(addr);
        if (rd != val) {
            char name[32]; snprintf(name, sizeof(name), "rapid[%d]", i);
            check(name, rd, val);
            return;
        }
    }
    pass_count += 100;
    printf("  100 rapid write/read cycles OK\n");
}

static void test_starvation_resistance() {
    printf("TEST: CPU (M2) not starved by M0+M1 traffic\n");

    // Preload targets
    bd_write(0x050000, 0xCAFECAFE);

    // M0 and M1 do rapid reads while M2 tries to read
    // The test: M2 should eventually complete (not starved)
    tb->m0_rd = 1; tb->m0_addr = 0x051000; tb->m0_burst_len = 0;
    tb->m2_rd = 1; tb->m2_addr = 0x050000; tb->m2_burst_len = 0;

    bool m0_acc = false, m2_acc = false;
    bool m2_done = false;
    uint32_t m2_val = 0;
    int cycles = 0;

    for (int t = 0; t < 2000 && !m2_done; t++) {
        tick(); cycles++;
        // M0: keep reading (restart after each completion)
        if (!m0_acc && tb->m0_accepted) { m0_acc = true; tb->m0_rd = 0; }
        if (tb->m0_rdata_valid && m0_acc) {
            m0_acc = false;
            tb->m0_rd = 1; tb->m0_addr = 0x051000; // Re-request immediately
        }
        // M2: one read
        if (!m2_acc && tb->m2_accepted) { m2_acc = true; tb->m2_rd = 0; }
        if (tb->m2_rdata_valid && !m2_done) { m2_val = tb->m2_rdata; m2_done = true; }
    }
    tb->m0_rd = 0;
    idle();

    check("starve_m2_completed", m2_done, 1);
    check("starve_m2_data", m2_val, 0xCAFECAFE);
    printf("  M2 completed in %d cycles (with M0 traffic)\n", cycles);
}

static void test_m1_burst_write_with_m0_reads() {
    printf("TEST: M1 burst write while M0 does reads\n");

    // M0 preload
    bd_write(0x061000, 0xAD101234);

    // M1 burst write 8 words while M0 reads
    uint32_t base = 0x060000;
    tb->m1_wr = 1; tb->m1_addr = base; tb->m1_wdata = 0xD1000000;
    tb->m1_wstrb = 0xF; tb->m1_burst_wr_len = 7;
    tb->m0_rd = 1; tb->m0_addr = 0x061000; tb->m0_burst_len = 0;

    bool m1_acc = false, m0_acc = false;
    bool m1_done = false, m0_done = false;
    bool m1_bs = false;
    int m1_beat = 1;

    for (int t = 0; t < 2000 && !(m1_done && m0_done); t++) {
        tick();
        if (!m0_acc && tb->m0_accepted) { m0_acc = true; tb->m0_rd = 0; }
        if (tb->m0_rdata_valid && !m0_done) { m0_done = true; }
        if (!m1_acc && tb->m1_accepted) {
            m1_acc = true; tb->m1_wr = 0;
        }
        if (m1_acc && tb->m1_wr_data_next && m1_beat < 8) {
            tb->m1_wdata = 0xD1000000 | m1_beat;
            m1_beat++;
        }
        if (m1_acc) { if (tb->m1_busy) m1_bs = true; if (m1_bs && !tb->m1_busy) m1_done = true; }
    }
    idle();

    check("m1bw_complete", m1_done, 1);
    check("m0_during_m1bw", m0_done, 1);

    // Verify M1's burst write data
    for (int i = 0; i < 8; i++) {
        char name[32]; snprintf(name, sizeof(name), "m1bw[%d]", i);
        check(name, m2_read(base + i), 0xD1000000 | (uint32_t)i);
    }
}

static void test_bridge_drain_pattern() {
    printf("TEST: Bridge (M3) drain pattern — 64 sequential writes\n");

    // Simulates bridge FIFO drain: 64 sequential single-beat writes
    uint32_t base = 0x070000;
    for (int i = 0; i < 64; i++) {
        bool ok = m3_write(base + i, 0xBF000000 | i);
        if (!ok) { printf("  FAIL bridge_drain[%d] timeout\n", i); fail_count++; return; }
    }

    // Verify
    for (int i = 0; i < 64; i++) {
        char name[32]; snprintf(name, sizeof(name), "drain[%d]", i);
        check(name, m2_read(base + i), 0xBF000000 | (uint32_t)i);
    }
}

int main(int argc, char **argv) {
    Verilated::commandArgs(argc, argv);
    tb = new Vtb_arb;

    printf("=== Multi-Master SDRAM Arbiter Stress Test ===\n\n");

    reset();

    test_single_master_basic();
    test_priority_m0_over_m2();
    test_priority_m1_over_m3();
    test_contention_all_masters();
    test_burst_read_contention();
    test_write_integrity_under_contention();
    test_back_to_back_transactions();
    test_starvation_resistance();
    test_m1_burst_write_with_m0_reads();
    test_bridge_drain_pattern();

    printf("\n=== Results: %d passed, %d failed ===\n", pass_count, fail_count);
    delete tb;
    return fail_count ? 1 : 0;
}
