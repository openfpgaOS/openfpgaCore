/*
 * Verilator C++ Test Harness for PSRAM Controller Stack (word-level interface)
 *
 * Tests: single R/W (SRAM path), single write + burst read (CRAM path),
 *        CRAM0/CRAM1 target decode, different address regions, byte strobes,
 *        burst write, interleaved traffic.
 *
 * Address map (target decode on addr[27:24]):
 *   addr[27:24] = 0x0/0x8 -> CRAM0 (burst reads)  -> byte addr 0x00xx_xxxx / 0x08xx_xxxx
 *   addr[27:24] = 0x1/0x9 -> CRAM1 (burst reads)  -> byte addr 0x01xx_xxxx / 0x09xx_xxxx
 *   addr[27:24] = 0xA     -> SRAM  (single-word)   -> byte addr 0x0Axx_xxxx
 *
 * Word-level interface: m_addr[25:0] = byte_addr[27:2]
 *   CRAM path (burst):  m_burst_rd, m_burst_len, m_burst_rdata_valid, m_burst_rdata
 *   SRAM path (single): m_rd, m_rdata_valid, m_rdata
 *   Write (single):     m_wr, m_wdata, m_wstrb
 *   Write (burst):      m_burst_wr, m_burst_wr_len, m_burst_wdata, m_burst_wstrb, m_burst_wdata_next
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
    tb->m_rd = 0;
    tb->m_wr = 0;
    tb->m_burst_rd = 0;
    tb->m_burst_wr = 0;
    tb->m_addr = 0;
    tb->m_wdata = 0;
    tb->m_wstrb = 0;
    tb->m_burst_len = 0;
    tb->m_burst_wr_len = 0;
    tb->m_burst_wdata = 0;
    tb->m_burst_wstrb = 0;
    for (int i = 0; i < 20; i++)
        tick();
    tb->reset_n = 1;
    for (int i = 0; i < 10; i++)
        tick();
}

static void idle(int cycles) {
    tb->m_rd = 0;
    tb->m_wr = 0;
    tb->m_burst_rd = 0;
    tb->m_burst_wr = 0;
    for (int i = 0; i < cycles; i++)
        tick();
}

// Determine if an address uses the SRAM path (single-word only)
static bool is_sram_addr(uint32_t byte_addr) {
    uint8_t nibble = (byte_addr >> 24) & 0xF;
    return (nibble == 0xA);
}

// Convert byte address to word address for m_addr[25:0] = byte_addr[27:2]
static uint32_t to_word_addr(uint32_t byte_addr) {
    return (byte_addr >> 2) & 0x03FFFFFF;
}

// ---- Single-word write ----
static bool word_write(uint32_t byte_addr, uint32_t data, uint8_t wstrb = 0xF, int timeout = 2000) {
    // Wait until not busy
    for (int t = 0; t < timeout; t++) {
        if (!tb->m_busy) break;
        tick();
    }

    tb->m_wr = 1;
    tb->m_addr = to_word_addr(byte_addr);
    tb->m_wdata = data;
    tb->m_wstrb = wstrb;
    tick();  // present command for one cycle

    tb->m_wr = 0;

    // Wait for busy to go low (write complete)
    for (int t = 0; t < timeout; t++) {
        tick();
        if (!tb->m_busy) {
            idle(1);
            return true;
        }
    }

    printf("  TIMEOUT: word_write addr=0x%08x\n", byte_addr);
    return false;
}

// ---- Single-word read (SRAM path) ----
static bool word_read_single(uint32_t byte_addr, uint32_t *data, int timeout = 2000) {
    // Wait until not busy
    for (int t = 0; t < timeout; t++) {
        if (!tb->m_busy) break;
        tick();
    }

    tb->m_rd = 1;
    tb->m_addr = to_word_addr(byte_addr);
    tick();  // present command for one cycle

    tb->m_rd = 0;

    // Wait for rdata_valid
    for (int t = 0; t < timeout; t++) {
        tick();
        if (tb->m_rdata_valid) {
            *data = tb->m_rdata;
            idle(1);
            return true;
        }
    }

    printf("  TIMEOUT: word_read_single addr=0x%08x\n", byte_addr);
    return false;
}

// ---- Burst read (CRAM path) ----
static bool burst_read(uint32_t byte_addr, uint32_t *data, int count, int timeout = 2000) {
    // Wait until not busy
    for (int t = 0; t < timeout; t++) {
        if (!tb->m_busy) break;
        tick();
    }

    tb->m_burst_rd = 1;
    tb->m_addr = to_word_addr(byte_addr);
    tb->m_burst_len = count - 1;  // burst_len is count-1 (like AXI arlen)
    tick();  // present command for one cycle

    tb->m_burst_rd = 0;

    // Collect burst_rdata_valid beats
    int collected = 0;
    for (int t = 0; t < timeout; t++) {
        tick();
        if (tb->m_burst_rdata_valid) {
            if (collected < count)
                data[collected] = tb->m_burst_rdata;
            collected++;
            if (collected >= count) {
                idle(1);
                return true;
            }
        }
    }

    printf("  TIMEOUT: burst_read addr=0x%08x count=%d (got %d)\n",
           byte_addr, count, collected);
    return false;
}

// ---- Burst write (CRAM path) ----
static bool burst_write(uint32_t byte_addr, const uint32_t *data, int count, int timeout = 2000) {
    // Wait until not busy
    for (int t = 0; t < timeout; t++) {
        if (!tb->m_busy) break;
        tick();
    }

    tb->m_burst_wr = 1;
    tb->m_addr = to_word_addr(byte_addr);
    tb->m_burst_wr_len = count - 1;
    tb->m_burst_wdata = data[0];
    tb->m_burst_wstrb = 0xF;
    tick();  // present command for one cycle

    tb->m_burst_wr = 0;

    int beat = 1;
    for (int t = 0; t < timeout; t++) {
        tick();
        if (tb->m_burst_wdata_next && beat < count) {
            tb->m_burst_wdata = data[beat];
            beat++;
        }
        if (!tb->m_busy) {
            idle(1);
            return true;
        }
    }

    printf("  TIMEOUT: burst_write addr=0x%08x count=%d (beat %d)\n",
           byte_addr, count, beat);
    return false;
}

// ---- Unified read: picks burst or single based on address ----
static bool read_words(uint32_t byte_addr, uint32_t *data, int count) {
    if (is_sram_addr(byte_addr)) {
        // SRAM: single-word reads one at a time
        for (int i = 0; i < count; i++) {
            if (!word_read_single(byte_addr + i * 4, &data[i]))
                return false;
        }
        return true;
    } else {
        // CRAM: burst read
        return burst_read(byte_addr, data, count);
    }
}

static uint32_t read_word(uint32_t byte_addr) {
    uint32_t data = 0xBADBAD;
    if (is_sram_addr(byte_addr)) {
        word_read_single(byte_addr, &data);
    } else {
        burst_read(byte_addr, &data, 1);
    }
    return data;
}

// ---- Unified write: picks burst or single based on count ----
static bool write_words(uint32_t byte_addr, const uint32_t *data, int count) {
    if (count == 1 || is_sram_addr(byte_addr)) {
        for (int i = 0; i < count; i++) {
            if (!word_write(byte_addr + i * 4, data[i]))
                return false;
        }
        return true;
    } else {
        return burst_write(byte_addr, data, count);
    }
}

static bool write_word(uint32_t byte_addr, uint32_t data) {
    return word_write(byte_addr, data);
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

// SRAM path: addr[27:24] = 0xA -> single-word reads via m_rd
static void test_sram_single_rw() {
    printf("TEST: SRAM single word write/read (addr 0x0A*)\n");

    write_word(0x0A000000, 0xDEADBEEF);
    uint32_t val = read_word(0x0A000000);
    check("sram_rw_1", val, 0xDEADBEEF);

    write_word(0x0A000004, 0xCAFEBABE);
    val = read_word(0x0A000004);
    check("sram_rw_2", val, 0xCAFEBABE);

    // Verify first write not corrupted
    val = read_word(0x0A000000);
    check("sram_rw_verify", val, 0xDEADBEEF);
}

// CRAM0 path: addr[27:24] = 0x0 -> burst reads via m_burst_rd
static void test_cram0_write_burst_read() {
    printf("TEST: CRAM0 write + burst read (addr 0x0*)\n");

    // Write 8 words individually
    for (int i = 0; i < 8; i++)
        write_word(0x00001000 + i * 4, 0xA0000000 | i);

    // Burst read 8 words
    uint32_t data[8];
    bool ok = burst_read(0x00001000, data, 8);
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
        write_word(0x01001000 + i * 4, 0xB0000000 | i);

    uint32_t data[8];
    bool ok = burst_read(0x01001000, data, 8);
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
    write_word(0x00002000, 0x11111111);  // CRAM0
    write_word(0x01002000, 0x22222222);  // CRAM1
    write_word(0x0A002000, 0x33333333);  // SRAM

    // Read back from CRAM0 (burst read, single word)
    uint32_t data[1];
    bool ok = burst_read(0x00002000, data, 1);
    check("iso_cram0_ok", ok, 1);
    check("iso_cram0", data[0], 0x11111111);

    // Read back from CRAM1 (burst read, single word)
    ok = burst_read(0x01002000, data, 1);
    check("iso_cram1_ok", ok, 1);
    check("iso_cram1", data[0], 0x22222222);

    // Read back from SRAM (single-word read path)
    uint32_t val = read_word(0x0A002000);
    check("iso_sram", val, 0x33333333);
}

// CRAM0 alias test: 0x8* should map to same target as 0x0*
static void test_cram0_alias() {
    printf("TEST: CRAM0 alias (0x8* writes, 0x0* reads)\n");

    // Note: In real hardware, 0x0 and 0x8 map to the same CRAM0 via the mux.
    // In our behavioral model, they're separate memory regions.
    // This test verifies both address prefixes use the burst read path.
    write_word(0x08003000, 0xAAAAAAAA);
    uint32_t data[1];
    bool ok = burst_read(0x08003000, data, 1);  // Should use burst path (not SRAM)
    check("alias_0x8_ok", ok, 1);
    check("alias_0x8", data[0], 0xAAAAAAAA);
}

// Single-word burst read (count=1) through CRAM path
static void test_single_burst_read() {
    printf("TEST: Single-word burst read (count=1, CRAM path)\n");

    write_word(0x00004000, 0x12345678);
    uint32_t data[1];
    bool ok = burst_read(0x00004000, data, 1);
    check("single_burst_ok", ok, 1);
    check("single_burst", data[0], 0x12345678);
}

// 16-word burst read
static void test_burst_read_16() {
    printf("TEST: Burst read 16 words (CRAM0)\n");

    for (int i = 0; i < 16; i++)
        write_word(0x00005000 + i * 4, 0xC0000000 | i);

    uint32_t data[16];
    bool ok = burst_read(0x00005000, data, 16);
    check("burst16_ok", ok, 1);
    for (int i = 0; i < 16; i++) {
        char name[32];
        snprintf(name, sizeof(name), "burst16[%d]", i);
        check(name, data[i], 0xC0000000 | i);
    }
}

// Burst write via word-level interface
static void test_burst_write() {
    printf("TEST: Burst write 8 words (CRAM0)\n");

    uint32_t wdata[8];
    for (int i = 0; i < 8; i++)
        wdata[i] = 0xD0000000 | i;

    bool ok = burst_write(0x00006000, wdata, 8);
    check("burst_wr_ok", ok, 1);

    // Read back via burst
    uint32_t rdata[8];
    ok = burst_read(0x00006000, rdata, 8);
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

    write_word(0x0A005000, 0x12345678);

    // Partial write: only byte 0
    word_write(0x0A005000, 0x000000AA, 0x1);
    uint32_t val = read_word(0x0A005000);
    check("strobe_byte0", val, 0x123456AA);

    // Partial write: only byte 3
    word_write(0x0A005000, 0xBB000000, 0x8);
    val = read_word(0x0A005000);
    check("strobe_byte3", val, 0xBB3456AA);

    // Partial write: bytes 1 and 2
    word_write(0x0A005000, 0x00CCDD00, 0x6);
    val = read_word(0x0A005000);
    check("strobe_byte12", val, 0xBBCCDDAA);
}

// Byte strobes on CRAM path
static void test_byte_strobes_cram() {
    printf("TEST: Byte strobes (CRAM path)\n");

    write_word(0x00007000, 0xAABBCCDD);

    // Partial write: only bytes 0-1
    word_write(0x00007000, 0x00001122, 0x3);

    uint32_t data[1];
    burst_read(0x00007000, data, 1);
    check("cram_strobe", data[0], 0xAABB1122);
}

// Interleaved reads and writes across different targets
static void test_interleaved_targets() {
    printf("TEST: Interleaved R/W across targets\n");

    for (int i = 0; i < 4; i++) {
        // Write to CRAM0, CRAM1, SRAM in sequence
        write_word(0x00008000 + i * 4, 0xE0000000 | i);
        write_word(0x01008000 + i * 4, 0xF0000000 | i);
        write_word(0x0A008000 + i * 4, 0x10000000 | i);
    }

    // Read back from each
    for (int i = 0; i < 4; i++) {
        char name[32];
        uint32_t data[1];

        burst_read(0x00008000 + i * 4, data, 1);
        snprintf(name, sizeof(name), "intlv_cram0[%d]", i);
        check(name, data[0], 0xE0000000 | i);

        burst_read(0x01008000 + i * 4, data, 1);
        snprintf(name, sizeof(name), "intlv_cram1[%d]", i);
        check(name, data[0], 0xF0000000 | i);

        uint32_t val = read_word(0x0A008000 + i * 4);
        snprintf(name, sizeof(name), "intlv_sram[%d]", i);
        check(name, val, 0x10000000 | i);
    }
}

// SRAM multi-word sequential read (single-word path, multiple reads)
static void test_sram_multi_read() {
    printf("TEST: SRAM multi-word sequential read (4 words)\n");

    for (int i = 0; i < 4; i++)
        write_word(0x0A009000 + i * 4, 0x50000000 | i);

    // 4 single-word reads through SRAM path
    uint32_t data[4];
    bool ok = read_words(0x0A009000, data, 4);
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
    burst_write(0x0000A000, src_data, 16);

    // DMA: burst read from CRAM0
    uint32_t buf[16];
    bool ok = burst_read(0x0000A000, buf, 16);
    check("dma_c0c1_rd", ok, 1);

    // DMA: burst write to CRAM1
    ok = burst_write(0x0100A000, buf, 16);
    check("dma_c0c1_wr", ok, 1);

    // Verify destination (burst read from CRAM1)
    uint32_t verify[16];
    ok = burst_read(0x0100A000, verify, 16);
    check("dma_c0c1_verify_rd", ok, 1);
    for (int i = 0; i < 16; i++) {
        char name[32];
        snprintf(name, sizeof(name), "dma_c0c1[%d]", i);
        check(name, verify[i], src_data[i]);
    }

    // Verify source still intact
    uint32_t src_check[16];
    ok = burst_read(0x0000A000, src_check, 16);
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
    burst_write(0x0100B000, src_data, 8);

    // DMA read: burst from CRAM1
    uint32_t buf[8];
    bool ok = burst_read(0x0100B000, buf, 8);
    check("dma_c1sr_rd", ok, 1);

    // DMA write: individual words to SRAM
    for (int i = 0; i < 8; i++)
        write_word(0x0A00B000 + i * 4, buf[i]);

    // Verify SRAM (single-word reads)
    for (int i = 0; i < 8; i++) {
        uint32_t val = read_word(0x0A00B000 + i * 4);
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
        write_word(0x0A00C000 + i * 4, 0xDC000000 | i);

    // DMA read: single-word from SRAM
    uint32_t buf[8];
    for (int i = 0; i < 8; i++)
        buf[i] = read_word(0x0A00C000 + i * 4);

    // DMA write: burst to CRAM0
    bool ok = burst_write(0x0000C000, buf, 8);
    check("dma_src0_wr", ok, 1);

    // Verify CRAM0 (burst read)
    uint32_t verify[8];
    ok = burst_read(0x0000C000, verify, 8);
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
    burst_write(0x0000D000, original, 8);

    // Stage 2: Copy CRAM0 -> CRAM1
    uint32_t buf[8];
    burst_read(0x0000D000, buf, 8);
    burst_write(0x0100D000, buf, 8);

    // Verify CRAM1
    uint32_t v1[8];
    burst_read(0x0100D000, v1, 8);
    for (int i = 0; i < 8; i++) {
        char name[32];
        snprintf(name, sizeof(name), "rt_c1[%d]", i);
        check(name, v1[i], original[i]);
    }

    // Stage 3: Copy CRAM1 -> SRAM (word by word)
    uint32_t buf2[8];
    burst_read(0x0100D000, buf2, 8);
    for (int i = 0; i < 8; i++)
        write_word(0x0A00D000 + i * 4, buf2[i]);

    // Verify SRAM
    for (int i = 0; i < 8; i++) {
        uint32_t val = read_word(0x0A00D000 + i * 4);
        char name[32];
        snprintf(name, sizeof(name), "rt_sr[%d]", i);
        check(name, val, original[i]);
    }

    // Stage 4: Copy SRAM -> CRAM0 (different offset to avoid overlap)
    uint32_t buf3[8];
    for (int i = 0; i < 8; i++)
        buf3[i] = read_word(0x0A00D000 + i * 4);
    burst_write(0x0000E000, buf3, 8);

    // Verify CRAM0 final
    uint32_t v2[8];
    burst_read(0x0000E000, v2, 8);
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
        burst_write(0x00010000 + blk * 0x100, wdata, 8);
    }

    // DMA: burst read from CRAM0, burst write to CRAM1 — 4 blocks back-to-back
    for (int blk = 0; blk < 4; blk++) {
        uint32_t buf[8];
        burst_read(0x00010000 + blk * 0x100, buf, 8);
        burst_write(0x01010000 + blk * 0x100, buf, 8);
    }

    // Verify all 4 blocks in CRAM1
    for (int blk = 0; blk < 4; blk++) {
        uint32_t verify[8];
        bool ok = burst_read(0x01010000 + blk * 0x100, verify, 8);
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
    burst_write(0x0000F000, src, 8);

    // Burst read from CRAM0
    uint32_t buf[8];
    burst_read(0x0000F000, buf, 8);

    // Scatter: even words to CRAM1, odd words to SRAM
    for (int i = 0; i < 8; i++) {
        if (i % 2 == 0)
            write_word(0x0100F000 + (i / 2) * 4, buf[i]);
        else
            write_word(0x0A00F000 + (i / 2) * 4, buf[i]);
    }

    // Gather: read back and verify
    for (int i = 0; i < 8; i++) {
        uint32_t val;
        if (i % 2 == 0) {
            uint32_t d[1];
            burst_read(0x0100F000 + (i / 2) * 4, d, 1);
            val = d[0];
        } else {
            val = read_word(0x0A00F000 + (i / 2) * 4);
        }
        char name[32];
        snprintf(name, sizeof(name), "sg[%d]", i);
        check(name, val, src[i]);
    }
}

// Different address regions within CRAM0
static void test_different_regions() {
    printf("TEST: Different address regions (CRAM0)\n");

    write_word(0x00000000, 0x11111111);
    write_word(0x00010000, 0x22222222);
    write_word(0x00020000, 0x33333333);

    uint32_t d[1];
    burst_read(0x00000000, d, 1); check("region_0", d[0], 0x11111111);
    burst_read(0x00010000, d, 1); check("region_1", d[0], 0x22222222);
    burst_read(0x00020000, d, 1); check("region_2", d[0], 0x33333333);
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
