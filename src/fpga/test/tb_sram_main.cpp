/*
 * Verilator C++ Test Harness for SRAM Controller
 *
 * Tests: single R/W, byte strobes, sequential access, different addresses.
 * Drives the sram_controller word-level interface directly (no AXI).
 */

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <cassert>
#include "Vtb_sram.h"
#include "verilated.h"
#include "verilated_vcd_c.h"

static Vtb_sram *tb;
static VerilatedVcdC *trace;
static uint64_t sim_time = 0;
static int pass_count = 0;
static int fail_count = 0;

static void tick() {
    tb->clk = 0;
    tb->eval();
    if (trace) trace->dump(sim_time);
    sim_time++;
    tb->clk = 1;
    tb->eval();
    if (trace) trace->dump(sim_time);
    sim_time++;
}

static void reset() {
    tb->reset_n = 0;
    tb->word_rd = 0;
    tb->word_wr = 0;
    tb->word_addr = 0;
    tb->word_data = 0;
    tb->word_wstrb = 0;
    for (int i = 0; i < 20; i++)
        tick();
    tb->reset_n = 1;
    for (int i = 0; i < 5; i++)
        tick();
}

// ---- Word-level Write ----
static bool word_write(uint32_t addr, uint32_t data, uint8_t wstrb = 0xF, int timeout = 500) {
    tb->word_wr = 1;
    tb->word_addr = addr;
    tb->word_data = data;
    tb->word_wstrb = wstrb;
    tick();  // Controller latches on this posedge
    tb->word_wr = 0;

    // Wait for busy to appear then clear
    for (int t = 0; t < timeout; t++) {
        tick();
        if (!tb->word_busy) return true;
    }
    printf("  TIMEOUT: word_write addr=0x%05x\n", addr);
    return false;
}

// ---- Word-level Read ----
static bool word_read(uint32_t addr, uint32_t *data, int timeout = 500) {
    tb->word_rd = 1;
    tb->word_addr = addr;
    tick();  // Controller latches on this posedge
    tb->word_rd = 0;

    for (int t = 0; t < timeout; t++) {
        tick();
        if (tb->word_q_valid) {
            *data = tb->word_q;
            return true;
        }
    }
    printf("  TIMEOUT: word_read addr=0x%05x\n", addr);
    return false;
}

static uint32_t word_read_val(uint32_t addr) {
    uint32_t val = 0xBADBAD;
    word_read(addr, &val);
    return val;
}

// ---- Test Helpers ----
static void check(const char *name, uint32_t got, uint32_t expected) {
    if (got == expected) {
        pass_count++;
    } else {
        printf("  FAIL %s: got 0x%08x, expected 0x%08x\n", name, got, expected);
        fail_count++;
    }
}

// =====================================================================
// Test Cases
// =====================================================================

static void test_single_write_read() {
    printf("TEST: Single word write/read\n");

    word_write(0x0000, 0xDEADBEEF);
    uint32_t val = word_read_val(0x0000);
    check("single_rw", val, 0xDEADBEEF);

    word_write(0x0001, 0xCAFEBABE);
    val = word_read_val(0x0001);
    check("single_rw_2", val, 0xCAFEBABE);

    // Verify first write not corrupted
    val = word_read_val(0x0000);
    check("single_rw_verify", val, 0xDEADBEEF);
}

static void test_byte_strobe_byte0() {
    printf("TEST: Byte strobe - byte 0 only\n");

    word_write(0x0100, 0x12345678);
    word_write(0x0100, 0x000000AA, 0x1);
    uint32_t val = word_read_val(0x0100);
    check("strobe_b0", val, 0x123456AA);
}

static void test_byte_strobe_byte3() {
    printf("TEST: Byte strobe - byte 3 only\n");

    word_write(0x0101, 0xAABBCCDD);
    word_write(0x0101, 0xFF000000, 0x8);
    uint32_t val = word_read_val(0x0101);
    check("strobe_b3", val, 0xFFBBCCDD);
}

static void test_byte_strobe_halfword() {
    printf("TEST: Byte strobe - low halfword (bytes 0-1)\n");

    word_write(0x0102, 0x11223344);
    word_write(0x0102, 0x0000AABB, 0x3);
    uint32_t val = word_read_val(0x0102);
    check("strobe_lo_half", val, 0x1122AABB);
}

static void test_byte_strobe_upper_halfword() {
    printf("TEST: Byte strobe - high halfword (bytes 2-3)\n");

    word_write(0x0103, 0x55667788);
    word_write(0x0103, 0xCCDD0000, 0xC);
    uint32_t val = word_read_val(0x0103);
    check("strobe_hi_half", val, 0xCCDD7788);
}

static void test_sequential_writes() {
    printf("TEST: Sequential word writes\n");

    for (int i = 0; i < 16; i++)
        word_write(0x0200 + i, 0xA0000000 | i);

    for (int i = 0; i < 16; i++) {
        uint32_t val = word_read_val(0x0200 + i);
        char name[32];
        snprintf(name, sizeof(name), "seq[%d]", i);
        check(name, val, 0xA0000000 | i);
    }
}

static void test_interleaved_rw() {
    printf("TEST: Interleaved read/write\n");

    for (int i = 0; i < 8; i++) {
        word_write(0x0300 + i, 0xF0000000 | i);
        uint32_t val = word_read_val(0x0300 + i);
        char name[32];
        snprintf(name, sizeof(name), "intlv[%d]", i);
        check(name, val, 0xF0000000 | i);
    }
}

static void test_different_addresses() {
    printf("TEST: Writes to spread-out addresses\n");

    word_write(0x0000, 0x11111111);
    word_write(0x1000, 0x22222222);
    word_write(0x2000, 0x33333333);
    word_write(0xFFFF, 0x44444444);  // Max 16-bit word addr

    check("addr_0x0000", word_read_val(0x0000), 0x11111111);
    check("addr_0x1000", word_read_val(0x1000), 0x22222222);
    check("addr_0x2000", word_read_val(0x2000), 0x33333333);
    check("addr_0xFFFF", word_read_val(0xFFFF), 0x44444444);
}

static void test_all_bytes_pattern() {
    printf("TEST: All-bytes walking pattern\n");

    // Write a pattern where each byte is different
    word_write(0x0400, 0x01020304);
    uint32_t val = word_read_val(0x0400);
    check("walk_full", val, 0x01020304);

    // Overwrite each byte individually
    word_write(0x0400, 0x000000FF, 0x1);
    val = word_read_val(0x0400);
    check("walk_b0", val, 0x010203FF);

    word_write(0x0400, 0x0000EE00, 0x2);
    val = word_read_val(0x0400);
    check("walk_b1", val, 0x0102EEFF);

    word_write(0x0400, 0x00DD0000, 0x4);
    val = word_read_val(0x0400);
    check("walk_b2", val, 0x01DDEEFF);

    word_write(0x0400, 0xCC000000, 0x8);
    val = word_read_val(0x0400);
    check("walk_b3", val, 0xCCDDEEFF);
}

int main(int argc, char **argv) {
    Verilated::commandArgs(argc, argv);
    Verilated::traceEverOn(true);
    tb = new Vtb_sram;
    trace = new VerilatedVcdC;
    tb->trace(trace, 99);
    trace->open("sram_test.vcd");

    printf("=== SRAM Controller Test Suite ===\n\n");

    reset();
    printf("SRAM initialized (%lu cycles)\n\n", (unsigned long)(sim_time / 2));

    test_single_write_read();
    test_byte_strobe_byte0();
    // Stop tracing to keep VCD small
    if (trace) { trace->close(); delete trace; trace = nullptr; }
    test_byte_strobe_byte3();
    test_byte_strobe_halfword();
    test_byte_strobe_upper_halfword();
    test_sequential_writes();
    test_interleaved_rw();
    test_different_addresses();
    test_all_bytes_pattern();

    printf("\n=== Results: %d passed, %d failed ===\n",
           pass_count, fail_count);

    if (trace) { trace->close(); delete trace; }
    delete tb;
    return fail_count > 0 ? 1 : 0;
}
