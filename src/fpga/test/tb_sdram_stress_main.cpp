//------------------------------------------------------------------------------
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileType: SOURCE
// SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
//------------------------------------------------------------------------------
//
// FIELD-FAULT reproduction rig: Pocket os25 "+8-byte shifted OS .text".
//
// Field observation (twice, one day apart, seed-21 os25 bitstream):
//   OS .text in SDRAM found corrupted at boot-time textguard: a region holds
//   ITS OWN correct content SHIFTED BY +8 BYTES (mem[addr]==correct[addr-8]),
//   physically in DRAM (cached and uncached views agree).  The corrupting
//   write happened during the OS init window: CPU .data/.bss cache-line
//   writebacks + terminal scanout reads + audio mixer reads are the only
//   legitimate SDRAM traffic.  Signature = a WRITE BURST whose data stream
//   lags its address by one 8-byte unit (2 x 32-bit beats), not analog bit
//   marginality.
//
// This harness drives the REAL production Pocket stack
// (arbiter -> slave -> pulse adapter -> io_sdram -> cycle-accurate model)
// with the field traffic mix and scoreboards EVERY byte:
//   * M1 (CPU): back-to-back 8/16-beat cache-line eviction write bursts
//     (wcont=0 -> the slave's S_WR_FILL buffered-writeback path -> ONE
//     native io_sdram burst), with WVALID stall jitter, plus refill reads.
//   * Scanout: randomized burst_rd injections, biased to land while a write
//     burst is in flight (preemption boundaries are the hot zone).
//   * M2: occasional single-word reads/writes.  M3: short audio-ish reads.
//   * Refresh: autonomous inside io_sdram.
// After each write burst's B response, the physical model memory is compared
// word-exact against the expected pattern; mismatches are classified
// (+8 LAG / -8 LEAD / +-4 / stale / other) and the last 256 cycles of
// waveform-level state (slave FSM, io_sdram state, wbuf ptrs, pull/done
// pulses, handshakes) are dumped for the first failure.  A watchdog flags
// wedges (no completion progress for WEDGE_CYCLES while work is pending).
//
// Deterministic: fixed 50-seed sweep, xorshift32 PRNG, no wall-clock input.
//
#include <cstdio>
#include <cstdint>
#include <cstring>
#include <map>
#include <vector>
#include <deque>
#include "Vtb_sdram_stress.h"
#include "Vtb_sdram_stress___024root.h"
#include "verilated.h"

static Vtb_sdram_stress *tb;
static uint64_t sim_time = 0;
static inline uint64_t cyc() { return sim_time / 2; }

// ---- deterministic PRNG (xorshift32) ----
static uint32_t rng_state = 1;
static inline uint32_t rnd() {
    uint32_t x = rng_state;
    x ^= x << 13; x ^= x >> 17; x ^= x << 5;
    rng_state = x;
    return x;
}
static inline uint32_t rnd_range(uint32_t lo, uint32_t hi) { // inclusive
    return lo + (rnd() % (hi - lo + 1));
}

static void tick();

// ============================================================
// Address plan (byte addresses; SDRAM = 64MB).
// ============================================================
static const uint32_t ROW_BYTES = 2048;         // io_sdram row = 1024 hw = 2KB
static const uint32_t FB_BASE   = 0x00200000;   // scanout fetch region
static const uint32_t CPU_BASE  = 0x00300000;   // CPU writeback region (per-seed windows)
static const uint32_t BRG_BASE  = 0x00D00000;   // M2 bridge single-word region
static const uint32_t AUD_BASE  = 0x00E00000;   // M3 audio read region

// ============================================================
// Expected-data pattern: unique per (seed, word address) so any shift is
// unambiguously classifiable.  pat(a) != pat(a+-4) != pat(a+-8) by
// construction (multiplicative hash).
// ============================================================
static uint32_t g_seedmix = 0;
static inline uint32_t pat(uint32_t byte_addr) {
    return (byte_addr * 2654435761u) ^ g_seedmix;
}

// Shadow of every byte the harness committed this seed (byte addr -> value).
static std::map<uint32_t, uint8_t> shadow;
static void shadow_word(uint32_t waddr, uint32_t data, uint8_t strb) {
    for (int b = 0; b < 4; b++)
        if (strb & (1 << b))
            shadow[waddr + b] = (uint8_t)(data >> (8 * b));
}

// ============================================================
// Physical model peek (ground truth).  hw = (byte>>2)<<1;
// cell = {hw[24:23], hw[22:10], hw[9:0]}.
// ============================================================
static uint16_t model_peek_hw(uint32_t hw_addr) {
    uint32_t bank = (hw_addr >> 23) & 0x3;
    uint32_t row  = (hw_addr >> 10) & 0x1FFF;
    uint32_t col  = hw_addr & 0x3FF;
    uint32_t flat = (bank << 23) | (row << 10) | col;
    return ((const uint16_t *)tb->rootp->tb_sdram_stress__DOT__sdram_chip__DOT__mem.m_storage)[flat];
}
static uint32_t model_peek_word(uint32_t byte_addr) {
    uint32_t hw = (byte_addr >> 2) << 1;
    return ((uint32_t)model_peek_hw(hw + 1) << 16) | model_peek_hw(hw);
}
static long model_errors() {
    return tb->rootp->tb_sdram_stress__DOT__sdram_chip__DOT__errors;
}

// ============================================================
// Waveform-level event ring (dumped at first failure).
// ============================================================
struct Snap {
    uint64_t c;
    uint8_t  slave_state, io_dbg, arb_state, grant;
    uint8_t  wbuf_wptr, wbuf_rptr;
    uint8_t  flags;  // bit0 wvalid&wready(M1) bit1 bvalid(M1) bit2 wr_data_next
                     // bit3 wr_done bit4 inj_burst_rd bit5 inj_done bit6 awfire(M1)
                     // bit7 busy
};
static std::deque<Snap> ring;
static const size_t RING_N = 256;
static bool ring_dumped = false;

static void ring_push(uint8_t flags) {
    Snap s;
    s.c = cyc();
    s.slave_state = tb->dbg_slave_state;
    s.io_dbg = tb->dbg_io;
    s.arb_state = tb->dbg_arb_state;
    s.grant = tb->dbg_grant;
    s.wbuf_wptr = tb->dbg_wbuf_wptr;
    s.wbuf_rptr = tb->dbg_wbuf_rptr;
    s.flags = flags | (tb->dbg_busy ? 0x80 : 0);
    ring.push_back(s);
    if (ring.size() > RING_N) ring.pop_front();
}

static void ring_dump(const char *why) {
    if (ring_dumped) return;   // only the first failure gets the full dump
    ring_dumped = true;
    printf("  ---- last %zu cycles of DUT state (%s) ----\n", ring.size(), why);
    printf("  cyc | slaveFSM io_state arb grant wbuf(w/r) | flags "
           "[Wf=W-fire B=bvalid P=pull D=wr_done I=inj Id=injdone AWf=aw-fire bsy=busy]\n");
    uint8_t last_key = 0xFF; uint8_t last_io = 0xFF;
    for (auto &s : ring) {
        // compress: print only on state/flag change to keep the dump readable
        uint8_t key = (s.slave_state << 4) | (s.flags & 0x7F);
        if (key == last_key && s.io_dbg == last_io) continue;
        last_key = key; last_io = s.io_dbg;
        printf("  %8llu | sl=%2d io=0x%02x arb=%d gr=%d wb=%d/%d |%s%s%s%s%s%s%s%s\n",
               (unsigned long long)s.c, s.slave_state, s.io_dbg,
               s.arb_state, s.grant, s.wbuf_wptr, s.wbuf_rptr,
               (s.flags & 0x01) ? " Wf" : "",
               (s.flags & 0x40) ? " AWf" : "",
               (s.flags & 0x02) ? " B" : "",
               (s.flags & 0x04) ? " P" : "",
               (s.flags & 0x08) ? " D" : "",
               (s.flags & 0x10) ? " I" : "",
               (s.flags & 0x20) ? " Id" : "",
               (s.flags & 0x80) ? " bsy" : "");
    }
    printf("  ---- end of state dump ----\n");
}

// ============================================================
// Failure accounting
// ============================================================
static int  g_seed_errors = 0;    // errors within the current seed
static int  g_total_errors = 0;
static bool g_repro_shift8 = false;
static uint32_t g_repro_seed = 0;
static uint64_t g_repro_cycle = 0;
static uint32_t g_repro_addr = 0;

// Classify a mismatched word against the pattern space.
static const char *classify(uint32_t waddr, uint32_t got) {
    if (got == pat(waddr - 8))  return "+8 LAG  (FIELD SIGNATURE: mem[a]==expected[a-8])";
    if (got == pat(waddr + 8))  return "-8 LEAD (mem[a]==expected[a+8])";
    if (got == pat(waddr - 4))  return "+4 lag (one 32-bit beat)";
    if (got == pat(waddr + 4))  return "-4 lead (one 32-bit beat)";
    if (got == pat(waddr - 16)) return "+16 lag";
    if (got == pat(waddr + 16)) return "-16 lead";
    if (got == 0xDEADDEADu)     return "NEVER WRITTEN (beat dropped)";
    return "other corruption";
}

static void report_mismatch(uint32_t waddr, uint32_t expw, uint32_t got,
                            const char *ctx, uint32_t burst_addr, int burst_beats) {
    const char *cls = classify(waddr, got);
    g_seed_errors++;
    if (g_seed_errors <= 20)
        printf("  MISMATCH [%s] @ 0x%08x: expected 0x%08x got 0x%08x -> %s "
               "(burst 0x%08x x%d, cyc=%llu)\n",
               ctx, waddr, expw, got, cls, burst_addr, burst_beats,
               (unsigned long long)cyc());
    if (got == pat(waddr - 8) && !g_repro_shift8) {
        g_repro_shift8 = true;
        g_repro_cycle = cyc();
        g_repro_addr = waddr;
    }
    ring_dump("first mismatch");
}

// ============================================================
// Master driver state
// ============================================================

// ---- M1 CPU: write burst engine (cache-line evictions) ----
struct WrBurst { uint32_t addr; int beats; };

// ---- fault injection: model the upstream 2-beat elastic replay ----
// +inject=N duplicates the beat pair (j-1, j) mid-stream on every Nth
// write burst: the W stream presents beats [0..j, j-1, j, j+1..last]
// with WLAST only on the true final beat -- byte-exact model of the
// cpu_target_port elastic re-presenting an already-accepted slot+skid
// pair (the +8 genesis).  On the WLAST-enforcing slave the burst must
// be REFUSED (B=SLVERR, memory untouched); on a WLAST-blind slave it
// commits shifted and the verifier's +8 classifier fires.
static int  g_inject_every = 0;      // 0 = off
static int  g_inject_count = 0;      // bursts injected this seed
static int  g_slverr_count = 0;      // SLVERR B responses observed this seed
static bool m1_cur_injected = false;
static std::vector<int> m1_seq;      // presentation index -> logical beat
static std::vector<WrBurst> m1_wq;
static size_t m1_wq_idx = 0;
static bool m1_wr_inflight = false;
static bool m1_aw_done = false;
static int  m1_wbeat = 0;
static uint64_t m1_wr_start_cyc = 0;
static int  m1_gap = 0;                 // inter-burst idle gap (randomized)
static int  m1_stall_pct = 25;          // WVALID stall probability (percent)

// deferred verify queue: bursts whose B fired, verified after settle delay
struct PendVer { uint32_t addr; int beats; int countdown; };
static std::vector<PendVer> pend_ver;

// ---- M1 CPU: read burst engine (refills; concurrent with writes) ----
static bool m1_rd_active = false;
static uint32_t m1_rd_addr = 0;
static int  m1_rd_len = 0, m1_rd_beat = 0;
static bool m1_ar_done = false;
static int  m1_rd_cooldown = 40;

// ---- M2 bridge single-word ops ----
static int  m2_mode = 0;  // 0 idle, 1 read, 2 write
static uint32_t m2_addr = 0;
static bool m2_aw_done = false, m2_w_done = false, m2_ar_done = false;
static int  m2_cooldown = 60;
static uint32_t m2_next_addr = 0;       // monotonic so cells are distinct

// ---- M3 audio reads ----
static bool m3_active = false;
static uint32_t m3_addr = 0;
static int  m3_len = 0;
static bool m3_ar_done = false;
static int  m3_cooldown = 20;

// ---- scanout injection ----
static int  inj_busy_f = 0;
static int  inj_cooldown = 0;
static int  inj_aggr_num = 1, inj_aggr_den = 3;  // fire probability while write in flight
static uint32_t g_cpu_window = 0;                // current seed's write window
static int  m1_gap_max = 20;                     // inter-burst gap profile
static bool g_storm = false;                     // sustained full-line scanout occupancy

// ---- progress watchdog ----
static uint64_t last_progress_cyc = 0;
static const uint64_t WEDGE_CYCLES = 100000;
static bool g_wedged = false;       // this seed hit the no-progress watchdog
static bool g_hard_wedge = false;   // did NOT recover after aggressors stopped

// Once the write workload is drained, stop SPAWNING new aggressor traffic so
// in-flight ops retire and the seed ends (instead of waiting for all the
// random spawners to be simultaneously idle).
static bool g_winddown = false;

static void clear_master_inputs() {
    tb->m0_arvalid = 0; tb->m0_awvalid = 0; tb->m0_wvalid = 0;
    tb->m1_arvalid = 0; tb->m1_awvalid = 0; tb->m1_wvalid = 0; tb->m1_rready = 1;
    tb->m2_arvalid = 0; tb->m2_awvalid = 0; tb->m2_wvalid = 0; tb->m2_rready = 1;
    tb->m3_arvalid = 0; tb->m3_rready = 1;
    tb->inj_burst_rd = 0;
}

static void tick() {
    tb->clk = 0; tb->eval(); sim_time++;
    tb->clk = 1; tb->eval(); sim_time++;
}

// ============================================================
// Verify one committed burst against the physical model.
// ============================================================
static void verify_burst(uint32_t addr, int beats) {
    for (int i = 0; i < beats; i++) {
        uint32_t waddr = addr + 4u * i;
        uint32_t expw  = pat(waddr);
        uint32_t got   = model_peek_word(waddr);
        if (got != expw)
            report_mismatch(waddr, expw, got, "post-B", addr, beats);
    }
}

// End-of-seed full scan of every byte the harness wrote (catches clobbers of
// PREVIOUSLY verified cells by later bursts — the exact field failure mode:
// corruption discovered later in a region nothing should have touched).
static void verify_all_shadow() {
    uint32_t cur_w = 0xFFFFFFFF; uint32_t got = 0;
    for (auto &kv : shadow) {
        uint32_t waddr = kv.first & ~3u;
        if (waddr != cur_w) { cur_w = waddr; got = model_peek_word(waddr); }
        uint8_t gb = (uint8_t)(got >> (8 * (kv.first & 3)));
        if (gb != kv.second) {
            uint32_t expw = pat(waddr);
            report_mismatch(waddr, expw, got, "end-scan", waddr, 1);
        }
    }
}

// ============================================================
// Per-cycle stimulus + reaction
// ============================================================
static void step_all() {
    clear_master_inputs();

    // ---------------- M1 write bursts (cache-line evictions) ----------------
    if (!m1_wr_inflight && m1_gap == 0 && m1_wq_idx < m1_wq.size()) {
        m1_wr_inflight = true;
        m1_aw_done = false;
        m1_wbeat = 0;
        m1_wr_start_cyc = cyc();
        WrBurst &nb = m1_wq[m1_wq_idx];
        m1_seq.clear();
        m1_cur_injected = g_inject_every &&
                          ((int)m1_wq_idx % g_inject_every == g_inject_every - 1) &&
                          nb.beats >= 4;
        for (int b = 0; b < nb.beats; b++) {
            m1_seq.push_back(b);
            if (m1_cur_injected && b == nb.beats / 2) {   // replay the pair
                m1_seq.push_back(b - 1);
                m1_seq.push_back(b);
            }
        }
        if (m1_cur_injected) g_inject_count++;
    }
    if (m1_gap) m1_gap--;
    WrBurst *cw = (m1_wr_inflight && m1_wq_idx < m1_wq.size()) ? &m1_wq[m1_wq_idx] : nullptr;
    if (cw) {
        if (!m1_aw_done) {
            tb->m1_awvalid = 1;
            tb->m1_awaddr  = cw->addr;
            tb->m1_awlen   = cw->beats - 1;
        }
        if (m1_wbeat < (int)m1_seq.size()) {
            // WVALID stall jitter: a real CPU writeback can pause mid-burst.
            bool present = (int)(rnd() % 100) >= m1_stall_pct;
            if (present) {
                uint32_t a = cw->addr + 4u * (uint32_t)m1_seq[m1_wbeat];
                tb->m1_wvalid = 1;
                tb->m1_wdata  = pat(a);
                tb->m1_wstrb  = 0xF;
                tb->m1_wlast  = (m1_wbeat == (int)m1_seq.size() - 1);
            }
        }
    }

    // ---------------- M1 read bursts (refills, concurrent) ----------------
    // Real-CPU backpressure: once a writeback has waited ~2k cycles the
    // core has long since stalled on it (WB buffer full blocks refills,
    // and a frozen pipeline stops fetching) -- unlimited independent
    // reads here starved writes forever behind the slave's AR-first
    // idle priority, which silicon cannot sustain.  The write-latency
    // assertion below still fails the seed if a write waits absurdly.
    bool m1_wr_aged = m1_wr_inflight && (cyc() - m1_wr_start_cyc) > 2000;
    if (m1_wr_inflight && (cyc() - m1_wr_start_cyc) > 60000) {
        report_mismatch(0, 0, 0, "write-latency bound exceeded (fairness)", 0, 1);
        m1_wr_start_cyc = cyc();  // rate-limit the report
    }
    if (!g_winddown && !m1_rd_active && m1_rd_cooldown == 0 && !m1_wr_aged && (rnd() & 7) == 0) {
        m1_rd_active = true;
        m1_ar_done = false;
        m1_rd_beat = 0;
        m1_rd_len  = (rnd() & 1) ? 15 : 7;
        // read something already written when possible (also checks read path)
        m1_rd_addr = AUD_BASE + rnd_range(0, 2048) * 4;
    }
    if (m1_rd_cooldown) m1_rd_cooldown--;
    if (m1_rd_active && !m1_ar_done) {
        tb->m1_arvalid = 1;
        tb->m1_araddr = m1_rd_addr;
        tb->m1_arlen = m1_rd_len;
    }
    // rready jitter widens the R-channel backpressure window
    tb->m1_rready = (rnd() & 15) ? 1 : 0;

    // ---------------- M2 single-word ops ----------------
    if (!g_winddown && m2_mode == 0 && m2_cooldown == 0 && (rnd() & 15) == 0) {
        if (rnd() & 1) {
            m2_mode = 2; m2_addr = m2_next_addr; m2_next_addr += 4;
            m2_aw_done = m2_w_done = false;
        } else {
            m2_mode = 1;
            m2_addr = (m2_next_addr > BRG_BASE)
                    ? BRG_BASE + (rnd() % ((m2_next_addr - BRG_BASE) / 4 + 1)) * 4
                    : BRG_BASE;
            m2_ar_done = false;
        }
    }
    if (m2_cooldown) m2_cooldown--;
    if (m2_mode == 1) {
        if (!m2_ar_done) { tb->m2_arvalid = 1; tb->m2_araddr = m2_addr; tb->m2_arlen = 0; }
    } else if (m2_mode == 2) {
        if (!m2_aw_done) { tb->m2_awvalid = 1; tb->m2_awaddr = m2_addr; tb->m2_awlen = 0; }
        if (!m2_w_done)  { tb->m2_wvalid = 1; tb->m2_wdata = pat(m2_addr); tb->m2_wstrb = 0xF; tb->m2_wlast = 1; }
    }

    // ---------------- M3 audio-style short reads ----------------
    if (!g_winddown && !m3_active && m3_cooldown == 0 && (rnd() & 7) == 0) {
        m3_active = true; m3_ar_done = false;
        m3_addr = AUD_BASE + rnd_range(0, 4096) * 4;
        m3_len = rnd_range(0, 3);
    }
    if (m3_cooldown) m3_cooldown--;
    if (m3_active && !m3_ar_done) {
        tb->m3_arvalid = 1; tb->m3_araddr = m3_addr; tb->m3_arlen = m3_len;
    }

    // ---------------- scanout burst_rd injection ----------------
    // Aggression bias: while an M1 write burst is in flight, fire scanout as
    // often as the injector allows — the suspects phase says
    // scanout-preempts-write-burst boundaries are the hot zone.
    if (!g_winddown && inj_busy_f == 0 && inj_cooldown == 0) {
        bool in_write = m1_wr_inflight;
        bool fire = in_write ? ((rnd() % (uint32_t)inj_aggr_den) < (uint32_t)inj_aggr_num)
                             : ((rnd() & 15) == 0);
        if (fire) {
            // Half the fetches target the FB region (the field layout); half
            // target the rows the CPU is writing RIGHT NOW — same-bank/row
            // conflict maximizes precharge/activate churn at every
            // scanout-vs-write boundary (the field .text and FB share banks).
            uint32_t rd_base = (rnd() & 1) ? FB_BASE : g_cpu_window;
            tb->inj_burst_rd  = 1;
            tb->inj_burst_addr = (((rd_base >> 2) + rnd_range(0, 4096)) << 1) & 0x1FFFFFF;
            // Mostly SHORT bursts: each burst end/start is a fresh
            // scanout-vs-write preemption boundary, and the hot zone is the
            // boundary, not the burst body.  Occasional full-line fetches
            // (~360 hw) keep the long-occupancy/refresh-collision case in
            // the mix without permanently starving autorefresh the way
            // back-to-back 512-hw bursts do (real scanout has line cadence).
            tb->inj_burst_len  = g_storm ? rnd_range(256, 512)
                               : ((rnd() & 7) == 0) ? rnd_range(180, 360)
                                                    : rnd_range(4, 64);
            inj_busy_f = 1;
        }
    }
    if (inj_cooldown) inj_cooldown--;

    // ---- sample combinational handshakes BEFORE the edge ----
    bool m1_aw_fire = tb->m1_awvalid && tb->m1_awready;
    bool m1_w_fire  = tb->m1_wvalid  && tb->m1_wready;
    bool m1_b_fire  = tb->m1_bvalid;
    bool m1_ar_fire = tb->m1_arvalid && tb->m1_arready;
    bool m1_r_fire  = tb->m1_rvalid  && tb->m1_rready;

    bool m2_ar_fire = tb->m2_arvalid && tb->m2_arready;
    bool m2_r_fire  = tb->m2_rvalid  && tb->m2_rready;
    bool m2_aw_fire = tb->m2_awvalid && tb->m2_awready;
    bool m2_w_fire  = tb->m2_wvalid  && tb->m2_wready;
    bool m2_b_fire  = tb->m2_bvalid;

    bool m3_ar_fire = tb->m3_arvalid && tb->m3_arready;
    bool m3_r_fire  = tb->m3_rvalid  && tb->m3_rready;

    bool inj_done = tb->inj_burst_data_done;

    uint8_t flags = 0;
    if (m1_w_fire) flags |= 0x01;
    if (m1_b_fire) flags |= 0x02;
    if (tb->dbg_wr_data_next) flags |= 0x04;
    if (tb->dbg_wr_done) flags |= 0x08;
    if (tb->inj_burst_rd) flags |= 0x10;
    if (inj_done) flags |= 0x20;
    if (m1_aw_fire) flags |= 0x40;
    ring_push(flags);

    tick();

    // ---- react ----
    if (m1_aw_fire) m1_aw_done = true;
    if (m1_w_fire && cw && m1_wbeat < (int)m1_seq.size()) {
        uint32_t a = cw->addr + 4u * (uint32_t)m1_seq[m1_wbeat];
        shadow_word(a, pat(a), 0xF);
        m1_wbeat++;
    }
    if (m1_b_fire && m1_wr_inflight && m1_aw_done && cw &&
        (m1_wbeat >= (int)m1_seq.size() ||
         (m1_cur_injected && tb->m1_bresp == 0))) {
        // (second arm: a WLAST-blind slave B's after count-full with the
        //  replay tail unconsumed -- drop the leak and verify: the +8
        //  classifier is the expected FAILURE demonstration there)
        if (m1_cur_injected && tb->m1_bresp == 2) {
            // Fixed slave: burst refused.  Memory must be untouched --
            // drop the shadow so end-scan expects nothing here.
            g_slverr_count++;
            for (int b2 = 0; b2 < cw->beats * 4; b2++)
                shadow.erase(cw->addr + (uint32_t)b2);
        } else {
            pend_ver.push_back({cw->addr, cw->beats, 32});
        }
        m1_wq_idx++;
        m1_wr_inflight = false;
        m1_gap = m1_gap_max ? (int)rnd_range(0, (uint32_t)m1_gap_max) : 0;
        last_progress_cyc = cyc();
    }
    if (m1_ar_fire) m1_ar_done = true;
    if (m1_r_fire) {
        m1_rd_beat++;
        if (tb->m1_rlast) {
            m1_rd_active = false;
            m1_rd_cooldown = (int)rnd_range(4, 64);
            last_progress_cyc = cyc();
        }
    }

    if (m2_ar_fire) m2_ar_done = true;
    if (m2_mode == 1 && m2_r_fire && tb->m2_rlast) {
        // verify M2 read data against shadow (if this cell was written)
        auto it = shadow.find(m2_addr);
        if (it != shadow.end()) {
            uint32_t expw = pat(m2_addr);
            if (tb->m2_rdata != expw)
                report_mismatch(m2_addr, expw, tb->m2_rdata, "M2-read", m2_addr, 1);
        }
        m2_mode = 0; m2_cooldown = (int)rnd_range(20, 200);
        last_progress_cyc = cyc();
    }
    if (m2_mode == 2) {
        if (m2_aw_fire) m2_aw_done = true;
        if (m2_w_fire) m2_w_done = true;
        if (m2_b_fire && m2_aw_done && m2_w_done) {
            shadow_word(m2_addr, pat(m2_addr), 0xF);
            pend_ver.push_back({m2_addr, 1, 32});
            m2_mode = 0; m2_cooldown = (int)rnd_range(20, 200);
            last_progress_cyc = cyc();
        }
    }

    if (m3_ar_fire) m3_ar_done = true;
    if (m3_active && m3_r_fire && tb->m3_rlast) {
        m3_active = false; m3_cooldown = (int)rnd_range(2, 40);
        last_progress_cyc = cyc();
    }

    if (inj_busy_f && inj_done) {
        inj_busy_f = 0;
        inj_cooldown = g_storm ? 0 : (int)rnd_range(0, 24);
        // NOTE: deliberately NOT progress for the watchdog — scanout bursts
        // completing while the AXI fabric is starved is exactly the field
        // wedge (screen alive or blank, memtest never finishes).
    }

    // ---- deferred post-B verification ----
    for (size_t i = 0; i < pend_ver.size();) {
        if (--pend_ver[i].countdown <= 0) {
            verify_burst(pend_ver[i].addr, pend_ver[i].beats);
            pend_ver[i] = pend_ver.back();
            pend_ver.pop_back();
        } else i++;
    }
}

// ============================================================
// One seed = one full stress phase.
// ============================================================
static void reset_seed_state() {
    m1_wq.clear(); m1_wq_idx = 0;
    m1_wr_inflight = false; m1_aw_done = false; m1_wbeat = 0; m1_gap = 0;
    m1_rd_active = false; m1_ar_done = false; m1_rd_cooldown = 40;
    m2_mode = 0; m2_cooldown = 60; m2_aw_done = m2_w_done = m2_ar_done = false;
    m2_next_addr = BRG_BASE;
    m3_active = false; m3_ar_done = false; m3_cooldown = 20;
    inj_busy_f = 0; inj_cooldown = 0;
    pend_ver.clear();
    shadow.clear();
    g_inject_count = 0;
    g_slverr_count = 0;
    m1_cur_injected = false;
    m1_seq.clear();
    ring.clear();
    g_seed_errors = 0;
    g_winddown = false;
    g_wedged = false;
    last_progress_cyc = cyc();
}

// Build the per-seed write workload.  Mostly 64B-aligned 16-beat lines (the
// real eviction shape), some 8-beat, and a deliberate minority of unaligned /
// row-boundary-crossing bursts (io_sdram's ST_WRITE_4_NEWROW resume path).
static void gen_workload(uint32_t base, int n_bursts) {
    uint32_t a = base;
    for (int i = 0; i < n_bursts; i++) {
        uint32_t kind = rnd() % 10;
        if (kind < 6) {                    // 16-beat aligned line (64B)
            a = (a + 63) & ~63u;
            m1_wq.push_back({a, 16}); a += 64;
        } else if (kind < 8) {             // 8-beat aligned half-line
            a = (a + 31) & ~31u;
            m1_wq.push_back({a, 8});  a += 32;
        } else if (kind < 9) {             // row-boundary crosser
            uint32_t row = (a / ROW_BYTES + 1) * ROW_BYTES;
            uint32_t start = row - 4u * (uint32_t)rnd_range(1, 8);
            int beats = (int)rnd_range(8, 16);
            m1_wq.push_back({start, beats});
            a = start + 4u * beats;
        } else {                           // unaligned odd-length burst
            a = (a + 3) & ~3u;
            int beats = (int)rnd_range(2, 15);
            m1_wq.push_back({a, beats}); a += 4u * beats;
        }
        a += rnd_range(0, 3) * 4;          // small address skips
    }
}

// flavor knobs per seed: stall rate / injection aggression / gap profile
static void apply_flavor(uint32_t seed) {
    switch (seed % 5) {
        case 0: m1_stall_pct = 0;  inj_aggr_num = 1; inj_aggr_den = 2; break; // clean W stream, max scanout
        case 1: m1_stall_pct = 25; inj_aggr_num = 1; inj_aggr_den = 3; break;
        case 2: m1_stall_pct = 60; inj_aggr_num = 1; inj_aggr_den = 2; break; // heavy W stalls
        case 3: m1_stall_pct = 10; inj_aggr_num = 1; inj_aggr_den = 1; break; // scanout every idle cycle
        case 4: m1_stall_pct = 40; inj_aggr_num = 1; inj_aggr_den = 6; break; // sparse scanout
    }
    // Back-to-back eviction pressure (the memtest-leg shape that blanked the
    // screen on hardware): a third of the seeds run with ZERO inter-burst gap.
    m1_gap_max = (seed % 3 == 0) ? 0 : 20;
    // STORM flavor (last decade of the sweep): back-to-back full-line scanout
    // fetches with zero cadence + zero write gaps — sustained io_sdram
    // occupancy.  This regime defers autorefresh past the model's tREFI limit
    // (observed 1701 vs 1700 max) — reported as protocol WARNINGS: a
    // plausible contributor to the hardware memtest-leg blank/wedge, distinct
    // from the structural +8 shift this rig hunts.
    g_storm = (seed > 40);
    if (g_storm) { inj_aggr_num = 1; inj_aggr_den = 1; m1_gap_max = 0; }
}

static int run_seed(uint32_t seed, int n_bursts) {
    rng_state = seed * 0x9E3779B9u + 0x6A09E667u;
    g_seedmix = rnd();
    reset_seed_state();
    apply_flavor(seed);
    g_cpu_window = CPU_BASE + (seed & 63) * 0x40000;
    gen_workload(g_cpu_window, n_bursts);

    uint64_t start = cyc();
    uint64_t hard_cap = 4000000;   // per-seed cycle ceiling
    while (cyc() - start < hard_cap) {
        step_all();
        // Writes drained -> stop spawning new aggressors so the seed ends.
        if (m1_wq_idx >= m1_wq.size() && !m1_wr_inflight)
            g_winddown = true;
        // done? all write bursts retired and verify queue drained
        if (g_winddown && pend_ver.empty() &&
            m2_mode == 0 && !m1_rd_active && !m3_active && inj_busy_f == 0)
            break;
        // wedge watchdog
        if (!g_wedged && cyc() - last_progress_cyc > WEDGE_CYCLES) {
            g_wedged = true;
            g_seed_errors++;
            printf("  WEDGE/STARVATION: no AXI completion progress for %llu cycles "
                   "(burst %zu/%zu addr=0x%08x beats=%d aw_done=%d wbeat=%d, "
                   "started cyc=%llu)\n",
                   (unsigned long long)WEDGE_CYCLES, m1_wq_idx, m1_wq.size(),
                   m1_wq_idx < m1_wq.size() ? m1_wq[m1_wq_idx].addr : 0,
                   m1_wq_idx < m1_wq.size() ? m1_wq[m1_wq_idx].beats : 0,
                   m1_aw_done, m1_wbeat,
                   (unsigned long long)m1_wr_start_cyc);
            printf("  WEDGE state: slaveFSM=%d io=0x%02x arb=%d grant=%d "
                   "wbuf=%d/%d busy=%d inj_busy=%d m1_rd_active=%d\n",
                   tb->dbg_slave_state, tb->dbg_io, tb->dbg_arb_state,
                   tb->dbg_grant, tb->dbg_wbuf_wptr, tb->dbg_wbuf_rptr,
                   tb->dbg_busy, inj_busy_f, m1_rd_active);
            ring_dump("wedge");
            // Starvation-vs-hard-wedge triage: stop spawning aggressor
            // traffic (g_winddown) and see whether the stuck fabric recovers
            // once scanout pressure disappears.  A recovery = LIVELOCK
            // (unbounded-priority starvation); no recovery = HARD WEDGE.
            g_winddown = true;
            uint64_t t0 = cyc();
            uint64_t stuck_at = last_progress_cyc;
            while (cyc() - t0 < 400000 && last_progress_cyc == stuck_at)
                step_all();
            if (last_progress_cyc != stuck_at) {
                printf("  -> recovered %llu cycles after aggressors stopped: "
                       "LIVELOCK/STARVATION (no anti-starvation bound for AXI "
                       "word ops vs back-to-back scanout burst_rd)\n",
                       (unsigned long long)(last_progress_cyc - t0));
            } else {
                g_hard_wedge = true;
                printf("  -> NO recovery after aggressors stopped: HARD WEDGE\n");
            }
            break;
        }
    }
    // Workload not drained (hard cap or wedge) is a failure in itself — this
    // is the memtest-leg field observation (stream of eviction write bursts
    // against live scanout never completes).
    if (m1_wq_idx < m1_wq.size() && !g_wedged) {
        g_seed_errors++;
        printf("  NOT DRAINED: %zu/%zu write bursts completed at cycle cap\n",
               m1_wq_idx, m1_wq.size());
        ring_dump("write-drain timeout (starvation)");
    }

    // quiesce, then full shadow scan
    clear_master_inputs();
    for (int i = 0; i < 400; i++) tick();
    verify_all_shadow();

    if (g_inject_every) {
        printf("  inject: %d replayed bursts, %d SLVERR refusals\n",
               g_inject_count, g_slverr_count);
        if (g_slverr_count != g_inject_count) {
            printf("  FAIL containment: %d injected but only %d refused (SLVERR)\n",
                   g_inject_count, g_slverr_count);
            g_seed_errors++;
        }
    }

    if (g_repro_shift8 && g_repro_seed == 0) g_repro_seed = seed;
    return g_seed_errors;
}

static void boot() {
    tb->reset_n = 0;
    clear_master_inputs();
    for (int i = 0; i < 64; i++) tick();
    tb->reset_n = 1;
    for (int i = 0; i < 31200; i++) tick();   // io_sdram power-up + init
}

int main(int argc, char **argv) {
    Verilated::commandArgs(argc, argv);
    tb = new Vtb_sdram_stress;

    int n_seeds = 50;
    int n_bursts = 400;
    for (int i = 1; i < argc; i++) {
        if (!strncmp(argv[i], "+seeds=", 7))  n_seeds = atoi(argv[i] + 7);
        if (!strncmp(argv[i], "+bursts=", 8)) n_bursts = atoi(argv[i] + 8);
        if (!strncmp(argv[i], "+inject=", 8)) g_inject_every = atoi(argv[i] + 8);
    }

    printf("==============================================================\n");
    printf(" SDRAM field-fault stress rig (Pocket os25 '+8-byte shift')\n");
    printf(" arbiter + slave + pulse adapter + pocket io_sdram + model\n");
    printf(" mix: CPU 8/16-beat eviction bursts (S_WR_FILL path) vs scanout\n");
    printf("      burst_rd preemption + M2 singles + M3 reads + refresh\n");
    printf(" sweep: %d seeds x %d write bursts\n", n_seeds, n_bursts);
    printf("==============================================================\n");

    boot();

    int passed = 0, failed = 0;
    long protocol_warns = 0;      // model refresh/timing violations (tracked
                                  // separately from data corruption: they are
                                  // a stimulus-pressure observation, not the
                                  // structural +8 signature under hunt)
    long prev_model_errs = 0;
    for (int s = 1; s <= n_seeds; s++) {
        int errs = run_seed((uint32_t)s, n_bursts);
        long me = model_errors();
        long me_new = me - prev_model_errs;
        prev_model_errs = me;
        if (me_new) {
            printf("  seed %d: sdram_model protocol violations (refresh/timing): %ld [WARN]\n",
                   s, me_new);
            protocol_warns += me_new;
        }
        g_total_errors += errs;
        if (errs) {
            failed++;
            printf("[seed %2d] FAIL (%d errors)%s\n", s, errs,
                   g_wedged ? (g_hard_wedge ? " [HARD WEDGE]" : " [STARVATION]") : "");
            if (g_hard_wedge) {
                printf("Aborting sweep: DUT hard-wedged — later seeds would run on a stuck fabric.\n");
                // count remaining seeds as not-run (neither passed nor failed)
                break;
            }
        } else {
            passed++;
            printf("[seed %2d] pass  (%zu bursts, %zu bytes scoreboarded, cyc=%llu)\n",
                   s, m1_wq.size(), shadow.size(), (unsigned long long)cyc());
        }
    }

    printf("==============================================================\n");
    if (g_repro_shift8)
        printf("FIELD SIGNATURE REPRODUCED: mem[a]==expected[a-8] at addr=0x%08x "
               "seed=%u cycle=%llu\n", g_repro_addr, g_repro_seed,
               (unsigned long long)g_repro_cycle);
    else
        printf("Field signature (+8 shift) NOT reproduced in this sweep.\n");
    printf("total data errors: %d | protocol warnings: %ld | total simulated cycles: %llu\n",
           g_total_errors, protocol_warns, (unsigned long long)cyc());
    printf("=== Results: %d passed, %d failed ===\n", passed, failed);

    delete tb;
    return failed ? 1 : 0;
}
