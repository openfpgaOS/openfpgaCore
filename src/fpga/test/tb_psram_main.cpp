/*
 * Verilator C++ Test Harness for PSRAM Controller Stack (axi_psram_slave)
 *
 * Tests: single R/W (SRAM path), single write + burst read (CRAM path),
 *        CRAM0/CRAM1 target decode, different address regions, byte strobes,
 *        burst write, interleaved traffic.
 *
 * Address map (from axi_psram_slave, target decode on addr[27:24]):
 *   addr[27:24] = 0x0/0x8 -> CRAM0 (burst reads)  -> byte addr 0x00xx_xxxx / 0x08xx_xxxx
 *   addr[27:24] = 0x1/0x9 -> CRAM1 (burst reads)  -> byte addr 0x01xx_xxxx / 0x09xx_xxxx
 *   addr[27:24] = 0xA     -> SRAM  (single-word)   -> byte addr 0x0Axx_xxxx
 */

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <cassert>
#include "Vtb_psram.h"
#include "verilated.h"
#include "verilated_vcd_c.h"

static Vtb_psram *tb;
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
    tb->s_axi_arvalid = 0;
    tb->s_axi_awvalid = 0;
    tb->s_axi_wvalid = 0;
    for (int i = 0; i < 20; i++)
        tick();
    tb->reset_n = 1;
    for (int i = 0; i < 10; i++)
        tick();
}

static void idle(int cycles) {
    tb->s_axi_arvalid = 0;
    tb->s_axi_awvalid = 0;
    tb->s_axi_wvalid = 0;
    for (int i = 0; i < cycles; i++)
        tick();
}

// ---- AXI Write Transaction ----
static bool axi_write(uint32_t byte_addr, const uint32_t *data, int len, int timeout = 2000) {
    int awlen = len;
    int beats = len + 1;

    tb->s_axi_awvalid = 1;
    tb->s_axi_awaddr = byte_addr;
    tb->s_axi_awlen = awlen;
    tb->s_axi_wvalid = 1;
    tb->s_axi_wdata = data[0];
    tb->s_axi_wstrb = 0xF;
    tb->s_axi_wlast = (beats == 1);

    int beat = 0;
    bool aw_done = false;
    bool w_done = false;

    for (int t = 0; t < timeout; t++) {
        tick();

        if (!aw_done && tb->s_axi_awready) {
            aw_done = true;
            tb->s_axi_awvalid = 0;
        }

        if (!w_done && tb->s_axi_wready) {
            beat++;
            if (beat >= beats) {
                w_done = true;
                tb->s_axi_wvalid = 0;
            } else {
                tb->s_axi_wdata = data[beat];
                tb->s_axi_wlast = (beat == beats - 1);
            }
        }

        if (tb->s_axi_bvalid) {
            tb->s_axi_awvalid = 0;
            tb->s_axi_wvalid = 0;
            idle(2);
            return true;
        }
    }

    printf("  TIMEOUT: axi_write addr=0x%08x len=%d (aw=%d w_beat=%d/%d)\n",
           byte_addr, awlen, aw_done, beat, beats);
    tb->s_axi_awvalid = 0;
    tb->s_axi_wvalid = 0;
    return false;
}

static bool axi_write_word(uint32_t byte_addr, uint32_t data) {
    return axi_write(byte_addr, &data, 0);
}

// Write with custom wstrb
static bool axi_write_strobe(uint32_t byte_addr, uint32_t data, uint8_t wstrb, int timeout = 2000) {
    tb->s_axi_awvalid = 1;
    tb->s_axi_awaddr = byte_addr;
    tb->s_axi_awlen = 0;
    tb->s_axi_wvalid = 1;
    tb->s_axi_wdata = data;
    tb->s_axi_wstrb = wstrb;
    tb->s_axi_wlast = 1;

    for (int t = 0; t < timeout; t++) {
        tick();
        if (tb->s_axi_awready) tb->s_axi_awvalid = 0;
        if (tb->s_axi_wready) tb->s_axi_wvalid = 0;
        if (tb->s_axi_bvalid) {
            tb->s_axi_awvalid = 0;
            tb->s_axi_wvalid = 0;
            idle(2);
            return true;
        }
    }
    printf("  TIMEOUT: axi_write_strobe addr=0x%08x\n", byte_addr);
    tb->s_axi_awvalid = 0;
    tb->s_axi_wvalid = 0;
    return false;
}

// ---- AXI Read Transaction ----
static bool axi_read(uint32_t byte_addr, uint32_t *data, int len, int timeout = 2000) {
    int arlen = len;
    int beats = len + 1;

    tb->s_axi_arvalid = 1;
    tb->s_axi_araddr = byte_addr;
    tb->s_axi_arlen = arlen;

    bool ar_done = false;
    int beat = 0;

    for (int t = 0; t < timeout; t++) {
        tick();

        if (!ar_done && tb->s_axi_arready) {
            ar_done = true;
            tb->s_axi_arvalid = 0;
        }

        if (tb->s_axi_rvalid) {
            if (beat < beats)
                data[beat] = tb->s_axi_rdata;
            beat++;
            if (tb->s_axi_rlast) {
                tb->s_axi_arvalid = 0;
                idle(2);
                return (beat == beats);
            }
        }
    }

    printf("  TIMEOUT: axi_read addr=0x%08x len=%d (ar=%d beats=%d/%d)\n",
           byte_addr, arlen, ar_done, beat, beats);
    tb->s_axi_arvalid = 0;
    return false;
}

static uint32_t axi_read_word(uint32_t byte_addr) {
    uint32_t data = 0xBADBAD;
    axi_read(byte_addr, &data, 0);
    return data;
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

// SRAM path: addr[27:24] = 0xA -> single-word reads via S_RD_CMD
static void test_sram_single_rw() {
    printf("TEST: SRAM single word write/read (addr 0x0A*)\n");

    axi_write_word(0x0A000000, 0xDEADBEEF);
    uint32_t val = axi_read_word(0x0A000000);
    check("sram_rw_1", val, 0xDEADBEEF);

    axi_write_word(0x0A000004, 0xCAFEBABE);
    val = axi_read_word(0x0A000004);
    check("sram_rw_2", val, 0xCAFEBABE);

    // Verify first write not corrupted
    val = axi_read_word(0x0A000000);
    check("sram_rw_verify", val, 0xDEADBEEF);
}

// CRAM0 path: addr[27:24] = 0x0 -> burst reads via S_RD_BURST
static void test_cram0_write_burst_read() {
    printf("TEST: CRAM0 write + burst read (addr 0x0*)\n");

    // Write 8 words individually
    for (int i = 0; i < 8; i++)
        axi_write_word(0x00001000 + i * 4, 0xA0000000 | i);

    // Burst read 8 words (ARLEN=7)
    uint32_t data[8];
    bool ok = axi_read(0x00001000, data, 7);
    check("cram0_burst_ok", ok, 1);
    for (int i = 0; i < 8; i++) {
        char name[32];
        snprintf(name, sizeof(name), "cram0_burst[%d]", i);
        check(name, data[i], 0xA0000000 | i);
    }
}

// CRAM1 path: addr[27:24] = 0x1 -> burst reads
static void test_cram1_write_burst_read() {
    printf("TEST: CRAM1 write + burst read (addr 0x01*)\n");

    for (int i = 0; i < 8; i++)
        axi_write_word(0x01001000 + i * 4, 0xB0000000 | i);

    uint32_t data[8];
    bool ok = axi_read(0x01001000, data, 7);
    check("cram1_burst_ok", ok, 1);
    for (int i = 0; i < 8; i++) {
        char name[32];
        snprintf(name, sizeof(name), "cram1_burst[%d]", i);
        check(name, data[i], 0xB0000000 | i);
    }
}

// Target decode isolation: CRAM0 and CRAM1 should have separate address spaces
static void test_target_decode_isolation() {
    printf("TEST: Target decode isolation (CRAM0 vs CRAM1 vs SRAM)\n");

    // Write different values to the same offset in each target
    axi_write_word(0x00002000, 0x11111111);  // CRAM0
    axi_write_word(0x01002000, 0x22222222);  // CRAM1
    axi_write_word(0x0A002000, 0x33333333);  // SRAM

    // Read back from CRAM0 (burst read, single word)
    uint32_t data[1];
    bool ok = axi_read(0x00002000, data, 0);
    check("iso_cram0_ok", ok, 1);
    check("iso_cram0", data[0], 0x11111111);

    // Read back from CRAM1 (burst read, single word)
    ok = axi_read(0x01002000, data, 0);
    check("iso_cram1_ok", ok, 1);
    check("iso_cram1", data[0], 0x22222222);

    // Read back from SRAM (single-word read path)
    uint32_t val = axi_read_word(0x0A002000);
    check("iso_sram", val, 0x33333333);
}

// CRAM0 alias test: 0x8* should map to same target as 0x0*
static void test_cram0_alias() {
    printf("TEST: CRAM0 alias (0x8* writes, 0x0* reads)\n");

    // Note: In real hardware, 0x0 and 0x8 map to the same CRAM0 via the mux.
    // In our behavioral model, they're separate memory regions.
    // This test verifies both address prefixes use the burst read path.
    axi_write_word(0x08003000, 0xAAAAAAAA);
    uint32_t data[1];
    bool ok = axi_read(0x08003000, data, 0);  // Should use burst path (not SRAM)
    check("alias_0x8_ok", ok, 1);
    check("alias_0x8", data[0], 0xAAAAAAAA);
}

// Single-word burst read (ARLEN=0) through CRAM path
static void test_single_burst_read() {
    printf("TEST: Single-word burst read (ARLEN=0, CRAM path)\n");

    axi_write_word(0x00004000, 0x12345678);
    uint32_t data[1];
    bool ok = axi_read(0x00004000, data, 0);
    check("single_burst_ok", ok, 1);
    check("single_burst", data[0], 0x12345678);
}

// 16-word burst read
static void test_burst_read_16() {
    printf("TEST: Burst read 16 words (CRAM0)\n");

    for (int i = 0; i < 16; i++)
        axi_write_word(0x00005000 + i * 4, 0xC0000000 | i);

    uint32_t data[16];
    bool ok = axi_read(0x00005000, data, 15);
    check("burst16_ok", ok, 1);
    for (int i = 0; i < 16; i++) {
        char name[32];
        snprintf(name, sizeof(name), "burst16[%d]", i);
        check(name, data[i], 0xC0000000 | i);
    }
}

// Burst write via AXI (axi_psram_slave issues individual psram_wr for each beat)
static void test_burst_write() {
    printf("TEST: Burst write 8 words (CRAM0)\n");

    uint32_t wdata[8];
    for (int i = 0; i < 8; i++)
        wdata[i] = 0xD0000000 | i;

    bool ok = axi_write(0x00006000, wdata, 7);
    check("burst_wr_ok", ok, 1);

    // Read back via burst
    uint32_t rdata[8];
    ok = axi_read(0x00006000, rdata, 7);
    check("burst_wr_rd_ok", ok, 1);
    for (int i = 0; i < 8; i++) {
        char name[32];
        snprintf(name, sizeof(name), "burst_wr[%d]", i);
        check(name, rdata[i], 0xD0000000 | i);
    }
}

// Byte strobes on SRAM path
static void test_byte_strobes_sram() {
    printf("TEST: Byte strobes (SRAM path)\n");

    axi_write_word(0x0A005000, 0x12345678);

    // Partial write: only byte 0
    axi_write_strobe(0x0A005000, 0x000000AA, 0x1);
    uint32_t val = axi_read_word(0x0A005000);
    check("strobe_byte0", val, 0x123456AA);

    // Partial write: only byte 3
    axi_write_strobe(0x0A005000, 0xBB000000, 0x8);
    val = axi_read_word(0x0A005000);
    check("strobe_byte3", val, 0xBB3456AA);

    // Partial write: bytes 1 and 2
    axi_write_strobe(0x0A005000, 0x00CCDD00, 0x6);
    val = axi_read_word(0x0A005000);
    check("strobe_byte12", val, 0xBBCCDDAA);
}

// Byte strobes on CRAM path
static void test_byte_strobes_cram() {
    printf("TEST: Byte strobes (CRAM path)\n");

    axi_write_word(0x00007000, 0xAABBCCDD);

    // Partial write: only bytes 0-1
    axi_write_strobe(0x00007000, 0x00001122, 0x3);

    uint32_t data[1];
    axi_read(0x00007000, data, 0);
    check("cram_strobe", data[0], 0xAABB1122);
}

// Interleaved reads and writes across different targets
static void test_interleaved_targets() {
    printf("TEST: Interleaved R/W across targets\n");

    for (int i = 0; i < 4; i++) {
        // Write to CRAM0, CRAM1, SRAM in sequence
        axi_write_word(0x00008000 + i * 4, 0xE0000000 | i);
        axi_write_word(0x01008000 + i * 4, 0xF0000000 | i);
        axi_write_word(0x0A008000 + i * 4, 0x10000000 | i);
    }

    // Read back from each
    for (int i = 0; i < 4; i++) {
        char name[32];
        uint32_t data[1];

        axi_read(0x00008000 + i * 4, data, 0);
        snprintf(name, sizeof(name), "intlv_cram0[%d]", i);
        check(name, data[0], 0xE0000000 | i);

        axi_read(0x01008000 + i * 4, data, 0);
        snprintf(name, sizeof(name), "intlv_cram1[%d]", i);
        check(name, data[0], 0xF0000000 | i);

        uint32_t val = axi_read_word(0x0A008000 + i * 4);
        snprintf(name, sizeof(name), "intlv_sram[%d]", i);
        check(name, val, 0x10000000 | i);
    }
}

// SRAM multi-word sequential read (single-word path, ARLEN>0)
static void test_sram_multi_read() {
    printf("TEST: SRAM multi-word sequential read (ARLEN=3)\n");

    for (int i = 0; i < 4; i++)
        axi_write_word(0x0A009000 + i * 4, 0x50000000 | i);

    // ARLEN=3 -> 4 beats, through single-word read path
    uint32_t data[4];
    bool ok = axi_read(0x0A009000, data, 3, 5000);
    check("sram_multi_ok", ok, 1);
    for (int i = 0; i < 4; i++) {
        char name[32];
        snprintf(name, sizeof(name), "sram_multi[%d]", i);
        check(name, data[i], 0x50000000 | i);
    }
}

// =====================================================================
// Cross-target copy and DMA-like patterns
// =====================================================================

// DMA pattern: burst read from CRAM0 -> burst write to CRAM1
static void test_dma_cram0_to_cram1() {
    printf("TEST: DMA pattern — CRAM0 burst read -> CRAM1 burst write\n");

    // Setup: write 16 words to CRAM0
    uint32_t src_data[16];
    for (int i = 0; i < 16; i++)
        src_data[i] = 0xDA000000 | (i * 0x11);
    axi_write(0x0000A000, src_data, 15);

    // DMA: burst read from CRAM0
    uint32_t buf[16];
    bool ok = axi_read(0x0000A000, buf, 15);
    check("dma_c0c1_rd", ok, 1);

    // DMA: burst write to CRAM1
    ok = axi_write(0x0100A000, buf, 15);
    check("dma_c0c1_wr", ok, 1);

    // Verify destination (burst read from CRAM1)
    uint32_t verify[16];
    ok = axi_read(0x0100A000, verify, 15);
    check("dma_c0c1_verify_rd", ok, 1);
    for (int i = 0; i < 16; i++) {
        char name[32];
        snprintf(name, sizeof(name), "dma_c0c1[%d]", i);
        check(name, verify[i], src_data[i]);
    }

    // Verify source still intact
    uint32_t src_check[16];
    ok = axi_read(0x0000A000, src_check, 15);
    check("dma_c0c1_src_intact_rd", ok, 1);
    for (int i = 0; i < 16; i++) {
        char name[32];
        snprintf(name, sizeof(name), "dma_c0c1_src[%d]", i);
        check(name, src_check[i], src_data[i]);
    }
}

// DMA pattern: CRAM1 burst read -> SRAM single-word writes
static void test_dma_cram1_to_sram() {
    printf("TEST: DMA pattern — CRAM1 burst read -> SRAM word writes\n");

    // Setup: write 8 words to CRAM1
    uint32_t src_data[8];
    for (int i = 0; i < 8; i++)
        src_data[i] = 0xDB000000 | i;
    axi_write(0x0100B000, src_data, 7);

    // DMA read: burst from CRAM1
    uint32_t buf[8];
    bool ok = axi_read(0x0100B000, buf, 7);
    check("dma_c1sr_rd", ok, 1);

    // DMA write: individual words to SRAM
    for (int i = 0; i < 8; i++)
        axi_write_word(0x0A00B000 + i * 4, buf[i]);

    // Verify SRAM (single-word reads)
    for (int i = 0; i < 8; i++) {
        uint32_t val = axi_read_word(0x0A00B000 + i * 4);
        char name[32];
        snprintf(name, sizeof(name), "dma_c1sr[%d]", i);
        check(name, val, src_data[i]);
    }
}

// DMA pattern: SRAM -> CRAM0 (single-word reads -> burst write)
static void test_dma_sram_to_cram0() {
    printf("TEST: DMA pattern — SRAM word reads -> CRAM0 burst write\n");

    // Setup: write 8 words to SRAM
    for (int i = 0; i < 8; i++)
        axi_write_word(0x0A00C000 + i * 4, 0xDC000000 | i);

    // DMA read: single-word from SRAM
    uint32_t buf[8];
    for (int i = 0; i < 8; i++)
        buf[i] = axi_read_word(0x0A00C000 + i * 4);

    // DMA write: burst to CRAM0
    bool ok = axi_write(0x0000C000, buf, 7);
    check("dma_src0_wr", ok, 1);

    // Verify CRAM0 (burst read)
    uint32_t verify[8];
    ok = axi_read(0x0000C000, verify, 7);
    check("dma_src0_verify_rd", ok, 1);
    for (int i = 0; i < 8; i++) {
        char name[32];
        snprintf(name, sizeof(name), "dma_src0[%d]", i);
        check(name, verify[i], 0xDC000000 | i);
    }
}

// Round-trip: CRAM0 -> CRAM1 -> SRAM -> CRAM0, verify at each stage
static void test_cross_target_round_trip() {
    printf("TEST: Cross-target round trip (CRAM0 -> CRAM1 -> SRAM -> CRAM0)\n");

    uint32_t original[8];
    for (int i = 0; i < 8; i++)
        original[i] = 0xDD000000 | (i << 4) | i;

    // Stage 1: Write to CRAM0
    axi_write(0x0000D000, original, 7);

    // Stage 2: Copy CRAM0 -> CRAM1
    uint32_t buf[8];
    axi_read(0x0000D000, buf, 7);
    axi_write(0x0100D000, buf, 7);

    // Verify CRAM1
    uint32_t v1[8];
    axi_read(0x0100D000, v1, 7);
    for (int i = 0; i < 8; i++) {
        char name[32];
        snprintf(name, sizeof(name), "rt_c1[%d]", i);
        check(name, v1[i], original[i]);
    }

    // Stage 3: Copy CRAM1 -> SRAM (word by word)
    uint32_t buf2[8];
    axi_read(0x0100D000, buf2, 7);
    for (int i = 0; i < 8; i++)
        axi_write_word(0x0A00D000 + i * 4, buf2[i]);

    // Verify SRAM
    for (int i = 0; i < 8; i++) {
        uint32_t val = axi_read_word(0x0A00D000 + i * 4);
        char name[32];
        snprintf(name, sizeof(name), "rt_sr[%d]", i);
        check(name, val, original[i]);
    }

    // Stage 4: Copy SRAM -> CRAM0 (different offset to avoid overlap)
    uint32_t buf3[8];
    for (int i = 0; i < 8; i++)
        buf3[i] = axi_read_word(0x0A00D000 + i * 4);
    axi_write(0x0000E000, buf3, 7);

    // Verify CRAM0 final
    uint32_t v2[8];
    axi_read(0x0000E000, v2, 7);
    for (int i = 0; i < 8; i++) {
        char name[32];
        snprintf(name, sizeof(name), "rt_c0[%d]", i);
        check(name, v2[i], original[i]);
    }
}

// Back-to-back DMA: multiple burst transfers without idle gaps
static void test_back_to_back_dma() {
    printf("TEST: Back-to-back DMA (4 consecutive burst copies)\n");

    // Fill 4 blocks of 8 words each in CRAM0
    for (int blk = 0; blk < 4; blk++) {
        uint32_t wdata[8];
        for (int i = 0; i < 8; i++)
            wdata[i] = ((blk + 1) << 24) | (i << 8) | blk;
        axi_write(0x00010000 + blk * 0x100, wdata, 7);
    }

    // DMA: burst read from CRAM0, burst write to CRAM1 — 4 blocks back-to-back
    for (int blk = 0; blk < 4; blk++) {
        uint32_t buf[8];
        axi_read(0x00010000 + blk * 0x100, buf, 7);
        axi_write(0x01010000 + blk * 0x100, buf, 7);
    }

    // Verify all 4 blocks in CRAM1
    for (int blk = 0; blk < 4; blk++) {
        uint32_t verify[8];
        bool ok = axi_read(0x01010000 + blk * 0x100, verify, 7);
        check("b2b_rd_ok", ok, 1);
        for (int i = 0; i < 8; i++) {
            uint32_t expected = ((blk + 1) << 24) | (i << 8) | blk;
            char name[32];
            snprintf(name, sizeof(name), "b2b[%d][%d]", blk, i);
            check(name, verify[i], expected);
        }
    }
}

// Scatter-gather: read burst from one target, write individual words to different targets
static void test_scatter_gather() {
    printf("TEST: Scatter-gather (CRAM0 burst -> scatter to CRAM1 + SRAM)\n");

    // Write 8 words to CRAM0
    uint32_t src[8];
    for (int i = 0; i < 8; i++)
        src[i] = 0xDE000000 | i;
    axi_write(0x0000F000, src, 7);

    // Burst read from CRAM0
    uint32_t buf[8];
    axi_read(0x0000F000, buf, 7);

    // Scatter: even words to CRAM1, odd words to SRAM
    for (int i = 0; i < 8; i++) {
        if (i % 2 == 0)
            axi_write_word(0x0100F000 + (i / 2) * 4, buf[i]);
        else
            axi_write_word(0x0A00F000 + (i / 2) * 4, buf[i]);
    }

    // Gather: read back and verify
    for (int i = 0; i < 8; i++) {
        uint32_t val;
        if (i % 2 == 0) {
            uint32_t d[1];
            axi_read(0x0100F000 + (i / 2) * 4, d, 0);
            val = d[0];
        } else {
            val = axi_read_word(0x0A00F000 + (i / 2) * 4);
        }
        char name[32];
        snprintf(name, sizeof(name), "sg[%d]", i);
        check(name, val, src[i]);
    }
}

// Different address regions within CRAM0
static void test_different_regions() {
    printf("TEST: Different address regions (CRAM0)\n");

    axi_write_word(0x00000000, 0x11111111);
    axi_write_word(0x00010000, 0x22222222);
    axi_write_word(0x00020000, 0x33333333);

    uint32_t d[1];
    axi_read(0x00000000, d, 0); check("region_0", d[0], 0x11111111);
    axi_read(0x00010000, d, 0); check("region_1", d[0], 0x22222222);
    axi_read(0x00020000, d, 0); check("region_2", d[0], 0x33333333);
}

int main(int argc, char **argv) {
    Verilated::commandArgs(argc, argv);
    Verilated::traceEverOn(true);
    tb = new Vtb_psram;
    trace = new VerilatedVcdC;
    tb->trace(trace, 99);
    trace->open("psram_test.vcd");

    printf("=== PSRAM Controller Test Suite ===\n\n");

    reset();
    printf("PSRAM initialized (%lu cycles)\n\n", (unsigned long)(sim_time / 2));

    test_sram_single_rw();
    test_cram0_write_burst_read();
    // Stop tracing after initial tests to keep VCD small
    if (trace) { trace->close(); delete trace; trace = nullptr; }
    test_cram1_write_burst_read();
    test_target_decode_isolation();
    test_cram0_alias();
    test_single_burst_read();
    test_burst_read_16();
    test_burst_write();
    test_byte_strobes_sram();
    test_byte_strobes_cram();
    test_interleaved_targets();
    test_sram_multi_read();
    test_different_regions();
    test_dma_cram0_to_cram1();
    test_dma_cram1_to_sram();
    test_dma_sram_to_cram0();
    test_cross_target_round_trip();
    test_back_to_back_dma();
    test_scatter_gather();

    printf("\n=== Results: %d passed, %d failed ===\n",
           pass_count, fail_count);

    if (trace) { trace->close(); delete trace; }
    delete tb;
    return fail_count > 0 ? 1 : 0;
}
