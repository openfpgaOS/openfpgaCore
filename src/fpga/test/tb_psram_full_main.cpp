/*
 * Verilator C++ Test Harness for Full-RTL PSRAM/SRAM Controller Stack
 *
 * High-resolution multi-clock: 20 time steps per clk period.
 *   clk:        posedge at step 0, negedge at step 10
 *   cram_clk:   posedge at step 12, negedge at step 2  (58% phase offset ≈ 5500ps/9524ps)
 *   clk_bridge: toggles every 3 clk periods (~1/3 rate, async)
 *
 * Bridge arbitration: concurrent bridge writes to CRAM0 via async FIFO CDC path.
 * Checks assertion_errors for bus contention and protocol violations.
 */

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <cassert>
#include "Vtb_psram_full.h"
#include "verilated.h"
#include "verilated_vcd_c.h"

static Vtb_psram_full *tb;
static VerilatedVcdC *trace;
static uint64_t sim_time = 0;
static int pass_count = 0;
static int fail_count = 0;
static int tick_count = 0;

// 20-step high-resolution tick: models precise phase relationships
// Step  0: clk ↑ (posedge)         — controller logic updates
// Step  2: cram_clk ↓ (negedge)
// Step 10: clk ↓ (negedge)
// Step 12: cram_clk ↑ (posedge)    — CRAM chip samples (58% phase offset)
// bridge_clk toggles every 3rd tick at step 7 (mid-period, async)
static void step(int s) {
    // Update clocks based on step within period
    switch (s) {
        case  0: tb->clk = 1; break;                      // clk posedge
        case  2: tb->cram_clk = 0; break;                 // cram_clk negedge
        case  7: if (tick_count % 3 == 0)                  // bridge_clk toggle
                     tb->clk_bridge = !tb->clk_bridge;
                 break;
        case 10: tb->clk = 0; break;                      // clk negedge
        case 12: tb->cram_clk = 1; break;                 // cram_clk posedge
        default: break;                                    // no edge
    }
    tb->eval();
    if (trace) trace->dump(sim_time);
    sim_time++;
}

static void tick() {
    tick_count++;
    for (int s = 0; s < 20; s++)
        step(s);
}

static unsigned long cycles() { return (unsigned long)(sim_time / 20); }

static void reset() {
    tb->reset_n = 0;
    tb->cram_clk = 0;
    tb->clk_bridge = 0;
    tb->s_axi_arvalid = 0;
    tb->s_axi_awvalid = 0;
    tb->s_axi_wvalid = 0;
    tb->bridge_wr = 0;
    tb->bridge_rd = 0;
    tb->bridge_addr = 0;
    tb->bridge_wr_data = 0;
    for (int i = 0; i < 20; i++) tick();
    tb->reset_n = 1;
    for (int i = 0; i < 5000; i++) {
        tick();
        if (tb->init_done) break;
    }
    if (!tb->init_done) { printf("ERROR: BCR init timeout\n"); exit(1); }
    for (int i = 0; i < 10; i++) tick();
}

static void idle(int n) {
    tb->s_axi_arvalid = 0; tb->s_axi_awvalid = 0; tb->s_axi_wvalid = 0;
    tb->bridge_wr = 0; tb->bridge_rd = 0;
    for (int i = 0; i < n; i++) tick();
}

// ---- AXI Write ----
static bool axi_write(uint32_t addr, const uint32_t *data, int len, int timeout = 5000) {
    int beats = len + 1;
    tb->s_axi_awvalid = 1; tb->s_axi_awaddr = addr; tb->s_axi_awlen = len;
    tb->s_axi_wvalid = 1; tb->s_axi_wdata = data[0]; tb->s_axi_wstrb = 0xF;
    tb->s_axi_wlast = (beats == 1);
    int beat = 0; bool aw_done = false, w_done = false;
    for (int t = 0; t < timeout; t++) {
        tick();
        if (!aw_done && tb->s_axi_awready) { aw_done = true; tb->s_axi_awvalid = 0; }
        if (!w_done && tb->s_axi_wready) {
            beat++;
            if (beat >= beats) { w_done = true; tb->s_axi_wvalid = 0; }
            else { tb->s_axi_wdata = data[beat]; tb->s_axi_wlast = (beat == beats - 1); }
        }
        if (tb->s_axi_bvalid) { tb->s_axi_awvalid = 0; tb->s_axi_wvalid = 0; idle(2); return true; }
    }
    printf("  TIMEOUT: axi_write 0x%08x\n", addr); tb->s_axi_awvalid = 0; tb->s_axi_wvalid = 0; return false;
}
static bool axi_write_word(uint32_t addr, uint32_t data) { return axi_write(addr, &data, 0); }
static bool axi_write_strobe(uint32_t addr, uint32_t data, uint8_t wstrb, int timeout = 5000) {
    tb->s_axi_awvalid = 1; tb->s_axi_awaddr = addr; tb->s_axi_awlen = 0;
    tb->s_axi_wvalid = 1; tb->s_axi_wdata = data; tb->s_axi_wstrb = wstrb; tb->s_axi_wlast = 1;
    for (int t = 0; t < timeout; t++) {
        tick();
        if (tb->s_axi_awready) tb->s_axi_awvalid = 0;
        if (tb->s_axi_wready) tb->s_axi_wvalid = 0;
        if (tb->s_axi_bvalid) { tb->s_axi_awvalid = 0; tb->s_axi_wvalid = 0; idle(2); return true; }
    }
    tb->s_axi_awvalid = 0; tb->s_axi_wvalid = 0; return false;
}

// ---- AXI Read ----
static bool axi_read(uint32_t addr, uint32_t *data, int len, int timeout = 5000) {
    int beats = len + 1;
    tb->s_axi_arvalid = 1; tb->s_axi_araddr = addr; tb->s_axi_arlen = len;
    bool ar_done = false; int beat = 0;
    for (int t = 0; t < timeout; t++) {
        tick();
        if (!ar_done && tb->s_axi_arready) { ar_done = true; tb->s_axi_arvalid = 0; }
        if (tb->s_axi_rvalid) {
            if (beat < beats) data[beat] = tb->s_axi_rdata;
            beat++;
            if (tb->s_axi_rlast) { tb->s_axi_arvalid = 0; idle(2); return (beat == beats); }
        }
    }
    printf("  TIMEOUT: axi_read 0x%08x len=%d\n", addr, len); tb->s_axi_arvalid = 0; return false;
}
static uint32_t axi_read_word(uint32_t a) { uint32_t d = 0xBADBAD; axi_read(a, &d, 0); return d; }

// ---- Bridge Write (drives bridge_wr for one bridge clock cycle) ----
static void bridge_write_word(uint32_t addr, uint32_t data) {
    // Set signals and hold for enough ticks for bridge_clk to see a full posedge.
    // bridge_clk toggles every 3 ticks, so full cycle = 6 ticks. Hold 8 to guarantee.
    tb->bridge_wr = 1;
    tb->bridge_addr = addr;
    tb->bridge_wr_data = data;
    for (int i = 0; i < 8; i++) tick();
    tb->bridge_wr = 0;
    for (int i = 0; i < 8; i++) tick();
}

// ---- Bridge Read (issues read request, waits for response) ----
static uint32_t bridge_read_word(uint32_t addr, int timeout = 5000) {
    // Issue read request (addr prefix 0x30 = CRAM1)
    // Must hold bridge_rd for enough ticks to guarantee a bridge_clk posedge
    // bridge_clk toggles every 3 ticks → full cycle = 6 ticks, hold for 10
    tb->bridge_rd = 1;
    tb->bridge_addr = addr;
    for (int i = 0; i < 10; i++) tick();
    tb->bridge_rd = 0;

    // Wait for response through: request FIFO CDC (~3 clk) + PSRAM read (~25 clk)
    // + response FIFO CDC (~6 bridge_clk ≈ 18 clk) ≈ 50 cycles
    for (int t = 0; t < timeout; t++) {
        tick();
        if (tb->bridge_rd_ready)
            return tb->bridge_rd_data;
    }
    printf("  TIMEOUT: bridge_read 0x%08x (%d ticks)\n", addr, timeout);
    return 0xBADBAD;
}

// Wait for bridge FIFO to drain
static void bridge_wait_idle(int timeout = 5000) {
    for (int t = 0; t < timeout; t++) {
        tick();
        if (!tb->busy) return;  // No pending bridge or CPU activity
    }
}

static void check(const char *name, uint32_t got, uint32_t expected) {
    if (got == expected) pass_count++;
    else { printf("  FAIL %s: got 0x%08x, expected 0x%08x\n", name, got, expected); fail_count++; }
}

// =====================================================================
// Tests
// =====================================================================

static void test_sram_single_rw() {
    printf("TEST: SRAM single word write/read\n");
    axi_write_word(0x0A000000, 0xDEADBEEF);
    check("sram_rw_1", axi_read_word(0x0A000000), 0xDEADBEEF);
    axi_write_word(0x0A000004, 0xCAFEBABE);
    check("sram_rw_2", axi_read_word(0x0A000004), 0xCAFEBABE);
    check("sram_rw_verify", axi_read_word(0x0A000000), 0xDEADBEEF);
}

static void test_cram0_write_burst_read() {
    printf("TEST: CRAM0 write + burst read (8 words)\n");
    for (int i = 0; i < 8; i++)
        axi_write_word(0x00001000 + i * 4, 0xA0000000 | i);
    uint32_t data[8];
    bool ok = axi_read(0x00001000, data, 7);
    check("cram0_burst_ok", ok, 1);
    for (int i = 0; i < 8; i++) {
        char n[32]; snprintf(n, sizeof(n), "cram0_burst[%d]", i);
        check(n, data[i], 0xA0000000 | i);
    }
}

static void test_cram1_write_burst_read() {
    printf("TEST: CRAM1 write + burst read (8 words)\n");
    for (int i = 0; i < 8; i++)
        axi_write_word(0x01001000 + i * 4, 0xB0000000 | i);
    uint32_t data[8];
    bool ok = axi_read(0x01001000, data, 7);
    check("cram1_burst_ok", ok, 1);
    for (int i = 0; i < 8; i++) {
        char n[32]; snprintf(n, sizeof(n), "cram1_burst[%d]", i);
        check(n, data[i], 0xB0000000 | i);
    }
}

static void test_target_decode_isolation() {
    printf("TEST: Target decode isolation\n");
    axi_write_word(0x00002000, 0x11111111);
    axi_write_word(0x01002000, 0x22222222);
    axi_write_word(0x0A002000, 0x33333333);
    uint32_t d[1];
    axi_read(0x00002000, d, 0); check("iso_cram0", d[0], 0x11111111);
    axi_read(0x01002000, d, 0); check("iso_cram1", d[0], 0x22222222);
    check("iso_sram", axi_read_word(0x0A002000), 0x33333333);
}

static void test_burst_read_16() {
    printf("TEST: Burst read 16 words (CRAM0)\n");
    for (int i = 0; i < 16; i++)
        axi_write_word(0x00005000 + i * 4, 0xC0000000 | i);
    uint32_t data[16];
    bool ok = axi_read(0x00005000, data, 15);
    check("burst16_ok", ok, 1);
    for (int i = 0; i < 16; i++) {
        char n[32]; snprintf(n, sizeof(n), "burst16[%d]", i);
        check(n, data[i], 0xC0000000 | i);
    }
}

static void test_burst_write() {
    printf("TEST: Burst write 8 words (CRAM0)\n");
    uint32_t wdata[8];
    for (int i = 0; i < 8; i++) wdata[i] = 0xD0000000 | i;
    bool ok = axi_write(0x00006000, wdata, 7);
    check("burst_wr_ok", ok, 1);
    uint32_t rdata[8];
    ok = axi_read(0x00006000, rdata, 7);
    check("burst_wr_rd_ok", ok, 1);
    for (int i = 0; i < 8; i++) {
        char n[32]; snprintf(n, sizeof(n), "burst_wr[%d]", i);
        check(n, rdata[i], 0xD0000000 | i);
    }
}

static void test_byte_strobes_sram() {
    printf("TEST: Byte strobes (SRAM)\n");
    axi_write_word(0x0A005000, 0x12345678);
    axi_write_strobe(0x0A005000, 0x000000AA, 0x1);
    check("strobe_b0", axi_read_word(0x0A005000), 0x123456AA);
    axi_write_strobe(0x0A005000, 0xBB000000, 0x8);
    check("strobe_b3", axi_read_word(0x0A005000), 0xBB3456AA);
}

static void test_byte_strobes_cram() {
    printf("TEST: Byte strobes (CRAM)\n");
    axi_write_word(0x00007000, 0xAABBCCDD);
    axi_write_strobe(0x00007000, 0x00001122, 0x3);
    uint32_t d[1]; axi_read(0x00007000, d, 0);
    check("cram_strobe", d[0], 0xAABB1122);
}

static void test_dma_cram0_to_cram1() {
    printf("TEST: DMA — CRAM0 -> CRAM1\n");
    uint32_t src[16];
    for (int i = 0; i < 16; i++) src[i] = 0xDA000000 | (i * 0x11);
    axi_write(0x0000A000, src, 15);
    uint32_t buf[16]; axi_read(0x0000A000, buf, 15);
    axi_write(0x0100A000, buf, 15);
    uint32_t verify[16];
    bool ok = axi_read(0x0100A000, verify, 15);
    check("dma_c0c1_ok", ok, 1);
    for (int i = 0; i < 16; i++) {
        char n[32]; snprintf(n, sizeof(n), "dma_c0c1[%d]", i);
        check(n, verify[i], src[i]);
    }
}

static void test_cross_target_round_trip() {
    printf("TEST: Cross-target round trip\n");
    uint32_t orig[8];
    for (int i = 0; i < 8; i++) orig[i] = 0xDD000000 | (i << 4) | i;
    axi_write(0x0000D000, orig, 7);
    uint32_t b1[8]; axi_read(0x0000D000, b1, 7);
    axi_write(0x0100D000, b1, 7);
    uint32_t b2[8]; axi_read(0x0100D000, b2, 7);
    for (int i = 0; i < 8; i++) axi_write_word(0x0A00D000 + i * 4, b2[i]);
    uint32_t b3[8];
    for (int i = 0; i < 8; i++) b3[i] = axi_read_word(0x0A00D000 + i * 4);
    axi_write(0x0000E000, b3, 7);
    uint32_t fin[8]; axi_read(0x0000E000, fin, 7);
    for (int i = 0; i < 8; i++) {
        char n[32]; snprintf(n, sizeof(n), "rt[%d]", i);
        check(n, fin[i], orig[i]);
    }
}

// =====================================================================
// Bridge arbitration tests (CDC path: clk_bridge → async_fifo → clk)
// =====================================================================

// Bridge-only write to CRAM0, then CPU reads back
static void test_bridge_write_cpu_read() {
    printf("TEST: Bridge write → CPU read (CDC path)\n");

    // Write 8 words via bridge (addr prefix 0x20 = CRAM0)
    for (int i = 0; i < 8; i++) {
        bridge_write_word(0x20030000 + i * 4, 0xBB000000 | i);
    }

    // Wait for all bridge writes to drain through FIFO + controller
    idle(200);

    // Read back via CPU AXI (CRAM0 addr = 0x0003xxxx)
    uint32_t data[8];
    bool ok = axi_read(0x00030000, data, 7);
    check("bridge_rd_ok", ok, 1);
    for (int i = 0; i < 8; i++) {
        char n[32]; snprintf(n, sizeof(n), "bridge_rw[%d]", i);
        check(n, data[i], 0xBB000000 | i);
    }
}

// Bridge writes while CPU writes to different addresses (no conflict on data, test arbitration)
static void test_bridge_cpu_interleaved() {
    printf("TEST: Bridge + CPU interleaved writes to CRAM0\n");

    // CPU writes to low addresses
    for (int i = 0; i < 4; i++)
        axi_write_word(0x00040000 + i * 4, 0xCC000000 | i);

    // Bridge writes to different addresses (bridge addr prefix 0x20)
    for (int i = 0; i < 4; i++)
        bridge_write_word(0x20040100 + i * 4, 0xDD000000 | i);

    // Wait for bridge to drain
    idle(200);

    // Verify CPU writes
    for (int i = 0; i < 4; i++) {
        uint32_t d[1]; axi_read(0x00040000 + i * 4, d, 0);
        char n[32]; snprintf(n, sizeof(n), "cpu_wr[%d]", i);
        check(n, d[0], 0xCC000000 | i);
    }

    // Verify bridge writes (bridge addr 0x20040100 → CRAM0 word addr = 0x40100>>2 = 0x10040)
    for (int i = 0; i < 4; i++) {
        uint32_t d[1]; axi_read(0x00040100 + i * 4, d, 0);
        char n[32]; snprintf(n, sizeof(n), "bridge_wr[%d]", i);
        check(n, d[0], 0xDD000000 | i);
    }
}

// Bridge burst during CPU read — CPU should stall until bridge drains
static void test_bridge_blocks_cpu_read() {
    printf("TEST: Bridge write blocks CPU CRAM0 read (priority)\n");

    // Pre-write data via CPU
    for (int i = 0; i < 8; i++)
        axi_write_word(0x00050000 + i * 4, 0xEE000000 | i);

    // Start bridge writes (these will block CPU access to CRAM0)
    for (int i = 0; i < 4; i++)
        bridge_write_word(0x20050100 + i * 4, 0xFF000000 | i);

    // CPU reads CRAM0 — should stall until bridge drains, then return correct data
    uint32_t data[8];
    bool ok = axi_read(0x00050000, data, 7, 10000);
    check("bridge_block_rd_ok", ok, 1);
    for (int i = 0; i < 8; i++) {
        char n[32]; snprintf(n, sizeof(n), "bridge_block[%d]", i);
        check(n, data[i], 0xEE000000 | i);
    }
}

// Bridge writes don't affect CRAM1 or SRAM
static void test_bridge_no_cram1_interference() {
    printf("TEST: Bridge CRAM0 writes don't affect CRAM1/SRAM\n");

    // Write via CPU to CRAM1 and SRAM
    axi_write_word(0x01060000, 0x11110000);
    axi_write_word(0x0A060000, 0x22220000);

    // Bridge writes to CRAM0
    for (int i = 0; i < 4; i++)
        bridge_write_word(0x20060000 + i * 4, 0x33330000 | i);
    idle(200);

    // CRAM1 and SRAM should be unaffected
    uint32_t d[1];
    axi_read(0x01060000, d, 0); check("no_cram1_corrupt", d[0], 0x11110000);
    check("no_sram_corrupt", axi_read_word(0x0A060000), 0x22220000);
}

// CRAM1 bridge read: write via CPU, verify via CPU, then read via bridge CDC path
static void test_bridge_cram1_read() {
    printf("TEST: Bridge CRAM1 read (CDC path)\n");

    // Write 1 word to CRAM1 via CPU AXI at address that test_cram1_write_burst_read uses
    axi_write_word(0x01002000, 0x99001234);

    // Verify via CPU (sanity)
    uint32_t d[1];
    axi_read(0x01002000, d, 0);
    check("bridge_cram1_cpu_ok", d[0], 0x99001234);

    // Bridge read: addr prefix 0x30 = CRAM1. bridge_addr[23:2] = word address.
    // 0x01002000 has cpu_psram_addr[21:0] = (0x01002000>>2)[21:0] = 0x000800
    // bridge_addr = 0x30002000, bridge_addr[23:2] = 0x000800
    uint32_t val = bridge_read_word(0x30002000);
    check("bridge_cram1_rd", val, 0x99001234);
}

// =====================================================================
// Pipeline depth canary (#19): detect off-by-one in cram_dq_r pipeline
// =====================================================================
static void test_pipeline_depth_canary() {
    printf("TEST: Pipeline depth canary (burst first-word integrity)\n");

    // Write two distinct patterns to adjacent burst-aligned regions
    for (int i = 0; i < 8; i++)
        axi_write_word(0x00070000 + i * 4, 0x11110000 | i);
    for (int i = 0; i < 8; i++)
        axi_write_word(0x00070020 + i * 4, 0x22220000 | i);

    // Burst read region A — if pipeline depth is wrong, first word may be
    // stale data from the previous burst or a shifted pattern
    uint32_t a[8]; axi_read(0x00070000, a, 7);
    check("canary_a0", a[0], 0x11110000);  // First word is critical
    check("canary_a7", a[7], 0x11110007);

    // Immediately burst read region B — tests pipeline flush between bursts
    uint32_t b[8]; axi_read(0x00070020, b, 7);
    check("canary_b0", b[0], 0x22220000);  // Must NOT be 0x11110007 (stale from A)
    check("canary_b7", b[7], 0x22220007);

    // Reverse order: B then A
    uint32_t b2[8]; axi_read(0x00070020, b2, 7);
    uint32_t a2[8]; axi_read(0x00070000, a2, 7);
    check("canary_rev_a0", a2[0], 0x11110000);
    check("canary_rev_b0", b2[0], 0x22220000);
}

// =====================================================================
// Simultaneous multi-master contention (#22)
// =====================================================================
static void test_simultaneous_contention() {
    printf("TEST: Simultaneous bridge + CPU contention (same tick)\n");

    // Pre-write via CPU
    for (int i = 0; i < 4; i++)
        axi_write_word(0x00080000 + i * 4, 0xAA000000 | i);

    // Set up BOTH bridge write and CPU read simultaneously before tick
    // Bridge write (addr prefix 0x20 = CRAM0)
    tb->bridge_wr = 1;
    tb->bridge_addr = 0x20080100;
    tb->bridge_wr_data = 0xBB000099;

    // CPU read request at the same time
    tb->s_axi_arvalid = 1;
    tb->s_axi_araddr = 0x00080000;
    tb->s_axi_arlen = 3;  // 4-word burst

    // Tick with both active — arbitration must resolve
    for (int t = 0; t < 8; t++) tick();
    tb->bridge_wr = 0;

    // Let bridge drain and CPU read complete
    uint32_t data[4] = {0};
    bool ar_done = false;
    int beat = 0;
    for (int t = 0; t < 10000; t++) {
        tick();
        if (!ar_done && tb->s_axi_arready) { ar_done = true; tb->s_axi_arvalid = 0; }
        if (tb->s_axi_rvalid) {
            if (beat < 4) data[beat] = tb->s_axi_rdata;
            beat++;
            if (tb->s_axi_rlast) break;
        }
    }
    tb->s_axi_arvalid = 0;
    idle(200);

    // CPU read should eventually succeed with correct data
    // (bridge may have delayed it, but data at 0x00080000 was pre-written)
    check("contention_ok", beat, 4);
    for (int i = 0; i < 4; i++) {
        char n[32]; snprintf(n, sizeof(n), "contention[%d]", i);
        check(n, data[i], 0xAA000000 | i);
    }

    // Bridge write should also have completed
    uint32_t d[1]; axi_read(0x00080100, d, 0);
    check("contention_bridge", d[0], 0xBB000099);
}

int main(int argc, char **argv) {
    Verilated::commandArgs(argc, argv);
    Verilated::traceEverOn(true);
    tb = new Vtb_psram_full;
    trace = new VerilatedVcdC;
    tb->trace(trace, 99);
    trace->open("psram_rtl_test.vcd");

    printf("=== PSRAM Full-RTL Test Suite (multi-clock + bridge CDC) ===\n\n");

    reset();
    printf("BCR init done, PSRAM ready (%lu cycles)\n\n", cycles());

    // Core tests
    test_sram_single_rw();
    test_cram0_write_burst_read();
    if (trace) { trace->close(); delete trace; trace = nullptr; }
    test_cram1_write_burst_read();
    test_target_decode_isolation();
    test_burst_read_16();
    test_burst_write();
    test_byte_strobes_sram();
    test_byte_strobes_cram();
    test_dma_cram0_to_cram1();
    test_cross_target_round_trip();

    // Pipeline depth + timing tests
    test_pipeline_depth_canary();

    // Bridge arbitration + CDC tests
    test_bridge_cram1_read();
    test_bridge_write_cpu_read();
    test_bridge_cpu_interleaved();
    test_bridge_blocks_cpu_read();
    test_bridge_no_cram1_interference();
    test_simultaneous_contention();

    // Assertion check
    uint16_t errs = tb->assertion_errors;
    if (errs > 0) {
        printf("\n  ASSERTION ERRORS: %d violations\n", errs);
        fail_count += errs;
    } else {
        printf("\n  Bus assertions: 0 errors (clean)\n");
        pass_count++;
    }

    printf("\n=== Results: %d passed, %d failed ===\n", pass_count, fail_count);
    if (trace) { trace->close(); delete trace; }
    delete tb;
    return fail_count > 0 ? 1 : 0;
}
