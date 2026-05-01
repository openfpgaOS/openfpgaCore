//
// C++ harness for tb_arbiter — probes whether axi_sdram_arbiter's
// "m_arvalid before m_awvalid" priority at ST_IDLE can starve the
// GPU's own write channel under continuous read pressure.
//
// Background: gpu_core merges its tex/blend reads and framebuffer
// writes onto a single AXI master (M0).  Live traces from Duke3D
// hangs show fbss=FBSS_FLUSH_W_RSP (state 2) sticky with
// m_wr_inflight = 0/1 — i.e. an AW that never gets a B response.
// Hypothesis: every time arb_state returns to ST_IDLE, m0_arvalid
// is re-asserted by tex_cache port-A/B and wins again over a queued
// m0_awvalid, so the AW never gets a grant.
//

#include "Vtb_arbiter.h"
#include "Vtb_arbiter___024root.h"
#include <verilated.h>
#include <cstdio>
#include <cstdint>
#include <cstring>
#include "liveness_watchdog.h"

static Vtb_arbiter *tb;
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

static void clear_all() {
    tb->m0_arvalid = 0; tb->m0_awvalid = 0; tb->m0_wvalid = 0;
    tb->m0_araddr = 0;  tb->m0_arlen = 0;
    tb->m0_awaddr = 0;  tb->m0_awlen = 0;
    tb->m0_wdata = 0;   tb->m0_wstrb = 0xF; tb->m0_wlast = 0;

    tb->m1_arvalid = 0; tb->m1_awvalid = 0; tb->m1_wvalid = 0; tb->m1_rready = 1;
    tb->m1_araddr = 0;  tb->m1_arlen = 0;
    tb->m1_awaddr = 0;  tb->m1_awlen = 0;
    tb->m1_wdata = 0;   tb->m1_wstrb = 0xF; tb->m1_wlast = 0;

    tb->m2_arvalid = 0; tb->m2_araddr = 0; tb->m2_arlen = 0;
    tb->m3_arvalid = 0; tb->m3_araddr = 0; tb->m3_arlen = 0;
}

static void reset_sequence() {
    tb->reset_n = 0;
    clear_all();
    for (int i = 0; i < 8; i++) tick();
    tb->reset_n = 1;
    for (int i = 0; i < 4; i++) tick();
}

static void check(const char *name, bool ok, const char *msg = nullptr) {
    if (ok) {
        passes++;
        printf("  PASS: %s\n", name);
    } else {
        fails++;
        printf("  FAIL: %s%s%s\n", name,
               msg ? " — " : "", msg ? msg : "");
    }
}

// AXI handshake helper — captures (valid && ready) BEFORE the posedge
// so deassertion in the next iteration drops valid one cycle AFTER the
// slave's FF latched the request.  Doing this post-tick deasserts a
// cycle too early — the slave never sees valid=1 at its sample edge.
static inline bool handshake(uint8_t valid, uint8_t ready) {
    return (valid && ready);
}

// ====================================================================
// Test 1: M0 read pressure starves M0 write?
//
// Drive m0_arvalid continuously (re-assert immediately after each AR
// handshake) and assert m0_awvalid concurrently.  Run for many
// cycles and check whether the AW ever gets a grant.
//
// Expected (current arbiter): AW never wins — every IDLE cycle picks
// m0_arvalid first because it's checked at line 187 before line 192
// in axi_sdram_arbiter.v.
// ====================================================================
static void test_read_starves_write() {
    printf("\n=== Test 1: M0 read pressure starves M0 write ===\n");
    reset_sequence();

    // Steady AR stream, single-beat reads (arlen=0).
    tb->m0_arvalid = 1;
    tb->m0_araddr  = 0x10000000;
    tb->m0_arlen   = 0;

    // Persistent AW request alongside, single-beat write.
    tb->m0_awvalid = 1;
    tb->m0_awaddr  = 0x20000000;
    tb->m0_awlen   = 0;
    tb->m0_wvalid  = 1;
    tb->m0_wdata   = 0xCAFEF00D;
    tb->m0_wstrb   = 0xF;
    tb->m0_wlast   = 1;

    int aw_grants = 0;
    int ar_grants = 0;
    int b_responses = 0;
    int r_lasts = 0;
    bool prev_in_idle = false;

    const int CYCLES = 600;
    for (int i = 0; i < CYCLES; i++) {
        tb->eval();
        // Count grants by watching the IDLE→non-IDLE transition.
        bool in_idle = (tb->dbg_arb_state == 0);
        if (prev_in_idle && !in_idle) {
            if (tb->dbg_arb_state == 1) ar_grants++;
            else if (tb->dbg_arb_state == 2) aw_grants++;
        }
        prev_in_idle = in_idle;
        if (tb->dbg_s_bvalid) b_responses++;
        if (tb->dbg_s_rvalid && tb->dbg_s_rlast) r_lasts++;

        // Sample handshakes BEFORE the posedge — deassertion happens
        // post-tick, so the slave's FF still latches valid=1 at its
        // sample edge.
        bool ar_hs = handshake(tb->m0_arvalid, tb->m0_arready);
        bool aw_hs = handshake(tb->m0_awvalid, tb->m0_awready);
        bool w_hs  = handshake(tb->m0_wvalid,  tb->m0_wready);

        tick();

        // AR re-arm — drop for one cycle to model tex_cache's brief
        // miss-then-refill gap.  If the arbiter starves AW even WITH
        // these gaps, the bug is structural.
        if (ar_hs) tb->m0_arvalid = 0;
        else if (!tb->m0_arvalid) tb->m0_arvalid = 1;

        if (aw_hs) tb->m0_awvalid = 0;
        if (w_hs)  tb->m0_wvalid  = 0;
    }

    printf("  cycles=%d  AR-grants=%d  AW-grants=%d  R-lasts=%d  B-rsps=%d\n",
           CYCLES, ar_grants, aw_grants, r_lasts, b_responses);

    check("AW eventually granted under steady AR pressure",
          aw_grants >= 1,
          "writes never landed despite AW held high — read-starves-write confirmed");
    check("B response observed",
          b_responses >= 1,
          "no B even though AW held — write starved");
}

// ====================================================================
// Test 2: Same as Test 1 but with AR held continuously (never drops).
//
// Worst case: tex_cache's AR is back-to-back with no break.  Confirms
// the arbiter's behavior when AR truly never deasserts — does the
// arbiter hold ST_RD forever?  (No, single-outstanding releases on
// rlast — but the next IDLE cycle picks AR again.)
// ====================================================================
static void test_continuous_ar_starves_aw() {
    printf("\n=== Test 2: Continuous AR (held high every cycle) ===\n");
    reset_sequence();

    tb->m0_arvalid = 1;
    tb->m0_araddr  = 0x10000000;
    tb->m0_arlen   = 0;

    tb->m0_awvalid = 1;
    tb->m0_awaddr  = 0x20000000;
    tb->m0_awlen   = 0;
    tb->m0_wvalid  = 1;
    tb->m0_wdata   = 0xCAFEF00D;
    tb->m0_wstrb   = 0xF;
    tb->m0_wlast   = 1;

    int aw_grants = 0;
    int ar_grants = 0;
    bool prev_in_idle = false;

    const int CYCLES = 600;
    for (int i = 0; i < CYCLES; i++) {
        tb->eval();
        bool in_idle = (tb->dbg_arb_state == 0);
        if (prev_in_idle && !in_idle) {
            if (tb->dbg_arb_state == 1) ar_grants++;
            else if (tb->dbg_arb_state == 2) aw_grants++;
        }
        prev_in_idle = in_idle;
        bool aw_hs = handshake(tb->m0_awvalid, tb->m0_awready);
        bool w_hs  = handshake(tb->m0_wvalid,  tb->m0_wready);
        tick();
        // Hold AR high indefinitely — never drop it.
        tb->m0_arvalid = 1;
        if (aw_hs) tb->m0_awvalid = 0;
        if (w_hs)  tb->m0_wvalid  = 0;
    }

    printf("  cycles=%d  AR-grants=%d  AW-grants=%d\n",
           CYCLES, ar_grants, aw_grants);
    check("AW eventually wins with continuous AR (current arbiter expected to FAIL)",
          aw_grants >= 1,
          "AW starved indefinitely — read-before-write priority bug");
}

// ====================================================================
// Test 3: Sanity — fixed priority M0>M1>M3 when no M0 pressure.
// ====================================================================
static void test_priority_chain() {
    printf("\n=== Test 3: Priority chain M0 > M1 > M3 ===\n");
    reset_sequence();

    // M0 idle; M1 + M3 both want a read.  M1 should win first.
    tb->m1_arvalid = 1; tb->m1_araddr = 0x30000000; tb->m1_arlen = 0;
    tb->m3_arvalid = 1; tb->m3_araddr = 0x40000000; tb->m3_arlen = 0;

    int first_grant = -1;
    for (int i = 0; i < 50; i++) {
        tb->eval();
        if (tb->dbg_arb_state != 0 && first_grant < 0) {
            first_grant = tb->dbg_grant;
        }
        bool m1_ar_hs = handshake(tb->m1_arvalid, tb->m1_arready);
        bool m3_ar_hs = handshake(tb->m3_arvalid, tb->m3_arready);
        tick();
        if (m1_ar_hs) tb->m1_arvalid = 0;
        if (m3_ar_hs) tb->m3_arvalid = 0;
    }

    char msg[128];
    snprintf(msg, sizeof(msg), "first granted master = %d (expected 1)", first_grant);
    check("M1 wins over M3 when both request", first_grant == 1, msg);
}

// ====================================================================
// Test 4: M0 AW alone (no AR competing) — does it eventually land?
// Sanity: confirms the slave-stub itself is wired correctly.
// ====================================================================
static void test_aw_alone() {
    printf("\n=== Test 4: M0 AW alone, no AR competition ===\n");
    reset_sequence();

    tb->m0_awvalid = 1;
    tb->m0_awaddr  = 0x20000000;
    tb->m0_awlen   = 0;
    tb->m0_wvalid  = 1;
    tb->m0_wdata   = 0xDEADBEEF;
    tb->m0_wstrb   = 0xF;
    tb->m0_wlast   = 1;

    int b_count = 0;
    for (int i = 0; i < 100; i++) {
        tb->eval();
        if (tb->dbg_s_bvalid) b_count++;
        bool aw_hs = handshake(tb->m0_awvalid, tb->m0_awready);
        bool w_hs  = handshake(tb->m0_wvalid,  tb->m0_wready);
        tick();
        if (aw_hs) tb->m0_awvalid = 0;
        if (w_hs)  tb->m0_wvalid  = 0;
    }

    check("AW alone gets a B response", b_count >= 1);
}

// ====================================================================
// Test 5: M0 AR/AW round-robin under continuous mixed pressure.
//
// Drive BOTH AR and AW continuously, re-arming each handshake.  The
// arbiter's M0 round-robin should produce ~1:1 ratio — symmetric,
// no AR or AW bias.
//
// Verifies the design hits the right point on the priority spectrum:
//   - Strict AR-prefer would give AW=0 (starves writes)
//   - Strict AW-prefer would give AR=0 (starves reads, the bug that
//     collapsed Duke3D to ~3 fps)
//   - 1:1 round-robin gives AR ≈ AW with both > 0
// ====================================================================
static void test_m0_fairness_ratio() {
    printf("\n=== Test 5: M0 AR/AW round-robin (continuous mixed) ===\n");
    reset_sequence();

    tb->m0_arvalid = 1;
    tb->m0_araddr  = 0x10000000;
    tb->m0_arlen   = 0;

    tb->m0_awvalid = 1;
    tb->m0_awaddr  = 0x20000000;
    tb->m0_awlen   = 0;
    tb->m0_wvalid  = 1;
    tb->m0_wdata   = 0xCAFEF00D;
    tb->m0_wstrb   = 0xF;
    tb->m0_wlast   = 1;

    int aw_grants = 0;
    int ar_grants = 0;
    bool prev_in_idle = false;

    const int CYCLES = 600;
    for (int i = 0; i < CYCLES; i++) {
        tb->eval();
        bool in_idle = (tb->dbg_arb_state == 0);
        if (prev_in_idle && !in_idle) {
            if (tb->dbg_arb_state == 1) ar_grants++;
            else if (tb->dbg_arb_state == 2) aw_grants++;
        }
        prev_in_idle = in_idle;

        bool ar_hs = handshake(tb->m0_arvalid, tb->m0_arready);
        bool aw_hs = handshake(tb->m0_awvalid, tb->m0_awready);
        bool w_hs  = handshake(tb->m0_wvalid,  tb->m0_wready);
        tick();

        // Re-arm AR/AW after each handshake — both stay continuously
        // pending so the fairness counter dictates the ratio.
        if (ar_hs) tb->m0_arvalid = 0;
        else if (!tb->m0_arvalid) tb->m0_arvalid = 1;

        if (aw_hs) {
            tb->m0_awvalid = 0;
            tb->m0_wvalid  = 0;
        } else if (!tb->m0_awvalid) {
            tb->m0_awvalid = 1;
            tb->m0_wvalid  = 1;
        }
    }

    int total = ar_grants + aw_grants;
    double ratio = (aw_grants > 0) ? (double)ar_grants / (double)aw_grants : 0.0;
    printf("  cycles=%d  AR=%d  AW=%d  ratio=%.2f (target ~1.0, pure round-robin)\n",
           CYCLES, ar_grants, aw_grants, ratio);

    check("AR grants > 0 (reads not starved)",  ar_grants > 0);
    check("AW grants > 0 (writes not starved)", aw_grants > 0);
    // Round-robin → ratio ≈ 1.  Allow 0.7–1.5 for startup transient.
    char msg[128];
    snprintf(msg, sizeof(msg), "ratio=%.2f outside 0.7..1.5 window (RR drift?)", ratio);
    check("AR:AW ratio in round-robin window", ratio >= 0.7 && ratio <= 1.5, msg);
    (void)total;
}

// ====================================================================
// Test 6: Multi-master continuous load — Duke3D-shape stress.
//
// Drives all four masters concurrently with realistic-ish patterns:
//   M0 AR: continuous (mimics tex_cache miss stream)
//   M0 AW: every ~50 cycles (mimics FB writes coalesced into words)
//   M1 AR/AW: every ~200 cycles (mimics CPU heap/stack reads/writes)
//   M3 AR: every ~130 cycles (mimics audio mixer 48 kHz fetch at 100 MHz)
//
// Uses the liveness watchdog to verify EVERY master makes progress.
// If any of the deadlock cycles I enumerated (multi-master races,
// fairness counter wrap, etc.) actually fires under load, this test
// catches it within a few thousand cycles.
// ====================================================================
static void test_multi_master_stress() {
    printf("\n=== Test 6: Multi-master continuous load (50K cycles) ===\n");
    reset_sequence();

    /* Counters incremented inline as we observe handshakes. */
    uint64_t m0_ar_grants = 0;
    uint64_t m0_aw_grants = 0;
    uint64_t m1_ar_grants = 0;
    uint64_t m1_aw_grants = 0;
    uint64_t m3_ar_grants = 0;

    LivenessWatchdog wd;
    wd.add("m0_ar", [&]() { return m0_ar_grants; }, 5000);
    wd.add("m0_aw", [&]() { return m0_aw_grants; }, 20000);  /* AW is rarer */
    wd.add("m1_ar", [&]() { return m1_ar_grants; }, 30000);
    wd.add("m1_aw", [&]() { return m1_aw_grants; }, 30000);
    wd.add("m3_ar", [&]() { return m3_ar_grants; }, 30000);

    /* Master drive state.  Each master holds its valid until handshake. */
    tb->m0_arvalid = 1; tb->m0_araddr = 0x10000000; tb->m0_arlen = 0;
    tb->m0_awvalid = 0; tb->m0_awaddr = 0x20000000; tb->m0_awlen = 0;
    tb->m0_wvalid  = 0; tb->m0_wdata  = 0xCAFEF00D; tb->m0_wstrb = 0xF; tb->m0_wlast = 1;

    tb->m1_arvalid = 0; tb->m1_araddr = 0x30000000; tb->m1_arlen = 0;
    tb->m1_awvalid = 0; tb->m1_awaddr = 0x31000000; tb->m1_awlen = 0;
    tb->m1_wvalid  = 0; tb->m1_wdata  = 0xDEADBEEF; tb->m1_wstrb = 0xF; tb->m1_wlast = 1;
    tb->m1_rready  = 1;

    tb->m3_arvalid = 0; tb->m3_araddr = 0x32000000; tb->m3_arlen = 0;

    /* Track when to re-arm each master after its handshake completes. */
    int m0_aw_cooldown = 50;
    int m1_arw_cooldown = 200;
    int m3_ar_cooldown = 130;

    bool prev_in_idle = false;
    const int CYCLES = 50000;
    for (int t = 0; t < CYCLES; t++) {
        tb->eval();

        /* Detect grants (IDLE→non-IDLE transition). */
        bool in_idle = (tb->dbg_arb_state == 0);
        if (prev_in_idle && !in_idle) {
            uint8_t grant = tb->dbg_grant;
            uint8_t st    = tb->dbg_arb_state;
            if (grant == 0) { if (st == 1) m0_ar_grants++; else if (st == 2) m0_aw_grants++; }
            if (grant == 1) { if (st == 1) m1_ar_grants++; else if (st == 2) m1_aw_grants++; }
            if (grant == 3 && st == 1) m3_ar_grants++;
        }
        prev_in_idle = in_idle;

        /* Sample handshakes BEFORE tick. */
        bool m0_ar_hs = handshake(tb->m0_arvalid, tb->m0_arready);
        bool m0_aw_hs = handshake(tb->m0_awvalid, tb->m0_awready);
        bool m0_w_hs  = handshake(tb->m0_wvalid,  tb->m0_wready);
        bool m1_ar_hs = handshake(tb->m1_arvalid, tb->m1_arready);
        bool m1_aw_hs = handshake(tb->m1_awvalid, tb->m1_awready);
        bool m1_w_hs  = handshake(tb->m1_wvalid,  tb->m1_wready);
        bool m3_ar_hs = handshake(tb->m3_arvalid, tb->m3_arready);

        tick();
        wd.tick();
        if (wd.failed()) break;

        /* M0 AR — re-arm immediately after handshake (continuous tex stream). */
        if (m0_ar_hs) tb->m0_arvalid = 0;
        else if (!tb->m0_arvalid) tb->m0_arvalid = 1;

        /* M0 AW — re-arm after cooldown. */
        if (m0_aw_hs) { tb->m0_awvalid = 0; m0_aw_cooldown = 50; }
        if (m0_w_hs)  tb->m0_wvalid = 0;
        if (m0_aw_cooldown > 0) {
            m0_aw_cooldown--;
            if (m0_aw_cooldown == 0 && !tb->m0_awvalid) {
                tb->m0_awvalid = 1;
                tb->m0_wvalid  = 1;
            }
        }

        /* M1 — alternates AR and AW every cooldown period. */
        if (m1_ar_hs) tb->m1_arvalid = 0;
        if (m1_aw_hs) tb->m1_awvalid = 0;
        if (m1_w_hs)  tb->m1_wvalid = 0;
        if (m1_arw_cooldown > 0) {
            m1_arw_cooldown--;
            if (m1_arw_cooldown == 0 && !tb->m1_arvalid && !tb->m1_awvalid) {
                /* Alternate AR/AW based on parity of t. */
                if ((t / 200) & 1) {
                    tb->m1_awvalid = 1;
                    tb->m1_wvalid  = 1;
                } else {
                    tb->m1_arvalid = 1;
                }
                m1_arw_cooldown = 200;
            }
        }

        /* M3 — fires AR every cooldown (audio mixer cadence). */
        if (m3_ar_hs) { tb->m3_arvalid = 0; m3_ar_cooldown = 130; }
        if (m3_ar_cooldown > 0) {
            m3_ar_cooldown--;
            if (m3_ar_cooldown == 0 && !tb->m3_arvalid) tb->m3_arvalid = 1;
        }
    }

    printf("  cycles=%d  M0_AR=%lu M0_AW=%lu M1_AR=%lu M1_AW=%lu M3_AR=%lu\n",
           CYCLES,
           (unsigned long)m0_ar_grants, (unsigned long)m0_aw_grants,
           (unsigned long)m1_ar_grants, (unsigned long)m1_aw_grants,
           (unsigned long)m3_ar_grants);
    wd.report();

    if (wd.failed()) {
        fails++;
    } else {
        check("M0 AR > 0", m0_ar_grants > 0);
        check("M0 AW > 0", m0_aw_grants > 0);
        check("M1 AR > 0", m1_ar_grants > 0);
        check("M1 AW > 0", m1_aw_grants > 0);
        check("M3 AR > 0", m3_ar_grants > 0);
    }
}

int main(int argc, char **argv) {
    Verilated::commandArgs(argc, argv);
    tb = new Vtb_arbiter;

    test_aw_alone();
    test_priority_chain();
    test_read_starves_write();
    test_continuous_ar_starves_aw();
    test_m0_fairness_ratio();
    test_multi_master_stress();

    printf("\n=== Results: %d passed, %d failed ===\n", passes, fails);
    delete tb;
    return fails == 0 ? 0 : 1;
}
