//
// C++ harness for tb_axi_periph — tests axi_periph_slave's BRAM and
// peripheral read paths.  Specifically exercises the scenarios that
// the HW boot stub hits (cacheline instruction fetches, polling
// system-register reads) so we can reproduce the "Load Failed E 2"
// state in sim if there's a bug in the hold-until-rready +
// bram_hold rework.
//

#include "Vtb_axi_periph.h"
#include "Vtb_axi_periph___024root.h"
#include <verilated.h>
#include <cstdio>
#include <cstdint>
#include <cstring>
#include <vector>

static Vtb_axi_periph *tb;
static vluint64_t sim_time = 0;
static uint64_t cycle_count = 0;
static int fails = 0;
static int passes = 0;

static void tick() {
    tb->clk = 0;
    tb->eval();
    sim_time++;
    tb->clk = 1;
    tb->eval();
    sim_time++;
    cycle_count++;
}

static void reset_sequence() {
    tb->reset_n = 0;
    tb->s_axi_arvalid = 0;
    tb->s_axi_rready  = 0;
    tb->s_axi_awvalid = 0;
    tb->s_axi_wvalid  = 0;
    tb->s_axi_bready  = 0;
    for (int i = 0; i < 10; i++) tick();
    tb->reset_n = 1;
    for (int i = 0; i < 10; i++) tick();
}

// Backdoor preload of BRAM via Verilator's public_flat_rw pragma on
// the altsyncram_stub's mem[] array.  Verilator exposes the mem in
// the root struct with a hierarchical-mangled name.
static void bram_poke(uint32_t word_addr, uint32_t value) {
    tb->rootp->tb_axi_periph__DOT__dut__DOT__ram__DOT__mem[word_addr] = value;
}

// ====================================================================
// AXI helpers
// ====================================================================
static bool axi_read_burst(uint32_t addr, uint32_t arlen,
                           std::vector<uint32_t> &out,
                           uint32_t stall_mask = 0) {
    out.clear();
    tb->s_axi_arvalid = 1;
    tb->s_axi_araddr  = addr;
    tb->s_axi_arlen   = arlen;
    tb->s_axi_rready  = 0;

    int cycles = 0;
    bool ar_done = false;
    while (cycles++ < 5000 && !ar_done) {
        tb->eval();
        if (tb->s_axi_arvalid && tb->s_axi_arready) ar_done = true;
        tick();
        if (ar_done) tb->s_axi_arvalid = 0;
    }
    if (!ar_done) {
        printf("  READ 0x%08x: AR timeout\n", addr);
        return false;
    }

    uint32_t want = arlen + 1;
    bool stall_this_cycle = false;
    cycles = 0;
    while (out.size() < want && cycles++ < 20000) {
        bool stall_bit  = (stall_mask >> (out.size() & 31)) & 1;
        bool should_stall = stall_bit && !stall_this_cycle;
        tb->s_axi_rready = should_stall ? 0 : 1;
        tb->eval();
        if (!should_stall && tb->s_axi_rvalid && tb->s_axi_rready) {
            out.push_back(tb->s_axi_rdata);
            stall_this_cycle = false;
        } else {
            stall_this_cycle = should_stall;
        }
        tick();
    }
    tb->s_axi_rready = 0;
    for (int i = 0; i < 4; i++) tick();
    if (out.size() != want) {
        printf("  READ 0x%08x: got %zu beats, expected %u\n",
               addr, out.size(), want);
        return false;
    }
    return true;
}

static void check_eq(const char *tag, uint32_t got, uint32_t expect) {
    if (got == expect) { passes++; printf("  OK  %s\n", tag); }
    else { fails++; printf("  FAIL %s got=0x%08x exp=0x%08x\n", tag, got, expect); }
}

// ====================================================================
// Tests
// ====================================================================
static void test_single_word_bram_read() {
    printf("test_single_word_bram_read:\n");
    bram_poke(0x000,  0xDEADBEEF);
    bram_poke(0x010,  0xCAFEBABE);
    bram_poke(0x400,  0x12345678);
    bram_poke(0x1000, 0x5A5A5A5A);

    std::vector<uint32_t> r;
    if (axi_read_burst(0x00000000, 0, r)) check_eq("bram[0x0000]", r[0], 0xDEADBEEF);
    if (axi_read_burst(0x00000040, 0, r)) check_eq("bram[0x0040]", r[0], 0xCAFEBABE);
    if (axi_read_burst(0x00001000, 0, r)) check_eq("bram[0x1000]", r[0], 0x12345678);
    if (axi_read_burst(0x00004000, 0, r)) check_eq("bram[0x4000]", r[0], 0x5A5A5A5A);
}

static void test_cacheline_bram_burst() {
    printf("test_cacheline_bram_burst (arlen=15, 16 beats):\n");
    // Preload cacheline with a ramp at word 0x100 (byte 0x400)
    for (uint32_t i = 0; i < 16; i++)
        bram_poke(0x100 + i, 0xB00B0000 + i);

    std::vector<uint32_t> r;
    if (axi_read_burst(0x00000400, 15, r)) {
        int ok = 1;
        for (uint32_t i = 0; i < 16; i++) {
            if (r[i] != (0xB00B0000 + i)) {
                ok = 0;
                printf("  beat %u: got=0x%08x exp=0x%08x\n",
                       i, r[i], 0xB00B0000 + i);
            }
        }
        check_eq("cacheline-burst-16", ok, 1);
    }
}

static void test_back_to_back_cachelines() {
    printf("test_back_to_back_cachelines:\n");
    for (uint32_t cl = 0; cl < 4; cl++)
        for (uint32_t i = 0; i < 16; i++)
            bram_poke(0x200 + cl*16 + i, 0xC0FFEE00 + cl*16 + i);

    int ok = 1;
    for (uint32_t cl = 0; cl < 4; cl++) {
        std::vector<uint32_t> r;
        if (!axi_read_burst(0x00000800 + cl*64, 15, r)) { ok = 0; break; }
        for (uint32_t i = 0; i < 16; i++) {
            uint32_t exp = 0xC0FFEE00 + cl*16 + i;
            if (r[i] != exp) {
                ok = 0;
                printf("  cl %u beat %u: got=0x%08x exp=0x%08x\n",
                       cl, i, r[i], exp);
            }
        }
    }
    check_eq("b2b-cachelines-4x16", ok, 1);
}

static void test_burst_with_backpressure() {
    printf("test_burst_with_backpressure (stall every other beat):\n");
    for (uint32_t i = 0; i < 16; i++)
        bram_poke(0x300 + i, 0xBACC0000 + i);

    std::vector<uint32_t> r;
    // Stall every other beat (mask = 0xAAAA)
    if (axi_read_burst(0x00000C00, 15, r, 0xAAAA)) {
        int ok = 1;
        for (uint32_t i = 0; i < 16; i++) {
            if (r[i] != (0xBACC0000 + i)) {
                ok = 0;
                printf("  beat %u: got=0x%08x exp=0x%08x\n",
                       i, r[i], 0xBACC0000 + i);
            }
        }
        check_eq("bp-burst-16", ok, 1);
    }
}

// Simulate the boot stub's polling of a sysreg: read the same
// peripheral address many times in a row.  0x40000000 is SYSREG_BASE.
// 0x4000003C is DS_STATUS (per regs.h).  The stub returns 0 here
// because we don't drive dataslot_ack/done; the important thing is
// that the polling loop completes without hanging.
static void test_sysreg_polling() {
    printf("test_sysreg_polling:\n");
    int ok = 1;
    for (int i = 0; i < 32; i++) {
        std::vector<uint32_t> r;
        if (!axi_read_burst(0x4000003C, 0, r)) { ok = 0; break; }
        // Value may be anything; the important bit is that each read
        // completes in bounded time (no hang).
        (void)r;
    }
    check_eq("poll-ds-status-32x", ok, 1);
}

// Mixed BRAM + peripheral pattern (like boot stub: fetch instruction,
// poll MMIO, fetch more, etc.)
static void test_mixed_bram_periph() {
    printf("test_mixed_bram_periph:\n");
    for (uint32_t i = 0; i < 16; i++) bram_poke(0x400 + i, 0xAA550000 + i);
    int ok = 1;
    for (int i = 0; i < 16; i++) {
        std::vector<uint32_t> r;
        // Fetch a BRAM word
        if (!axi_read_burst(0x00001000 + i*4, 0, r)) { ok = 0; break; }
        if (r[0] != (0xAA550000 + i)) {
            ok = 0;
            printf("  iter %d: bram got=0x%08x\n", i, r[0]);
        }
        // Poll a peripheral
        if (!axi_read_burst(0x40000000, 0, r)) { ok = 0; break; }
    }
    check_eq("mixed-32-ops", ok, 1);
}

int main(int argc, char **argv) {
    Verilated::commandArgs(argc, argv);
    tb = new Vtb_axi_periph;

    printf("=== axi_periph_slave test ===\n");
    reset_sequence();

    test_single_word_bram_read();
    test_cacheline_bram_burst();
    test_back_to_back_cachelines();
    test_burst_with_backpressure();
    test_sysreg_polling();
    test_mixed_bram_periph();

    printf("\n=== Results: %d passed, %d failed ===\n", passes, fails);
    delete tb;
    return fails == 0 ? 0 : 1;
}
