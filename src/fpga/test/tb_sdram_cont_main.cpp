//------------------------------------------------------------------------------
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileType: SOURCE
// SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
//------------------------------------------------------------------------------
//
// Decisive MiSTer SDRAM WRITE-PATH multi-master contention experiment.
//
// Hypothesis under test: under MiSTer's heavy 4-master SDRAM contention, the
// GPU framebuffer-WRITE path (arbiter gpu_wq coalescer -> slave native-write
// handshake -> io_sdram row/refresh) drops or corrupts a write beat -> one FB
// word is never written -> a black column/span that moves with the view.
//
// Method:
//   * Drive M0 (GPU) with realistic FB-write traffic: single 2-beat native
//     writes, coalesced 3..8-beat contiguous full-strobe bursts, AND
//     partial-strobe single-word writes (the strobe forces the coalescer to
//     fall back to singles, the exact Doom/Quake sub-word FB case).  Interleave
//     M0 texture READS between write bursts.
//   * M1 (CPU) read+write, M2 (bridge) read+write, M3 (audio) read aggressors
//     run continuously to: fill gpu_wq to full (deassert m0_wready mid-burst),
//     force arbiter read<->write grant switches mid GPU write burst, and (via
//     long bursts spanning address ranges) push GPU bursts across SDRAM row
//     boundaries.
//   * Scanout burst_rd injection collides the hard-real-time video fetch with
//     GPU writes (the burst_defer_word path + io_sdram word_busy sharing).
//   * Refresh fires autonomously inside io_sdram (every 736 cycles); long runs
//     guarantee refresh collisions with in-flight GPU write bursts.
//
// Golden model: a byte-exact C++ shadow of every byte M0 commits (honoring
// strobes).  After the contention storm, every written address is read back
// THROUGH the slave (M1 read path) and compared.  ANY mismatch = a dropped or
// corrupted write beat = the bug.  The sdram_model also self-checks SDRAM
// protocol/refresh timing.
//
// Deterministic pseudo-randomness only (xorshift32 seeded by a constant); no
// real RNG, no Date.now/random.
//
#include <cstdio>
#include <cstdint>
#include <cstring>
#include <vector>
#include <map>
#include "Vtb_sdram_cont.h"
#include "Vtb_sdram_cont___024root.h"
#include "verilated.h"

static Vtb_sdram_cont *tb;
static uint64_t sim_time = 0;

// ---- deterministic PRNG (xorshift32) ----
static uint32_t rng_state = 0x1234abcdu;
static inline uint32_t rnd() {
    uint32_t x = rng_state;
    x ^= x << 13; x ^= x >> 17; x ^= x << 5;
    rng_state = x;
    return x;
}
static inline uint32_t rnd_range(uint32_t lo, uint32_t hi) { // inclusive
    return lo + (rnd() % (hi - lo + 1));
}

// ---- Golden shadow of M0 (GPU) framebuffer writes, byte-granular ----
// Key = byte address; value = byte.  Only bytes M0 actually committed (per
// strobe) are recorded.  Read-back compares against this.
static std::map<uint32_t, uint8_t> golden;

static int errors = 0;
static int g_first_fail_addr = -1;
// Once all M0 writes are drained, stop launching NEW reads/aggressors so the
// few in-flight ops retire promptly and the sim ends (instead of burning to
// the cycle ceiling under sustained aggressor load).  Does not affect the
// write-path coverage — the whole storm already ran.
static bool g_winddown = false;

static void tick() {
    tb->clk = 0; tb->eval(); sim_time++;
    tb->clk = 1; tb->eval(); sim_time++;
}

// ============================================================
// SDRAM address layout (must match io_sdram / sdram_model).
//   AXI byte addr -> word_addr = byte>>2.  io_sdram: hw = word<<1.
//   hw[24:23]=bank, hw[22:10]=row, hw[9:0]=col (but model uses col[8:0],
//   512 cols).  A row holds 512 halfwords = 256 32-bit words = 1KB.
// We pick FB write addresses that deliberately straddle row boundaries.
// ============================================================
// Controller SDRAM row = 1024 halfwords = 512 words = 2048 bytes (io_sdram
// uses a 10-bit column addr[9:0]).  Row-crossing test cases straddle this.
static const uint32_t ROW_BYTES = 2048;
static const uint32_t FB_BASE   = 0x00200000;     // arbitrary FB region
static const uint32_t TEX_BASE  = 0x00800000;     // GPU texture reads
static const uint32_t CPU_BASE  = 0x00C00000;     // CPU r/w aggressor region
static const uint32_t BRG_BASE  = 0x00D00000;     // bridge r/w aggressor region
static const uint32_t AUD_BASE  = 0x00E00000;     // audio read aggressor region

// ------------------------------------------------------------
// AXI master driver FSMs (free-running, per-cycle, fully overlapped).
// Each master pumps its channels every tick so the arbiter sees genuine
// concurrent multi-master traffic — not serialized one-at-a-time txns.
// ------------------------------------------------------------

// ---------- M0 GPU posted WRITE engine ----------
struct M0Write {
    uint32_t addr;            // byte addr of beat 0
    int      beats;           // total beats
    bool     full_strobe;     // all beats 0xF (coalesce) vs partial (singles)
    std::vector<uint32_t> data;
    std::vector<uint8_t>  strb;
};
static std::vector<M0Write> m0_wr_q;   // pending GPU writes to push
static size_t m0_wr_idx = 0;
// AW phase
static bool m0_inflight  = false;      // a burst is in flight (not yet retired)
static bool m0_aw_active = false;      // AW still needs to be accepted
static bool m0_w_active  = false;      // W beats still need to be sent
static bool m0_w_done    = false;      // all W beats accepted
static int  m0_beat = 0;
static int  m0_b_pending = 0;          // outstanding B responses (informational)

// ---------- M0 GPU READ engine (texture fetch) ----------
static bool m0_rd_active = false;
static uint32_t m0_rd_addr = 0;
static int m0_rd_len = 0;
static int m0_rd_beat = 0;
static int m0_rd_cooldown = 0;

// ---------- M1 CPU read+write aggressor ----------
static int m1_mode = 0;                // 0 idle, 1 read, 2 write
static uint32_t m1_addr = 0;
static int m1_len = 0, m1_beat = 0;
static bool m1_aw_done = false, m1_w_done = false;
static int m1_cooldown = 0;

// ---------- M2 bridge read+write aggressor ----------
static int m2_mode = 0;
static uint32_t m2_addr = 0;
static int m2_len = 0, m2_beat = 0;
static bool m2_aw_done = false, m2_w_done = false;
static int m2_cooldown = 0;

// ---------- M3 audio read aggressor ----------
static bool m3_active = false;
static uint32_t m3_addr = 0;
static int m3_len = 0, m3_beat = 0;
static int m3_cooldown = 0;

// ---------- scanout burst injection ----------
static int inj_cooldown = 0;
static int inj_busy = 0;

static void clear_master_inputs() {
    tb->m0_arvalid = 0; tb->m0_awvalid = 0; tb->m0_wvalid = 0;
    tb->m1_arvalid = 0; tb->m1_awvalid = 0; tb->m1_wvalid = 0; tb->m1_rready = 1;
    tb->m2_arvalid = 0; tb->m2_awvalid = 0; tb->m2_wvalid = 0; tb->m2_rready = 1;
    tb->m3_arvalid = 0; tb->m3_rready = 1;
    tb->inj_burst_rd = 0;
}

// Record a committed M0 write beat into the golden shadow.
static void golden_record(uint32_t byte_addr, uint32_t data, uint8_t strb) {
    for (int b = 0; b < 4; b++)
        if (strb & (1 << b))
            golden[byte_addr + b] = (data >> (8 * b)) & 0xFF;
}

// Enqueue a GPU framebuffer write.
static void enqueue_m0_write(uint32_t addr, int beats, bool full_strobe) {
    M0Write w;
    w.addr = addr; w.beats = beats; w.full_strobe = full_strobe;
    for (int i = 0; i < beats; i++) {
        uint32_t d = rnd();
        uint8_t s = full_strobe ? 0xF : (uint8_t)(rnd_range(1, 15)); // never 0
        w.data.push_back(d);
        w.strb.push_back(s);
    }
    m0_wr_q.push_back(w);
}

// ------------------------------------------------------------
// Per-cycle master pumping.  Called twice around each tick: we set inputs
// based on the *current* (pre-edge) handshake state, sample fires, tick,
// then advance FSM state on observed handshakes.
// ------------------------------------------------------------

// We drive all inputs, sample combinational readies, tick, then react.
static void step_all_masters() {
    clear_master_inputs();

    // ---------------- M0 GPU posted WRITE ----------------
    // Start a new write burst only when the previous one is FULLY retired
    // (AW accepted AND all W beats accepted AND B returned).  cw stays
    // pinned to the in-flight burst so a late AW handshake never re-points
    // at the next burst's address (the GPU posted-write contract: AW
    // reserves the whole burst, then W beats stream).
    if (!m0_inflight && m0_wr_idx < m0_wr_q.size()) {
        m0_inflight  = true;
        m0_aw_active = true;
        m0_w_active  = true;
        m0_beat = 0;
    }
    M0Write *cw = (m0_inflight && m0_wr_idx < m0_wr_q.size()) ? &m0_wr_q[m0_wr_idx] : nullptr;
    if (m0_aw_active && cw) {
        tb->m0_awvalid = 1;
        tb->m0_awaddr  = cw->addr;
        tb->m0_awlen   = cw->beats - 1;
    }
    if (m0_w_active && cw && m0_beat < cw->beats) {
        tb->m0_wvalid = 1;
        tb->m0_wdata  = cw->data[m0_beat];
        tb->m0_wstrb  = cw->strb[m0_beat];
        tb->m0_wlast  = (m0_beat == cw->beats - 1);
    }

    // ---------------- M0 GPU READ (texture) ----------------
    if (!g_winddown && !m0_rd_active && m0_rd_cooldown == 0 && (rnd() & 3) == 0) {
        m0_rd_active = true;
        m0_rd_addr = TEX_BASE + (rnd_range(0, 4096) * 4);
        m0_rd_len  = rnd_range(0, 7);   // 1..8 beats
        m0_rd_beat = 0;
    }
    if (m0_rd_cooldown) m0_rd_cooldown--;
    if (m0_rd_active && m0_rd_beat == 0) {
        tb->m0_arvalid = 1;
        tb->m0_araddr  = m0_rd_addr;
        tb->m0_arlen   = m0_rd_len;
    }

    // ---------------- M1 CPU aggressor ----------------
    if (!g_winddown && m1_mode == 0 && m1_cooldown == 0) {
        int pick = rnd() % 3;
        if (pick == 0) { // read
            m1_mode = 1;
            m1_addr = CPU_BASE + rnd_range(0, 8192) * 4;
            m1_len = rnd_range(0, 7);
            m1_beat = 0;
        } else if (pick == 1) { // write (serialized — non-wcont path)
            m1_mode = 2;
            m1_addr = CPU_BASE + rnd_range(0, 8192) * 4;
            m1_len = rnd_range(0, 7);
            m1_beat = 0; m1_aw_done = false; m1_w_done = false;
        }
    }
    if (m1_cooldown) m1_cooldown--;
    if (m1_mode == 1) {
        tb->m1_arvalid = 1; tb->m1_araddr = m1_addr; tb->m1_arlen = m1_len;
        // m1_rready jitter: occasionally stall the read to widen contention
        tb->m1_rready = (rnd() & 7) ? 1 : 0;
    } else if (m1_mode == 2) {
        if (!m1_aw_done) { tb->m1_awvalid = 1; tb->m1_awaddr = m1_addr; tb->m1_awlen = m1_len; }
        if (!m1_w_done) {
            // Deliberate W stall jitter (CPU writeback can stall WVALID).
            if (rnd() & 3) {
                tb->m1_wvalid = 1; tb->m1_wdata = rnd(); tb->m1_wstrb = 0xF;
                tb->m1_wlast = (m1_beat == m1_len);
            }
        }
    }

    // ---------------- M2 bridge aggressor ----------------
    if (!g_winddown && m2_mode == 0 && m2_cooldown == 0 && (rnd() & 1)) {
        int pick = rnd() % 2;
        if (pick == 0) {
            m2_mode = 1; m2_addr = BRG_BASE + rnd_range(0, 4096) * 4;
            m2_len = rnd_range(0, 7); m2_beat = 0;
        } else {
            m2_mode = 2; m2_addr = BRG_BASE + rnd_range(0, 4096) * 4;
            m2_len = rnd_range(0, 7); m2_beat = 0; m2_aw_done = false; m2_w_done = false;
        }
    }
    if (m2_cooldown) m2_cooldown--;
    if (m2_mode == 1) {
        tb->m2_arvalid = 1; tb->m2_araddr = m2_addr; tb->m2_arlen = m2_len;
        tb->m2_rready = 1;
    } else if (m2_mode == 2) {
        if (!m2_aw_done) { tb->m2_awvalid = 1; tb->m2_awaddr = m2_addr; tb->m2_awlen = m2_len; }
        if (!m2_w_done) {
            tb->m2_wvalid = 1; tb->m2_wdata = rnd(); tb->m2_wstrb = 0xF;
            tb->m2_wlast = (m2_beat == m2_len);
        }
    }

    // ---------------- M3 audio read aggressor ----------------
    if (!g_winddown && !m3_active && m3_cooldown == 0 && (rnd() & 3) == 0) {
        m3_active = true; m3_addr = AUD_BASE + rnd_range(0, 4096) * 4;
        m3_len = rnd_range(0, 3); m3_beat = 0;
    }
    if (m3_cooldown) m3_cooldown--;
    if (m3_active && m3_beat == 0) {
        tb->m3_arvalid = 1; tb->m3_araddr = m3_addr; tb->m3_arlen = m3_len;
    }

    // ---------------- scanout burst_rd injection ----------------
    if (!g_winddown && inj_busy == 0 && inj_cooldown == 0 && (rnd() & 7) == 0) {
        // io_sdram burst_addr is a halfword (25-bit) address into FB region.
        tb->inj_burst_rd  = 1;
        tb->inj_burst_addr = ((FB_BASE >> 2) << 1) & 0x1FFFFFF;
        tb->inj_burst_len  = rnd_range(8, 64);
        inj_busy = 1;
    }
    if (inj_cooldown) inj_cooldown--;

    // ---- sample combinational handshakes BEFORE the edge ----
    bool m0_aw_fire = tb->m0_awvalid && tb->m0_awready;
    bool m0_w_fire  = tb->m0_wvalid  && tb->m0_wready;
    bool m0_ar_fire = tb->m0_arvalid && tb->m0_arready;
    bool m0_r_fire  = m0_rd_active && tb->m0_rvalid; // M0 has no rready (=1)
    bool m0_b_fire  = tb->m0_bvalid;

    bool m1_ar_fire = tb->m1_arvalid && tb->m1_arready;
    bool m1_r_fire  = tb->m1_rvalid && tb->m1_rready;
    bool m1_aw_fire = tb->m1_awvalid && tb->m1_awready;
    bool m1_w_fire  = tb->m1_wvalid && tb->m1_wready;
    bool m1_b_fire  = tb->m1_bvalid;

    bool m2_ar_fire = tb->m2_arvalid && tb->m2_arready;
    bool m2_r_fire  = tb->m2_rvalid && tb->m2_rready;
    bool m2_aw_fire = tb->m2_awvalid && tb->m2_awready;
    bool m2_w_fire  = tb->m2_wvalid && tb->m2_wready;
    bool m2_b_fire  = tb->m2_bvalid;

    bool m3_ar_fire = tb->m3_arvalid && tb->m3_arready;
    bool m3_r_fire  = tb->m3_rvalid && tb->m3_rready;

    bool inj_done = tb->inj_burst_data_done;

    tick();

    // ---- react to observed handshakes ----
    // M0 AW
    if (m0_aw_fire) { m0_aw_active = false; }
    // M0 W — record into golden only on a real W handshake (honoring strobe).
    if (m0_w_fire && cw && m0_beat < cw->beats) {
        golden_record(cw->addr + 4 * m0_beat, cw->data[m0_beat], cw->strb[m0_beat]);
        m0_beat++;
        if (m0_beat >= cw->beats) { m0_w_active = false; m0_w_done = true; }
    }
    // M0 B — retire the in-flight burst ONLY when AW accepted + all W beats
    // accepted + B returned.  This is the GPU posted-write completion (one B
    // per AW).  Advancing the queue index here (not on WLAST) prevents a late
    // AW handshake from re-pointing at the next burst's address.
    if (m0_b_fire && m0_inflight && !m0_aw_active && m0_w_done) {
        m0_wr_idx++;
        m0_inflight = false;
        m0_w_done = false;
        if (m0_b_pending > 0) m0_b_pending--;
    }
    // M0 read
    if (m0_ar_fire) { /* address accepted */ }
    if (m0_r_fire) {
        m0_rd_beat++;
        if (tb->m0_rlast) { m0_rd_active = false; m0_rd_cooldown = rnd_range(1, 8); }
    }

    // M1
    if (m1_mode == 1) {
        if (m1_r_fire && tb->m1_rlast) { m1_mode = 0; m1_cooldown = rnd_range(0, 4); }
    } else if (m1_mode == 2) {
        if (m1_aw_fire) m1_aw_done = true;
        if (m1_w_fire) {
            m1_beat++;
            if (m1_beat > m1_len) m1_w_done = true;
        }
        if (m1_b_fire) { m1_mode = 0; m1_cooldown = rnd_range(0, 4); }
    }

    // M2
    if (m2_mode == 1) {
        if (m2_r_fire && tb->m2_rlast) { m2_mode = 0; m2_cooldown = rnd_range(0, 4); }
    } else if (m2_mode == 2) {
        if (m2_aw_fire) m2_aw_done = true;
        if (m2_w_fire) {
            m2_beat++;
            if (m2_beat > m2_len) m2_w_done = true;
        }
        if (m2_b_fire) { m2_mode = 0; m2_cooldown = rnd_range(0, 4); }
    }

    // M3
    if (m3_active && m3_r_fire && tb->m3_rlast) { m3_active = false; m3_cooldown = rnd_range(1, 8); }

    // scanout injection lifecycle
    if (inj_busy && inj_done) { inj_busy = 0; inj_cooldown = rnd_range(4, 32); }
}

static void dump_state(const char *why) {
    printf("  [%s] cyc=%llu m0_wr_idx=%zu/%zu aw=%d w=%d beat=%d b_pend=%d "
           "rd=%d | m1=%d m2=%d m3=%d inj=%d | "
           "arb_state=%d grant=%d wq_count=%d wq_full=%d busy=%d io_state=0x%02x\n",
           why, (unsigned long long)(sim_time / 2), m0_wr_idx, m0_wr_q.size(),
           m0_aw_active, m0_w_active, m0_beat, m0_b_pending, m0_rd_active,
           m1_mode, m2_mode, m3_active, inj_busy,
           tb->dbg_arb_state, tb->dbg_grant, tb->dbg_gpu_wq_count,
           tb->dbg_gpu_wq_full, tb->dbg_busy, tb->dbg_io);
}

// Drain: run masters until M0 write queue fully consumed AND no in-flight.
// Stall watchdog: if no M0 write commits for STALL_WIN cycles, dump state.
static void run_until_m0_drained(uint64_t max_cycles) {
    uint64_t c = 0;
    size_t last_idx = m0_wr_idx;
    uint64_t since_progress = 0;
    int dumps = 0;
    const uint64_t STALL_WIN = 50000;
    while (c < max_cycles) {
        step_all_masters();
        c++;
        if (m0_wr_idx != last_idx) { last_idx = m0_wr_idx; since_progress = 0; }
        else since_progress++;
        // Stall watchdog only meaningful while writes remain to drain.
        if (!g_winddown && since_progress == STALL_WIN && dumps < 8) {
            dump_state("STALL");
            dumps++;
        }
        // Once all writes are accepted+committed, wind down: stop new traffic.
        if (m0_wr_idx >= m0_wr_q.size() && !m0_inflight)
            g_winddown = true;
        if (m0_wr_idx >= m0_wr_q.size() && !m0_inflight && !m0_rd_active &&
            m1_mode == 0 && m2_mode == 0 && !m3_active && inj_busy == 0)
            break;
    }
}

// ============================================================
// Readback verification: read every golden address THROUGH the slave
// (single-word M1 reads) and compare byte-exact.  Masters are quiesced.
// ============================================================
static uint32_t read_word_via_slave(uint32_t byte_addr) {
    // Issue a single-word M1 read with a proper AR handshake (deassert AR
    // once accepted), then wait for the R beat.  Masters are quiesced.
    uint32_t data = 0;
    bool ar_done = false, got = false;
    for (int t = 0; t < 4000 && !got; t++) {
        clear_master_inputs();
        if (!ar_done) { tb->m1_arvalid = 1; tb->m1_araddr = byte_addr; tb->m1_arlen = 0; }
        tb->m1_rready = 1;
        bool ar_fire = tb->m1_arvalid && tb->m1_arready;
        bool r_fire  = tb->m1_rvalid && tb->m1_rready;
        if (r_fire) { data = tb->m1_rdata; got = true; }
        tick();
        if (ar_fire) ar_done = true;
    }
    clear_master_inputs();
    tick();
    if (!got) { printf("  READBACK TIMEOUT @ 0x%08x\n", byte_addr); errors++; }
    return data;
}

// Direct peek of the sdram_model_full physical memory (ground truth) for
// bisection: did the WRITE land wrong (model corrupt) or did the READ path
// misreport (model correct, slave-read wrong)?  Replicates the full model's
// flat addressing exactly: hw = (byte>>2)<<1; cell = {hw[24:23], hw[22:10],
// hw[9:0]} (full 10-bit column).
static uint16_t model_peek_hw(uint32_t hw_addr) {
    uint32_t bank = (hw_addr >> 23) & 0x3;
    uint32_t row  = (hw_addr >> 10) & 0x1FFF;
    uint32_t col  = hw_addr & 0x3FF;          // col[9:0] — full row width
    uint32_t flat = (bank << 23) | (row << 10) | col;  // {bank, row, col[9:0]}
    return ((const uint16_t *)tb->rootp->tb_sdram_cont__DOT__sdram_chip__DOT__mem.m_storage)[flat];
}
static uint32_t model_peek_word(uint32_t byte_addr) {
    uint32_t hw = (byte_addr >> 2) << 1;
    return ((uint32_t)model_peek_hw(hw + 1) << 16) | model_peek_hw(hw);
}

static void verify_all() {
    printf("Verifying %zu golden bytes...\n", golden.size());
    // Group golden bytes into words to minimize read-backs.
    std::map<uint32_t, bool> word_seen;
    for (auto &kv : golden) word_seen[kv.first & ~3u] = true;

    int checked = 0;
    for (auto &wkv : word_seen) {
        uint32_t waddr = wkv.first;
        uint32_t rd = read_word_via_slave(waddr);
        for (int b = 0; b < 4; b++) {
            auto it = golden.find(waddr + b);
            if (it == golden.end()) continue; // byte was never written by M0
            uint8_t exp = it->second;
            uint8_t got = (rd >> (8 * b)) & 0xFF;
            if (exp != got) {
                errors++;
                if (g_first_fail_addr < 0) g_first_fail_addr = (int)(waddr + b);
                if (errors <= 30) {
                    uint32_t mw = model_peek_word(waddr);
                    uint8_t  mb = (mw >> (8 * b)) & 0xFF;
                    printf("  MISMATCH @ 0x%08x: golden 0x%02x | slave-read 0x%02x | "
                           "model-mem 0x%02x  [%s]\n",
                           waddr + b, exp, got, mb,
                           (mb == exp) ? "READ-PATH" : (mb == got ? "WRITE-PATH" : "BOTH-DIFFER"));
                }
            }
        }
        checked++;
    }
    printf("Verified %d words.\n", checked);
}

// Peek the sdram_model's internal error counter (protocol/refresh self-check).
static long model_errors() {
    return tb->rootp->tb_sdram_cont__DOT__sdram_chip__DOT__errors;
}

// ============================================================
// Workload generators
// ============================================================

// Realistic Doom/Quake column-write pattern: vertical spans hit a stride of
// addresses (one word per row of pixels in a column-major framebuffer chunk),
// but the GPU coalescer emits CONTIGUOUS bursts per horizontal run.  We mix:
//   - 2-beat native writes (the minimal GPU FB command)
//   - 3..8-beat coalesced full-strobe bursts (horizontal spans)
//   - partial-strobe single-word writes (sub-word edge pixels -> coalescer
//     falls back to singles)
// Some bursts deliberately straddle a real 2KB SDRAM row boundary.
//
// NON-OVERLAPPING addressing: addresses advance monotonically across a large
// FB region so EVERY write lands at a distinct cell.  A dropped or corrupted
// beat therefore cannot be masked by a later overwrite — maximally sensitive.
static void gen_workload(int n_groups, uint32_t window_bytes) {
    uint32_t a = FB_BASE;
    for (int g = 0; g < n_groups; g++) {
        int kind = rnd() % 5;
        switch (kind) {
            case 0: // 2-beat native
                enqueue_m0_write(a, 2, true);
                a += 8;
                break;
            case 1: { // coalesced 3..8 beat full-strobe span
                int beats = rnd_range(3, 8);
                enqueue_m0_write(a, beats, true);
                a += beats * 4;
                break;
            }
            case 2: // partial-strobe single word (sub-word pixel)
                enqueue_m0_write(a, 1, false);
                a += 4;
                break;
            case 3: { // burst deliberately placed to cross a real row boundary
                uint32_t row = (a / ROW_BYTES) * ROW_BYTES;
                uint32_t near_edge = row + ROW_BYTES - 12; // 3 words before edge
                if (near_edge < a) near_edge += ROW_BYTES;
                int beats = rnd_range(4, 8);
                enqueue_m0_write(near_edge, beats, true);
                a = near_edge + beats * 4 + 16;
                break;
            }
            case 4: { // partial-strobe inside a longer logical run -> singles
                int n = rnd_range(2, 5);
                for (int i = 0; i < n; i++) { enqueue_m0_write(a, 1, false); a += 4; }
                break;
            }
        }
        // Distinct-cell guarantee: wrap only at the (large) window edge and
        // re-base to a fresh, non-overlapping band so addresses never collide
        // within a phase.
        if (a > FB_BASE + window_bytes) a = FB_BASE;
    }
}

// Reset all master/aggressor FSM state between sweep phases.
static void reset_harness_state() {
    m0_wr_q.clear(); m0_wr_idx = 0;
    m0_inflight = m0_aw_active = m0_w_active = m0_w_done = false;
    m0_beat = 0; m0_b_pending = 0;
    m0_rd_active = false; m0_rd_cooldown = 0; m0_rd_beat = 0;
    m1_mode = 0; m1_cooldown = 0; m1_beat = 0; m1_aw_done = m1_w_done = false;
    m2_mode = 0; m2_cooldown = 0; m2_beat = 0; m2_aw_done = m2_w_done = false;
    m3_active = false; m3_cooldown = 0; m3_beat = 0;
    inj_busy = 0; inj_cooldown = 0;
    g_winddown = false;
    golden.clear();
}

// Run one full contention phase: generate workload, storm, verify byte-exact.
// Golden + SDRAM accumulate within the phase (distinct cells => no masking).
static int run_phase(const char *name, uint32_t seed, int groups,
                     uint32_t window_bytes) {
    printf("\n[%s] seed=0x%08x groups=%d window=0x%x ...\n",
           name, seed, groups, window_bytes);
    rng_state = seed;
    reset_harness_state();
    int err_before = errors;

    gen_workload(groups, window_bytes);
    printf("  queued GPU writes: %zu\n", m0_wr_q.size());

    run_until_m0_drained(60000000ULL);
    if (m0_wr_idx < m0_wr_q.size()) {
        printf("  WARNING: not all GPU writes drained (%zu/%zu) — WEDGE\n",
               m0_wr_idx, m0_wr_q.size());
        errors++;
    } else {
        printf("  all %zu GPU writes accepted+committed (cyc=%llu).\n",
               m0_wr_q.size(), (unsigned long long)(sim_time / 2));
    }

    clear_master_inputs();
    for (int i = 0; i < 200; i++) tick();
    verify_all();

    int phase_errs = errors - err_before;
    printf("  [%s] result: %s (%d new errors)\n", name,
           phase_errs ? "FAIL" : "PASS", phase_errs);
    return phase_errs;
}

static void boot() {
    tb->reset_n = 0;
    clear_master_inputs();
    for (int i = 0; i < 64; i++) tick();
    tb->reset_n = 1;
    // io_sdram boot: 30000-cycle power-up + init sequence.
    for (int i = 0; i < 31200; i++) tick();
}

int main(int argc, char **argv) {
    Verilated::commandArgs(argc, argv);
    tb = new Vtb_sdram_cont;

    printf("========================================================\n");
    printf(" MiSTer SDRAM write-path multi-master contention test\n");
    printf(" arbiter + slave + MiSTer io_sdram + sdram_model\n");
    printf("========================================================\n");

    boot();

    // ---- Contention sweep: multiple seeds x intensities ----
    // Each phase reseeds the deterministic PRNG, regenerates a non-overlapping
    // FB-write workload, runs the full 4-master + scanout storm, and verifies
    // every committed byte read back through the slave is byte-exact AGAINST a
    // golden shadow.  Distinct-cell addressing => a dropped beat cannot be
    // masked by a later overwrite.
    struct Phase { const char *name; uint32_t seed; int groups; uint32_t win; };
    const Phase phases[] = {
        { "P1 baseline",      0x1234abcdu, 4000, 0x100000 },
        { "P2 dense-window",  0xC0FFEE11u, 5000, 0x020000 }, // tighter -> more row reuse, refresh collisions
        { "P3 wide-window",   0xDEADBEEFu, 6000, 0x400000 }, // many distinct rows -> row-cross churn
        { "P4 seed-A",        0x00000001u, 4000, 0x080000 },
        { "P5 seed-B",        0xFFFFFFFFu, 4000, 0x080000 },
        { "P6 seed-C",        0xA5A5A5A5u, 5000, 0x040000 },
    };

    int total_phase_errs = 0;
    for (const auto &p : phases)
        total_phase_errs += run_phase(p.name, p.seed, p.groups, p.win);

    long me = model_errors();
    printf("\n========================================================\n");
    printf("sdram_model protocol/refresh self-check errors: %ld\n", me);
    printf("total byte mismatches / readback timeouts:      %d\n", errors);
    if (g_first_fail_addr >= 0)
        printf("FIRST FAILING ADDRESS: 0x%08x\n", (uint32_t)g_first_fail_addr);
    printf("total simulated cycles: %llu\n", (unsigned long long)(sim_time / 2));
    printf("========================================================\n");

    int total = total_phase_errs + (int)me;
    if (total == 0) {
        printf("RESULT: PASS — no write beat dropped/corrupted across %zu "
               "contention phases.\n", sizeof(phases) / sizeof(phases[0]));
    } else {
        printf("RESULT: FAIL — %d write/protocol errors reproduced.\n", total);
    }

    delete tb;
    return total ? 1 : 0;
}
