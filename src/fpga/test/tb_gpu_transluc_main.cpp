//------------------------------------------------------------------------------
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileType: SOURCE
// SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
//------------------------------------------------------------------------------
//
// MiSTer translucent-sprite span-dropout repro rig.
//
// Drives the REAL full-stack GPU pipeline (gpu_core + edge walker + tex
// cache, built with +define+INCLUDE_TRANSLUC and the exact emu.sv MiSTer
// parameter set) through the REAL MiSTer SDRAM stack (arbiter + slave +
// io_sdram_mister + sdram_model_full) with native 0x4C translucent column
// lists — the Doom II fireball path that drops spans at close range on
// MiSTer hardware while opaque rendering is perfect.
//
// Verification, every frame:
//   (a) byte-exact whole-FB compare against a CPU reference implementing
//       the acceptance-suite transluc semantics (cmap first, then
//       LUT[(src>>1)*256+dst]); untouched pixels must hold the prefill.
//   (b) per-lane coverage: every submitted column lane is checked pixel by
//       pixel — a lane with missing pixels is THE hardware symptom.
//   (c) structural taps: slave R-chain drop-arm counter (must stay 0 while
//       the GPU owns the read), doorbell-DMA starvation-override fires,
//       DMA-owns-R overlap with blend/tex traffic, ring-capture word count
//       vs submitted word count, and a decoder header histogram (any opcode
//       outside {0x4C, 0x02} or a mis-sized 0x4C = ring desync evidence).
//
// Deterministic pseudo-randomness only (xorshift32); no real RNG.
//
#include <cstdio>
#include <cstdint>
#include <cstring>
#include <cstdlib>
#include <vector>
#include <deque>
#include <string>
#include "Vtb_gpu_transluc.h"
#include "verilated.h"

static Vtb_gpu_transluc *tb;
static uint64_t sim_time = 0;   // half-cycles
static uint64_t cyc = 0;        // full cycles

static uint32_t rng_state = 0xBADC0DEu;
static inline uint32_t rnd() {
    uint32_t x = rng_state; x ^= x << 13; x ^= x >> 17; x ^= x << 5;
    rng_state = x; return x;
}
static inline uint32_t rnd_range(uint32_t lo, uint32_t hi) { return lo + (rnd() % (hi - lo + 1)); }

// ============================================================
// Memory map (byte addresses into sdram_model_full, 64MB)
// ============================================================
static const uint32_t FB_BASE        = 0x00080000; // 320x200 8bpp framebuffer
// Doorbell-DMA staging: FOUR rotating buffers.  The descriptor FIFO is
// 2-deep and its full-bit clears on POP (pull start), not completion, so
// after wait_for_desc_slot the two most recent kicks may still be unread
// (one active + one queued).  A 4-deep rotation restages a buffer only
// 4 kicks later — provably retired.  (A 2-buffer version corrupted the
// stream by overwriting a buffer mid-pull.)
static const uint32_t BATCH_BUFS[4]  = { 0x00040000, 0x00048000, 0x00050000, 0x00058000 };
static const uint32_t TEX_BASE       = 0x00060000; // 64x64 row-major texture
static const uint32_t PALOOKUP_BASE  = 0x03FC0000; // gpu_core default
static const uint32_t PALOOKUP_STRIDE= 0x00004000; // 16 KB per cmap slot
static const uint32_t FB_W = 320, FB_H = 200;
static const uint32_t FB_BYTES = FB_W * FB_H;
static const uint32_t TEX_W = 64, TEX_H = 64;

// Aggressor regions (away from FB/batch/tex/palookup)
static const uint32_t CPU_BASE = 0x00C00000;
static const uint32_t BRG_BASE = 0x00D00000;
static const uint32_t AUD_BASE = 0x00E00000;

// ---- MMIO register map (word index) ----
enum {
    REG_CTRL = 0, REG_RING_WRPTR = 1, REG_DMA_SRC = 3, REG_RING_RDPTR = 4,
    REG_STATUS = 5, REG_FENCE = 6, REG_DMA_LEN = 7,
    REG_TRANSLUC_ADDR = 8, REG_TRANSLUC_DATA = 9,
    REG_TEX_FLUSH = 10, REG_DMA_KICK = 11, REG_PALOOKUP_BASE = 12,
};

// span flags (wire bit positions, pre-shift; w0 carries flags<<20)
static const uint8_t F_COLORMAP  = 1 << 0;
static const uint8_t F_SKIP_ZERO = 1 << 2;
static const uint8_t F_TRANSLUC  = 1 << 6;

// ============================================================
// Structural event counters (sampled pre-edge in tick())
// ============================================================
struct Counters {
    uint64_t drop_arm = 0;          // slave R-chain overflow arm, any master
    uint64_t drop_arm_gpu = 0;      // ... while grant==GPU (MUST stay 0)
    uint64_t dma_forced = 0;        // starvation-override fires (cycles at thresh)
    uint64_t hijack_blend = 0;      // rising edges: DMA owns R while blend waits R
    uint64_t hijack_tex = 0;        // rising edges: DMA owns R while tex in flight
    uint64_t ring_words = 0;        // words captured into ring BRAM
    uint64_t hdr_total = 0;         // headers decoded (S_DECODE entries)
    uint64_t hdr_4c = 0, hdr_fence = 0;
    uint64_t hdr_unknown = 0;       // opcode outside {0x4C,0x02}
    uint64_t hdr_badsize = 0;       // 0x4C size != submitted (24) or fence != 1
    uint64_t rd_expose_d1 = 0;      // read beats with A[12:11]!=0 one cycle prior
    uint64_t rd_expose_d2 = 0;      // ... two cycles prior
    uint64_t read_masked = 0;       // read beats actually poisoned (ss1r modes)
    uint64_t masked_scanout = 0;    // ... of which during a scanout burst
};
static Counters g_ctr;
static bool prev_hb = false, prev_ht = false;
static uint8_t prev_gpu_state = 0;
static bool g_first_anomaly_logged = false;
static uint64_t g_words_submitted = 0;   // total command-stream words DMA'd

static void log_anomaly_context(const char *what) {
    printf("  [ANOMALY %s] cyc=%llu fbss=%u dma_state=%u starve=%u ring wr=0x%04x rd=0x%04x "
           "gpu_state=%u cmd=0x%02x/%u grant=%u\n",
           what, (unsigned long long)cyc, tb->dbg_fbss, tb->dbg_dma_state,
           tb->dbg_dma_starve, tb->dbg_ring_wrptr_b, tb->dbg_ring_rdptr_b,
           tb->dbg_gpu_state, tb->dbg_cmd_type, tb->dbg_cmd_words, tb->dbg_grant);
}

// ============================================================
// M0 beat-ownership adjudication.
//
// Every M0 AR acceptance is queued with its owner, decoded from the AR-mux
// selects at the accept cycle (dma_owns_ar ? DMA : blend_owns_m0 ? BLEND :
// TEX).  Every returned R beat is charged to the oldest un-finished burst.
// A TEX-owned beat presented while dma_owns_r=1 is a SWALLOWED beat (the
// tex cache's rvalid is masked, the DMA captures the beat into the ring) —
// the hypothesized ring-desync mechanism.  Symmetrically, a DMA-owned beat
// with dma_owns_r=0 is a beat the ring MISSED.  Both must stay 0.
// ============================================================
enum { OWN_TEX = 0, OWN_BLEND = 1, OWN_DMA = 2 };
static const char *own_name[3] = { "TEX", "BLEND", "DMA" };
struct Burst { uint8_t owner; int beats; };
static std::deque<Burst> m0q;
static uint64_t adj_beats[3] = {0, 0, 0};
static uint64_t adj_swallowed_tex = 0, adj_masked_blend = 0, adj_misrouted_dma = 0;
static uint64_t adj_orphan = 0, adj_len_mismatch = 0;

// Cycle-window trace around the first hijackT assertion.
struct TraceRow {
    uint64_t cyc;
    uint8_t tex_if, tex_arv, tex_state, dma_ar, dma_r, dma_state;
    uint8_t m0_arv, m0_ardy, m0_rv, m0_rl, blend, fbss;
    uint8_t arlen; uint16_t ring_wr; uint8_t qown; int qbeats;
};
static std::deque<TraceRow> trace_buf;
static bool g_trace_arm = false, g_trace_fired = false;
static int g_trace_post = 0;
static int g_trace_trigger = 0;          // 0 = hijackT, 1 = orphan beat
static bool g_orphan_now = false;        // set by the classifier this cycle

static void trace_dump() {
    printf("---- hijackT cycle-window trace (48 pre + 40 post) ----\n");
    printf("%-10s texIF texARV texST dmaAR dmaR dmaST m0ARV m0RDY m0RV m0RL blnd fbss arln ringwr Qhead\n", "cyc");
    for (const TraceRow &r : trace_buf)
        printf("%-10llu   %u     %u     %u     %u    %u    %u     %u     %u     %u    %u    %u   %2u   %2u  0x%04x %s/%d\n",
               (unsigned long long)r.cyc, r.tex_if, r.tex_arv, r.tex_state,
               r.dma_ar, r.dma_r, r.dma_state, r.m0_arv, r.m0_ardy, r.m0_rv,
               r.m0_rl, r.blend, r.fbss, r.arlen, r.ring_wr,
               r.qbeats >= 0 ? own_name[r.qown] : "-", r.qbeats);
    printf("---- end trace ----\n");
}

struct ArLog { uint64_t cyc; uint8_t owner, arlen; };
static std::deque<ArLog> ar_hist;

static void adjudicate_preedge() {
    // AR acceptance -> queue with owner.
    if (tb->dbg_m0_arvalid && tb->dbg_m0_arready) {
        uint8_t owner = tb->dbg_dma_owns_ar ? OWN_DMA
                      : tb->dbg_blend_owns  ? OWN_BLEND : OWN_TEX;
        m0q.push_back({owner, (int)tb->dbg_m0_arlen + 1});
        ar_hist.push_back({cyc, owner, tb->dbg_m0_arlen});
        if (ar_hist.size() > 6) ar_hist.pop_front();
    }
    // R beat -> classify against queue head.
    g_orphan_now = false;
    if (tb->dbg_m0_rvalid) {
        if (m0q.empty()) {
            g_orphan_now = true;
            if (adj_orphan < 8) {
                printf("  [ADJ] ORPHAN BEAT at cyc=%llu (rlast=%u dma_r=%u blend=%u tex_if=%u slave_st=%u arb=%u grant=%u)\n",
                       (unsigned long long)cyc, tb->dbg_m0_rlast, tb->dbg_dma_owns_r,
                       tb->dbg_blend_owns, tb->dbg_tex_inflight, tb->dbg_slave_state,
                       tb->dbg_arb_state, tb->dbg_grant);
                for (const ArLog &a : ar_hist)
                    printf("        AR hist: cyc=%llu owner=%s arlen=%u\n",
                           (unsigned long long)a.cyc, own_name[a.owner], a.arlen);
            }
            adj_orphan++;
        } else {
            Burst &b = m0q.front();
            adj_beats[b.owner]++;
            b.beats--;
            if (b.owner == OWN_TEX && tb->dbg_dma_owns_r) {
                if (adj_swallowed_tex == 0)
                    printf("  [ADJ] SWALLOWED TEX BEAT at cyc=%llu\n", (unsigned long long)cyc);
                adj_swallowed_tex++;
            }
            if (b.owner == OWN_BLEND && tb->dbg_dma_owns_r) {
                if (adj_masked_blend == 0)
                    printf("  [ADJ] MASKED BLEND BEAT at cyc=%llu\n", (unsigned long long)cyc);
                adj_masked_blend++;
            }
            if (b.owner == OWN_DMA && !tb->dbg_dma_owns_r) {
                if (adj_misrouted_dma == 0)
                    printf("  [ADJ] DMA BEAT WITHOUT dma_owns_r at cyc=%llu\n", (unsigned long long)cyc);
                adj_misrouted_dma++;
            }
            if (tb->dbg_m0_rlast) {
                if (b.beats != 0) adj_len_mismatch++;
                m0q.pop_front();
            }
        }
    }
    // Trace window recording.
    if (g_trace_arm) {
        TraceRow r;
        r.cyc = cyc; r.tex_if = tb->dbg_tex_inflight; r.tex_arv = tb->dbg_tex_arvalid;
        r.tex_state = tb->dbg_tex_state; r.dma_ar = tb->dbg_dma_owns_ar;
        r.dma_r = tb->dbg_dma_owns_r; r.dma_state = tb->dbg_dma_state;
        r.m0_arv = tb->dbg_m0_arvalid; r.m0_ardy = tb->dbg_m0_arready;
        r.m0_rv = tb->dbg_m0_rvalid; r.m0_rl = tb->dbg_m0_rlast;
        r.blend = tb->dbg_blend_owns; r.fbss = tb->dbg_fbss;
        r.arlen = tb->dbg_m0_arlen; r.ring_wr = tb->dbg_ring_wrptr_b;
        if (!m0q.empty()) { r.qown = m0q.front().owner; r.qbeats = m0q.front().beats; }
        else { r.qown = 0; r.qbeats = -1; }
        trace_buf.push_back(r);
        if (!g_trace_fired) {
            if (trace_buf.size() > 48) trace_buf.pop_front();
            bool trig = (g_trace_trigger == 0) ? (bool)tb->evt_dma_hijack_tex : g_orphan_now;
            if (trig) { g_trace_fired = true; g_trace_post = 40; }
        } else if (g_trace_post > 0) {
            if (--g_trace_post == 0) { trace_dump(); g_trace_arm = false; }
        }
    }
}

static void sample_ss1_read_preedge();

static void sample_events_preedge() {
    adjudicate_preedge();
    if (tb->evt_drop_arm) {
        g_ctr.drop_arm++;
        if (tb->dbg_grant == 0) {
            if (g_ctr.drop_arm_gpu == 0) log_anomaly_context("DROP-ARM-WHILE-GPU-GRANTED");
            g_ctr.drop_arm_gpu++;
        }
    }
    if (tb->evt_dma_forced) g_ctr.dma_forced++;
    bool hb = tb->evt_dma_hijack_blend, ht = tb->evt_dma_hijack_tex;
    if (hb && !prev_hb) {
        if (g_ctr.hijack_blend == 0) log_anomaly_context("DMA-OWNS-R-WHILE-BLEND-WAITS");
        g_ctr.hijack_blend++;
    }
    if (ht && !prev_ht) {
        if (g_ctr.hijack_tex == 0) log_anomaly_context("DMA-OWNS-R-WHILE-TEX-INFLIGHT");
        g_ctr.hijack_tex++;
    }
    prev_hb = hb; prev_ht = ht;
    if (tb->evt_ring_capture) g_ctr.ring_words++;
    sample_ss1_read_preedge();
}

static void sample_events_postedge() {
    // Decoder header histogram: S_DECODE == 2, entered this cycle.
    uint8_t st = tb->dbg_gpu_state;
    if (st == 2 && prev_gpu_state != 2) {
        g_ctr.hdr_total++;
        uint8_t op = tb->dbg_cmd_type;
        uint16_t words = tb->dbg_cmd_words;
        if (op == 0x4C) {
            g_ctr.hdr_4c++;
            if (words != 24) { g_ctr.hdr_badsize++;
                if (!g_first_anomaly_logged) { log_anomaly_context("BAD-0x4C-SIZE"); g_first_anomaly_logged = true; } }
        } else if (op == 0x02) {
            g_ctr.hdr_fence++;
            if (words != 1) { g_ctr.hdr_badsize++;
                if (!g_first_anomaly_logged) { log_anomaly_context("BAD-FENCE-SIZE"); g_first_anomaly_logged = true; } }
        } else {
            g_ctr.hdr_unknown++;
            if (!g_first_anomaly_logged) { log_anomaly_context("UNKNOWN-OPCODE"); g_first_anomaly_logged = true; }
        }
    }
    prev_gpu_state = st;
}

// ============================================================
// Aggressors: M1 CPU r/w, M2 bridge r/w, M3 audio r (with patterned
// rready stalls on M1/M3), plus scanout burst injection.
// ============================================================
static int m1_mode = 0; static uint32_t m1_addr = 0; static int m1_len = 0, m1_beat = 0;
static bool m1_aw_done = false, m1_w_done = false; static int m1_cooldown = 0;
static int m1_stall = 0;
static int m2_mode = 0; static uint32_t m2_addr = 0; static int m2_len = 0, m2_beat = 0;
static bool m2_aw_done = false, m2_w_done = false; static int m2_cooldown = 0;
static bool m3_active = false; static uint32_t m3_addr = 0; static int m3_len = 0; static int m3_cooldown = 0;
static int m3_stall = 0;
static int inj_busy = 0, inj_cooldown = 0;
static bool g_aggr = false;
static uint32_t g_aggr_intv = 40;
// Max rready stall length for M1/M3 read bursts.  The slave's R chain is
// 3-deep (rvalid + 2 skids); stalls <= 2 stay inside the contract.  Stalls
// up to 10 (the dedicated stall-grind phase) exceed it: the slave DROPS a
// beat for that master ("invariant broken by pathological back-pressure"),
// rlast is lost, the arbiter's rd_completing never fires and the whole
// read path wedges — a documented bounded finding, NOT reachable by the
// GPU (M0 has no rready).  Main sweep keeps 2.
static uint32_t g_rready_stall_max = 2;

// Scanout injection mode: 0=off, 1=sparse-random (wrpath style),
// 2=line-cadence (80-word line fetch every SCAN_PERIOD cycles).
static int g_scan_mode = 0;
static uint32_t g_scan_phase = 0;
static const uint32_t SCAN_PERIOD = 3172;
static uint32_t scan_line = 0;

static void clear_aggr_inputs() {
    tb->m1_arvalid = 0; tb->m1_awvalid = 0; tb->m1_wvalid = 0; tb->m1_rready = 1;
    tb->m2_arvalid = 0; tb->m2_awvalid = 0; tb->m2_wvalid = 0; tb->m2_rready = 1;
    tb->m3_arvalid = 0; tb->m3_rready = 1;
    tb->inj_burst_rd = 0;
}

static void drive_aggressors_pre() {
    clear_aggr_inputs();

    // Scanout injection runs whenever a mode is selected (it is the display
    // fetch — on real hardware it never stops), independent of aggr_en.
    if (g_scan_mode == 1) {
        if (inj_busy == 0 && inj_cooldown == 0 && (rnd() % (g_aggr_intv * 8)) == 0) {
            tb->inj_burst_rd = 1;
            tb->inj_burst_addr = ((FB_BASE >> 2) << 1) & 0x1FFFFFF;
            tb->inj_burst_len = rnd_range(8, 32);
            inj_busy = 1;
        }
        if (inj_cooldown) inj_cooldown--;
    } else if (g_scan_mode == 2) {
        if (inj_busy == 0 && ((cyc + g_scan_phase) % SCAN_PERIOD) == 0) {
            tb->inj_burst_rd = 1;
            tb->inj_burst_addr = (((FB_BASE >> 2) + scan_line * 80u) << 1) & 0x1FFFFFF;
            tb->inj_burst_len = 80;
            inj_busy = 1;
            scan_line = (scan_line + 1) % FB_H;
        }
    }

    if (!g_aggr) return;

    // M1 CPU — read (with patterned rready stalls) or serialized write.
    if (m1_mode == 0 && m1_cooldown == 0 && (rnd() % g_aggr_intv) == 0) {
        int pick = rnd() % 2;
        if (pick == 0) { m1_mode = 1; m1_addr = CPU_BASE + rnd_range(0, 8192) * 4; m1_len = rnd_range(0, 7); m1_beat = 0; }
        else { m1_mode = 2; m1_addr = CPU_BASE + rnd_range(0, 8192) * 4; m1_len = rnd_range(0, 7); m1_beat = 0; m1_aw_done = m1_w_done = false; }
    }
    if (m1_cooldown) m1_cooldown--;
    if (m1_mode == 1) {
        tb->m1_arvalid = 1; tb->m1_araddr = m1_addr; tb->m1_arlen = m1_len;
        if (m1_stall > 0) { tb->m1_rready = 0; m1_stall--; }
        else { tb->m1_rready = 1; if ((rnd() & 3) == 0) m1_stall = (int)rnd_range(0, g_rready_stall_max); }
    } else if (m1_mode == 2) {
        if (!m1_aw_done) { tb->m1_awvalid = 1; tb->m1_awaddr = m1_addr; tb->m1_awlen = m1_len; }
        if (!m1_w_done) { tb->m1_wvalid = 1; tb->m1_wdata = rnd(); tb->m1_wstrb = 0xF; tb->m1_wlast = (m1_beat == m1_len); }
    }
    // M2 bridge — sparse read/write.
    if (m2_mode == 0 && m2_cooldown == 0 && (rnd() % g_aggr_intv) == 0) {
        if (rnd() & 1) { m2_mode = 1; m2_addr = BRG_BASE + rnd_range(0, 4096) * 4; m2_len = rnd_range(0, 7); m2_beat = 0; }
        else { m2_mode = 2; m2_addr = BRG_BASE + rnd_range(0, 4096) * 4; m2_len = rnd_range(0, 7); m2_beat = 0; m2_aw_done = m2_w_done = false; }
    }
    if (m2_cooldown) m2_cooldown--;
    if (m2_mode == 1) { tb->m2_arvalid = 1; tb->m2_araddr = m2_addr; tb->m2_arlen = m2_len; }
    else if (m2_mode == 2) {
        if (!m2_aw_done) { tb->m2_awvalid = 1; tb->m2_awaddr = m2_addr; tb->m2_awlen = m2_len; }
        if (!m2_w_done) { tb->m2_wvalid = 1; tb->m2_wdata = rnd(); tb->m2_wstrb = 0xF; tb->m2_wlast = (m2_beat == m2_len); }
    }
    // M3 audio — sparse short reads with patterned rready stalls.
    if (!m3_active && m3_cooldown == 0 && (rnd() % g_aggr_intv) == 0) { m3_active = true; m3_addr = AUD_BASE + rnd_range(0, 4096) * 4; m3_len = rnd_range(0, 3); }
    if (m3_cooldown) m3_cooldown--;
    if (m3_active) {
        tb->m3_arvalid = 1; tb->m3_araddr = m3_addr; tb->m3_arlen = m3_len;
        if (m3_stall > 0) { tb->m3_rready = 0; m3_stall--; }
        else { tb->m3_rready = 1; if ((rnd() & 3) == 0) m3_stall = (int)rnd_range(0, g_rready_stall_max); }
    }
}

// Handshake fires captured PRE-EDGE (after the clk=0 eval): these are the
// exact valid/ready pairs the RTL commits at the coming posedge.  Reacting
// from post-edge samples miscounts W beats when wready toggles mid-burst
// under heavy load (observed: aggressor write FSM believed done, slave
// parked in S_WR_FILL waiting for a beat -> whole read path wedged).
struct HsFires {
    bool m1_rdone, m1_aw, m1_w, m1_b;
    bool m2_rdone, m2_aw, m2_w, m2_b;
    bool m3_rdone;
    bool inj_done;
};
static HsFires hs;

static void capture_handshakes_preedge() {
    hs.m1_rdone = tb->m1_rvalid && tb->m1_rready && tb->m1_rlast;
    hs.m1_aw    = tb->m1_awvalid && tb->m1_awready;
    hs.m1_w     = tb->m1_wvalid && tb->m1_wready;
    hs.m1_b     = tb->m1_bvalid;
    hs.m2_rdone = tb->m2_rvalid && tb->m2_rready && tb->m2_rlast;
    hs.m2_aw    = tb->m2_awvalid && tb->m2_awready;
    hs.m2_w     = tb->m2_wvalid && tb->m2_wready;
    hs.m2_b     = tb->m2_bvalid;
    hs.m3_rdone = tb->m3_rvalid && tb->m3_rready && tb->m3_rlast;
    hs.inj_done = tb->inj_burst_data_done;
}

static void react_aggressors_post() {
    if (inj_busy && hs.inj_done) { inj_busy = 0; inj_cooldown = rnd_range(4, 32); }
    if (!g_aggr) return;
    if (m1_mode == 1) { if (hs.m1_rdone) { m1_mode = 0; m1_cooldown = rnd_range(4, 24); } }
    else if (m1_mode == 2) {
        if (hs.m1_aw) m1_aw_done = true;
        if (hs.m1_w) { m1_beat++; if (m1_beat > m1_len) m1_w_done = true; }
        if (hs.m1_b) { m1_mode = 0; m1_cooldown = rnd_range(4, 24); }
    }
    if (m2_mode == 1) { if (hs.m2_rdone) { m2_mode = 0; m2_cooldown = rnd_range(4, 24); } }
    else if (m2_mode == 2) {
        if (hs.m2_aw) m2_aw_done = true;
        if (hs.m2_w) { m2_beat++; if (m2_beat > m2_len) m2_w_done = true; }
        if (hs.m2_b) { m2_mode = 0; m2_cooldown = rnd_range(4, 24); }
    }
    if (m3_active && hs.m3_rdone) { m3_active = false; m3_cooldown = rnd_range(1, 8); }
}

// ============================================================
// SS1 read-DQM exposure monitor + phy-level trace.
// Exposure is counted in EVERY mode (the taps are pure observers); the
// poison (evt_read_masked) only fires in ss1r modes.  On the first exposed
// or masked beat, a phy-level window (24 pre + 12 post cycles) is dumped:
// command pins, bank, full A bus, dedicated-DQM pins, DQ-bus value.
// ============================================================
struct PhyRow {
    uint64_t cyc; uint8_t cmd, ba; uint16_t a; uint8_t dqm, oe;
    uint16_t dq; uint8_t exp1, exp2, masked; uint8_t slave_st;
};
static std::deque<PhyRow> phy_buf;
static bool g_phy_fired = false;
static int g_phy_post = 0;

static const char *phy_cmd_name(uint8_t c) {
    switch (c) { case 7: return "NOP "; case 3: return "ACT "; case 5: return "READ";
                 case 4: return "WRIT"; case 2: return "PRE "; case 1: return "REF ";
                 case 0: return "LMR "; default: return "??? "; }
}
static void phy_dump() {
    printf("---- SS1 read-DQM phy window (first exposed/masked beat) ----\n");
    printf("%-10s cmd  ba a[12:11] a       dqm oe dq     exp1 exp2 masked slave\n", "cyc");
    for (const PhyRow &r : phy_buf)
        printf("%-10llu %s %u    %u%u    0x%04x  %u%u  %u  0x%04x   %u    %u     %u     %u\n",
               (unsigned long long)r.cyc, phy_cmd_name(r.cmd), r.ba,
               (r.a >> 12) & 1, (r.a >> 11) & 1, r.a,
               (r.dqm >> 1) & 1, r.dqm & 1, r.oe, r.dq, r.exp1, r.exp2, r.masked,
               r.slave_st);
    printf("---- end phy window ----\n");
}
static void sample_ss1_read_preedge() {
    if (tb->evt_rd_expose_d1) g_ctr.rd_expose_d1++;
    if (tb->evt_rd_expose_d2) g_ctr.rd_expose_d2++;
    if (tb->evt_read_masked) {
        if (g_ctr.read_masked == 0)
            printf("  [SS1-READ] FIRST POISONED READ BEAT at cyc=%llu (inj_busy=%d slave_st=%u)\n",
                   (unsigned long long)cyc, inj_busy, tb->dbg_slave_state);
        g_ctr.read_masked++;
        if (inj_busy) g_ctr.masked_scanout++;
    }
    // phy trace
    PhyRow r;
    r.cyc = cyc; r.cmd = tb->dbg_phy_cmd; r.ba = tb->dbg_phy_ba;
    r.a = tb->dbg_phy_a; r.dqm = tb->dbg_phy_dqm; r.oe = tb->dbg_dq_oe;
    r.dq = tb->dbg_dq_bus; r.exp1 = tb->evt_rd_expose_d1; r.exp2 = tb->evt_rd_expose_d2;
    r.masked = tb->evt_read_masked; r.slave_st = tb->dbg_slave_state;
    phy_buf.push_back(r);
    if (!g_phy_fired) {
        if (phy_buf.size() > 24) phy_buf.pop_front();
        if (tb->evt_rd_expose_d1 || tb->evt_rd_expose_d2 || tb->evt_read_masked) {
            g_phy_fired = true; g_phy_post = 12;
        }
    } else if (g_phy_post > 0) {
        if (--g_phy_post == 0) phy_dump();
    }
}

static void tick(int n = 1) {
    for (int i = 0; i < n; i++) {
        drive_aggressors_pre();
        tb->clk = 0; tb->eval(); sim_time++;
        sample_events_preedge();
        capture_handshakes_preedge();
        tb->clk = 1; tb->eval(); sim_time++;
        cyc++;
        sample_events_postedge();
        react_aggressors_post();
    }
}

// ============================================================
// Backdoor SDRAM access
// ============================================================
static void sdram_write(uint32_t word_addr, uint32_t data) {
    tb->bd_we = 1; tb->bd_addr = word_addr; tb->bd_wdata = data;
    tick(); tb->bd_we = 0;
}
static uint32_t sdram_read(uint32_t word_addr) {
    tb->bd_rd_addr = word_addr; tb->eval(); return tb->bd_rd_data;
}
static uint8_t sdram_read_byte(uint32_t byte_addr) {
    return (sdram_read(byte_addr >> 2) >> ((byte_addr & 3) * 8)) & 0xFF;
}
static void sdram_write_byte(uint32_t byte_addr, uint8_t v) {
    uint32_t w = sdram_read(byte_addr >> 2);
    int sh = (byte_addr & 3) * 8;
    w = (w & ~(0xFFu << sh)) | ((uint32_t)v << sh);
    sdram_write(byte_addr >> 2, w);
}

// ============================================================
// MMIO + ring/DMA submission
// ============================================================
static const uint32_t RING_BYTES = 0x4000;
static std::vector<uint32_t> pending_stream;
static bool g_harness_error = false;
static uint32_t g_queued_desc_bytes = 0;  // kicked but maybe unpublished

static void mmio_write(uint32_t reg, uint32_t val) {
    tb->reg_wr = 1; tb->reg_addr = reg; tb->reg_wdata = val; tick(); tb->reg_wr = 0;
}
static uint32_t mmio_read(uint32_t reg) { tb->reg_addr = reg; tb->eval(); return tb->reg_rdata; }

static void dump_wedge_state(const char *tag) {
    printf("  [WEDGE %s] cyc=%llu arb_state=%u grant=%u active_rd=%u m0_arvalid=%u "
           "slave_state=%u word_busy=%u dma_state=%u fbss=%u gpu_state=%u "
           "m1(m=%d s=%d) m2(m=%d) m3(a=%d s=%d) inj_busy=%d\n",
           tag, (unsigned long long)cyc, tb->dbg_arb_state, tb->dbg_grant,
           tb->dbg_arb_active_rd, tb->dbg_m0_arvalid, tb->dbg_slave_state,
           tb->dbg_word_busy, tb->dbg_dma_state, tb->dbg_fbss, tb->dbg_gpu_state,
           m1_mode, m1_stall, m2_mode, (int)m3_active, m3_stall, inj_busy);
}

static void dump_counters(const char *tag) {
    printf("  [%s] drops=%llu (gpu=%llu) dma_forced=%llu hijackB=%llu hijackT=%llu "
           "ring_words=%llu/%llu hdr=%llu (4C=%llu fence=%llu unk=%llu badsz=%llu)\n",
           tag,
           (unsigned long long)g_ctr.drop_arm, (unsigned long long)g_ctr.drop_arm_gpu,
           (unsigned long long)g_ctr.dma_forced,
           (unsigned long long)g_ctr.hijack_blend, (unsigned long long)g_ctr.hijack_tex,
           (unsigned long long)g_ctr.ring_words, (unsigned long long)g_words_submitted,
           (unsigned long long)g_ctr.hdr_total, (unsigned long long)g_ctr.hdr_4c,
           (unsigned long long)g_ctr.hdr_fence, (unsigned long long)g_ctr.hdr_unknown,
           (unsigned long long)g_ctr.hdr_badsize);
    printf("  [%s] ss1-read: expose_d1=%llu expose_d2=%llu masked=%llu (scanout=%llu)\n",
           tag,
           (unsigned long long)g_ctr.rd_expose_d1, (unsigned long long)g_ctr.rd_expose_d2,
           (unsigned long long)g_ctr.read_masked, (unsigned long long)g_ctr.masked_scanout);
    printf("  [%s] beat-ownership: tex=%llu blend=%llu dma=%llu | swallowed_tex=%llu "
           "masked_blend=%llu misrouted_dma=%llu orphan=%llu len_mismatch=%llu\n",
           tag,
           (unsigned long long)adj_beats[OWN_TEX], (unsigned long long)adj_beats[OWN_BLEND],
           (unsigned long long)adj_beats[OWN_DMA],
           (unsigned long long)adj_swallowed_tex, (unsigned long long)adj_masked_blend,
           (unsigned long long)adj_misrouted_dma, (unsigned long long)adj_orphan,
           (unsigned long long)adj_len_mismatch);
}

static void wait_for_dma_idle(int timeout = 4000000) {
    for (int t = 0; t < timeout; t++) { if ((mmio_read(REG_STATUS) & (1 << 2)) == 0) { g_queued_desc_bytes = 0; return; } tick(); }
    fprintf(stderr, "wait_for_dma_idle timeout (status=0x%08x)\n", mmio_read(REG_STATUS));
    dump_counters("dma-idle-timeout"); dump_wedge_state("dma-idle");
    g_harness_error = true;
}
static void wait_for_desc_slot(int timeout = 4000000) {
    for (int t = 0; t < timeout; t++) { if ((mmio_read(REG_STATUS) & (1 << 6)) == 0) return; tick(); }
    fprintf(stderr, "wait_for_desc_slot timeout (status=0x%08x)\n", mmio_read(REG_STATUS));
    dump_counters("desc-slot-timeout"); dump_wedge_state("desc-slot");
    g_harness_error = true;
}
// Ring overflow guard: published occupancy + queued-desc bytes + new batch
// must fit with margin, or DMA would overwrite unconsumed ring words.
static void wait_for_ring_room(uint32_t new_bytes, int timeout = 8000000) {
    for (int t = 0; t < timeout; t++) {
        uint32_t wr = mmio_read(REG_RING_WRPTR) & 0xFFFF;
        uint32_t rd = mmio_read(REG_RING_RDPTR) & 0xFFFF;
        uint32_t occ = (wr - rd) & (RING_BYTES - 1);
        if (occ + g_queued_desc_bytes + new_bytes <= RING_BYTES - 0x800) return;
        tick();
    }
    fprintf(stderr, "wait_for_ring_room timeout\n");
    dump_counters("ring-room-timeout"); dump_wedge_state("ring-room");
    g_harness_error = true;
}

static void ring_write(uint32_t w) { pending_stream.push_back(w); }
static void ring_cmd(uint8_t cmd, uint32_t payload_words) {
    ring_write(((uint32_t)cmd << 24) | (payload_words & 0x00FFFFFFu));
}

// Rotating staging buffers + desc-slot wait (NOT full-idle wait): keeps a
// descriptor queued nearly continuously so the doorbell DMA contends with
// in-flight translucent rendering — the starvation-override window.
static int g_kick_buf = 0;
static void gpu_kick() {
    if (pending_stream.empty()) return;
    uint32_t words = (uint32_t)pending_stream.size();
    wait_for_ring_room(words * 4u);
    wait_for_desc_slot();          // <=1 desc outstanding -> buf from 4 kicks ago is retired
    uint32_t buf = BATCH_BUFS[g_kick_buf & 3];
    g_kick_buf = (g_kick_buf + 1) & 3;
    uint32_t aw = buf >> 2;
    for (uint32_t x : pending_stream) sdram_write(aw++, x);
    mmio_write(REG_DMA_SRC, buf);
    mmio_write(REG_DMA_LEN, words);
    mmio_write(REG_DMA_KICK, 1);
    g_words_submitted += words;
    g_queued_desc_bytes += words * 4u;
    pending_stream.clear();
}

static uint32_t next_fence = 1;
static bool submit_and_wait(int timeout = 16000000) {
    uint32_t t = next_fence++;
    ring_cmd(0x02, 1); ring_write(t); gpu_kick();
    for (int i = 0; i < timeout; i++) { tick(); if ((int32_t)(tb->fence_reached - t) >= 0) return true; }
    fprintf(stderr, "  FENCE TIMEOUT tok=%u reached=%u dbg_state=%u status=0x%08x\n",
            t, tb->fence_reached, tb->dbg_state, mmio_read(REG_STATUS));
    dump_counters("fence-timeout"); dump_wedge_state("fence");
    g_harness_error = true;
    return false;
}
static void drain_idle() {
    for (int i = 0; i < 4000000; i++) {
        if (!tb->busy && (mmio_read(REG_STATUS) & ((1 << 2) | (1 << 3))) == 0) break;
        tick();
    }
    g_queued_desc_bytes = 0;
}

static void hard_reset() {
    tb->reset_n = 0; tb->reg_wr = 0; tb->bd_we = 0; tb->aggr_en = 0;
    clear_aggr_inputs();
    for (int i = 0; i < 64; i++) tick();
    tb->reset_n = 1;
    for (int i = 0; i < 31200; i++) tick();  // io_sdram power-up + init
}

// Full recovery after a wedge (dropped-rlast arbiter deadlock etc.): reset
// the whole stack and reprogram the GPU.  SDRAM + tb SRAM (transluc LUT)
// contents survive reset, so no re-upload is needed.
static void recover_from_wedge() {
    bool aggr_save = g_aggr; int scan_save = g_scan_mode;
    g_aggr = false; g_scan_mode = 0;
    hard_reset();
    mmio_write(REG_CTRL, 4);                  // ring_reset
    mmio_write(REG_PALOOKUP_BASE, PALOOKUP_BASE);
    mmio_write(REG_RING_WRPTR, 0);
    next_fence = 1;                            // fence_reached reset to 0
    pending_stream.clear();
    g_queued_desc_bytes = 0;
    g_kick_buf = 0;
    g_aggr = aggr_save; tb->aggr_en = aggr_save ? 1 : 0;
    g_scan_mode = scan_save;
}

// ============================================================
// Static assets: transluc LUT, palookup slots, texture
// ============================================================
static std::vector<uint8_t> g_transluc(32768);
static uint8_t g_pal[2][256];              // slot 0 identity, slot 1 inverted (light 0)
static std::vector<uint8_t> g_tex(TEX_W * TEX_H);
static bool g_tex_holes = false;           // 0xFF holes for SKIP_ZERO variant

static void make_transluc_table_avg() {
    for (int s7 = 0; s7 < 128; s7++) {
        int src = s7 << 1;
        for (int dst = 0; dst < 256; dst++)
            g_transluc[s7 * 256 + dst] = (uint8_t)((src + dst) >> 1);
    }
}
static void wait_for_transluc_idle(int timeout = 200000) {
    for (int t = 0; t < timeout; t++) {
        if ((mmio_read(REG_STATUS) & (1 << 3)) == 0) return;
        tick();
    }
    fprintf(stderr, "wait_for_transluc_idle timeout (status=0x%08x)\n", mmio_read(REG_STATUS));
    g_harness_error = true;
}
static void upload_transluc_table() {
    mmio_write(REG_TRANSLUC_ADDR, 0);
    for (size_t i = 0; i < g_transluc.size(); i += 4) {
        uint32_t w = (uint32_t)g_transluc[i]
                   | ((uint32_t)g_transluc[i + 1] << 8)
                   | ((uint32_t)g_transluc[i + 2] << 16)
                   | ((uint32_t)g_transluc[i + 3] << 24);
        wait_for_transluc_idle();
        mmio_write(REG_TRANSLUC_DATA, w);
    }
    wait_for_transluc_idle();
}
static void upload_palookups() {
    for (int i = 0; i < 256; i++) { g_pal[0][i] = (uint8_t)i; g_pal[1][i] = (uint8_t)(255 - i); }
    for (int s = 0; s < 2; s++)
        for (int i = 0; i < 256; i++)
            sdram_write_byte(PALOOKUP_BASE + (uint32_t)s * PALOOKUP_STRIDE + i, g_pal[s][i]);
}
static void upload_texture(bool holes) {
    g_tex_holes = holes;
    for (uint32_t y = 0; y < TEX_H; y++)
        for (uint32_t x = 0; x < TEX_W; x++) {
            uint8_t v = (uint8_t)(1 + ((x * 5 + y * 13) % 0xFD));  // 1..0xFD, never 0/0xFF
            if (holes && (((x >> 2) + (y >> 2)) & 1)) v = 0xFF;    // 4x4 checker holes
            g_tex[y * TEX_W + x] = v;
        }
    for (uint32_t i = 0; i < TEX_W * TEX_H; i += 4) {
        uint32_t w = (uint32_t)g_tex[i] | ((uint32_t)g_tex[i+1] << 8)
                   | ((uint32_t)g_tex[i+2] << 16) | ((uint32_t)g_tex[i+3] << 24);
        sdram_write((TEX_BASE + i) >> 2, w);
    }
    // The GPU tex cache is NOT coherent with backdoor SDRAM writes: flush it
    // (asynchronous; give it a few cycles), exactly as firmware/acceptance do.
    mmio_write(REG_TEX_FLUSH, 1);
    tick(16);
}

// ============================================================
// 0x4C column-list command model
// ============================================================
struct Lane {
    uint32_t fb_addr;    // absolute byte address of first pixel
    uint32_t tex_addr;   // absolute byte address (texture column select)
    uint16_t count;
    uint8_t  cmap, light;
    uint32_t t0, tstep;  // Q16.16
};
struct Cmd {
    uint8_t flags;
    std::vector<Lane> lanes;   // 1..4
};

static void encode_cmd(const Cmd &c) {
    uint32_t n = (uint32_t)c.lanes.size();
    ring_cmd(0x4C, 4 + 5 * n);
    ring_write((n << 28) | ((uint32_t)c.flags << 20));
    ring_write(TEX_W);                                   // tex_width
    ring_write(((TEX_H - 1) << 16) | (TEX_W - 1));       // h_mask | w_mask
    ring_write(FB_W);                                    // fb_step (vertical column)
    for (const Lane &l : c.lanes) {
        ring_write(l.fb_addr);
        ring_write(l.tex_addr);
        ring_write((((uint32_t)l.cmap & 0xF) << 28) | (((uint32_t)l.light & 0x3F) << 16) | l.count);
        ring_write(l.t0);
        ring_write(l.tstep);
    }
}

// CPU reference: acceptance-suite semantics (s=0/sstep=0 forced for 0x4C).
static void ref_apply(const Cmd &c, std::vector<uint8_t> &fb) {
    for (const Lane &l : c.lanes) {
        uint32_t t = l.t0;
        uint32_t fb_off = l.fb_addr - FB_BASE;
        for (uint16_t i = 0; i < l.count; i++) {
            uint32_t t_int = (t >> 16) & (TEX_H - 1);
            uint32_t tex_off = (l.tex_addr - TEX_BASE) + t_int * TEX_W;   // s_int = 0
            uint8_t texel = g_tex[tex_off];
            if ((c.flags & F_SKIP_ZERO) && texel == 0xFF) {
                // skip — FB byte untouched
            } else {
                uint8_t color = texel;
                if (c.flags & F_COLORMAP)
                    color = g_pal[l.cmap & 1][color];    // light 0 rows only
                if (c.flags & F_TRANSLUC) {
                    uint8_t dst = fb[fb_off];
                    color = g_transluc[((uint32_t)(color >> 1) * 256u) + dst];
                }
                fb[fb_off] = color;
            }
            t += l.tstep;
            fb_off += FB_W;
        }
    }
}

// ============================================================
// Frame runner
// ============================================================
struct FrameCfg {
    const char *tag;
    uint32_t ncmds = 24;        // 0x4C commands per frame (4 lanes each)
    uint16_t height = 100;      // column height (pixels)
    uint8_t  flags = F_COLORMAP | F_TRANSLUC;   // 0x41
    uint32_t tstep = 0x8000;    // Q16.16 texture v step
    bool     cmap_alt = false;  // alternate colormap 0/1 per lane
    bool     opaque_interleave = true; // every 4th command opaque control
    bool     adjacent_lanes = true;    // consecutive x per group (Doom shape);
                                       // false = scattered x (per-pixel blend groups)
    uint32_t subbatch = 8;      // commands per DMA kick
    uint32_t seed = 1;
};

struct FrameResult {
    int mismatches = 0;         // whole-FB byte mismatches
    int lanes_missed = 0;       // lanes with >=1 wrong pixel
    int missing_pixels = 0;     // wrong pixels still holding the PREFILL value
    int corrupt_pixels = 0;     // wrong pixels holding something else
    bool fence_ok = true;
    uint64_t frame_cycles = 0;
};

static uint8_t prefill_at(uint32_t off) { return (uint8_t)(0x20 + ((off * 7) & 0x5F)); }

static FrameResult run_frame(const FrameCfg &cfg, int frame_idx, bool verbose_fail) {
    FrameResult res;
    uint64_t cyc0 = cyc;
    rng_state = cfg.seed * 0x9E3779B9u + (uint32_t)frame_idx * 0x85EBCA6Bu + 1u;

    // --- prefill FB (model backdoor word writes + reference mirror) ---
    std::vector<uint8_t> ref(FB_BYTES);
    for (uint32_t off = 0; off < FB_BYTES; off += 4) {
        uint32_t w = 0;
        for (int b = 0; b < 4; b++) { ref[off + b] = prefill_at(off + b); w |= (uint32_t)ref[off + b] << (b * 8); }
        sdram_write((FB_BASE + off) >> 2, w);
    }

    // --- build command list: unique x per column (no overlap) ---
    // adjacent_lanes: each 0x4C group covers 4 CONSECUTIVE x (x0..x0+3,
    // x0 = a shuffled multiple of 4) — the exact Doom fireball shape; the
    // blend unit merges the 4 lanes' same-row pixels into one FB-word group.
    // scattered: every column at a shuffled random x — every pixel its own
    // blend group (max dest-read traffic).
    std::vector<uint16_t> xs;
    if (cfg.adjacent_lanes) {
        for (uint32_t i = 0; i < FB_W / 4; i++) xs.push_back((uint16_t)(i * 4));
    } else {
        for (uint32_t i = 0; i < FB_W; i++) xs.push_back((uint16_t)i);
    }
    for (uint32_t i = (uint32_t)xs.size() - 1; i > 0; i--) { uint32_t j = rnd() % (i + 1); std::swap(xs[i], xs[j]); }

    std::vector<Cmd> cmds;
    uint32_t col = 0;
    for (uint32_t k = 0; k < cfg.ncmds; k++) {
        Cmd c;
        bool opaque_ctrl = cfg.opaque_interleave && ((k & 3) == 3);
        c.flags = opaque_ctrl ? F_COLORMAP : cfg.flags;
        uint16_t H = cfg.height;
        uint32_t y0 = rnd_range(0, FB_H - H);
        uint16_t x_group = cfg.adjacent_lanes ? xs[(col / 4) % xs.size()] : 0;
        for (int ln = 0; ln < 4; ln++) {
            Lane l;
            uint16_t x = cfg.adjacent_lanes ? (uint16_t)(x_group + ln)
                                            : xs[col % xs.size()];
            col++;
            l.fb_addr  = FB_BASE + y0 * FB_W + x;
            l.tex_addr = TEX_BASE + (rnd() & (TEX_W - 1));
            l.count    = H;
            l.cmap     = cfg.cmap_alt ? (uint8_t)(ln & 1) : 0;
            l.light    = 0;
            l.t0       = (rnd() & 63) << 16;
            l.tstep    = cfg.tstep;
            c.lanes.push_back(l);
        }
        cmds.push_back(c);
    }

    // --- submit in sub-batches (encode + ref-apply in the same order) ---
    for (size_t k = 0; k < cmds.size(); k++) {
        encode_cmd(cmds[k]);
        if (((k + 1) % cfg.subbatch) == 0) gpu_kick();
    }
    for (const Cmd &c : cmds) ref_apply(c, ref);
    res.fence_ok = submit_and_wait();
    drain_idle();
    res.frame_cycles = cyc - cyc0;
    if (!res.fence_ok) return res;

    // --- (a) whole-FB byte-exact compare ---
    int shown = 0;
    for (uint32_t off = 0; off < FB_BYTES; off++) {
        uint8_t got = sdram_read_byte(FB_BASE + off);
        if (got != ref[off]) {
            res.mismatches++;
            if (got == prefill_at(off)) res.missing_pixels++; else res.corrupt_pixels++;
            if (verbose_fail && shown < 12) {
                printf("    DIFF @(x=%u y=%u) exp=0x%02x got=0x%02x%s\n",
                       off % FB_W, off / FB_W, ref[off], got,
                       got == prefill_at(off) ? " [=prefill: MISSING]" : "");
                shown++;
            }
        }
    }

    // --- (b) per-lane coverage ---
    if (res.mismatches) {
        for (size_t k = 0; k < cmds.size(); k++) {
            const Cmd &c = cmds[k];
            for (size_t ln = 0; ln < c.lanes.size(); ln++) {
                const Lane &l = c.lanes[ln];
                int bad = 0, first_row = -1, last_row = -1;
                uint32_t off = l.fb_addr - FB_BASE;
                for (uint16_t i = 0; i < l.count; i++, off += FB_W) {
                    uint8_t got = sdram_read_byte(FB_BASE + off);
                    if (got != ref[off]) { bad++; if (first_row < 0) first_row = i; last_row = i; }
                }
                if (bad) {
                    res.lanes_missed++;
                    if (verbose_fail)
                        printf("    LANE MISS cmd=%zu lane=%zu flags=0x%02x x=%u y0=%u H=%u: "
                               "%d/%u wrong (rows %d..%d)\n",
                               k, ln, c.flags, (l.fb_addr - FB_BASE) % FB_W,
                               (l.fb_addr - FB_BASE) / FB_W, l.count, bad, l.count,
                               first_row, last_row);
                }
            }
        }
    }
    return res;
}

// ============================================================
// Phase driver
// ============================================================
static int g_frames_total = 0, g_frames_failed = 0, g_frames_invalid = 0;
static int g_phase_fail = 0;

static bool run_phase(const char *name, const FrameCfg &cfg, int frames,
                      bool aggr, uint32_t intv, int scan_mode, uint32_t scan_phase) {
    g_aggr = aggr; tb->aggr_en = aggr ? 1 : 0;
    g_aggr_intv = intv;
    g_scan_mode = scan_mode; g_scan_phase = scan_phase; scan_line = 0;
    Counters c0 = g_ctr;
    uint64_t sub0 = g_words_submitted;
    uint64_t cyc0 = cyc;
    int fail = 0, invalid = 0;
    for (int f = 0; f < frames; f++) {
        FrameCfg fc = cfg; fc.seed = cfg.seed + (uint32_t)f * 977u;
        FrameResult r = run_frame(fc, f, true);
        g_frames_total++;
        if (!r.fence_ok || g_harness_error) {
            invalid++; g_frames_invalid++;
            printf("  [%s] frame %d INVALID (fence timeout / harness error) — recovering\n", name, f);
            g_harness_error = false;  // continue the campaign; run flagged via counter
            recover_from_wedge();
            g_words_submitted = g_ctr.ring_words;  // realign (queued words died with the wedge)
        } else if (r.mismatches) {
            fail++; g_frames_failed++;
            printf("  [%s] frame %d FAIL: %d byte diffs (%d missing / %d corrupt), %d lanes hit, %llu cyc\n",
                   name, f, r.mismatches, r.missing_pixels, r.corrupt_pixels,
                   r.lanes_missed, (unsigned long long)r.frame_cycles);
            dump_counters("at-fail");
        }
    }
    printf("[%s] %d frames: %d fail, %d invalid  (%llu cycles)\n",
           name, frames, fail, invalid, (unsigned long long)(cyc - cyc0));
    printf("  counters: drops=+%llu (gpu=+%llu) dma_forced=+%llu hijackB=+%llu hijackT=+%llu "
           "ring=+%llu/sub=+%llu hdr: 4C=+%llu fence=+%llu unk=+%llu badsz=+%llu\n",
           (unsigned long long)(g_ctr.drop_arm - c0.drop_arm),
           (unsigned long long)(g_ctr.drop_arm_gpu - c0.drop_arm_gpu),
           (unsigned long long)(g_ctr.dma_forced - c0.dma_forced),
           (unsigned long long)(g_ctr.hijack_blend - c0.hijack_blend),
           (unsigned long long)(g_ctr.hijack_tex - c0.hijack_tex),
           (unsigned long long)(g_ctr.ring_words - c0.ring_words),
           (unsigned long long)(g_words_submitted - sub0),
           (unsigned long long)(g_ctr.hdr_4c - c0.hdr_4c),
           (unsigned long long)(g_ctr.hdr_fence - c0.hdr_fence),
           (unsigned long long)(g_ctr.hdr_unknown - c0.hdr_unknown),
           (unsigned long long)(g_ctr.hdr_badsize - c0.hdr_badsize));
    if (fail || invalid) g_phase_fail++;
    return fail == 0 && invalid == 0;
}

#ifdef GPU_TEST_CB_CHAIN
// =====================================================================
// Truecolor CB blend through the REAL SDRAM chain — the SM64 cloud
// differential.  Byte-exactness of the CB path is already proven on the
// 1-cycle stub (tb_gpu_acceptance truecolor_blend_*); what has NEVER been
// tested is the CB dst read + halfword blended writes against the real
// arbiter/slave/io_sdram — including the skid-full beat-loss window this
// rig's header documents — under contention.  Method: render the identical
// 6-billboard cloud scene (32x32 disc texture with SKIP_ZERO transparent
// surround, const-alpha 240, the exact state the SM64 trace pinned) three
// times: A = aggressors off, B and C = aggressors on.  A==B==C exonerates
// the chain; differences reproduce the artifact and the histograms print
// its geometry (diagonal stripes = per-row phase-drifting periodic loss).
// =====================================================================
static const uint32_t CB_FB_BYTE  = 0x00080000;   // RGB565, 320-px stride
static const uint32_t CB_TEX_BYTE = 0x00070000;   // 32x32 RGB565 disc
static const int CB_W = 176, CB_H = 136;          // compared region

static uint16_t cb_pattern(int x, int y) {
    uint16_t r = (uint16_t)((x * 7 + y * 13) & 0x1F);
    uint16_t g = (uint16_t)((x * 3 + y) & 0x3E);
    uint16_t b = (uint16_t)((x + y * 5) & 0x1F);
    return (uint16_t)((r << 11) | (g << 5) | b);
}

static void cb_emit_state(uint8_t alpha) {
    // 0x4A 17-word wire form (matches tb_gpu_acceptance encode_set_tri_state_wire)
    uint32_t flags = (1u << 5)      /* SPAN_PERSP     */
                   | (1u << 7)      /* TRUECOLOR      */
                   | (1u << 1)      /* BLEND          */
                   | (1u << 2);     /* SKIP_ZERO      */
    uint32_t control = flags & 0xFFu;   // z_mode 0, no mirror, integer Y
    ring_cmd(0x4A, 17);
    ring_write(CB_FB_BYTE);             // fb_base
    ring_write(640);                    // fb_major_step (bytes/row)
    ring_write(2);                      // fb_minor_step (bytes/px)
    ring_write(CB_TEX_BYTE);            // tex_addr
    ring_write(32);                     // tex_width
    ring_write((31u << 16) | 31u);      // h_mask | w_mask
    ring_write(control);
    ring_write(0); ring_write(0);       // clamp s min/max (disabled)
    ring_write(0); ring_write(0);       // clamp t min/max (disabled)
    ring_write(0); ring_write(0); ring_write(0);   // z_base/major/minor
    ring_write((320u << 16) | 0u);      // clip x1|x0
    ring_write((200u << 16) | 0u);      // clip y1|y0
    ring_write(alpha);                  // const_alpha (17th word, BLEND set)
}

static void cb_emit_tri(const int16_t vx[3], const int16_t vy[3],
                        const int32_t s[3], const int32_t t[3]) {
    ring_cmd(0x4B, 14);
    ring_write(((uint32_t)(uint16_t)vy[0] << 16) | (uint16_t)vx[0]);
    ring_write(((uint32_t)(uint16_t)vy[1] << 16) | (uint16_t)vx[1]);
    ring_write(((uint32_t)(uint16_t)vy[2] << 16) | (uint16_t)vx[2]);
    ring_write((uint32_t)s[0]); ring_write((uint32_t)s[1]); ring_write((uint32_t)s[2]);
    ring_write((uint32_t)t[0]); ring_write((uint32_t)t[1]); ring_write((uint32_t)t[2]);
    ring_write(1u << 16); ring_write(1u << 16); ring_write(1u << 16);  // zi = 1.0
    ring_write(63u | (63u << 6) | (63u << 12));                        // full-bright
    ring_write(0);
}

static void cb_emit_quad(int cx, int cy) {
    const int H = 32;                                  // half-size (64x64 quad)
    const int32_t S1 = 32 << 16;
    int16_t x0 = (int16_t)((cx - H) * 16), x1 = (int16_t)((cx + H) * 16);
    int16_t y0 = (int16_t)(cy - H),        y1 = (int16_t)(cy + H);
    {
        int16_t vx[3] = { x0, x1, x0 }, vy[3] = { y0, y0, y1 };
        int32_t s[3] = { 0, S1, 0 },    t[3] = { 0, 0, S1 };
        cb_emit_tri(vx, vy, s, t);
    }
    {
        int16_t vx[3] = { x1, x1, x0 }, vy[3] = { y0, y1, y1 };
        int32_t s[3] = { S1, S1, 0 },   t[3] = { 0, S1, S1 };
        cb_emit_tri(vx, vy, s, t);
    }
}

static bool cb_run_pass(std::vector<uint16_t> &out, bool aggr) {
    for (int y = 0; y < CB_H; y++)
        for (int x = 0; x < 320; x += 2) {
            uint32_t byte = CB_FB_BYTE + (uint32_t)y * 640u + (uint32_t)x * 2u;
            sdram_write(byte >> 2, (uint32_t)cb_pattern(x, y)
                                 | ((uint32_t)cb_pattern(x + 1, y) << 16));
        }
    for (int ty = 0; ty < 32; ty++)
        for (int tx = 0; tx < 32; tx += 2) {
            int dx0 = tx - 16, dy = ty - 16, dx1 = tx + 1 - 16;
            uint16_t p0 = (dx0 * dx0 + dy * dy <= 196) ? 0xFFFF : 0x0000;
            uint16_t p1 = (dx1 * dx1 + dy * dy <= 196) ? 0xFFFF : 0x0000;
            uint32_t byte = CB_TEX_BYTE + (uint32_t)(ty * 32 + tx) * 2u;
            sdram_write(byte >> 2, (uint32_t)p0 | ((uint32_t)p1 << 16));
        }

    g_aggr = aggr; tb->aggr_en = aggr ? 1 : 0;
    if (aggr) g_aggr_intv = 8;                        // intense contention

    cb_emit_state(240);
    // Flower cloud: center + 5-billboard ring (radius 33), heavy overlap.
    static const int off[6][2] = { {0,0}, {33,0}, {10,31}, {-27,19}, {-27,-19}, {10,-31} };
    for (int i = 0; i < 6; i++)
        cb_emit_quad(88 + off[i][0], 68 + off[i][1]);

    bool ok = submit_and_wait();
    g_aggr = false; tb->aggr_en = 0;
    if (!ok) return false;
    drain_idle();

    out.assign((size_t)CB_W * CB_H, 0);
    for (int y = 0; y < CB_H; y++)
        for (int x = 0; x < CB_W; x++) {
            uint32_t byte = CB_FB_BYTE + (uint32_t)y * 640u + (uint32_t)x * 2u;
            uint32_t w = sdram_read(byte >> 2);
            out[(size_t)y * CB_W + x] = (byte & 2) ? (uint16_t)(w >> 16) : (uint16_t)w;
        }
    return true;
}

static int test_cb_chain() {
    printf("TEST cb_chain_cloud_differential\n");
    std::vector<uint16_t> A, B, C;
    if (!cb_run_pass(A, false)) { printf("  FAIL: quiet pass wedged\n"); return 1; }
    if (!cb_run_pass(B, true))  { printf("  FAIL: aggr pass 1 wedged\n"); return 1; }
    if (!cb_run_pass(C, true))  { printf("  FAIL: aggr pass 2 wedged\n"); return 1; }

    // Sanity: the quiet pass must actually have blended (not all-pattern).
    int touched = 0;
    for (int y = 0; y < CB_H; y++)
        for (int x = 0; x < CB_W; x++)
            if (A[(size_t)y * CB_W + x] != cb_pattern(x, y)) touched++;
    printf("  quiet pass blended %d px\n", touched);
    if (touched < 5000) { printf("  FAIL: scene did not render\n"); return 1; }

    int ab = 0, bc = 0;
    static int colbad[CB_W], rowbad[CB_H];
    memset(colbad, 0, sizeof colbad); memset(rowbad, 0, sizeof rowbad);
    for (int y = 0; y < CB_H; y++)
        for (int x = 0; x < CB_W; x++) {
            size_t i = (size_t)y * CB_W + x;
            if (A[i] != B[i]) {
                if (ab < 8)
                    printf("  A!=B (%d,%d) quiet=%04X aggr=%04X pat=%04X\n",
                           x, y, A[i], B[i], cb_pattern(x, y));
                ab++; colbad[x]++; rowbad[y]++;
            }
            if (B[i] != C[i]) bc++;
        }
    if (ab || bc) {
        printf("  DIFFS: quiet-vs-aggr=%d  aggr-vs-aggr=%d (%s)\n", ab, bc,
               bc ? "NONDETERMINISTIC" : "deterministic under contention");
        printf("  per-column:");
        for (int x = 0; x < CB_W; x++) if (colbad[x]) printf(" c%d=%d", x, colbad[x]);
        printf("\n  per-row:");
        for (int y = 0; y < CB_H; y++) if (rowbad[y]) printf(" r%d=%d", y, rowbad[y]);
        printf("\n  FAIL cb_chain_cloud_differential\n");
        return 1;
    }
    printf("  PASS cb_chain_cloud_differential (chain exonerated under aggressors)\n");
    return 0;
}
#endif  /* GPU_TEST_CB_CHAIN */

int main(int argc, char **argv) {
    setvbuf(stdout, NULL, _IONBF, 0);
    Verilated::commandArgs(argc, argv);
    tb = new Vtb_gpu_transluc;

    std::string mode = (argc > 1) ? argv[1] : "all";
    int soak_frames = (argc > 2) ? atoi(argv[2]) : 300;

    // "ss1" / "ss1-<mode>": run with the SDRAM model taking its write mask
    // from A[12:11] (SuperStation One module wiring) instead of the
    // dedicated DQM pins — end-to-end check of io_sdram's mask mirror.
    bool ss1 = false; int ss1_read = 0;   // 0=off, else model-time mask delay
    if (mode.rfind("ss1r2", 0) == 0) {
        ss1 = true; ss1_read = 2;
        mode = (mode.size() > 6 && mode[5] == '-') ? mode.substr(6) : "all";
    } else if (mode.rfind("ss1r", 0) == 0) {
        ss1 = true; ss1_read = 1;             // silicon-equivalent alignment
        mode = (mode.size() > 5 && mode[4] == '-') ? mode.substr(5) : "all";
    } else if (mode.rfind("ss1", 0) == 0) {
        ss1 = true;
        mode = (mode.size() > 4 && mode[3] == '-') ? mode.substr(4) : "all";
    }
    tb->ss1_dqm_mode = ss1 ? 1 : 0;
    tb->ss1_read_dqm = ss1_read ? 1 : 0;
    tb->ss1_read_delay = ss1_read ? ss1_read : 1;

    printf("================================================================\n");
    printf(" MiSTer translucent-column span-dropout repro rig\n");
    printf(" gpu_core(+INCLUDE_TRANSLUC, emu.sv params) -> arbiter -> slave\n");
    printf(" -> io_sdram(mister) -> sdram_model_full ; 0x4C columns, byte-exact\n");
    printf(" mode=%s  dqm=%s  read-dqm=%s\n", mode.c_str(),
           ss1 ? "SS1 (A[12:11] mask mirror)" : "dedicated pins",
           ss1_read == 0 ? "off" : ss1_read == 1 ? "ON delay=1 (silicon-aligned)"
                                                 : "ON delay=2 (bracket)");
    printf("================================================================\n");

    hard_reset();
    mmio_write(REG_CTRL, 4);                  // ring_reset
    mmio_write(REG_PALOOKUP_BASE, PALOOKUP_BASE);
    mmio_write(REG_RING_WRPTR, 0);

    printf("[init] uploading transluc LUT (32KB via MMIO), palookups, texture...\n");
    make_transluc_table_avg();
    upload_transluc_table();
    upload_palookups();
    upload_texture(false);
    printf("[init] done at cyc=%llu\n", (unsigned long long)cyc);

#ifdef GPU_TEST_CB_CHAIN
    if (mode == "cb" || mode == "all") {
        int rc = test_cb_chain();
        delete tb;
        return rc;
    }
#endif

    FrameCfg base;
    base.ncmds = 24; base.height = 100; base.flags = F_COLORMAP | F_TRANSLUC;
    base.tstep = 0x8000; base.subbatch = 8; base.seed = 0x5EED0001u;

    if (mode == "adjudicate") {
        // Focused adjudication of the DMA-OWNS-R-WHILE-TEX-INFLIGHT anomaly:
        // run the sanity config (it fires deterministically at ~cyc 99k, no
        // aggressors needed) with the cycle-window trace armed, and let the
        // beat-ownership tracker classify every M0 R beat.  Verdict:
        // swallowed_tex==0 -> the anomaly is a STALE tex_m0_in_flight flag
        // (its clear is gated by blend_owns_m0), not beat misrouting.
        g_trace_arm = true;
        g_trace_trigger = (argc > 2 && !strcmp(argv[2], "orphan")) ? 1 : 0;
        FrameCfg cfg = base; cfg.tag = "adjudicate";
        run_phase("ADJ-sanity", cfg, 1, false, 40, 0, 0);
        printf("\n[ADJUDICATION] hijackT rising edges: %llu\n",
               (unsigned long long)g_ctr.hijack_tex);
        printf("[ADJUDICATION] beats: tex=%llu blend=%llu dma=%llu\n",
               (unsigned long long)adj_beats[OWN_TEX],
               (unsigned long long)adj_beats[OWN_BLEND],
               (unsigned long long)adj_beats[OWN_DMA]);
        printf("[ADJUDICATION] swallowed_tex=%llu masked_blend=%llu misrouted_dma=%llu "
               "orphan=%llu len_mismatch=%llu\n",
               (unsigned long long)adj_swallowed_tex,
               (unsigned long long)adj_masked_blend,
               (unsigned long long)adj_misrouted_dma,
               (unsigned long long)adj_orphan,
               (unsigned long long)adj_len_mismatch);
        // Orphan beats are PHANTOM TEX READS: a pending tex AR is latched by
        // the arbiter/slave in the same window the doorbell DMA pops its next
        // descriptor and re-takes dma_owns_ar, so the returning m_rd_arready
        // is masked away from the tex cache (tex_axi_arready gate).  The
        // slave executes the read anyway; the beats drain unconsumed (M0
        // rready=1, DMA not yet in S_R, blend idle) and tex re-issues.
        // Data-safe ONLY because arbiter+slave serialize reads (DMA cannot
        // reach S_R while phantom beats return).  Misrouting counters are
        // the actual corruption predicates and must be zero.
        bool clean = !adj_swallowed_tex && !adj_masked_blend && !adj_misrouted_dma
                  && !adj_len_mismatch && !g_frames_failed;
        printf("[ADJUDICATION] phantom tex reads (orphan beats / unhandshaked AR): %llu beats\n",
               (unsigned long long)adj_orphan);
        printf("[ADJUDICATION] VERDICT: %s\n",
               clean ? "DATA-SAFE — hijackT = tex_m0_in_flight bookkeeping on RAW "
                       "m_rd_arready/rlast (false set/clear on DMA handshakes); "
                       "orphans = phantom tex reads (AR acceptance masked by "
                       "ownership-mux flip, slave serialization prevents capture "
                       "by DMA/blend); ZERO beats misrouted"
                     : "REAL MISROUTING — see counters above");
        delete tb; return clean ? 0 : 1;
    }

    if (mode == "all" || mode == "sanity") {
        // P0: reference-model sanity — no contention at all.
        FrameCfg cfg = base; cfg.tag = "sanity"; cfg.opaque_interleave = true;
        run_phase("P0-sanity", cfg, 3, false, 40, 0, 0);
    }

    if (mode == "all") {
        // P1: opaque control under full stress (HW says opaque is perfect).
        FrameCfg cfg = base; cfg.tag = "opaque"; cfg.flags = F_COLORMAP;
        cfg.height = 168; cfg.opaque_interleave = false;
        run_phase("P1-opaque-stress", cfg, 4, true, 8, 2, 0);

        // P2: transluc sweep — aggressor intensity x scanout mode/phase x height.
        static const uint32_t intvs[] = {40, 16, 8};
        static const uint16_t heights[] = {50, 100, 168};
        struct ScanCfg { int mode; uint32_t phase; const char *n; };
        static const ScanCfg scans[] = { {1, 0, "rand"}, {2, 0, "line-p0"}, {2, 1586, "line-p1586"} };
        for (uint32_t iv : intvs)
            for (const ScanCfg &sc : scans)
                for (uint16_t h : heights) {
                    char nm[96];
                    snprintf(nm, sizeof nm, "P2 iv=%u scan=%s H=%u", iv, sc.n, h);
                    FrameCfg cfg = base; cfg.tag = "sweep"; cfg.height = h;
                    cfg.seed = 0xA0000000u + iv * 131u + h * 7u + sc.mode * 3u + sc.phase;
                    run_phase(nm, cfg, 2, true, iv, sc.mode, sc.phase);
                }

        // P2b: scattered lanes (every pixel its own blend group = max
        // dest-read traffic; the non-Doom-shaped stress variant).
        {
            FrameCfg cfgS = base; cfgS.tag = "scatter"; cfgS.height = 168;
            cfgS.adjacent_lanes = false; cfgS.seed = 0xA5000001u;
            run_phase("P2b-scatter iv=8", cfgS, 4, true, 8, 2, 0);
        }

        // P3: DMA-starvation targeting — tall columns, miss-heavy tstep,
        // alternating cmaps, big frames, max descriptor pressure.
        FrameCfg cfg3 = base; cfg3.tag = "starve"; cfg3.ncmds = 40; cfg3.height = 168;
        cfg3.tstep = 0x18000; cfg3.cmap_alt = true; cfg3.subbatch = 5;
        cfg3.seed = 0xB0000001u;
        run_phase("P3-starve", cfg3, 10, true, 8, 2, 0);

        // P4: SKIP_ZERO variant (flags 0x45) with 0xFF texture holes.
        upload_texture(true);
        FrameCfg cfg4 = base; cfg4.tag = "skipzero";
        cfg4.flags = F_COLORMAP | F_SKIP_ZERO | F_TRANSLUC;
        cfg4.seed = 0xC0000001u;
        run_phase("P4-skipzero", cfg4, 5, true, 16, 2, 0);
        upload_texture(false);

        // P4b: STALL-GRIND (documentation phase, outside the verdict).
        // 0-10 cycle rready stalls on M1/M3 exceed the slave's 3-deep R
        // chain: beats DROP for those masters, rlast is lost and the
        // arbiter read grant wedges (observed: status=0x57, DMA parked in
        // S_AR, desc FIFO full).  The GPU can never arm this (no rready);
        // drop_arm_gpu stays in the global verdict.  Frames here are
        // expected to wedge; each recovers via full reset.
        {
            printf("[P4b-stallgrind] 0-10 cycle rready stalls (EXPECTED to wedge; "
                   "documents the drop-arm invariant)\n");
            g_rready_stall_max = 10;
            int inv_before = g_frames_invalid, fail_before = g_frames_failed;
            uint64_t drops_before = g_ctr.drop_arm;
            FrameCfg cfgS = base; cfgS.tag = "stallgrind"; cfgS.height = 168;
            cfgS.seed = 0xE0000001u;
            run_phase("P4b-stallgrind", cfgS, 3, true, 8, 2, 0);
            printf("[P4b-stallgrind] drops armed: +%llu; wedged frames: %d "
                   "(excluded from verdict; drop_arm_gpu still global)\n",
                   (unsigned long long)(g_ctr.drop_arm - drops_before),
                   g_frames_invalid - inv_before);
            // Exclude this documentation phase from the pass/fail verdict.
            g_frames_invalid = inv_before;
            g_frames_failed  = fail_before;
            g_rready_stall_max = 2;
            recover_from_wedge();
            g_words_submitted = g_ctr.ring_words;
        }
    }

    if (mode == "all" || mode == "soak") {
        // P5: long soak at the most starve-prone configuration.
        FrameCfg cfg = base; cfg.tag = "soak"; cfg.ncmds = 40; cfg.height = 168;
        cfg.tstep = 0x18000; cfg.cmap_alt = true; cfg.subbatch = 5;
        cfg.seed = 0xD0000001u;
        int frames = (mode == "soak") ? soak_frames : (soak_frames < 100 ? soak_frames : 100);
        printf("[P5-soak] %d frames...\n", frames);
        g_aggr = true; tb->aggr_en = 1; g_aggr_intv = 8;
        g_scan_mode = 2; g_scan_phase = 0; scan_line = 0;
        Counters c0 = g_ctr;
        int fail = 0, invalid = 0;
        for (int f = 0; f < frames; f++) {
            FrameCfg fc = cfg; fc.seed = cfg.seed + (uint32_t)f * 977u;
            FrameResult r = run_frame(fc, f, true);
            g_frames_total++;
            if (!r.fence_ok || g_harness_error) {
                invalid++; g_frames_invalid++; g_harness_error = false;
                printf("  [P5-soak] frame %d INVALID — recovering\n", f);
                recover_from_wedge();
                g_words_submitted = g_ctr.ring_words;
            } else if (r.mismatches) {
                fail++; g_frames_failed++;
                printf("  [P5-soak] frame %d FAIL: %d diffs (%d missing/%d corrupt) %d lanes\n",
                       f, r.mismatches, r.missing_pixels, r.corrupt_pixels, r.lanes_missed);
                dump_counters("at-fail");
            }
            if ((f % 25) == 24)
                printf("  [P5-soak] %d/%d frames, cyc=%llu, fails=%d\n",
                       f + 1, frames, (unsigned long long)cyc, fail);
        }
        printf("[P5-soak] %d frames: %d fail, %d invalid\n", frames, fail, invalid);
        if (fail || invalid) g_phase_fail++;
        (void)c0;
    }

    printf("\n================================================================\n");
    printf("TOTALS: %d frames — %d FAILED, %d INVALID\n",
           g_frames_total, g_frames_failed, g_frames_invalid);
    dump_counters("final");
    if (g_ctr.drop_arm_gpu)
        printf("*** STRUCTURAL: slave R-chain drop armed while GPU granted (%llu) ***\n",
               (unsigned long long)g_ctr.drop_arm_gpu);
    if (g_ctr.hdr_unknown || g_ctr.hdr_badsize)
        printf("*** RING DESYNC EVIDENCE: unknown-opcode=%llu bad-size=%llu headers ***\n",
               (unsigned long long)g_ctr.hdr_unknown, (unsigned long long)g_ctr.hdr_badsize);
    if (g_ctr.ring_words != g_words_submitted)
        printf("*** RING WORD MISMATCH: captured=%llu submitted=%llu ***\n",
               (unsigned long long)g_ctr.ring_words, (unsigned long long)g_words_submitted);
    printf("total cycles: %llu\n", (unsigned long long)cyc);
    printf("================================================================\n");

    if (adj_swallowed_tex || adj_masked_blend || adj_misrouted_dma || adj_len_mismatch)
        printf("*** BEAT MISROUTING: swallowed_tex=%llu masked_blend=%llu misrouted_dma=%llu len=%llu ***\n",
               (unsigned long long)adj_swallowed_tex, (unsigned long long)adj_masked_blend,
               (unsigned long long)adj_misrouted_dma, (unsigned long long)adj_len_mismatch);
    if (g_frames_failed || g_ctr.drop_arm_gpu || g_ctr.hdr_unknown || g_ctr.hdr_badsize
        || adj_swallowed_tex || adj_masked_blend || adj_misrouted_dma || adj_len_mismatch) {
        printf("RESULT: FAIL — repro / structural anomaly (see above)\n");
        delete tb; return 1;
    }
    if (g_frames_invalid) {
        printf("RESULT: INVALID — harness timeouts occurred; tune aggressors\n");
        delete tb; return 2;
    }
    printf("RESULT: PASS — translucent columns byte-exact under all swept contention\n");
    delete tb;
    return 0;
}
