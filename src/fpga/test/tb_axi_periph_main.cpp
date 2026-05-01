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
    tb->s_axi_awburst = 1;  // INCR is the default for everything except FIXED bursts
    tb->s_axi_wvalid  = 0;
    tb->s_axi_bready  = 0;
    tb->gpu_swap_req_in = 0;  // CMD_FLIP side-port idle
    tb->gpu_swap_idx_in = 0;
    tb->vsync_in        = 0;
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
// AXI single-word write with optional "bundled AW+W" behaviour.
//   bundled=true:  drive AW+W on the same cycle (VexiiRiscv LSU pattern)
//   bundled=false: drive AW first, wait one cycle, then drive W
// Returns true if the B response came back; false on timeout.
// ====================================================================
static bool axi_write_single(uint32_t addr, uint32_t data, uint8_t wstrb = 0xF,
                              bool bundled = true) {
    tb->s_axi_awvalid = 1;
    tb->s_axi_awaddr  = addr;
    tb->s_axi_awlen   = 0;
    if (bundled) {
        tb->s_axi_wvalid = 1;
        tb->s_axi_wdata  = data;
        tb->s_axi_wstrb  = wstrb;
        tb->s_axi_wlast  = 1;
    } else {
        tb->s_axi_wvalid = 0;
    }

    int cycles = 0;
    bool aw_done = false, w_done = false;
    while (cycles++ < 5000 && !(aw_done && w_done)) {
        tb->eval();
        if (tb->s_axi_awvalid && tb->s_axi_awready) aw_done = true;
        if (tb->s_axi_wvalid  && tb->s_axi_wready ) w_done  = true;
        tick();
        if (aw_done) tb->s_axi_awvalid = 0;
        if (w_done)  tb->s_axi_wvalid  = 0;
        if (!bundled && aw_done && !w_done) {
            // One cycle after AW handshake, drive W
            tb->s_axi_wvalid = 1;
            tb->s_axi_wdata  = data;
            tb->s_axi_wstrb  = wstrb;
            tb->s_axi_wlast  = 1;
        }
    }
    if (!(aw_done && w_done)) {
        printf("  WRITE 0x%08x: AW/W timeout (aw=%d w=%d)\n", addr, aw_done, w_done);
        return false;
    }
    // Wait for B response
    tb->s_axi_bready = 1;
    cycles = 0;
    bool b_done = false;
    while (cycles++ < 1000 && !b_done) {
        tb->eval();
        if (tb->s_axi_bvalid && tb->s_axi_bready) b_done = true;
        tick();
    }
    tb->s_axi_bready = 0;
    if (!b_done) {
        printf("  WRITE 0x%08x: B timeout\n", addr);
        return false;
    }
    return true;
}

// ====================================================================
// GPU MMIO hammer — stress the ring-BRAM write path
// ====================================================================
// Reproduce the CPU-side sequence that fills a GPU command.  Every
// write to 0x4A000008 (GPU_RING_DATA) must produce exactly one
// gpu_reg_wr pulse at the slave's output.  If any write is silently
// dropped, our pulse count will lag the submission count and we've
// found the CPU↔GPU MMIO loss the hardware freeze pointed at.
//
// Back-to-back variant: issue writes as fast as AXI will accept them
// (bundled AW+W), with NO idle cycles between B response and the next
// AW.  That's the worst-case pattern the CPU hits when bursting into
// _gpu_ring_write() from an unrolled inner loop.

static uint32_t g_gpu_wr_pulses = 0;
static uint32_t g_gpu_last_wdata = 0;
static uint32_t g_gpu_last_addr = 0xFFFFFFFF;

static void count_gpu_pulses() {
    // Sample gpu_reg_wr on every tick so we don't miss a 1-cycle pulse.
    if (tb->dbg_gpu_reg_wr) {
        g_gpu_wr_pulses++;
        g_gpu_last_wdata = tb->dbg_gpu_reg_wdata;
        g_gpu_last_addr  = tb->dbg_gpu_reg_addr;
    }
}

// Override tick to also count gpu pulses.  Sample ONCE per clock cycle
// (after the rising edge) — gpu_reg_wr is a posedge-driven reg that
// holds high for one cycle; sampling on both edges double-counts.
static void tick_and_count() {
    tb->clk = 0;
    tb->eval();
    sim_time++;
    tb->clk = 1;
    tb->eval();
    count_gpu_pulses();
    sim_time++;
    cycle_count++;
}

static bool axi_write_single_counted(uint32_t addr, uint32_t data,
                                      bool bundled = true) {
    tb->s_axi_awvalid = 1;
    tb->s_axi_awaddr  = addr;
    tb->s_axi_awlen   = 0;
    if (bundled) {
        tb->s_axi_wvalid = 1;
        tb->s_axi_wdata  = data;
        tb->s_axi_wstrb  = 0xF;
        tb->s_axi_wlast  = 1;
    } else {
        tb->s_axi_wvalid = 0;
    }

    int cycles = 0;
    bool aw_done = false, w_done = false;
    while (cycles++ < 5000 && !(aw_done && w_done)) {
        tb->eval();
        if (tb->s_axi_awvalid && tb->s_axi_awready) aw_done = true;
        if (tb->s_axi_wvalid  && tb->s_axi_wready ) w_done  = true;
        tick_and_count();
        if (aw_done) tb->s_axi_awvalid = 0;
        if (w_done)  tb->s_axi_wvalid  = 0;
        if (!bundled && aw_done && !w_done) {
            tb->s_axi_wvalid = 1;
            tb->s_axi_wdata  = data;
            tb->s_axi_wstrb  = 0xF;
            tb->s_axi_wlast  = 1;
        }
    }
    if (!(aw_done && w_done)) return false;

    tb->s_axi_bready = 1;
    cycles = 0;
    bool b_done = false;
    while (cycles++ < 1000 && !b_done) {
        tb->eval();
        if (tb->s_axi_bvalid && tb->s_axi_bready) b_done = true;
        tick_and_count();
    }
    tb->s_axi_bready = 0;
    return b_done;
}

static void test_gpu_write_back_to_back(void) {
    printf("test_gpu_write_back_to_back (hammer GPU_RING_DATA):\n");
    const uint32_t GPU_RING_DATA = 0x4A000008u;
    const int N = 1000;

    g_gpu_wr_pulses = 0;
    g_gpu_last_wdata = 0;
    g_gpu_last_addr = 0xFFFFFFFF;

    int submit_fail = 0;
    for (int i = 0; i < N; i++) {
        uint32_t data = 0xA5A50000u | (uint32_t)i;
        if (!axi_write_single_counted(GPU_RING_DATA, data)) {
            submit_fail++;
            if (submit_fail < 5)
                printf("  submit failed @ i=%d\n", i);
        }
    }
    // Drain a few more cycles so any in-flight pulse lands.
    for (int i = 0; i < 10; i++) tick_and_count();

    printf("  %d AXI writes issued, %u gpu_reg_wr pulses seen\n",
           N - submit_fail, g_gpu_wr_pulses);
    printf("  last_addr=%u last_wdata=0x%08x\n",
           g_gpu_last_addr, g_gpu_last_wdata);
    check_eq("gpu-ring-data-pulses", g_gpu_wr_pulses, (uint32_t)(N - submit_fail));
}

// VexiiRiscv's LSU may not bundle AW+W on the same cycle for every
// store — especially under back-pressure.  Try the AW-first / W-next
// pattern that hits the S_WR_NEXT code path in the slave.
static void test_gpu_write_split_aw_w(void) {
    printf("test_gpu_write_split_aw_w (AW-first, W one cycle later):\n");
    const uint32_t GPU_RING_DATA = 0x4A000008u;
    const int N = 1000;

    g_gpu_wr_pulses = 0;

    int submit_fail = 0;
    for (int i = 0; i < N; i++) {
        uint32_t data = 0x5A5A0000u | (uint32_t)i;
        if (!axi_write_single_counted(GPU_RING_DATA, data, /*bundled*/false)) {
            submit_fail++;
        }
    }
    for (int i = 0; i < 10; i++) tick_and_count();

    printf("  %d AXI writes issued, %u gpu_reg_wr pulses seen\n",
           N - submit_fail, g_gpu_wr_pulses);
    check_eq("gpu-ring-data-pulses-split", g_gpu_wr_pulses, (uint32_t)(N - submit_fail));
}

// Writes to GPU_RING_WRPTR interleaved with GPU_RING_DATA — mimics
// the _gpu_ring_write() + of_gpu_kick() pattern the SDK issues.
// If the AW-decode latches differently between consecutive writes to
// different registers, the pulse count should still match.
static void test_gpu_write_mixed_addr(void) {
    printf("test_gpu_write_mixed_addr (RING_DATA + RING_WRPTR interleaved):\n");
    const uint32_t GPU_RING_DATA  = 0x4A000008u;
    const uint32_t GPU_RING_WRPTR = 0x4A000004u;

    g_gpu_wr_pulses = 0;
    const int CHUNK = 19;   // one CMD_DRAW_SPAN worth
    const int N_CMDS = 50;

    for (int c = 0; c < N_CMDS; c++) {
        for (int w = 0; w < CHUNK; w++) {
            axi_write_single_counted(GPU_RING_DATA, 0xDEAD0000u | (c*CHUNK + w));
        }
        // Kick after each command
        axi_write_single_counted(GPU_RING_WRPTR, (c + 1) * CHUNK * 4);
    }
    for (int i = 0; i < 10; i++) tick_and_count();

    uint32_t expected = N_CMDS * (CHUNK + 1);
    printf("  issued=%u pulses=%u\n", expected, g_gpu_wr_pulses);
    check_eq("gpu-mixed-addr-pulses", g_gpu_wr_pulses, expected);
}

// ====================================================================
// FIXED-burst write — proves req_addr stays pinned across all beats.
// Used by the LSU shim's M2 coalescer to pack consecutive writes to a
// single MMIO register (e.g. GPU_RING_DATA) into one AW + N W beats.
// Without FIXED support, beat 2 would walk to GPU_RING_WRPTR and beat
// 3 to GPU_FENCE, corrupting state.  Also tests the address-pinned
// gpu_reg_addr matches the AW address on every pulse.
// ====================================================================
static uint32_t g_fixed_addrs[16];
static uint32_t g_fixed_wdata[16];
static uint32_t g_fixed_pulse_idx;
static void count_gpu_pulses_fixed_capture() {
    if (tb->dbg_gpu_reg_wr && g_fixed_pulse_idx < 16) {
        g_fixed_addrs[g_fixed_pulse_idx] = tb->dbg_gpu_reg_addr;
        g_fixed_wdata[g_fixed_pulse_idx] = tb->dbg_gpu_reg_wdata;
        g_fixed_pulse_idx++;
    }
}
static void tick_and_count_fixed() {
    tb->clk = 0;
    tb->eval();
    sim_time++;
    tb->clk = 1;
    tb->eval();
    count_gpu_pulses_fixed_capture();
    sim_time++;
    cycle_count++;
}

// Issue an AW with awburst=FIXED (00), awlen=N-1; pump N W beats with
// distinct data; expect bvalid back, gpu_reg_wr to fire N times all at
// the same gpu_reg_addr (= AW address >> 2 & 0xF), and the captured
// wdata sequence to match what we sent.
static bool axi_write_fixed_burst(uint32_t addr, const uint32_t *data, int n) {
    tb->s_axi_awvalid = 1;
    tb->s_axi_awaddr  = addr;
    tb->s_axi_awlen   = (uint8_t)(n - 1);
    tb->s_axi_awburst = 0;  // FIXED
    tb->s_axi_wvalid  = 0;

    int cycles = 0;
    while (cycles++ < 5000 && !(tb->s_axi_awvalid == 0)) {
        tb->eval();
        if (tb->s_axi_awvalid && tb->s_axi_awready) tb->s_axi_awvalid = 0;
        tick_and_count_fixed();
    }
    // Pump W beats one at a time so wlast lands on the last beat.
    int beat = 0;
    while (beat < n && cycles++ < 5000) {
        tb->s_axi_wvalid = 1;
        tb->s_axi_wdata  = data[beat];
        tb->s_axi_wstrb  = 0xF;
        tb->s_axi_wlast  = (beat == n - 1) ? 1 : 0;
        tb->eval();
        if (tb->s_axi_wready) {
            beat++;
        }
        tick_and_count_fixed();
    }
    tb->s_axi_wvalid = 0;
    tb->s_axi_awburst = 1;  // restore default

    tb->s_axi_bready = 1;
    cycles = 0;
    bool b_done = false;
    while (cycles++ < 1000 && !b_done) {
        tb->eval();
        if (tb->s_axi_bvalid && tb->s_axi_bready) b_done = true;
        tick_and_count_fixed();
    }
    tb->s_axi_bready = 0;
    return b_done && (beat == n);
}

// =====================================================================
// CMD_FLIP side-port — gpu_swap_req → fb_swap_pending latch path.
// Closes the coverage gap that let a hardware regression slip through:
// tb_gpu verifies gpu_core's gpu_swap_req pulse, tb_axi_periph (this
// file) verified the slave's burst behavior, but neither tested the
// side-port → fb_swap_pending coupling that the production design
// depends on.  If this test passes but real hardware fails, the
// failure must be in core_top.v wiring or post-fit optimization.
// =====================================================================
static void test_gpu_swap_side_port(void) {
    printf("test_gpu_swap_side_port (gpu_swap_req → fb_swap_pending):\n");

    /* sysreg 0x18 read returns {fb_display_idx[1:0], fb_swap_pending[0]}. */
    auto read_swap_ctrl = [&]() -> uint32_t {
        std::vector<uint32_t> r;
        if (!axi_read_burst(0x40000018u, 0, r)) return 0xFFFFFFFFu;
        return r[0];
    };

    /* Initial state: fb_swap_pending must be 0 (no swap queued). */
    uint32_t pre = read_swap_ctrl();
    check_eq("side-port-pre-pending=0", pre & 1, 0u);

    /* Pulse gpu_swap_req for one cycle with idx=2.  Expect fb_swap_pending
     * to latch high AND fb_ready_idx (bits[2:1] of the readback) to
     * become 2 on the next clock edge. */
    tb->gpu_swap_req_in = 1;
    tb->gpu_swap_idx_in = 2;
    tick();   /* posedge: slave latches fb_ready_idx<=2, fb_swap_pending<=1 */
    tb->gpu_swap_req_in = 0;
    tb->gpu_swap_idx_in = 0;
    /* Drain a few cycles so the AXI read transaction has time to fire. */
    for (int i = 0; i < 5; i++) tick();

    uint32_t post = read_swap_ctrl();
    check_eq("side-port-pending-set",   post & 1,             1u);
    /* fb_ready_idx isn't directly exposed via sysreg readback (sysreg
     * 0x18 returns fb_display_idx in bits [2:1]) — we verify it
     * indirectly below via the post-vsync display_idx check. */

    /* Now pulse vsync_in to clear fb_swap_pending and update display_idx. */
    tb->vsync_in = 1;
    tick();   /* slave's vsync_sync shift register sees vsync=1 */
    tick();   /* shift to vsync_sync[1] — vsync_rising fires this cycle */
    tb->vsync_in = 0;
    for (int i = 0; i < 5; i++) tick();

    uint32_t after = read_swap_ctrl();
    check_eq("side-port-vsync-clear",     after & 1,           0u);
    check_eq("side-port-display-idx-set", (after >> 1) & 0x3u, 2u);
}

// =====================================================================
// Race test: gpu_swap_req fires SAME CYCLE as vsync_rising.
//
// Per the slave's reorder: vsync clear path runs BEFORE the GPU
// side-port path.  Same-cycle expected behavior:
//   - Vsync clear: fb_display_idx <= old_fb_ready_idx, schedules
//                  fb_swap_pending <= 0
//   - GPU pulse:   fb_ready_idx <= new gpu_swap_idx, schedules
//                  fb_swap_pending <= 1   (LAST WRITE WINS)
// End state: fb_display_idx = OLD ready (display advances to
// previous queued frame), fb_ready_idx = NEW (next frame queued),
// fb_swap_pending = 1 (queued for next vsync).
//
// If this test fails, the same-cycle race in the slave is broken
// and the GPU's swap could be silently dropped — exactly the kind
// of bug that would manifest as "deterministic deadlock after N
// frames" because every Nth frame falls on the race cycle.
// =====================================================================
static void test_gpu_swap_req_vsync_race(void) {
    printf("test_gpu_swap_req_vsync_race (gpu_swap_req + vsync_rising same cycle):\n");

    auto read_swap_ctrl = [&]() -> uint32_t {
        std::vector<uint32_t> r;
        if (!axi_read_burst(0x40000018u, 0, r)) return 0xFFFFFFFFu;
        return r[0];
    };

    /* Phase 1 — set up a previously-queued flip (idx=1) so
     * fb_swap_pending=1 and fb_ready_idx=1 going into the race. */
    tb->gpu_swap_req_in = 1;
    tb->gpu_swap_idx_in = 1;
    tick();
    tb->gpu_swap_req_in = 0;
    tb->gpu_swap_idx_in = 0;
    for (int i = 0; i < 5; i++) tick();

    uint32_t mid = read_swap_ctrl();
    check_eq("race-pre-pending=1", mid & 1, 1u);

    /* Phase 2 — drive vsync rising AND gpu_swap_req with idx=2 in
     * the SAME cycle.  vsync_sync shifts on each posedge so we
     * arrange vsync_rising to fire on the cycle gpu_swap_req=1.
     *
     * vsync_sync = {sync[1:0], vsync_in}.  vsync_rising = sync[1] &&
     * !sync[2].  We need sync[1]=1 and sync[2]=0 at the cycle gpu
     * pulse fires.  That means vsync_in went 0→1 two cycles ago. */
    tb->vsync_in = 1;
    tick();   /* sync[0] <= 1, sync[1]=0, sync[2]=0 */
    tick();   /* sync[0]=1, sync[1] <= 1, sync[2]=0 */
    /* Now on this cycle, vsync_sync = {0, 1, 1}.  vsync_rising =
     * sync[1] && !sync[2] = 1.  Drive gpu_swap_req simultaneously. */
    tb->gpu_swap_req_in = 1;
    tb->gpu_swap_idx_in = 2;
    tick();   /* THIS is the race cycle */
    tb->gpu_swap_req_in = 0;
    tb->gpu_swap_idx_in = 0;
    tb->vsync_in = 0;
    for (int i = 0; i < 5; i++) tick();

    /* After race: display_idx should be 1 (the OLD ready, displayed
     * by the vsync clear), pending should be 1 (GPU's new pulse
     * overrode the vsync clear).  fb_ready_idx is now 2 (will be
     * displayed at NEXT vsync). */
    uint32_t after_race = read_swap_ctrl();
    check_eq("race-display-idx-advanced",  (after_race >> 1) & 0x3u, 1u);
    check_eq("race-pending-retained",       after_race & 1,           1u);

    /* Phase 3 — drive a CLEAN vsync (no concurrent GPU pulse) and
     * verify fb_display_idx advances to 2 (the queued ready). */
    tb->vsync_in = 1;
    tick();
    tick();
    tb->vsync_in = 0;
    for (int i = 0; i < 5; i++) tick();

    uint32_t final_state = read_swap_ctrl();
    check_eq("race-final-display-idx-2", (final_state >> 1) & 0x3u, 2u);
    check_eq("race-final-pending-clear",  final_state & 1,           0u);
}

static void test_gpu_write_fixed_burst(void) {
    printf("test_gpu_write_fixed_burst (awburst=FIXED, addr stays pinned):\n");
    const uint32_t GPU_RING_DATA = 0x4A000008u;       // gpu_reg_addr = 2
    const uint32_t expected_reg  = (GPU_RING_DATA >> 2) & 0xFu;
    const uint32_t data[4] = {0xCAFEBE00u, 0xCAFEBE01u, 0xCAFEBE02u, 0xCAFEBE03u};

    g_fixed_pulse_idx = 0;
    bool ok = axi_write_fixed_burst(GPU_RING_DATA, data, 4);
    for (int i = 0; i < 10; i++) tick_and_count_fixed();

    if (!ok) { printf("  burst transfer failed\n"); fails++; return; }

    check_eq("fixed-burst-pulse-count", g_fixed_pulse_idx, 4u);
    int addrs_ok = 1, data_ok = 1;
    for (uint32_t i = 0; i < 4 && i < g_fixed_pulse_idx; i++) {
        if (g_fixed_addrs[i] != expected_reg) {
            printf("  beat %u: addr=%u expected=%u (would have walked under INCR)\n",
                   i, g_fixed_addrs[i], expected_reg);
            addrs_ok = 0;
        }
        if (g_fixed_wdata[i] != data[i]) {
            printf("  beat %u: wdata=0x%08x expected=0x%08x\n",
                   i, g_fixed_wdata[i], data[i]);
            data_ok = 0;
        }
    }
    check_eq("fixed-burst-addr-pinned", addrs_ok, 1);
    check_eq("fixed-burst-wdata-order", data_ok, 1);
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

// ====================================================================
// GPU MMIO READ path — every AR to the GPU MMIO base must return the
// data of the *requested* register, not a stale value from the prior
// read.  The harness's gpu_reg_rdata mux returns a per-register sentinel
// (4'd1=>0x11..11, 4'd4=>0x44..44, 4'd5=>0x55..55, etc.) so each beat
// is unambiguously identifiable.  Originally caught the registered
// `gpu_reg_rdata_r` pipeline boundary that off-by-one'd every read in
// production.
// ====================================================================
static void test_gpu_read_addressing(void) {
    printf("test_gpu_read_addressing (verify GPU MMIO returns the requested reg):\n");
    struct { uint32_t addr; uint32_t expect; const char *tag; } cases[] = {
        { 0x4A000004, 0x11111111, "gpu_rd_0x04_wrptr"      },
        { 0x4A000010, 0x44444444, "gpu_rd_0x10_rdptr"      },
        { 0x4A000014, 0x55555555, "gpu_rd_0x14_status"     },
        { 0x4A000018, 0x66666666, "gpu_rd_0x18_fence"      },
    };
    for (size_t i = 0; i < sizeof(cases)/sizeof(cases[0]); i++) {
        std::vector<uint32_t> r;
        if (!axi_read_burst(cases[i].addr, 0, r)) {
            printf("  FAIL %s: AR did not complete\n", cases[i].tag);
            fails++; continue;
        }
        check_eq(cases[i].tag, r[0], cases[i].expect);
    }

    // Adversarial sequence — the off-by-one bug only manifested when
    // a *prior* GPU read had primed a register's data into the latch.
    // Issue every pair (a -> b) and verify each read returns its own
    // expected value, never the prior read's.
    printf("test_gpu_read_addressing (adversarial pairs):\n");
    int ok = 1;
    for (size_t i = 0; i < sizeof(cases)/sizeof(cases[0]); i++) {
        for (size_t j = 0; j < sizeof(cases)/sizeof(cases[0]); j++) {
            std::vector<uint32_t> r1, r2;
            if (!axi_read_burst(cases[i].addr, 0, r1)) { ok = 0; break; }
            if (!axi_read_burst(cases[j].addr, 0, r2)) { ok = 0; break; }
            if (r2[0] != cases[j].expect) {
                printf("  pair (%s -> %s): got=0x%08x expected=0x%08x\n",
                       cases[i].tag, cases[j].tag, r2[0], cases[j].expect);
                ok = 0;
            }
        }
    }
    check_eq("gpu-rd-adversarial-pairs", ok, 1);
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

    // GPU MMIO hammer — probe the CPU↔GPU write path for dropped
    // MMIO writes that tb_gpu's drop-sim showed wedge the GPU.
    test_gpu_write_back_to_back();
    test_gpu_write_split_aw_w();
    test_gpu_write_mixed_addr();
    test_gpu_write_fixed_burst();
    test_gpu_swap_side_port();
    test_gpu_swap_req_vsync_race();

    // GPU MMIO read addressing — caught the registered-rdata off-by-one.
    test_gpu_read_addressing();

    printf("\n=== Results: %d passed, %d failed ===\n", passes, fails);
    delete tb;
    return fails == 0 ? 0 : 1;
}
