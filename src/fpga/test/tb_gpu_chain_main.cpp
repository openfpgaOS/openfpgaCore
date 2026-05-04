//
// C++ harness for tb_gpu_chain — exercises the
// (per_axi → slices → cpu_target_port → axi_periph_slave) write path
// to reproduce / disprove the cr-gpu-and-tri-wedges issue 1 wedge.
//
// We drive per_axi from the harness with the same shape the
// cpu_system shim uses: AW + W presented simultaneously when there
// are no in-flight transactions, both held until their respective
// ready fires, then we wait for B (we always assert bready=1).  The
// shim's posted-FIFO + coalescer is NOT modeled here on purpose —
// this bench isolates the lower three layers so we can pinpoint where
// (if anywhere) a wedge surfaces.
//

#include "Vtb_gpu_chain.h"
#include "Vtb_gpu_chain___024root.h"
#include <verilated.h>
#include <cstdio>
#include <cstdint>
#include <cstring>
#include <vector>

static Vtb_gpu_chain *tb;
static vluint64_t sim_time = 0;
static uint64_t cycle_count = 0;
static int fails = 0;
static int passes = 0;

// gpu_reg_wr trace — the harness counts pulses + records the wdata so
// we can compare against what we submitted.  Captured every cycle from
// the eval()'d state, NOT from sequential edges, so a wide-pulse bug
// (gpu_reg_wr held high for >1 cycle) would be detected by the
// pulse-counter checking the rising edge.
static std::vector<uint32_t> reg_wr_trace;
static bool prev_reg_wr = false;

static void tick() {
    tb->clk = 0;
    tb->eval();
    sim_time++;

    tb->clk = 1;
    tb->eval();
    sim_time++;
    cycle_count++;

    // gpu_reg_wr is registered; capture rising-edge pulses.  W1
    // address (0x4A000008 = GPU_RING_DATA) decodes to gpu_reg_addr=2.
    bool rw = tb->dbg_gpu_reg_wr != 0;
    if (rw && !prev_reg_wr) {
        reg_wr_trace.push_back(tb->dbg_gpu_reg_wdata);
    }
    prev_reg_wr = rw;
}

static void reset_sequence(bool use_lsu_path = false) {
    tb->reset_n = 0;
    tb->lsu_path_en = use_lsu_path ? 1 : 0;

    // Legacy per_axi master inputs (used when lsu_path_en=0).
    tb->s_per_arvalid = 0;
    tb->s_per_rready  = 0;
    tb->s_per_arsize  = 2;       // 4-byte
    tb->s_per_arburst = 1;
    tb->s_per_arlen   = 0;

    tb->s_per_awvalid = 0;
    tb->s_per_awsize  = 2;
    tb->s_per_awburst = 1;       // INCR default
    tb->s_per_awlen   = 0;

    tb->s_per_wvalid  = 0;
    tb->s_per_wstrb   = 0xF;
    tb->s_per_wlast   = 0;

    tb->s_per_bready  = 1;       // always accept B

    // LSU master inputs (used when lsu_path_en=1).
    tb->lsu_cmd_valid = 0;
    tb->lsu_cmd_write = 0;
    tb->lsu_cmd_addr  = 0;
    tb->lsu_cmd_data  = 0;
    tb->lsu_cmd_size  = 2;
    tb->lsu_cmd_mask  = 0xF;

    for (int i = 0; i < 16; i++) tick();
    tb->reset_n = 1;
    for (int i = 0; i < 16; i++) tick();

    reg_wr_trace.clear();
    prev_reg_wr = false;
}

// Single-beat AXI4 write through the chain.  Mimics what the cpu_system
// shim does for a 1-deep FIFO: present AW + W same cycle, hold until
// each handshakes, then wait for B.  Returns false on timeout.
static bool axi_write_single(uint32_t addr, uint32_t data, int max_cycles = 200) {
    tb->s_per_awaddr  = addr;
    tb->s_per_awlen   = 0;
    tb->s_per_awburst = 1;       // INCR for single-beat
    tb->s_per_awvalid = 1;

    tb->s_per_wdata   = data;
    tb->s_per_wstrb   = 0xF;
    tb->s_per_wlast   = 1;
    tb->s_per_wvalid  = 1;

    bool aw_done = false;
    bool w_done  = false;
    bool b_done  = false;

    int spent = 0;
    while ((!aw_done || !w_done || !b_done) && spent++ < max_cycles) {
        tb->eval();
        if (!aw_done && tb->s_per_awvalid && tb->s_per_awready) {
            aw_done = true;
        }
        if (!w_done && tb->s_per_wvalid && tb->s_per_wready) {
            w_done = true;
        }
        if (!b_done && tb->s_per_bvalid && tb->s_per_bready) {
            b_done = true;
        }
        tick();
        if (aw_done) tb->s_per_awvalid = 0;
        if (w_done)  tb->s_per_wvalid  = 0;
    }

    if (!aw_done || !w_done || !b_done) {
        printf("    axi_write_single(0x%08x, 0x%08x) timed out: aw=%d w=%d b=%d wr_state=%d\n",
               addr, data, aw_done, w_done, b_done, (int)tb->dbg_wr_state);
        return false;
    }
    return true;
}

// FIXED-burst write — mimics the cpu_system shim's coalesced 4-beat
// AW+4W to GPU_RING_DATA.  Returns false on timeout / drop.
//
// Handshake order: observe the combinational valid+ready in the current
// cycle, then tick() (the transfer commits at the posedge inside tick),
// then update inputs for the NEXT beat.  Updating before tick would
// race the posedge and corrupt the just-committed beat with the next
// one's payload.
static bool axi_write_fixed_burst(uint32_t addr, const uint32_t *data, int n,
                                   int max_cycles = 500) {
    tb->s_per_awaddr  = addr;
    tb->s_per_awlen   = (uint8_t)(n - 1);
    tb->s_per_awburst = 0;       // FIXED
    tb->s_per_awvalid = 1;

    tb->s_per_wdata   = data[0];
    tb->s_per_wstrb   = 0xF;
    tb->s_per_wlast   = (n == 1);
    tb->s_per_wvalid  = 1;

    bool aw_done = false;
    int  beat = 0;
    bool b_done = false;
    int  spent = 0;

    while ((!aw_done || beat < n || !b_done) && spent++ < max_cycles) {
        tb->eval();
        bool aw_xfer = !aw_done && tb->s_per_awvalid && tb->s_per_awready;
        bool w_xfer  = beat < n && tb->s_per_wvalid && tb->s_per_wready;
        bool b_xfer  = !b_done && tb->s_per_bvalid && tb->s_per_bready;

        tick();  // posedge: transfers commit here

        if (aw_xfer) { aw_done = true; tb->s_per_awvalid = 0; }
        if (w_xfer) {
            beat++;
            if (beat < n) {
                tb->s_per_wdata = data[beat];
                tb->s_per_wlast = (beat == n - 1) ? 1 : 0;
            } else {
                tb->s_per_wvalid = 0;
            }
        }
        if (b_xfer) b_done = true;
    }

    if (!aw_done || beat != n || !b_done) {
        printf("    axi_write_fixed_burst(0x%08x, n=%d) timed out: aw=%d beats=%d b=%d wr_state=%d\n",
               addr, n, aw_done, beat, b_done, (int)tb->dbg_wr_state);
        return false;
    }
    return true;
}

// Single-beat AXI4 read — used to interleave a read between writes.
static bool axi_read_single(uint32_t addr, uint32_t &out, int max_cycles = 200) {
    tb->s_per_araddr  = addr;
    tb->s_per_arlen   = 0;
    tb->s_per_arburst = 1;
    tb->s_per_arvalid = 1;
    tb->s_per_rready  = 1;

    bool ar_done = false, r_done = false;
    int  spent = 0;

    while ((!ar_done || !r_done) && spent++ < max_cycles) {
        tb->eval();
        if (!ar_done && tb->s_per_arvalid && tb->s_per_arready) ar_done = true;
        if (!r_done  && tb->s_per_rvalid  && tb->s_per_rready) {
            out = tb->s_per_rdata;
            r_done = true;
        }
        tick();
        if (ar_done) tb->s_per_arvalid = 0;
    }
    tb->s_per_rready = 0;

    if (!ar_done || !r_done) {
        printf("    axi_read_single(0x%08x) timed out: ar=%d r=%d wr_state=%d\n",
               addr, ar_done, r_done, (int)tb->dbg_wr_state);
        return false;
    }
    return true;
}

// ====================================================================
// Test 1: 23 single-beat writes back-to-back to GPU_RING_DATA from a
// cold-idle chain.  This is the exact CR repro shape (3 bind_texture
// + 20 draw_triangles, no kick between).
// ====================================================================
static void test_back_to_back_singles(void) {
    printf("test_back_to_back_singles (23 single-beat writes):\n");
    reg_wr_trace.clear();

    const uint32_t GPU_RING_DATA = 0x4A000008u;
    bool ok = true;
    for (int i = 0; i < 23 && ok; i++) {
        uint32_t pat = 0xC0FFEE00u | (uint8_t)i;
        ok = axi_write_single(GPU_RING_DATA, pat);
        if (!ok) {
            printf("  FAIL at write %d/23: chain stalled\n", i);
            fails++;
            return;
        }
    }
    if (reg_wr_trace.size() != 23) {
        printf("  FAIL: expected 23 gpu_reg_wr pulses, saw %zu\n", reg_wr_trace.size());
        fails++; return;
    }
    for (int i = 0; i < 23; i++) {
        uint32_t want = 0xC0FFEE00u | (uint8_t)i;
        if (reg_wr_trace[i] != want) {
            printf("  FAIL: pulse %d expected 0x%08x got 0x%08x\n",
                   i, want, reg_wr_trace[i]);
            fails++; return;
        }
    }
    passes++;
    printf("  PASS  all 23 writes landed at gpu_reg_wr in order\n");
}

// ====================================================================
// Test 2: same but with a read injected after the 6th write — checks
// whether the R/W mutex inside cpu_target_port serializes correctly
// without dropping subsequent writes.
// ====================================================================
static void test_writes_with_interleaved_read(void) {
    printf("test_writes_with_interleaved_read (6 writes, 1 read, 17 writes):\n");
    reg_wr_trace.clear();

    const uint32_t GPU_RING_DATA   = 0x4A000008u;
    const uint32_t GPU_FENCE_REACH = 0x4A000004u;  // gpu_reg 1 → 0x11111111

    bool ok = true;
    for (int i = 0; i < 6 && ok; i++) {
        ok = axi_write_single(GPU_RING_DATA, 0xC0FFEE00u | (uint8_t)i);
    }
    if (!ok) {
        printf("  FAIL at first 6 writes\n"); fails++; return;
    }

    uint32_t rd = 0;
    if (!axi_read_single(GPU_FENCE_REACH, rd)) {
        printf("  FAIL: read mid-sequence stalled\n"); fails++; return;
    }
    if (rd != 0x11111111u) {
        printf("  FAIL: read returned 0x%08x, expected 0x11111111\n", rd);
        fails++; return;
    }

    for (int i = 6; i < 23 && ok; i++) {
        ok = axi_write_single(GPU_RING_DATA, 0xC0FFEE00u | (uint8_t)i);
    }
    if (!ok) {
        printf("  FAIL at writes 6..23 (after the read)\n"); fails++; return;
    }
    if (reg_wr_trace.size() != 23) {
        printf("  FAIL: expected 23 gpu_reg_wr pulses, saw %zu\n", reg_wr_trace.size());
        fails++; return;
    }
    passes++;
    printf("  PASS  read mid-sequence didn't drop or wedge writes\n");
}

// ====================================================================
// Test 3: FIXED-burst coalesced — 5 4-beat AW+4W bursts + one 3-beat
// burst = 23 writes total.  Mimics what the shim's M2 coalescer
// produces when the FIFO accumulates 4 same-addr entries.
// ====================================================================
static void test_fixed_burst_coalesced(void) {
    printf("test_fixed_burst_coalesced (5x4 + 1x3 = 23 beats):\n");
    reg_wr_trace.clear();

    const uint32_t GPU_RING_DATA = 0x4A000008u;
    uint32_t buf[4];
    int written = 0;
    bool ok = true;

    for (int b = 0; b < 5 && ok; b++) {
        for (int j = 0; j < 4; j++) buf[j] = 0xCAFE0000u | written++;
        ok = axi_write_fixed_burst(GPU_RING_DATA, buf, 4);
    }
    if (!ok) { printf("  FAIL during 4-beat bursts\n"); fails++; return; }

    for (int j = 0; j < 3; j++) buf[j] = 0xCAFE0000u | written++;
    ok = axi_write_fixed_burst(GPU_RING_DATA, buf, 3);
    if (!ok) { printf("  FAIL during 3-beat tail burst\n"); fails++; return; }

    if (reg_wr_trace.size() != 23) {
        printf("  FAIL: expected 23 gpu_reg_wr pulses, saw %zu\n", reg_wr_trace.size());
        fails++; return;
    }
    for (int i = 0; i < 23; i++) {
        uint32_t want = 0xCAFE0000u | i;
        if (reg_wr_trace[i] != want) {
            printf("  FAIL: pulse %d expected 0x%08x got 0x%08x\n",
                   i, want, reg_wr_trace[i]);
            fails++; return;
        }
    }
    passes++;
    printf("  PASS  all 23 coalesced beats landed at gpu_reg_wr in order\n");
}

// ====================================================================
// Test 4: post-finish pattern — many reads (mock fence_reached spin),
// then 23 writes.  Mimics what happens after of_gpu_finish() returns.
// ====================================================================
static void test_post_finish_pattern(void) {
    printf("test_post_finish_pattern (10 reads, then 23 writes):\n");
    reg_wr_trace.clear();

    const uint32_t GPU_RING_DATA   = 0x4A000008u;
    const uint32_t GPU_FENCE_REACH = 0x4A000004u;

    for (int i = 0; i < 10; i++) {
        uint32_t rd = 0;
        if (!axi_read_single(GPU_FENCE_REACH, rd)) {
            printf("  FAIL during fence-poll read %d\n", i);
            fails++; return;
        }
    }

    bool ok = true;
    for (int i = 0; i < 23 && ok; i++) {
        ok = axi_write_single(GPU_RING_DATA, 0xC0FFEE00u | (uint8_t)i);
        if (!ok) {
            printf("  FAIL: write %d wedged after the read sequence\n", i);
            fails++; return;
        }
    }
    if (reg_wr_trace.size() != 23) {
        printf("  FAIL: expected 23, saw %zu\n", reg_wr_trace.size());
        fails++; return;
    }
    passes++;
    printf("  PASS  reads → writes transition was clean\n");
}

// ====================================================================
// LSU-path helpers — drive the lsu_cmd / lsu_rsp interface that the
// VexiiRiscv LsuPlugin uses in production.  These exercise the
// FULL chain: lsu_axi_shim → slices → cpu_target_port → slave.
//
// Stores are posted: cmd_ready falls only when the FIFO is full.
// rsp_valid pulses one cycle after each cmd fire.  Reads block:
// cmd_ready stays low until the previous read's R-last lands.
// ====================================================================

// Issue an LSU store.  Returns false if cmd_ready never goes high
// within max_cycles (FIFO chronically full).
static bool lsu_store(uint32_t addr, uint32_t data, int max_cycles = 200) {
    tb->lsu_cmd_valid = 1;
    tb->lsu_cmd_write = 1;
    tb->lsu_cmd_addr  = addr;
    tb->lsu_cmd_data  = data;
    tb->lsu_cmd_size  = 2;
    tb->lsu_cmd_mask  = 0xF;

    bool fired = false;
    int  spent = 0;
    while (!fired && spent++ < max_cycles) {
        tb->eval();
        if (tb->lsu_cmd_valid && tb->lsu_cmd_ready) fired = true;
        tick();
    }
    tb->lsu_cmd_valid = 0;

    if (!fired) {
        printf("    lsu_store(0x%08x) timed out: cmd_ready never went high\n", addr);
        printf("      shim:    count=%d aw_sent=%d w_sent=%d awlen=%d awvalid=%d wvalid=%d b_xfer=%d\n",
               (int)tb->dbg_shim_count, (int)tb->dbg_shim_aw_sent, (int)tb->dbg_shim_w_sent,
               (int)tb->dbg_shim_burst_awlen, (int)tb->dbg_shim_per_awvalid,
               (int)tb->dbg_shim_per_wvalid, (int)tb->dbg_shim_b_xfer);
        printf("      slave:   awready=%d wready=%d bvalid=%d port_wr_state=%d\n",
               (int)tb->dbg_slave_awready, (int)tb->dbg_slave_wready,
               (int)tb->dbg_slave_bvalid, (int)tb->dbg_wr_state);
        return false;
    }
    return true;
}

// Issue an LSU load.  Reads block until R-last; returns the rsp data.
static bool lsu_load(uint32_t addr, uint32_t &out, int max_cycles = 400) {
    tb->lsu_cmd_valid = 1;
    tb->lsu_cmd_write = 0;
    tb->lsu_cmd_addr  = addr;
    tb->lsu_cmd_size  = 2;
    tb->lsu_cmd_mask  = 0xF;

    bool fired = false, got_rsp = false;
    int  spent = 0;
    while ((!fired || !got_rsp) && spent++ < max_cycles) {
        tb->eval();
        if (!fired && tb->lsu_cmd_valid && tb->lsu_cmd_ready) fired = true;
        if (fired && tb->lsu_rsp_valid && !tb->lsu_rsp_error) {
            out = tb->lsu_rsp_data;
            got_rsp = true;
        }
        tick();
        if (fired) tb->lsu_cmd_valid = 0;
    }

    if (!fired || !got_rsp) {
        printf("    lsu_load(0x%08x) timed out: fired=%d rsp=%d\n",
               addr, fired, got_rsp);
        return false;
    }
    return true;
}

// ====================================================================
// LSU Test 1: 23 stores back-to-back through the full chain.  This
// is the EXACT post-finish bind_texture+draw_triangles pattern.  The
// shim should pump them via M2-coalesced AWs as the FIFO accumulates.
// Wedge symptom would be lsu_cmd_ready stuck low (FIFO full forever).
// ====================================================================
static void test_lsu_back_to_back(void) {
    printf("test_lsu_back_to_back (23 LSU stores via shim):\n");
    reg_wr_trace.clear();

    const uint32_t GPU_RING_DATA = 0x4A000008u;
    bool ok = true;
    for (int i = 0; i < 23 && ok; i++) {
        ok = lsu_store(GPU_RING_DATA, 0xC0FFEE00u | (uint8_t)i);
        if (!ok) {
            printf("  FAIL: chain wedged at write %d/23\n", i);
            fails++; return;
        }
    }
    // Drain — give bursts time to retire so all pulses land.
    for (int i = 0; i < 50; i++) tick();

    if (reg_wr_trace.size() != 23) {
        printf("  FAIL: expected 23 gpu_reg_wr pulses, saw %zu\n", reg_wr_trace.size());
        fails++; return;
    }
    for (int i = 0; i < 23; i++) {
        uint32_t want = 0xC0FFEE00u | (uint8_t)i;
        if (reg_wr_trace[i] != want) {
            printf("  FAIL: pulse %d expected 0x%08x got 0x%08x\n",
                   i, want, reg_wr_trace[i]);
            fails++; return;
        }
    }
    passes++;
    printf("  PASS  shim → slice → port → slave path delivered all 23 in order\n");
}

// ====================================================================
// LSU Test 2: post-finish pattern — many reads (mock fence_reached
// spin), then 23 writes.  Mirrors what happens after of_gpu_finish()
// on hardware, with the shim's R-blocks-W ordering enforced.
// ====================================================================
static void test_lsu_post_finish(void) {
    printf("test_lsu_post_finish (10 LSU reads then 23 LSU writes):\n");
    reg_wr_trace.clear();

    const uint32_t GPU_RING_DATA   = 0x4A000008u;
    const uint32_t GPU_FENCE_REACH = 0x4A000004u;

    for (int i = 0; i < 10; i++) {
        uint32_t v = 0;
        if (!lsu_load(GPU_FENCE_REACH, v)) {
            printf("  FAIL during fence-poll read %d\n", i);
            fails++; return;
        }
        if (v != 0x11111111u) {
            printf("  FAIL: fence read %d returned 0x%08x\n", i, v);
            fails++; return;
        }
    }
    bool ok = true;
    for (int i = 0; i < 23 && ok; i++) {
        ok = lsu_store(GPU_RING_DATA, 0xC0FFEE00u | (uint8_t)i);
        if (!ok) {
            printf("  FAIL: write %d wedged after the read sequence\n", i);
            fails++; return;
        }
    }
    for (int i = 0; i < 50; i++) tick();

    if (reg_wr_trace.size() != 23) {
        printf("  FAIL: expected 23, saw %zu\n", reg_wr_trace.size());
        fails++; return;
    }
    passes++;
    printf("  PASS  10 reads → 23 writes through shim+chain clean\n");
}

// ====================================================================
// LSU Test 3: bind_texture + draw_triangles cadence — 3 writes, no
// kick, then 20 writes.  This is the literal CR repro shape.
// ====================================================================
static void test_lsu_bind_then_draw(void) {
    printf("test_lsu_bind_then_draw (3 writes, no kick, 20 writes):\n");
    reg_wr_trace.clear();

    const uint32_t GPU_RING_DATA = 0x4A000008u;
    bool ok = true;

    // bind_texture: 3 ring words.
    for (int i = 0; i < 3 && ok; i++)
        ok = lsu_store(GPU_RING_DATA, 0xB1ED0000u | (uint8_t)i);
    if (!ok) { printf("  FAIL during bind_texture writes\n"); fails++; return; }

    // No intermediate of_gpu_kick() — exact wedge condition from CR.

    // draw_triangles header + count + 18 vertex words = 20.
    for (int i = 0; i < 20 && ok; i++)
        ok = lsu_store(GPU_RING_DATA, 0xD2A40000u | (uint8_t)i);
    if (!ok) { printf("  FAIL during draw_triangles writes\n"); fails++; return; }

    for (int i = 0; i < 50; i++) tick();
    if (reg_wr_trace.size() != 23) {
        printf("  FAIL: expected 23 (3 bind + 20 draw), saw %zu\n", reg_wr_trace.size());
        fails++; return;
    }
    passes++;
    printf("  PASS  bind+draw with no kick completed cleanly\n");
}

int main(int argc, char **argv) {
    Verilated::commandArgs(argc, argv);
    tb = new Vtb_gpu_chain;

    reset_sequence(false);
    test_back_to_back_singles();

    reset_sequence(false);
    test_writes_with_interleaved_read();

    reset_sequence(false);
    test_fixed_burst_coalesced();

    reset_sequence(false);
    test_post_finish_pattern();

    // LSU-path tests — shim → slices → port → slave.
    reset_sequence(true);
    {
        printf("test_lsu_single (one LSU store, sanity):\n");
        reg_wr_trace.clear();
        bool ok = lsu_store(0x4A000008u, 0xDEADBEEFu);
        for (int i = 0; i < 30; i++) tick();
        if (!ok) { printf("  FAIL: single store wedged\n"); fails++; }
        else if (reg_wr_trace.size() != 1)
            { printf("  FAIL: expected 1 pulse, got %zu\n", reg_wr_trace.size()); fails++; }
        else { passes++; printf("  PASS  single LSU store landed\n"); }
    }

    reset_sequence(true);
    test_lsu_back_to_back();

    reset_sequence(true);
    test_lsu_post_finish();

    reset_sequence(true);
    test_lsu_bind_then_draw();

    printf("\n=== Results: %d passed, %d failed ===\n", passes, fails);
    delete tb;
    return fails > 0 ? 1 : 0;
}
