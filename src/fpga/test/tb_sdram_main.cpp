//------------------------------------------------------------------------------
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileType: SOURCE
// SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
//------------------------------------------------------------------------------

/*
 * Verilator C++ Test Harness for SDRAM Controller Stack
 *
 * Tests: single read/write, burst read, burst write (NEW),
 *        row-hit optimization, mixed traffic, DMA-like patterns.
 */

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <cassert>
#include "Vtb_sdram.h"
#include "verilated.h"
#include "verilated_vcd_c.h"

static Vtb_sdram *tb;
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
    tb->inj_burst_rd = 0;
    tb->inj_burst_addr = 0;
    tb->inj_burst_len = 0;
    for (int i = 0; i < 100; i++)
        tick();
    tb->reset_n = 1;
    // io_sdram boot: 30000 cycle delay + init sequence (~100 more)
    for (int i = 0; i < 31000; i++)
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
// Writes N words starting at byte_addr. For single word, len=0.
static bool axi_write(uint32_t byte_addr, const uint32_t *data, int len, int timeout = 2000) {
    int awlen = len;  // AWLEN = number of beats - 1
    int beats = len + 1;

    // Phase 1: AW channel
    tb->s_axi_awvalid = 1;
    tb->s_axi_awaddr = byte_addr;
    tb->s_axi_awlen = awlen;
    // Also present first W beat
    tb->s_axi_wvalid = 1;
    tb->s_axi_wdata = data[0];
    tb->s_axi_wstrb = 0xF;
    tb->s_axi_wlast = (beats == 1);

    int beat = 0;
    bool aw_done = false;
    bool w_done = false;

    for (int t = 0; t < timeout; t++) {
        bool aw_fire = tb->s_axi_awvalid && tb->s_axi_awready;
        bool w_fire = tb->s_axi_wvalid && tb->s_axi_wready;
        tick();

        // AW handshake
        if (!aw_done && aw_fire) {
            aw_done = true;
            tb->s_axi_awvalid = 0;
        }

        // W handshake
        if (!w_done && w_fire) {
            beat++;
            if (beat >= beats) {
                w_done = true;
                tb->s_axi_wvalid = 0;
            } else {
                tb->s_axi_wdata = data[beat];
                tb->s_axi_wlast = (beat == beats - 1);
            }
        }

        // B response
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

// Convenience: single word write
static bool axi_write_word(uint32_t byte_addr, uint32_t data) {
    return axi_write(byte_addr, &data, 0);
}

// ---- AXI Read Transaction ----
// Reads N words starting at byte_addr. For single word, len=0.
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

// ---- Test Cases ----

static void test_single_write_read() {
    printf("TEST: Single word write/read\n");

    axi_write_word(0x10000000, 0xDEADBEEF);
    uint32_t val = axi_read_word(0x10000000);
    check("single_rw", val, 0xDEADBEEF);

    axi_write_word(0x10000004, 0xCAFEBABE);
    val = axi_read_word(0x10000004);
    check("single_rw_2", val, 0xCAFEBABE);

    // Verify first write wasn't corrupted
    val = axi_read_word(0x10000000);
    check("single_rw_verify", val, 0xDEADBEEF);
}

static void test_burst_read() {
    printf("TEST: Burst read (8 words)\n");

    // Write 8 individual words
    for (int i = 0; i < 8; i++)
        axi_write_word(0x10001000 + i * 4, 0xA0000000 | i);

    // Burst read 8 words
    uint32_t data[8];
    bool ok = axi_read(0x10001000, data, 7);  // ARLEN=7 = 8 beats
    check("burst_rd_ok", ok, 1);
    for (int i = 0; i < 8; i++) {
        char name[32];
        snprintf(name, sizeof(name), "burst_rd[%d]", i);
        check(name, data[i], 0xA0000000 | i);
    }
}

static void test_burst_write() {
    printf("TEST: Burst write (8 words)\n");

    uint32_t wdata[8];
    for (int i = 0; i < 8; i++)
        wdata[i] = 0xB0000000 | i;

    bool ok = axi_write(0x10002000, wdata, 7);  // AWLEN=7 = 8 beats
    check("burst_wr_ok", ok, 1);

    // Read back individually
    for (int i = 0; i < 8; i++) {
        uint32_t val = axi_read_word(0x10002000 + i * 4);
        char name[32];
        snprintf(name, sizeof(name), "burst_wr_verify[%d]", i);
        check(name, val, 0xB0000000 | i);
    }
}

static bool axi_write_2beat_wvalid_on_pull(uint32_t byte_addr,
                                           uint32_t first,
                                           uint32_t second,
                                           int timeout = 4000) {
    tb->s_axi_awvalid = 1;
    tb->s_axi_awaddr = byte_addr;
    tb->s_axi_awlen = 1;  // 2 beats
    tb->s_axi_wvalid = 1;
    tb->s_axi_wdata = first;
    tb->s_axi_wstrb = 0xF;
    tb->s_axi_wlast = 0;

    bool aw_done = false;
    int w_beats = 0;
    int first_w_cycle = -1;
    bool second_presented = false;

    for (int t = 0; t < timeout; t++) {
        bool aw_fire = tb->s_axi_awvalid && tb->s_axi_awready;
        bool w_fire = tb->s_axi_wvalid && tb->s_axi_wready;
        tick();

        if (!aw_done && aw_fire) {
            aw_done = true;
            tb->s_axi_awvalid = 0;
        }

        if (w_fire) {
            w_beats++;
            if (first_w_cycle < 0) first_w_cycle = t;
            tb->s_axi_wvalid = 0;
        }

        // Native 2-beat writes now preload beat 1 before starting io_sdram;
        // older streaming and serialized modes may request it later.  In all
        // cases, present the delayed beat only after the first handshake and
        // when the slave gives an explicit readiness signal.
        bool wants_second = (first_w_cycle >= 0 && t > first_w_cycle) &&
            (tb->s_axi_wready || tb->dbg_word_wr_data_next);
        if (w_beats == 1 && !second_presented && !tb->s_axi_wvalid
            && wants_second) {
            tb->s_axi_wdata = second;
            tb->s_axi_wstrb = 0xF;
            tb->s_axi_wlast = 1;
            tb->s_axi_wvalid = 1;
            second_presented = true;
        }

        if (tb->s_axi_bvalid) {
            tb->s_axi_awvalid = 0;
            tb->s_axi_wvalid = 0;
            idle(2);
            return aw_done && (w_beats == 2);
        }
    }

    printf("  TIMEOUT: wvalid-on-pull 2-beat write addr=0x%08x ser=%d aw=%d beats=%d second=%d\n",
           byte_addr, tb->dbg_serialize_write_bursts, aw_done, w_beats, second_presented);
    tb->s_axi_awvalid = 0;
    tb->s_axi_wvalid = 0;
    return false;
}

struct Write2Stats {
    bool ok;
    bool aw_done;
    bool second_presented;
    int w_beats;
    int pull_count;
    int b_latency;
};

static Write2Stats axi_write_2beat_observed(uint32_t byte_addr,
                                            uint32_t first,
                                            uint32_t second,
                                            uint8_t strb0 = 0xF,
                                            uint8_t strb1 = 0xF,
                                            bool delay_second = false,
                                            bool inject_burst_rd = false,
                                            int timeout = 5000) {
    Write2Stats st = {};
    st.b_latency = -1;

    tb->s_axi_awvalid = 1;
    tb->s_axi_awaddr = byte_addr;
    tb->s_axi_awlen = 1;
    tb->s_axi_wvalid = 1;
    tb->s_axi_wdata = first;
    tb->s_axi_wstrb = strb0;
    tb->s_axi_wlast = 0;

    int aw_cycle = -1;
    int first_w_cycle = -1;
    int burst_started = -1;

    for (int t = 0; t < timeout; t++) {
        bool aw_fire = tb->s_axi_awvalid && tb->s_axi_awready;
        bool w_fire = tb->s_axi_wvalid && tb->s_axi_wready;
        tick();

        if (tb->dbg_word_wr_data_next)
            st.pull_count++;

        if (!st.aw_done && aw_fire) {
            st.aw_done = true;
            aw_cycle = t;
            tb->s_axi_awvalid = 0;
        }

        if (w_fire) {
            st.w_beats++;
            if (first_w_cycle < 0)
                first_w_cycle = t;
            tb->s_axi_wvalid = 0;
        }

        bool present_second = false;
        if (st.w_beats == 1 && !st.second_presented && !tb->s_axi_wvalid) {
            if (delay_second) {
                present_second = (first_w_cycle >= 0 && t > first_w_cycle) &&
                    (tb->s_axi_wready || tb->dbg_word_wr_data_next);
            } else {
                present_second = true;
            }
        }
        if (present_second) {
            tb->s_axi_wdata = second;
            tb->s_axi_wstrb = strb1;
            tb->s_axi_wlast = 1;
            tb->s_axi_wvalid = 1;
            st.second_presented = true;
        }

        if (inject_burst_rd && st.w_beats == 2 && tb->busy && burst_started < 0) {
            tb->inj_burst_rd   = 1;
            tb->inj_burst_addr = 0x00204000;
            tb->inj_burst_len  = 160;
            burst_started = t;
        }
        if (burst_started >= 0 && (t - burst_started) >= 1)
            tb->inj_burst_rd = 0;

        if (tb->s_axi_bvalid) {
            tb->s_axi_awvalid = 0;
            tb->s_axi_wvalid = 0;
            tb->inj_burst_rd = 0;
            st.b_latency = (aw_cycle >= 0) ? (t - aw_cycle) : -1;
            st.ok = st.aw_done && st.second_presented && (st.w_beats == 2);
            idle(inject_burst_rd ? 180 : 2);
            return st;
        }
    }

    tb->s_axi_awvalid = 0;
    tb->s_axi_wvalid = 0;
    tb->inj_burst_rd = 0;
    idle(5);
    return st;
}

static void test_native_2word_local_preload() {
    printf("TEST: Native 2-word write uses local preload, not pull bus\n");

    Write2Stats st = axi_write_2beat_observed(0x10002100,
                                              0x01234567,
                                              0x89ABCDEF);
    check("native2_preload_ok", st.ok, 1);
    check("native2_preload_no_pull", st.pull_count, 0);
    check("native2_preload[0]", axi_read_word(0x10002100), 0x01234567);
    check("native2_preload[1]", axi_read_word(0x10002104), 0x89ABCDEF);
}

static void test_burst_write_wvalid_on_pull() {
    printf("TEST: Burst write accepts WVALID on data pull\n");

    bool ok = axi_write_2beat_wvalid_on_pull(0x10002200,
                                             0x13572468,
                                             0x24681357);
    check("burst_wr_wvalid_on_pull_ok", ok, 1);
    check("burst_wr_wvalid_on_pull[0]", axi_read_word(0x10002200), 0x13572468);
    check("burst_wr_wvalid_on_pull[1]", axi_read_word(0x10002204), 0x24681357);
}

static void test_native_2word_row_crossing() {
    printf("TEST: Native 2-word write crosses SDRAM row boundary\n");

    Write2Stats st = axi_write_2beat_observed(0x100007FC,
                                              0x11223344,
                                              0x55667788);
    check("native2_row_cross_ok", st.ok, 1);
    check("native2_row_cross_no_pull", st.pull_count, 0);
    check("native2_row_cross[0]", axi_read_word(0x100007FC), 0x11223344);
    check("native2_row_cross[1]", axi_read_word(0x10000800), 0x55667788);
}

static void test_native_2word_byte_strobes() {
    printf("TEST: Native 2-word write preserves per-beat byte strobes\n");

    axi_write_word(0x10002400, 0x11223344);
    axi_write_word(0x10002404, 0x55667788);

    Write2Stats st = axi_write_2beat_observed(0x10002400,
                                              0x0000AA00,
                                              0xBB000000,
                                              0x2,
                                              0x8);
    check("native2_strobe_ok", st.ok, 1);
    check("native2_strobe_no_pull", st.pull_count, 0);
    check("native2_strobe[0]", axi_read_word(0x10002400), 0x1122AA44);
    check("native2_strobe[1]", axi_read_word(0x10002404), 0xBB667788);
}

static void test_native_2word_under_burst_rd_contention() {
    printf("TEST: Native 2-word write completes while scanout burst_rd is queued\n");

    Write2Stats st = axi_write_2beat_observed(0x10002600,
                                              0xFACE0001,
                                              0xFACE0002,
                                              0xF,
                                              0xF,
                                              false,
                                              true);
    check("native2_contention_ok", st.ok, 1);
    check("native2_contention_no_pull", st.pull_count, 0);
    if (!tb->dbg_serialize_write_bursts && (st.b_latency < 0 || st.b_latency > 70)) {
        printf("  FAIL native2_contention_latency: got %d, expected <= 70\n", st.b_latency);
        fail_count++;
    } else {
        pass_count++;
    }
    check("native2_contention[0]", axi_read_word(0x10002600), 0xFACE0001);
    check("native2_contention[1]", axi_read_word(0x10002604), 0xFACE0002);
}

static void test_burst_write_16() {
    printf("TEST: Burst write 16 words (cache line)\n");

    uint32_t wdata[16];
    for (int i = 0; i < 16; i++)
        wdata[i] = 0xC0000000 | i;

    bool ok = axi_write(0x10003000, wdata, 15);  // AWLEN=15 = 16 beats
    check("burst_wr16_ok", ok, 1);

    // Burst read back
    uint32_t rdata[16];
    ok = axi_read(0x10003000, rdata, 15);
    check("burst_rd16_ok", ok, 1);
    for (int i = 0; i < 16; i++) {
        char name[32];
        snprintf(name, sizeof(name), "burst_wr16[%d]", i);
        check(name, rdata[i], 0xC0000000 | i);
    }
}

static void test_row_hit_writes() {
    printf("TEST: Row-hit writes (sequential addresses, same row)\n");

    // Write 16 consecutive words — all should hit same SDRAM row
    for (int i = 0; i < 16; i++)
        axi_write_word(0x10004000 + i * 4, 0xD0000000 | i);

    // Read back
    for (int i = 0; i < 16; i++) {
        uint32_t val = axi_read_word(0x10004000 + i * 4);
        char name[32];
        snprintf(name, sizeof(name), "row_hit[%d]", i);
        check(name, val, 0xD0000000 | i);
    }
}

static void test_different_rows() {
    printf("TEST: Writes to different rows\n");

    // Write to addresses in different rows (rows are 1KB apart for 16-bit SDRAM)
    // Row = addr[22:10], so addresses 2KB apart are different rows
    axi_write_word(0x10000000, 0x11111111);
    axi_write_word(0x10002000, 0x22222222);  // Different row
    axi_write_word(0x10004000, 0x33333333);  // Different row

    check("diff_row_0", axi_read_word(0x10000000), 0x11111111);
    check("diff_row_1", axi_read_word(0x10002000), 0x22222222);
    check("diff_row_2", axi_read_word(0x10004000), 0x33333333);
}

static void test_byte_strobes() {
    printf("TEST: Byte strobes (partial writes)\n");

    // Write full word
    axi_write_word(0x10005000, 0x12345678);

    // Partial write: only byte 0
    uint32_t partial = 0x000000AA;
    tb->s_axi_awvalid = 1;
    tb->s_axi_awaddr = 0x10005000;
    tb->s_axi_awlen = 0;
    tb->s_axi_wvalid = 1;
    tb->s_axi_wdata = partial;
    tb->s_axi_wstrb = 0x1;  // Only byte 0
    tb->s_axi_wlast = 1;

    for (int t = 0; t < 500; t++) {
        tick();
        if (tb->s_axi_awready) tb->s_axi_awvalid = 0;
        if (tb->s_axi_wready) tb->s_axi_wvalid = 0;
        if (tb->s_axi_bvalid) break;
    }
    tb->s_axi_awvalid = 0;
    tb->s_axi_wvalid = 0;
    idle(5);

    uint32_t val = axi_read_word(0x10005000);
    check("byte_strobe", val, 0x123456AA);  // Only byte 0 changed
}

static void test_dma_pattern() {
    printf("TEST: DMA-like pattern (burst read → burst write)\n");

    // Setup source data
    uint32_t src_data[16];
    for (int i = 0; i < 16; i++)
        src_data[i] = 0xE0000000 | (i * 0x11);
    axi_write(0x10010000, src_data, 15);

    // DMA pattern: burst read from src, burst write to dst
    uint32_t buf[16];
    bool ok = axi_read(0x10010000, buf, 15);
    check("dma_rd", ok, 1);

    ok = axi_write(0x10020000, buf, 15);
    check("dma_wr", ok, 1);

    // Verify destination
    uint32_t verify[16];
    ok = axi_read(0x10020000, verify, 15);
    check("dma_verify_rd", ok, 1);
    for (int i = 0; i < 16; i++) {
        char name[32];
        snprintf(name, sizeof(name), "dma_copy[%d]", i);
        check(name, verify[i], src_data[i]);
    }
}

static void test_interleaved_rw() {
    printf("TEST: Interleaved reads and writes\n");

    for (int i = 0; i < 8; i++) {
        axi_write_word(0x10030000 + i * 4, 0xF0000000 | i);
        uint32_t val = axi_read_word(0x10030000 + i * 4);
        char name[32];
        snprintf(name, sizeof(name), "interleave[%d]", i);
        check(name, val, 0xF0000000 | i);
    }
}

static void test_burst_write_performance() {
    printf("TEST: Burst write performance measurement\n");

    uint32_t wdata[16];
    for (int i = 0; i < 16; i++)
        wdata[i] = i;

    // Measure single-word writes (16 individual)
    uint64_t t0 = sim_time;
    for (int i = 0; i < 16; i++)
        axi_write_word(0x10040000 + i * 4, wdata[i]);
    uint64_t single_cycles = (sim_time - t0) / 2;  // /2 because tick() does 2 time units

    // Measure burst write (1 transaction, 16 beats)
    t0 = sim_time;
    axi_write(0x10050000, wdata, 15);
    uint64_t burst_cycles = (sim_time - t0) / 2;

    printf("  16 single writes: %lu cycles\n", (unsigned long)single_cycles);
    printf("  1 burst write (16): %lu cycles\n", (unsigned long)burst_cycles);
    printf("  Speedup: %.1fx\n", (double)single_cycles / burst_cycles);

    // Verify both wrote correctly
    for (int i = 0; i < 16; i++) {
        check("perf_single", axi_read_word(0x10040000 + i * 4), wdata[i]);
        check("perf_burst", axi_read_word(0x10050000 + i * 4), wdata[i]);
    }
}

// Thorough burst write pipeline test: stresses the 2-deep request pipeline
// with various burst lengths, patterns, and back-to-back sequences.
static void test_burst_write_pipeline() {
    printf("TEST: Burst write pipeline (2-deep, all lengths)\n");

    // Test every burst length from 1 to 16
    for (int len = 1; len <= 16; len++) {
        uint32_t wdata[16];
        for (int i = 0; i < len; i++)
            wdata[i] = (len << 24) | (0xBEEF00 + i);

        uint32_t base = 0x10060000 + (len - 1) * 0x100;
        bool ok = axi_write(base, wdata, len - 1);
        char name[48];
        snprintf(name, sizeof(name), "pipe_wr_len%d_ok", len);
        check(name, ok, 1);

        // Read back and verify every word
        for (int i = 0; i < len; i++) {
            uint32_t val = axi_read_word(base + i * 4);
            snprintf(name, sizeof(name), "pipe_wr_len%d[%d]", len, i);
            check(name, val, wdata[i]);
        }
    }

    // Back-to-back burst writes to sequential addresses (tests pipeline refill)
    printf("TEST: Back-to-back burst writes (pipeline stress)\n");
    for (int blk = 0; blk < 8; blk++) {
        uint32_t wdata[16];
        for (int i = 0; i < 16; i++)
            wdata[i] = ((blk + 1) << 28) | (i << 4) | blk;
        axi_write(0x10070000 + blk * 64, wdata, 15);
    }
    // Verify all 8 blocks
    for (int blk = 0; blk < 8; blk++) {
        uint32_t rdata[16];
        bool ok = axi_read(0x10070000 + blk * 64, rdata, 15);
        check("b2b_pipe_rd", ok, 1);
        for (int i = 0; i < 16; i++) {
            uint32_t expected = ((blk + 1) << 28) | (i << 4) | blk;
            char name[48];
            snprintf(name, sizeof(name), "b2b_pipe[%d][%d]", blk, i);
            check(name, rdata[i], expected);
        }
    }

    // Walking ones: each word has a single bit set, catches data corruption
    printf("TEST: Walking ones burst write\n");
    uint32_t walk[16];
    for (int i = 0; i < 16; i++)
        walk[i] = 1u << (i * 2);  // Spread bits across word
    axi_write(0x10080000, walk, 15);
    uint32_t rdata[16];
    axi_read(0x10080000, rdata, 15);
    for (int i = 0; i < 16; i++) {
        char name[48];
        snprintf(name, sizeof(name), "walk1[%d]", i);
        check(name, rdata[i], walk[i]);
    }

    // Alternating pattern: catches pipeline data mismatch
    printf("TEST: Alternating pattern burst write\n");
    uint32_t alt[16];
    for (int i = 0; i < 16; i++)
        alt[i] = (i & 1) ? 0xFFFFFFFF : 0x00000000;
    axi_write(0x10090000, alt, 15);
    axi_read(0x10090000, rdata, 15);
    for (int i = 0; i < 16; i++) {
        char name[48];
        snprintf(name, sizeof(name), "alt[%d]", i);
        check(name, rdata[i], alt[i]);
    }

    // Address-as-data: catches address pipeline errors
    printf("TEST: Address-as-data burst write\n");
    uint32_t addr_data[16];
    for (int i = 0; i < 16; i++)
        addr_data[i] = 0x100A0000 + i * 4;
    axi_write(0x100A0000, addr_data, 15);
    axi_read(0x100A0000, rdata, 15);
    for (int i = 0; i < 16; i++) {
        char name[48];
        snprintf(name, sizeof(name), "addrdata[%d]", i);
        check(name, rdata[i], addr_data[i]);
    }

    // Burst write followed by single-word overwrite + re-read
    printf("TEST: Burst write then single-word overwrite\n");
    uint32_t fill[16];
    for (int i = 0; i < 16; i++) fill[i] = 0xAAAAAAAA;
    axi_write(0x100B0000, fill, 15);
    // Overwrite word 7 only
    axi_write_word(0x100B001C, 0x12345678);
    axi_read(0x100B0000, rdata, 15);
    for (int i = 0; i < 16; i++) {
        char name[48];
        snprintf(name, sizeof(name), "overwrite[%d]", i);
        check(name, rdata[i], (i == 7) ? 0x12345678 : 0xAAAAAAAA);
    }
}

// Regression for axi_sdram_slave's word_wr_done-pulse exit (Stage 1 of
// the saw-busy-gate fix).  io_sdram's word_busy is shared across all
// of: word writes, scanout burst_rd, autorefresh, burstwr.  The
// original S_WR_DON exit polled `!sdram_busy`, which stayed high any
// time io_sdram was processing an unrelated op queued back-to-back —
// throttling Duke3D FB writes to ~8 fps.  The fix replaces polling
// with a 1-cycle word_wr_done pulse that fires when io_sdram completes
// THIS write (ST_WRITE_4→ST_IDLE), independent of subsequent activity.
//
// This test reproduces the contention: issue a single-word write,
// then immediately fire a long burst_rd into io_sdram.  io_sdram sees
// word_wr_queue + burst_rd_queue both queued, services word_wr first
// (ST_WRITE_*), then in ST_IDLE picks up burst_rd_queue and returns
// to ST_BURSTRD — keeping word_busy HIGH the whole time.  The slave's
// bvalid must still arrive within a tight bound, proving the exit
// runs off the pulse, not the busy level.
static void test_wr_done_pulse_under_burst_rd_contention() {
    printf("TEST: word_wr_done pulse exits S_WR_DON despite concurrent burst_rd\n");

    tb->s_axi_awvalid = 1;
    tb->s_axi_awaddr  = 0x100C0000;
    tb->s_axi_awlen   = 0;
    tb->s_axi_wvalid  = 1;
    tb->s_axi_wdata   = 0xDEAFCAFE;
    tb->s_axi_wstrb   = 0xF;
    tb->s_axi_wlast   = 1;

    int aw_cycle = -1, b_cycle = -1, burst_started = -1;

    for (int t = 0; t < 500; t++) {
        tick();

        if (tb->s_axi_awready && aw_cycle < 0) {
            aw_cycle = t;
            tb->s_axi_awvalid = 0;
        }
        if (tb->s_axi_wready) {
            tb->s_axi_wvalid = 0;
        }

        // Fire burst_rd one cycle after AW handshake so io_sdram has
        // already accepted the slave's word_wr.  burst_rd_queue is
        // latched and processed AFTER the word_wr completes —
        // extending word_busy=1 across the slave's S_WR_DON wait.
        if (aw_cycle >= 0 && burst_started < 0 && (t - aw_cycle) >= 1) {
            tb->inj_burst_rd   = 1;
            tb->inj_burst_addr = 0x00200000;  // word addr; safe region
            tb->inj_burst_len  = 80;          // long burst, ~85 cycles
            burst_started = t;
        }
        if (burst_started >= 0 && (t - burst_started) >= 1) {
            tb->inj_burst_rd = 0;  // 1-cycle pulse
        }

        if (tb->s_axi_bvalid) {
            b_cycle = t;
            break;
        }
    }

    tb->s_axi_awvalid = 0;
    tb->s_axi_wvalid  = 0;
    tb->inj_burst_rd  = 0;

    if (b_cycle < 0) {
        printf("  FAIL: B never asserted (slave wedged on !sdram_busy?)\n");
        fail_count++;
    } else {
        int latency = b_cycle - aw_cycle;
        printf("  AW@%d B@%d latency=%d cycles\n", aw_cycle, b_cycle, latency);
        // Without Stage 1, the slave waits for the burst_rd to drain
        // (~85 cycles) before bvalid.  With Stage 1, it should be
        // under ~25 cycles (io_sdram processes the write in ~10
        // cycles, slave then asserts bvalid on the pulse).
        if (latency > 40) {
            printf("  FAIL: B latency %d > 40 — slave likely polling !sdram_busy "
                   "across burst_rd\n", latency);
            fail_count++;
        } else {
            pass_count++;
        }
    }

    // Drain io_sdram's burst_rd before next test
    idle(120);
}

int main(int argc, char **argv) {
    Verilated::commandArgs(argc, argv);
    Verilated::traceEverOn(true);
    tb = new Vtb_sdram;
    trace = new VerilatedVcdC;
    tb->trace(trace, 99);
    trace->open("sdram_test.vcd");

    printf("=== SDRAM Controller Test Suite ===\n\n");

    reset();
    printf("SDRAM initialized (%lu cycles)\n\n", (unsigned long)(sim_time / 2));

    test_single_write_read();
    test_burst_read();
    if (trace) { trace->close(); delete trace; trace = nullptr; }  // Stop tracing to keep VCD small
    test_burst_write();
    test_native_2word_local_preload();
    test_burst_write_wvalid_on_pull();
    test_native_2word_row_crossing();
    test_native_2word_byte_strobes();
    test_native_2word_under_burst_rd_contention();
    test_burst_write_16();
    test_row_hit_writes();
    test_different_rows();
    test_byte_strobes();
    test_dma_pattern();
    test_interleaved_rw();
    test_burst_write_performance();
    test_burst_write_pipeline();
    test_wr_done_pulse_under_burst_rd_contention();

    printf("\n=== Results: %d passed, %d failed ===\n",
           pass_count, fail_count);

    if (trace) { trace->close(); delete trace; }
    delete tb;
    return fail_count > 0 ? 1 : 0;
}
