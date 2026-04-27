// Audio mixer (audio_mixer.v v3) Verilator test harness.
//
// Drives the same MMIO interface that axi_periph_slave drives in the
// real design — voice_wr pulse + voice_field/voice_sel/voice_wdata.
// The flat-addressing decode lives one level up in the slave, but the
// race-free property is a property of the underlying interface (no SEL
// latch register state), and that's what these tests exercise.
//
// VTBL field indices match audio_mixer.v's localparams.

#include "Vtb_audio_mixer.h"
#include "Vtb_audio_mixer___024root.h"
#include "Vtb_audio_mixer_tb_audio_mixer.h"
#include <verilated.h>

#include <cstdio>
#include <cstdint>
#include <cstdlib>

#define VTBL_ADDR        0
#define VTBL_LEN         1
#define VTBL_RATE        2
#define VTBL_CTRL        3
#define VTBL_POS_INT     4
#define VTBL_POS_FRAC    5
#define VTBL_VOL_LR      6
#define VTBL_LOOP_END    7
#define VTBL_LOOP_START  8
#define VTBL_VOL_TARGET  9
#define VTBL_VOL_RATE    10

static Vtb_audio_mixer *dut;
static vluint64_t        sim_time;
static int               passes;
static int               fails;

static void tick(int n = 1) {
    for (int i = 0; i < n; i++) {
        dut->clk = 0;
        dut->eval();
        sim_time++;
        dut->clk = 1;
        dut->eval();
        sim_time++;
    }
}

static void reset_inputs(void) {
    dut->voice_wr             = 0;
    dut->voice_field          = 0;
    dut->voice_sel            = 0;
    dut->voice_sel_rd         = 0;
    dut->voice_wdata          = 0;
    dut->master_vol           = 0xFF;
    dut->group_vol_0          = 0xFF;
    dut->group_vol_1          = 0xFF;
    dut->group_vol_2          = 0xFF;
    dut->group_vol_3          = 0xFF;
    dut->voice_group_packed   = 0;
    dut->irq_clear_wr         = 0;
    dut->irq_clear            = 0;
}

static void reset_dut(void) {
    reset_inputs();
    dut->reset_n = 0;
    tick(20);
    dut->reset_n = 1;
    tick(20);
}

static void check(bool cond, const char *name) {
    if (cond) { passes++; printf("  OK   %s\n", name); }
    else      { fails++;  printf("  FAIL %s\n", name); }
}

static void check_eq_u32(const char *name, uint32_t got, uint32_t expect) {
    if (got == expect) { passes++; printf("  OK   %s (0x%08x)\n", name, got); }
    else { fails++; printf("  FAIL %s got=0x%08x expect=0x%08x\n", name, got, expect); }
}

static void check_in_range(const char *name, int got, int lo, int hi) {
    if (got >= lo && got <= hi) {
        passes++; printf("  OK   %s = %d (expected [%d..%d])\n", name, got, lo, hi);
    } else {
        fails++;  printf("  FAIL %s = %d (expected [%d..%d])\n", name, got, lo, hi);
    }
}

// One-cycle pulse on voice_wr with the given field/voice/data.
static void mmio_voice_write(int voice, int field, uint32_t data) {
    dut->voice_sel   = voice;
    dut->voice_field = field;
    dut->voice_wdata = data;
    dut->voice_wr    = 1;
    tick(1);
    dut->voice_wr    = 0;
    tick(1);
}

// Backdoor read.  audio_mixer.v splits the voice table into two memories
// (see is_fsm_field): POS_INT (4), POS_FRAC (5), VOL_LR (6) live in
// vtbl_fsm; everything else lives in vtbl_cpu.  Pick the right backing
// store for the requested field.
static uint32_t vtbl_read(int voice, int field) {
    uint32_t addr = (voice << 4) | (field & 0xF);
    int is_fsm = (field == VTBL_POS_INT) ||
                 (field == VTBL_POS_FRAC) ||
                 (field == VTBL_VOL_LR);
    return is_fsm
        ? dut->rootp->tb_audio_mixer->dut__DOT__vtbl_fsm_mem[addr]
        : dut->rootp->tb_audio_mixer->dut__DOT__vtbl_cpu_mem[addr];
}

// ---------------------------------------------------------------------
// Test 1: flat_mmio_independence
//
// Two back-to-back per-voice writes for distinct voices land in distinct
// vtbl entries with no cross-talk.  Tests the fundamental property that
// the new interface relies on: voice index is per-write, not latched.
// ---------------------------------------------------------------------
static void test_flat_mmio_independence(void) {
    printf("test_flat_mmio_independence:\n");

    // Voice 0: VTBL_RATE = 0xDEAD0001
    mmio_voice_write(0, VTBL_RATE, 0xDEAD0001);
    // Voice 31: VTBL_RATE = 0xBEEF0001 (note: low bit cleared by sample loop;
    // we just use distinct values).
    mmio_voice_write(31, VTBL_RATE, 0xBEEF0001);

    check_eq_u32("voice  0 RATE", vtbl_read(0,  VTBL_RATE), 0xDEAD0001);
    check_eq_u32("voice 31 RATE", vtbl_read(31, VTBL_RATE), 0xBEEF0001);

    // Same with VTBL_VOL_TARGET — different field, different voices.
    mmio_voice_write(7,  VTBL_VOL_TARGET, 0x1234);
    mmio_voice_write(15, VTBL_VOL_TARGET, 0x5678);
    check_eq_u32("voice  7 VOL_TARGET", vtbl_read(7,  VTBL_VOL_TARGET), 0x1234);
    check_eq_u32("voice 15 VOL_TARGET", vtbl_read(15, VTBL_VOL_TARGET), 0x5678);
}

// ---------------------------------------------------------------------
// Test 2: group_composition
//
// Configure a voice with vol_target=0xFF, group=2, master=0x80,
// group_vol[2]=0xC0.  After running long enough for the FSM's per-channel
// volume ramp to reach the (composed) target, the voice's VOL_LR field
// should sit near 0xFF * 0xC0 * 0x80 / 65536 ≈ 0x60.
//
// We snap the volume so the ramp doesn't add latency: write VOL_RATE=0
// (snap mode in audio_mixer.v's ramp_step()).
// ---------------------------------------------------------------------
static void test_group_composition(void) {
    printf("test_group_composition:\n");

    reset_dut();

    // Set master + group state
    dut->master_vol  = 0x80;
    dut->group_vol_2 = 0xC0;
    // voice 5 is in group 2 (low 2 bits of nibble 5 in voice_group_packed)
    dut->voice_group_packed = ((uint64_t)2) << (5 * 2);

    // Configure voice 5: small length, snap volume, full target, active.
    mmio_voice_write(5, VTBL_ADDR,        0);
    mmio_voice_write(5, VTBL_LEN,         32);
    mmio_voice_write(5, VTBL_RATE,        0x10000);   // 1.0
    mmio_voice_write(5, VTBL_LOOP_START,  0);
    mmio_voice_write(5, VTBL_LOOP_END,    32);
    mmio_voice_write(5, VTBL_VOL_TARGET,  0x00FF);     // L=0xFF, R=0x00
    mmio_voice_write(5, VTBL_VOL_RATE,    0);          // snap to target
    mmio_voice_write(5, VTBL_VOL_LR,      0);          // start at 0
    mmio_voice_write(5, VTBL_CTRL,        1 | 4);      // active | loop

    // Let the mixer FSM cycle through several full sample passes.  Each
    // sample takes ~ 32 voices × ~30 cycles + overhead; 8 samples is
    // plenty for the ramp_step (snap mode) to settle the L target into
    // VTBL_VOL_LR.
    tick(20000);

    uint32_t vol_lr = vtbl_read(5, VTBL_VOL_LR) & 0xFFFF;
    int vol_l = vol_lr & 0xFF;

    // Expected: 0xFF * 0xC0 * 0x80 >> 16  (HW does two >>8 stages)
    //   gxm_2  = (0xC0 * 0x80) >> 8 = 0x60
    //   tgt_l  = (0xFF * 0x60) >> 8 = 0x5F
    int expected = (0xFF * ((0xC0 * 0x80) >> 8)) >> 8;
    check_in_range("voice 5 vol_l after compose", vol_l,
                   expected - 2, expected + 2);
}

// ---------------------------------------------------------------------
// Test 3: master_mute
//
// master_vol = 0 should drive all per-voice composed targets to zero,
// so vol_lr settles to 0 regardless of vol_target / group_vol.
// ---------------------------------------------------------------------
static void test_master_mute(void) {
    printf("test_master_mute:\n");

    reset_dut();

    dut->master_vol  = 0;       // total mute
    dut->group_vol_0 = 0xFF;
    dut->group_vol_1 = 0xFF;

    // voice 12: vol_target full, group 0, snap.
    mmio_voice_write(12, VTBL_LEN,         32);
    mmio_voice_write(12, VTBL_RATE,        0x10000);
    mmio_voice_write(12, VTBL_LOOP_START,  0);
    mmio_voice_write(12, VTBL_LOOP_END,    32);
    mmio_voice_write(12, VTBL_VOL_TARGET,  0xFFFF);     // both channels full
    mmio_voice_write(12, VTBL_VOL_RATE,    0);
    mmio_voice_write(12, VTBL_VOL_LR,      0xFFFF);     // start full to verify it ramps DOWN
    mmio_voice_write(12, VTBL_CTRL,        1 | 4);      // active | loop

    tick(20000);

    uint32_t vol_lr = vtbl_read(12, VTBL_VOL_LR) & 0xFFFF;
    check_eq_u32("voice 12 vol_lr after master=0", vol_lr, 0);
}

// ---------------------------------------------------------------------
// Test 4: voice_end_irq
//
// Configure a non-looping voice and let it walk off the end.  HW must
// raise the corresponding bit in voice_end_pending; W1C must clear it.
// ---------------------------------------------------------------------
static void test_voice_end_irq(void) {
    printf("test_voice_end_irq:\n");

    reset_dut();

    mmio_voice_write(3, VTBL_ADDR,        0);
    mmio_voice_write(3, VTBL_LEN,         32);
    mmio_voice_write(3, VTBL_RATE,        0x80000);  // 8x → walks off fast
    mmio_voice_write(3, VTBL_LOOP_START,  0);
    mmio_voice_write(3, VTBL_LOOP_END,    32);
    mmio_voice_write(3, VTBL_VOL_TARGET,  0xFFFF);
    mmio_voice_write(3, VTBL_VOL_RATE,    0);
    mmio_voice_write(3, VTBL_VOL_LR,      0);
    mmio_voice_write(3, VTBL_CTRL,        1);  // active, no loop

    tick(40000);

    bool got_irq = (dut->voice_end_pending & (1u << 3)) != 0;
    check(got_irq, "voice 3 voice_end_pending bit set");

    // Verify W1C clears it.
    dut->irq_clear    = (1u << 3);
    dut->irq_clear_wr = 1;
    tick(2);
    dut->irq_clear_wr = 0;
    tick(2);
    bool cleared = (dut->voice_end_pending & (1u << 3)) == 0;
    check(cleared, "voice 3 voice_end_pending cleared by W1C");
}

int main(int argc, char **argv) {
    Verilated::commandArgs(argc, argv);
    dut = new Vtb_audio_mixer;

    printf("=== Audio mixer (audio_mixer.v v3) Test Suite ===\n\n");

    reset_dut();
    test_flat_mmio_independence();

    reset_dut();
    test_group_composition();

    reset_dut();
    test_master_mute();

    reset_dut();
    test_voice_end_irq();

    printf("\n=== Results: %d passed, %d failed ===\n", passes, fails);

    delete dut;
    return fails == 0 ? 0 : 1;
}
