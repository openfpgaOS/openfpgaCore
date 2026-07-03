//------------------------------------------------------------------------------
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileType: SOURCE
// SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
//------------------------------------------------------------------------------
//
// MiSTer boot-lottery hunt: exhaustive phase-sweep hammer of the READ path.
//
//   CPU-style word-READ bursts through axi_sdram_slave -> pulse adapter ->
//   io_sdram (mister copy), data-checked beat-by-beat against the LIVE
//   sdram_model_full backdoor oracle, while scanout burst_rd traffic is
//   injected at every phase alignment.  Drives the slave's AXI port
//   DIRECTLY — no arbiter, no CPU.
//
// Phases:
//   a) READ-ONLY BASELINE     arlen {0,3,7,15}; sequential / row-crossing /
//                             bank-crossing bases; zero-gap AR pre-grant and
//                             re-arm-right-after-RLAST patterns.
//   b) SCANOUT PHASE SWEEP    continuous arlen=15 reads; one scanout burst
//                             fired at EVERY P=0..120 cycles after an AR
//                             handshake (len=80), P=0..40 after mid-burst
//                             beat 8, plus coarse P sweeps for len {1,4,160}.
//   c) WRITES+READS+SCANOUT   single-word (0xF/0x1/0x3/0xC strobes), 2-beat
//                             and 16-beat writes vs reads + swept scanout;
//                             intended-content map verified via oracle.
//   d) RREADY BACKPRESSURE    stall rready N=0..10 cycles at beat 5 with
//                             scanout mid-burst; characterize the real
//                             skid-drop threshold (3-deep skid, ~1 beat per
//                             2 cycles from SDRAM).  Characterization only.
//   e) REFRESH SOAK           long run so AUTOREF (every 736 cycles) lands
//                             inside read+scanout bursts at many alignments;
//                             model protocol error counter must be 0.
//
// Deterministic only (xorshift32, fixed seed).  Exit nonzero on any failure.
//
#include <cstdio>
#include <cstdint>
#include <cstdarg>
#include <cstring>
#include <vector>
#include <deque>
#include <map>
#include "Vtb_sdram_rdscan.h"
#include "verilated.h"

static Vtb_sdram_rdscan *tb;
static uint64_t sim_time = 0;
static inline uint64_t cyc() { return sim_time / 2; }

static void tick() {
    tb->clk = 0; tb->eval(); sim_time++;
    tb->clk = 1; tb->eval(); sim_time++;
}

// ---- deterministic PRNG ----
static uint32_t rng_state = 0x5EEDC0DEu;
static inline uint32_t rnd() {
    uint32_t x = rng_state;
    x ^= x << 13; x ^= x >> 17; x ^= x << 5;
    rng_state = x;
    return x;
}

// ============================================================
// Memory pattern + live oracle
// ============================================================
static inline uint32_t pat(uint32_t w) { return (w * 2654435761u) ^ 0xA5A50000u; }

// Live combinational backdoor read of the model — ground truth at this instant.
static uint32_t oracle(uint32_t word_addr) {
    tb->bd_rd_word_addr = word_addr & 0xFFFFFF;
    tb->eval();
    return tb->bd_rd_data;
}

// Preloaded WORD-address regions (see preload()):
//   MAIN   0x000000..0x040000   (1MB)  — read stream / scanout / write areas
//   KERNEL 0x0C7C00..0x0C8400          — window at the HW kernel-image base
//   BANK   0x3FFF00..0x400100          — straddles the bank0->bank1 boundary
static const uint32_t RD_REGION   = 0x000000;  // reads walk [0x000000,0x020000)
static const uint32_t SCAN_REGION = 0x020000;  // scanout    [0x020000,0x028000)
static const uint32_t WR_REGION   = 0x030000;  // writes     [0x030000,0x031400)

// ============================================================
// Counters / failure reporting
// ============================================================
struct Cnt { long pass = 0, fail = 0; };
static Cnt c_read, c_scan, c_write, c_proto;
static long g_fail_total = 0;
static int  g_fail_prints = 0;
static const int FAIL_PRINT_MAX = 60;
static const char *g_phase = "init";

static void fail_note(Cnt &c, const char *fmt, ...) {
    c.fail++;
    g_fail_total++;
    if (g_fail_prints < FAIL_PRINT_MAX) {
        va_list ap; va_start(ap, fmt);
        printf("  FAIL [%s] cyc=%llu: ", g_phase, (unsigned long long)cyc());
        vprintf(fmt, ap);
        printf("\n");
        va_end(ap);
        g_fail_prints++;
    }
}

// ============================================================
// Bus engine: drives AXI + scanout inputs, checks every response beat.
// ============================================================

// AR driver (one pending request; phases refill per their pattern)
static bool     ar_req = false;
static uint32_t ar_word = 0;
static uint8_t  ar_len_ = 0;

// outstanding read transactions (slave may accept AR#n+1 while last beat of
// #n still sits in its R slot)
struct ReadTxn { uint32_t word; uint8_t len; };
static std::deque<ReadTxn> rq;
static uint32_t r_beat = 0;          // beats received of rq.front()

// rready backpressure control (phase d)
static int rready_low_cycles = 0;

// R-capture mode (phase d): record beats, skip engine golden-check
static bool capture_r = false;
static std::vector<uint32_t> r_capture;
static int r_capture_last_beat = -1; // beat index at which RLAST was seen

// write driver
static bool wr_active = false, aw_done = false, w_done = false;
static bool wr_w_immediate = true;   // drive W alongside AW vs after AW
static uint32_t wr_word = 0;
static std::vector<uint32_t> wr_data;
static std::vector<uint8_t>  wr_strb;
static uint32_t w_beat = 0;
static long b_count = 0;

// scanout driver/checker
static bool scan_fire = false, scan_active = false;
static uint32_t scan_word = 0, scan_len = 0, scan_beat = 0;

// watchdog
static bool wd_enabled = true;
static uint64_t last_event_cyc = 0;
static const uint64_t WD_LIMIT = 5000;

struct Ev {
    bool ar_fire = false;
    bool r_fire = false;    // an R beat handshake happened
    bool r_last = false;    // ... and it was RLAST (txn retired)
    uint32_t r_txn_beat = 0;
    bool aw_fire = false, w_fire = false, b_fire = false;
    bool scan_done = false;
};

static void dump_wd_state(void) {
    printf("  WATCHDOG [%s] cyc=%llu: no R/scan/B progress for %llu cycles\n"
           "    slave_state=%u dbg_io=0x%02x arvalid=%d(word=0x%06x len=%u) "
           "rq=%zu r_beat=%u rvalid=%d rready=%d\n"
           "    wr_active=%d aw_done=%d w_beat=%u scan_active=%d scan_beat=%u/%u\n",
           g_phase, (unsigned long long)cyc(), (unsigned long long)WD_LIMIT,
           (unsigned)tb->dbg_slave_state, (unsigned)tb->dbg_io,
           ar_req ? 1 : 0, ar_word, ar_len_,
           rq.size(), r_beat, tb->s_axi_rvalid ? 1 : 0, tb->s_axi_rready ? 1 : 0,
           wr_active ? 1 : 0, aw_done ? 1 : 0, w_beat,
           scan_active ? 1 : 0, scan_beat, scan_len);
}

static Ev step() {
    Ev ev;

    // ---- drive inputs ----
    tb->s_axi_arvalid = ar_req ? 1 : 0;
    if (ar_req) { tb->s_axi_araddr = ar_word << 2; tb->s_axi_arlen = ar_len_; }
    tb->s_axi_rready = (rready_low_cycles > 0) ? 0 : 1;

    tb->s_axi_awvalid = 0;
    tb->s_axi_wvalid = 0;
    tb->s_axi_wlast = 0;
    if (wr_active) {
        if (!aw_done) {
            tb->s_axi_awvalid = 1;
            tb->s_axi_awaddr = wr_word << 2;
            tb->s_axi_awlen = (uint8_t)(wr_data.size() - 1);
        }
        if (!w_done && (wr_w_immediate || aw_done)) {
            tb->s_axi_wvalid = 1;
            tb->s_axi_wdata = wr_data[w_beat];
            tb->s_axi_wstrb = wr_strb[w_beat];
            tb->s_axi_wlast = (w_beat == wr_data.size() - 1) ? 1 : 0;
        }
    }
    tb->s_axi_wcont = 0;

    tb->burst_rd = scan_fire ? 1 : 0;
    if (scan_fire) {
        tb->burst_addr = (scan_word << 1) & 0x1FFFFFF;
        tb->burst_len = (uint16_t)(scan_len & 0x7FF);
    }
    tb->bd_we = 0;

    // ---- sample pre-edge (registered DUT outputs = post-previous-tick) ----
    tb->eval();
    bool ar_fire = tb->s_axi_arvalid && tb->s_axi_arready;
    bool r_fire  = tb->s_axi_rvalid && tb->s_axi_rready;
    bool r_last  = tb->s_axi_rlast != 0;
    uint32_t r_data = tb->s_axi_rdata;
    uint8_t  r_resp = tb->s_axi_rresp;
    bool aw_fire = tb->s_axi_awvalid && tb->s_axi_awready;
    bool w_fire  = tb->s_axi_wvalid && tb->s_axi_wready;
    bool b_fire  = tb->s_axi_bvalid != 0;   // bready tied 1 in the tb
    bool sc_beat = tb->burst_data_valid != 0;
    uint32_t sc_data = tb->burst_data;
    bool sc_done = tb->burst_data_done != 0;

    tick();
    if (rready_low_cycles > 0) rready_low_cycles--;
    if (scan_fire) { scan_fire = false; scan_active = true; scan_beat = 0; }

    // ---- react ----
    if (ar_fire) {
        rq.push_back({ar_word, ar_len_});
        ar_req = false;
        ev.ar_fire = true;
    }
    if (aw_fire) { aw_done = true; ev.aw_fire = true; }
    if (w_fire && wr_active && !w_done) {
        w_beat++;
        if (w_beat >= wr_data.size()) w_done = true;
        ev.w_fire = true;
    }
    if (b_fire && wr_active) {
        if (!w_done)
            fail_note(c_proto, "B response before all W beats accepted (w_beat=%u/%zu addr=0x%06x)",
                      w_beat, wr_data.size(), wr_word);
        wr_active = false;
        b_count++;
        ev.b_fire = true;
    }

    if (r_fire) {
        ev.r_fire = true;
        if (r_resp != 0)
            fail_note(c_proto, "RRESP=%u (nonzero)", r_resp);
        if (rq.empty()) {
            fail_note(c_proto, "R beat with no outstanding read (data=0x%08x last=%d)",
                      r_data, r_last ? 1 : 0);
        } else {
            ReadTxn &t = rq.front();
            ev.r_txn_beat = r_beat;
            if (capture_r) {
                r_capture.push_back(r_data);
                if (r_last) r_capture_last_beat = (int)r_beat;
            } else {
                uint32_t want = oracle(t.word + r_beat);
                bool data_ok = (r_data == want);
                bool last_ok = (r_last == (r_beat == t.len));
                if (data_ok && last_ok) {
                    c_read.pass++;
                } else {
                    fail_note(c_read, "R beat MISMATCH base=0x%06x(word) beat=%u/%u "
                              "got=0x%08x want=0x%08x rlast=%d(exp %d)",
                              t.word, r_beat, t.len, r_data, want,
                              r_last ? 1 : 0, (r_beat == t.len) ? 1 : 0);
                }
            }
            if (r_last) {
                rq.pop_front();
                r_beat = 0;
                ev.r_last = true;
            } else {
                r_beat++;
                if (r_beat > t.len) {
                    fail_note(c_proto, "R beat overrun without RLAST base=0x%06x len=%u",
                              t.word, t.len);
                    rq.pop_front();
                    r_beat = 0;
                }
            }
        }
    }

    if (sc_beat) {
        if (!scan_active) {
            fail_note(c_proto, "scanout beat with no active burst (data=0x%08x)", sc_data);
        } else {
            // burst_addr is a HALFWORD address; each beat is one 32-bit word.
            uint32_t want = oracle(((scan_word << 1) + 2 * scan_beat) >> 1);
            if (sc_data == want) c_scan.pass++;
            else fail_note(c_scan, "SCANOUT beat MISMATCH base=0x%06x(word) beat=%u/%u "
                           "got=0x%08x want=0x%08x",
                           scan_word, scan_beat, scan_len, sc_data, want);
            scan_beat++;
        }
    }
    if (sc_done) {
        if (scan_active) {
            if (scan_beat != scan_len)
                fail_note(c_scan, "scanout burst delivered %u beats, expected %u (base=0x%06x)",
                          scan_beat, scan_len, scan_word);
            scan_active = false;
            ev.scan_done = true;
        }
    }

    // ---- watchdog ----
    bool progress = ev.r_fire || ev.b_fire || sc_beat || sc_done || ev.ar_fire ||
                    ev.aw_fire || ev.w_fire;
    bool outstanding = !rq.empty() || ar_req || wr_active || scan_active;
    if (progress || !outstanding) last_event_cyc = cyc();
    if (wd_enabled && outstanding && (cyc() - last_event_cyc) > WD_LIMIT) {
        dump_wd_state();
        fail_note(c_proto, "WATCHDOG: hang with transaction outstanding");
        last_event_cyc = cyc();   // re-arm (avoid spam)
    }

    return ev;
}

// ---- engine helpers ----
static void issue_ar(uint32_t word, uint8_t len) { ar_word = word; ar_len_ = len; ar_req = true; }

static bool fire_scan(uint32_t word, uint32_t len) {
    if (scan_active || scan_fire) return false;
    scan_word = word; scan_len = len; scan_fire = true;
    return true;
}

static void start_write(uint32_t word, const std::vector<uint32_t> &d,
                        const std::vector<uint8_t> &s, bool w_immediate) {
    wr_active = true; aw_done = false; w_done = false; w_beat = 0;
    wr_word = word; wr_data = d; wr_strb = s; wr_w_immediate = w_immediate;
}

static void run_idle(int n) { for (int i = 0; i < n; i++) step(); }

// Drain all outstanding reads (driver quiet).
static bool drain_reads(int guard) {
    while ((!rq.empty() || ar_req || scan_active) && guard-- > 0) step();
    return guard > 0;
}

// ---- per-phase bookkeeping ----
static Cnt s_read, s_scan, s_write, s_proto;
static unsigned s_model_errs = 0;
static void begin_phase(const char *name) {
    g_phase = name;
    s_read = c_read; s_scan = c_scan; s_write = c_write; s_proto = c_proto;
    s_model_errs = (unsigned)tb->dbg_model_errors;
    printf("\n[phase %s]\n", name);
}
static void end_phase() {
    unsigned me = (unsigned)tb->dbg_model_errors - s_model_errs;
    printf("  [%s] read beats: %ld pass / %ld fail | scan beats: %ld pass / %ld fail | "
           "write checks: %ld pass / %ld fail | protocol: %ld fail | model errs: +%u\n",
           g_phase,
           c_read.pass - s_read.pass, c_read.fail - s_read.fail,
           c_scan.pass - s_scan.pass, c_scan.fail - s_scan.fail,
           c_write.pass - s_write.pass, c_write.fail - s_write.fail,
           c_proto.fail - s_proto.fail, me);
}

// ============================================================
// Preload + boot
// ============================================================
static void preload_range(uint32_t w0, uint32_t w1) {
    for (uint32_t w = w0; w < w1; w++) {
        tb->bd_we = 1;
        tb->bd_word_addr = w;
        tb->bd_wdata = pat(w);
        tick();
    }
    tb->bd_we = 0;
}

static void boot_and_preload() {
    g_phase = "boot";
    tb->reset_n = 0;
    tb->s_axi_arvalid = 0; tb->s_axi_awvalid = 0; tb->s_axi_wvalid = 0;
    tb->s_axi_rready = 1; tb->s_axi_wcont = 0;
    tb->burst_rd = 0; tb->burst_addr = 0; tb->burst_len = 0;
    tb->bd_we = 0; tb->bd_word_addr = 0; tb->bd_wdata = 0; tb->bd_rd_word_addr = 0;
    for (int i = 0; i < 16; i++) tick();

    printf("[boot] preloading pattern (word = (W*2654435761)^0xA5A50000)...\n");
    preload_range(0x000000, 0x040000);   // 1MB main region
    preload_range(0x0C7C00, 0x0C8400);   // kernel-image window (row-boundary at 0x0C8000)
    preload_range(0x3FFF00, 0x400100);   // bank0 -> bank1 boundary window
    for (int i = 0; i < 16; i++) tick();

    tb->reset_n = 1;
    // io_sdram runs reset_n through a 3-stage synchronizer (synch_3) — its
    // internal reset clause stays active ~5 cycles after the top release and
    // clears word_rd_queue while word_busy reads 0, so a read forwarded in
    // that window is silently swallowed and the slave wedges in S_RD_DAT.
    // No real master issues within 3 cycles of reset; idle the window out.
    for (int i = 0; i < 64; i++) tick();
    // io_sdram power-up init is ~30000+ cycles; poll with single-word reads
    // at 0x0 until one completes and matches.  Watchdog off (the first read
    // legitimately waits out the whole init).
    wd_enabled = false;
    bool ok = false;
    for (int attempt = 0; attempt < 3 && !ok; attempt++) {
        long pass_before = c_read.pass, fail_before = c_read.fail;
        issue_ar(0x000000, 0);
        int guard = 45000;
        while (guard-- > 0) {
            Ev e = step();
            if (getenv("RDSCAN_DEBUG") && (guard % 5000) == 0)
                printf("[boot-dbg] cyc=%llu slave_state=%u dbg_io=0x%02x rvalid=%d ar_req=%d rq=%zu\n",
                       (unsigned long long)cyc(), (unsigned)tb->dbg_slave_state,
                       (unsigned)tb->dbg_io, tb->s_axi_rvalid ? 1 : 0, ar_req ? 1 : 0, rq.size());
            if (getenv("RDSCAN_DEBUG") && e.r_fire)
                printf("[boot-dbg] R beat cyc=%llu data=0x%08x last=%d (want 0x%08x)\n",
                       (unsigned long long)cyc(), (unsigned)tb->s_axi_rdata,
                       tb->s_axi_rlast ? 1 : 0, pat(0));
            if (e.r_last) { ok = (c_read.pass > pass_before) && (c_read.fail == fail_before); break; }
        }
        if (!ok) printf("[boot] attempt %d: first read did not complete/match, retrying\n", attempt);
    }
    wd_enabled = true;
    last_event_cyc = cyc();
    if (!ok) {
        fail_note(c_proto, "BOOT: first single-word read at 0x0 never completed correctly");
        printf("[boot] FATAL: io_sdram init poll failed\n");
    } else {
        printf("[boot] init complete at cyc=%llu, first read matches\n",
               (unsigned long long)cyc());
    }
}

// ============================================================
// Phase a: READ-ONLY BASELINE
// ============================================================
// mode 0: zero-gap pre-grant — next AR asserted immediately after the
//         previous AR handshake (ARVALID effectively continuous).
// mode 1: re-assert ARVALID on the cycle right after the previous burst's
//         last R beat handshake.
static void run_read_list(const std::vector<ReadTxn> &list, int mode) {
    if (list.empty()) return;
    size_t idx = 0;
    issue_ar(list[0].word, list[0].len);
    idx = 1;
    long guard = 400000;
    while ((idx < list.size() || !rq.empty() || ar_req) && guard-- > 0) {
        Ev e = step();
        if (mode == 0) {
            if (!ar_req && idx < list.size()) { issue_ar(list[idx].word, list[idx].len); idx++; }
        } else {
            if (e.r_last && idx < list.size()) { issue_ar(list[idx].word, list[idx].len); idx++; }
        }
    }
    if (guard <= 0) fail_note(c_proto, "phase-a read list wedged (idx=%zu/%zu)", idx, list.size());
}

static void phase_a() {
    begin_phase("a: read-only baseline");
    const uint8_t lens[4] = {0, 3, 7, 15};
    std::vector<ReadTxn> list;
    for (int li = 0; li < 4; li++) {
        uint8_t len = lens[li];
        // sequential back-to-back
        uint32_t a = 0x001000 + (uint32_t)li * 0x400;
        for (int i = 0; i < 32; i++) { list.push_back({a, len}); a += len + 1; }
        // row-crossing: straddle word addresses ending 0x1FC -> 0x200
        // (io_sdram row = 1024 halfwords = 512 words, boundary every 0x200 words)
        for (uint32_t k = 2; k <= 9; k++) {
            uint32_t edge = k * 0x200;
            if (len == 0) {
                list.push_back({edge - 1, 0});
                list.push_back({edge, 0});
            } else {
                list.push_back({edge - 1, len});
                list.push_back({edge - 2, len});
                list.push_back({edge - (uint32_t)((len + 1) / 2), len});
                list.push_back({edge - (uint32_t)len, len});
                list.push_back({edge - 4, len});   // ...0x1FC start
            }
        }
        // kernel-image window row boundary at word 0x0C8000
        if (len == 0) {
            list.push_back({0x0C7FFF, 0});
            list.push_back({0x0C8000, 0});
        } else {
            list.push_back({0x0C8000 - 1, len});
            list.push_back({0x0C8000 - (uint32_t)((len + 1) / 2), len});
            list.push_back({0x0C8000 - (uint32_t)len, len});
        }
        // bank-crossing: bank0 -> bank1 at word 0x400000 (hw[24:23] changes)
        if (len == 0) {
            list.push_back({0x3FFFFF, 0});
            list.push_back({0x400000, 0});
        } else {
            list.push_back({0x400000 - 1, len});
            list.push_back({0x400000 - (uint32_t)((len + 1) / 2), len});
            list.push_back({0x400000 - (uint32_t)len, len});
        }
    }
    printf("  %zu read bursts x 2 AR patterns\n", list.size());
    run_read_list(list, 0);   // zero-gap pre-grant
    run_read_list(list, 1);   // re-arm right after RLAST
    drain_reads(50000);
    end_phase();

    // -- minimal repro probe: bank-crossing re-cross ACT-to-active-bank --
    // Two back-to-back arlen=3 reads at word 0x3FFFFE, each crossing the
    // bank0->bank1 boundary at word 0x400000.  Burst #1 legally ACTs bank1
    // row0 and (open-page policy) leaves it open; burst #2's mid-burst
    // row-crossing continuation precharges only the CURRENT bank (bank0)
    // and unconditionally re-ACTs bank1 row0 while it is still active —
    // an illegal SDRAM command sequence.  4 pairs (an AUTOREF between a
    // pair precharges-all and masks that pair).
    begin_phase("a2: bank-recross minimal repro");
    for (int p = 0; p < 4; p++) {
        unsigned e0 = (unsigned)tb->dbg_model_errors;
        std::vector<ReadTxn> pair = {{0x3FFFFE, 3}, {0x3FFFFE, 3}};
        run_read_list(pair, 0);
        drain_reads(5000);
        unsigned d = (unsigned)tb->dbg_model_errors - e0;
        printf("  pair %d: 2x [arlen=3 @ word 0x3FFFFE] -> model errors +%u%s\n",
               p, d, d ? "  (ACT to bank1 while row 0 open = illegal ACT-to-active-bank)" : "");
    }
    end_phase();
}

// ============================================================
// Phase b: SCANOUT PHASE SWEEP vs continuous reads
// ============================================================
static uint32_t stream_word = 0;
static void stream_refill() {
    if (!ar_req) {
        issue_ar(RD_REGION + stream_word, 15);
        stream_word = (stream_word + 16) & 0x1FFFF;
    }
}

// Wait (with stream refill) until predicate-ish events; helpers return false on guard expiry.
static bool wait_scan_idle(int guard) {
    while ((scan_active || scan_fire) && guard-- > 0) { stream_refill(); step(); }
    return guard > 0;
}
static bool wait_ar_fire(int guard) {
    while (guard-- > 0) { stream_refill(); Ev e = step(); if (e.ar_fire) return true; }
    return false;
}
static bool wait_r_beat(uint32_t beat, int guard) {
    while (guard-- > 0) {
        stream_refill();
        Ev e = step();
        if (e.r_fire && !e.r_last && e.r_txn_beat == beat) return true;
    }
    return false;
}

static void phase_b() {
    begin_phase("b: scanout phase sweep vs reads");
    stream_word = 0;

    // Full sweep: P = 0..120 after an AR handshake, scan len 80
    for (int P = 0; P <= 120; P++) {
        if (!wait_scan_idle(20000)) { fail_note(c_proto, "P=%d: scan never idles", P); break; }
        if (!wait_ar_fire(20000))   { fail_note(c_proto, "P=%d: no AR fire", P); break; }
        for (int i = 0; i < P; i++) { stream_refill(); step(); }
        uint32_t base = SCAN_REGION + ((uint32_t)P * 0x1F0) % 0x7000;
        fire_scan(base, 80);
        if (!wait_scan_idle(20000)) fail_note(c_proto, "P=%d: scan len=80 never completed", P);
    }

    // Mid-burst sweep: P = 0..40 after the R handshake of beat 8
    for (int P = 0; P <= 40; P++) {
        if (!wait_scan_idle(20000)) { fail_note(c_proto, "mid P=%d: scan never idles", P); break; }
        if (!wait_r_beat(8, 20000)) { fail_note(c_proto, "mid P=%d: no beat-8 anchor", P); break; }
        for (int i = 0; i < P; i++) { stream_refill(); step(); }
        uint32_t base = SCAN_REGION + 0x1000 + ((uint32_t)P * 0x230) % 0x6000;
        fire_scan(base, 80);
        if (!wait_scan_idle(20000)) fail_note(c_proto, "mid P=%d: scan len=80 never completed", P);
    }

    // Coarse sweep for other scanout lengths (incl. row-straddling bases)
    const uint32_t clens[3] = {1, 4, 160};
    const int cps[5] = {0, 7, 13, 29, 61};
    for (int li = 0; li < 3; li++) {
        for (int pi = 0; pi < 5; pi++) {
            int P = cps[pi];
            if (!wait_scan_idle(20000)) { fail_note(c_proto, "len=%u: scan never idles", clens[li]); break; }
            if (!wait_ar_fire(20000))   { fail_note(c_proto, "len=%u: no AR fire", clens[li]); break; }
            for (int i = 0; i < P; i++) { stream_refill(); step(); }
            uint32_t base;
            switch (clens[li]) {
                case 160: base = 0x021FC0; break;              // crosses row edge 0x022000
                case 4:   base = 0x0203FE; break;              // straddles 0x020400
                default:  base = SCAN_REGION + 0x2000 + (uint32_t)P; break;
            }
            fire_scan(base, clens[li]);
            if (!wait_scan_idle(20000))
                fail_note(c_proto, "len=%u P=%d: scan never completed", clens[li], P);
        }
    }

    drain_reads(50000);
    end_phase();
}

// ============================================================
// Phase c: WRITES + READS + SCANOUT
// ============================================================
static std::map<uint32_t, uint32_t> intended;  // word addr -> intended content
static uint32_t intended_get(uint32_t w) {
    auto it = intended.find(w);
    return it == intended.end() ? pat(w) : it->second;
}
static void intended_write(uint32_t w, uint32_t d, uint8_t strb) {
    uint32_t v = intended_get(w);
    for (int b = 0; b < 4; b++)
        if (strb & (1u << b)) {
            v &= ~(0xFFu << (8 * b));
            v |= (d & (0xFFu << (8 * b)));
        }
    intended[w] = v;
}

static void phase_c() {
    begin_phase("c: writes + reads + scanout");
    intended.clear();
    b_count = 0;

    const int NWRITES = 240;
    uint32_t wcur = WR_REGION;
    uint32_t wmax = WR_REGION;
    int widx = 0;
    long expected_b = 0;

    // read stream (with gaps so AW can win the slave's AR-priority S_IDLE)
    const int gap_pat[6] = {0, 2, 0, 6, 1, 10};
    int gap = 0, gi = 0;
    uint32_t rdw = 0;
    bool rd_outstanding_arm = false;

    // scanout anchored P cycles after each AW handshake, P swept 0..40
    bool scan_armed = false;
    int scan_wait = 0;
    uint32_t scan_base_c = SCAN_REGION + 0x4000;

    long guard = 2000000;
    while ((widx < NWRITES || wr_active || !rq.empty() || ar_req || scan_active) && guard-- > 0) {
        // reads: one burst at a time, gap after RLAST
        if (!ar_req && rq.empty() && !rd_outstanding_arm && gap == 0) {
            issue_ar(RD_REGION + rdw, 15);
            rdw = (rdw + 16) & 0x1FFFF;
            rd_outstanding_arm = true;
        }
        if (gap > 0) gap--;

        // writes: issue next op when previous fully retired
        if (!wr_active && widx < NWRITES) {
            std::vector<uint32_t> d;
            std::vector<uint8_t> s;
            uint32_t base = wcur;
            int kind = widx % 6;
            switch (kind) {
                case 0: case 1: case 2: case 3: {   // single word, strobes F/1/3/C
                    static const uint8_t strbs[4] = {0xF, 0x1, 0x3, 0xC};
                    d.push_back(rnd());
                    s.push_back(strbs[kind]);
                    wcur = base + 1;
                    break;
                }
                case 4: {                            // native-class 2-beat (buffered, wcont=0)
                    d.push_back(rnd()); d.push_back(rnd());
                    s.push_back(0xF);  s.push_back(0xF);
                    wcur = base + 2;
                    break;
                }
                default: {                           // 16-beat writeback burst
                    if ((widx / 6) % 2 == 0)         // half of them straddle a row edge
                        base = ((wcur / 0x200) + 1) * 0x200 - 8;
                    for (int i = 0; i < 16; i++) { d.push_back(rnd()); s.push_back(0xF); }
                    wcur = base + 16;
                    break;
                }
            }
            for (size_t i = 0; i < d.size(); i++)
                intended_write(base + (uint32_t)i, d[i], s[i]);
            if (wcur > wmax) wmax = wcur;
            start_write(base, d, s, (widx & 1) != 0);
            expected_b++;
            widx++;
        }

        Ev e = step();
        if (e.r_last) { gap = gap_pat[gi++ % 6]; rd_outstanding_arm = false; }
        if (e.aw_fire && !scan_armed) {
            scan_armed = true;
            scan_wait = (widx * 7) % 41;   // coarse P sweep 0..40
        }
        if (scan_armed) {
            if (scan_wait > 0) scan_wait--;
            else if (fire_scan(scan_base_c, 80)) {
                scan_armed = false;
                scan_base_c = SCAN_REGION + 0x4000 + (scan_base_c + 0x150) % 0x3E00;
            }
        }
        // keep write region out of the wrap range of scan_base_c: SCAN_REGION+0x4000..+0x7F50+80 < 0x30000 OK
    }
    if (guard <= 0) fail_note(c_proto, "phase-c wedged (widx=%d wr_active=%d rq=%zu)",
                              widx, wr_active ? 1 : 0, rq.size());
    run_idle(500);   // quiesce

    if (b_count != expected_b)
        fail_note(c_write, "B count %ld != writes issued %ld", b_count, expected_b);

    // verify the whole write region span via the oracle: written words must
    // equal the intended map, untouched words must still hold the preload
    // pattern (catches writes that landed at a WRONG address nearby).
    uint32_t v0 = WR_REGION - 0x20, v1 = wmax + 0x20;
    long vfail0 = c_write.fail;
    for (uint32_t w = v0; w < v1; w++) {
        uint32_t want = intended_get(w);
        uint32_t got = oracle(w);
        if (got == want) c_write.pass++;
        else fail_note(c_write, "write-region verify word=0x%06x got=0x%08x want=0x%08x (%s)",
                       w, got, want, intended.count(w) ? "written" : "UNTOUCHED");
    }
    printf("  %d writes (%ld B resps), region verify 0x%06x..0x%06x: %s\n",
           NWRITES, b_count, v0, v1, (c_write.fail == vfail0) ? "clean" : "MISMATCHES");
    end_phase();

    // -- minimal repro probe: bank-crossing WRITE burst into an open bank --
    // Open bank1 row0 with a single read at word 0x400000 (open-page policy
    // leaves it active), then issue a 16-beat write at word 0x3FFFF8: its
    // mid-burst row-crossing (ST_WRITE_4_NEWROW path) precharges only the
    // bank just written (bank0) and unconditionally ACTs bank1 row0 while
    // it is still active — same illegal ACT-to-active-bank as the read path.
    begin_phase("c2: bank-crossing write minimal repro");
    for (int p = 0; p < 4; p++) {
        unsigned e0 = (unsigned)tb->dbg_model_errors;
        issue_ar(0x400000, 0);            // opens bank1 row0
        drain_reads(5000);
        const uint32_t base = 0x3FFFF8;   // 16 beats -> crosses into bank1
        std::vector<uint32_t> d;
        std::vector<uint8_t> s;
        for (int i = 0; i < 16; i++) { d.push_back(rnd()); s.push_back(0xF); }
        for (int i = 0; i < 16; i++) intended_write(base + (uint32_t)i, d[i], s[i]);
        start_write(base, d, s, true);
        int guard = 5000;
        while (wr_active && guard-- > 0) step();
        run_idle(50);
        unsigned dd = (unsigned)tb->dbg_model_errors - e0;
        for (uint32_t w = base; w < base + 16; w++) {
            uint32_t got = oracle(w), want = intended_get(w);
            if (got == want) c_write.pass++;
            else fail_note(c_write, "bank-cross write word=0x%06x got=0x%08x want=0x%08x",
                           w, got, want);
        }
        printf("  pair %d: rd@0x400000 then 16-beat wr@0x3FFFF8 -> model errors +%u%s\n",
               p, dd, dd ? "  (illegal ACT-to-active-bank on write row-cross)" : "");
    }
    end_phase();
}

// ============================================================
// Phase d: RREADY BACKPRESSURE CHARACTERIZATION
// ============================================================
static void phase_d() {
    begin_phase("d: rready backpressure characterization (not pass/fail unless N<=6)");
    printf("  slave R pipeline: slot + 2 skid entries; SDRAM streams ~1 beat/2 cycles.\n"
           "  On hardware CPU rready is constant-1 — this maps the design margin.\n");
    printf("  %-4s %-7s %-6s %-9s %-6s %-11s %s\n",
           "N", "scanP", "beats", "rlast@", "mism", "lost", "verdict");

    int max_clean_N = -1;
    int min_lossy_N = 999;
    int run = 0;
    for (int N = 0; N <= 10; N++) {
        bool n_clean = true;
        const int scanPs[4] = {-1, 3, 12, 24};
        for (int spi = 0; spi < 4; spi++) {
            int scanP = scanPs[spi];
            uint32_t base = 0x010000 + (uint32_t)(run++ * 32) % 0x8000;

            capture_r = true;
            r_capture.clear();
            r_capture_last_beat = -1;

            issue_ar(base, 15);
            // wait for AR fire
            int guard = 2000;
            bool anchored = false;
            while (guard-- > 0) { Ev e = step(); if (e.ar_fire) { anchored = true; break; } }
            if (!anchored) { fail_note(c_proto, "d: AR never accepted"); capture_r = false; break; }

            int scan_cnt = scanP;
            bool stall_armed = false;
            bool done = false;
            guard = 4000;
            while (guard-- > 0 && !done) {
                if (scan_cnt == 0) fire_scan(SCAN_REGION + 0x6000 + (uint32_t)run * 32, 20);
                if (scan_cnt >= 0) scan_cnt--;
                Ev e = step();
                if (e.r_fire && e.r_txn_beat == 4 && !stall_armed && N > 0) {
                    rready_low_cycles = N;   // stall starts at beat 5
                    stall_armed = true;
                }
                if (r_capture_last_beat >= 0 && !scan_active && !scan_fire) done = true;
            }
            bool timeout = !done;

            // analyze
            int beats = (int)r_capture.size();
            int mism = 0, first_mism = -1;
            for (int i = 0; i < beats && i < 16; i++) {
                if (r_capture[i] != oracle(base + (uint32_t)i)) {
                    mism++;
                    if (first_mism < 0) first_mism = i;
                }
            }
            int lost = 16 - beats;
            bool lossy = (lost != 0) || (mism != 0) || (r_capture_last_beat != beats - 1) || timeout;
            const char *verdict;
            if (!lossy) verdict = "clean";
            else if (N <= 6) verdict = "REAL BUG (within design tolerance)";
            else verdict = "expected-by-design (skid overflow)";

            char rl[16];
            if (timeout) snprintf(rl, sizeof rl, "TIMEOUT");
            else snprintf(rl, sizeof rl, "%d", r_capture_last_beat);
            printf("  %-4d %-7d %-6d %-9s %-6d %-11d %s%s\n",
                   N, scanP, beats, rl, mism, lost, verdict,
                   (first_mism >= 0) ? "" : "");
            if (lossy && first_mism >= 0 && g_fail_prints < FAIL_PRINT_MAX)
                printf("        first shifted/mismatching beat idx=%d got=0x%08x want=0x%08x\n",
                       first_mism, r_capture[first_mism], oracle(base + (uint32_t)first_mism));

            if (lossy) {
                n_clean = false;
                if (N < min_lossy_N) min_lossy_N = N;
                if (N <= 6)
                    fail_note(c_read, "backpressure N=%d scanP=%d lost/corrupted beats "
                              "(beats=%d mism=%d lost=%d) — inside design tolerance",
                              N, scanP, beats, mism, lost);
            }

            // cleanup: drain any residual beats, reset tracker
            capture_r = true;
            int idle = 0;
            guard = 3000;
            while (idle < 60 && guard-- > 0) {
                step();
                if (tb->s_axi_rvalid) idle = 0; else idle++;
            }
            rq.clear(); r_beat = 0;
            capture_r = false;
            rready_low_cycles = 0;
        }
        if (n_clean && max_clean_N == N - 1) max_clean_N = N;
    }
    printf("  EMPIRICAL THRESHOLD: stalls up to N=%d cycles are loss-free; first loss at N=%d\n",
           max_clean_N, (min_lossy_N == 999) ? -1 : min_lossy_N);
    end_phase();
}

// ============================================================
// Phase e: REFRESH-INTERACTION SOAK
// ============================================================
static void phase_e() {
    begin_phase("e: refresh soak (AUTOREF every ~736 cycles vs reads+scanout)");
    unsigned e_errs0 = (unsigned)tb->dbg_model_errors;
    stream_word = 0;
    const uint32_t slens[4] = {80, 4, 160, 1};
    int sli = 0, cooldown = 0, k = 0;
    uint32_t sbase = SCAN_REGION;
    for (long i = 0; i < 250000; i++) {
        stream_refill();
        if (!scan_active && !scan_fire) {
            if (cooldown > 0) cooldown--;
            else {
                uint32_t len = slens[sli % 4];
                uint32_t base = (len == 160) ? 0x021FC0 : (SCAN_REGION + (sbase % 0x6000));
                fire_scan(base, len);
                sli++;
                sbase += 0x137;
                cooldown = (k++ * 13) % 37 + 1;   // co-prime-ish with the 736-cycle AUTOREF
            }
        }
        step();
    }
    drain_reads(50000);
    unsigned e_errs = (unsigned)tb->dbg_model_errors - e_errs0;
    printf("  model protocol/refresh errors during soak: %u (cumulative %u)\n",
           e_errs, (unsigned)tb->dbg_model_errors);
    if (e_errs != 0)
        fail_note(c_proto, "refresh soak added %u model protocol/refresh errors", e_errs);
    end_phase();
}

// ============================================================
int main(int argc, char **argv) {
    Verilated::commandArgs(argc, argv);
    tb = new Vtb_sdram_rdscan;

    printf("==============================================================\n");
    printf(" MiSTer SDRAM READ-path vs scanout phase hammer (boot lottery)\n");
    printf(" axi_sdram_slave + pulse adapter + io_sdram(mister) + model\n");
    printf("==============================================================\n");

    boot_and_preload();

    if (c_proto.fail == 0) {
        phase_a();
        phase_b();
        phase_c();
        phase_d();
        phase_e();
    }

    unsigned model_errs = (unsigned)tb->dbg_model_errors;
    printf("\n==============================================================\n");
    printf("read beats checked : %ld pass / %ld fail\n", c_read.pass, c_read.fail);
    printf("scanout beats      : %ld pass / %ld fail\n", c_scan.pass, c_scan.fail);
    printf("write verifies     : %ld pass / %ld fail\n", c_write.pass, c_write.fail);
    printf("protocol/hang fails: %ld\n", c_proto.fail);
    printf("model error counter: %u\n", model_errs);
    printf("total simulated cycles: %llu\n", (unsigned long long)cyc());
    long total = g_fail_total + (long)model_errs;
    printf("=== Results: %ld passed, %ld failed ===\n",
           c_read.pass + c_scan.pass + c_write.pass, total);
    printf(total == 0 ? "RESULT: PASS\n" : "RESULT: FAIL\n");

    delete tb;
    return total ? 1 : 0;
}
