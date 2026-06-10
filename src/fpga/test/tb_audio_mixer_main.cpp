//------------------------------------------------------------------------------
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileType: SOURCE
// SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
//------------------------------------------------------------------------------

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
#define VTBL_WPTR        11

#define CTRL_ACTIVE  1u
#define CTRL_STEREO  2u
#define CTRL_LOOP    4u
#define CTRL_STREAM  8u

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

static int abs_i16(int v) {
    return (v < 0) ? -v : v;
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

// Backdoor read. audio_mixer.v keeps the MMIO-visible 16-field voice
// layout in two packed tables: CPU-owned fields and FSM-owned fields.
static uint32_t vtbl_read(int voice, int field) {
    int addr = (voice << 4) | field;
    switch (field) {
    case VTBL_POS_INT:
    case VTBL_POS_FRAC:
    case VTBL_VOL_LR:
        return dut->rootp->tb_audio_mixer->dut__DOT__vtbl_fsm_mem[addr];
    case VTBL_ADDR:
    case VTBL_LEN:
    case VTBL_RATE:
    case VTBL_CTRL:
    case VTBL_LOOP_END:
    case VTBL_LOOP_START:
    case VTBL_VOL_TARGET:
    case VTBL_VOL_RATE:
    case VTBL_WPTR:
        return dut->rootp->tb_audio_mixer->dut__DOT__vtbl_cpu_mem[addr];
    default:
        return 0;
    }
}

static void sdram_fill_constant(uint32_t word) {
    for (int i = 0; i < 1024; i++)
        dut->rootp->tb_audio_mixer->sdram_mem[i] = word;
}

static uint32_t wait_for_sample(int sample_index, const char *name) {
    int seen = 0;
    uint32_t last = 0;

    for (int i = 0; i < 200000 && seen < sample_index; i++) {
        tick(1);
        if (dut->sample_wr) {
            last = dut->sample_data;
            seen++;
        }
    }

    check(seen >= sample_index, name);
    return last;
}

static void program_constant_looped_voice(int voice, uint32_t target_lr) {
    reset_dut();
    sdram_fill_constant(0x40004000);       // two mono s16 samples: +16384, +16384

    mmio_voice_write(voice, VTBL_ADDR,        0);
    mmio_voice_write(voice, VTBL_LEN,         32);
    mmio_voice_write(voice, VTBL_RATE,        0x10000);
    mmio_voice_write(voice, VTBL_LOOP_START,  0);
    mmio_voice_write(voice, VTBL_LOOP_END,    32);
    mmio_voice_write(voice, VTBL_VOL_TARGET,  target_lr);
    mmio_voice_write(voice, VTBL_VOL_RATE,    0);       // snap after first pass
    mmio_voice_write(voice, VTBL_VOL_LR,      0);
    mmio_voice_write(voice, VTBL_CTRL,        1 | 4);   // active | loop, mono
}

static void program_zero_then_post_target_voice(int voice, uint32_t target_lr) {
    reset_dut();
    sdram_fill_constant(0x40004000);       // two mono s16 samples: +16384, +16384

    mmio_voice_write(voice, VTBL_ADDR,        0);
    mmio_voice_write(voice, VTBL_LEN,         32);
    mmio_voice_write(voice, VTBL_RATE,        0x10000);
    mmio_voice_write(voice, VTBL_LOOP_START,  0);
    mmio_voice_write(voice, VTBL_LOOP_END,    32);
    mmio_voice_write(voice, VTBL_VOL_TARGET,  0);       // alloc/play volume 0
    mmio_voice_write(voice, VTBL_VOL_RATE,    8);       // firmware note-on ramp
    mmio_voice_write(voice, VTBL_VOL_LR,      0);
    mmio_voice_write(voice, VTBL_CTRL,        1 | 4);   // active | loop, mono
    mmio_voice_write(voice, VTBL_VOL_TARGET,  target_lr);
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

    // Configure voice 5: small length, snap volume, full stereo target, active.
    mmio_voice_write(5, VTBL_ADDR,        0);
    mmio_voice_write(5, VTBL_LEN,         32);
    mmio_voice_write(5, VTBL_RATE,        0x10000);   // 1.0
    mmio_voice_write(5, VTBL_LOOP_START,  0);
    mmio_voice_write(5, VTBL_LOOP_END,    32);
    mmio_voice_write(5, VTBL_VOL_TARGET,  0xFFFF);     // L=0xFF, R=0xFF
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
    int vol_r = (vol_lr >> 8) & 0xFF;

    // Expected: 0xFF * 0xC0 * 0x80 >> 16  (HW does two >>8 stages)
    //   gxm_2  = (0xC0 * 0x80) >> 8 = 0x60
    //   tgt_l  = (0xFF * 0x60) >> 8 = 0x5F
    int expected = (0xFF * ((0xC0 * 0x80) >> 8)) >> 8;
    check_in_range("voice 5 vol_l after compose", vol_l,
                   expected - 2, expected + 2);
    check_in_range("voice 5 vol_r after compose", vol_r,
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
// Test 3b: master_fade_tracks_settled_voice
//
// Regression guard for any "settled-ramp" fast-path.  Once a voice's
// VOL_LR has converged to its composed target, a *later* change to
// master_vol (or group_vol) MUST still propagate: the composed target is
// raw_vol_target * group_vol * master_vol, so a master fade has to
// re-converge every voice even when its per-voice ramp already looks done.
//
// A fast-path that skips the recompute when cur_vol == old_target without
// noticing gxm changed would leave the voice stuck at full volume while the
// master fades — a silent, ship-it-broken bug.  This test fails loudly in
// that case, and passes on the current always-recompute RTL.
// ---------------------------------------------------------------------
static void test_master_fade_tracks_settled_voice(void) {
    printf("test_master_fade_tracks_settled_voice:\n");

    reset_dut();
    sdram_fill_constant(0x40004000);
    dut->master_vol         = 0xFF;
    dut->group_vol_0        = 0xFF;
    dut->voice_group_packed = 0;            // all voices in group 0

    // Voice 8: full raw target, snap ramp (VOL_RATE=0), looping constant —
    // so VOL_LR settles to the composed target within one sample pass.
    mmio_voice_write(8, VTBL_ADDR,        0);
    mmio_voice_write(8, VTBL_LEN,         32);
    mmio_voice_write(8, VTBL_RATE,        0x10000);
    mmio_voice_write(8, VTBL_LOOP_START,  0);
    mmio_voice_write(8, VTBL_LOOP_END,    32);
    mmio_voice_write(8, VTBL_VOL_TARGET,  0xFFFF);     // L=R=0xFF
    mmio_voice_write(8, VTBL_VOL_RATE,    0);          // snap
    mmio_voice_write(8, VTBL_VOL_LR,      0);
    mmio_voice_write(8, VTBL_CTRL,        1 | 4);      // active | loop

    // Composed target with both stages: raw * ((group*master)>>8) >> 8.
    int exp_full = (0xFF * ((0xFF * 0xFF) >> 8)) >> 8;   // ~0xFD

    // 1) Settle at full master.
    tick(20000);
    int settled = vtbl_read(8, VTBL_VOL_LR) & 0xFF;
    check_in_range("settled vol_l at master=0xFF", settled, exp_full - 3, exp_full + 2);

    // 2) Fade the master DOWN on the already-settled voice — VOL_LR must drop.
    dut->master_vol = 0x40;
    tick(20000);
    int faded_lr = vtbl_read(8, VTBL_VOL_LR) & 0xFFFF;
    int exp_fade = (0xFF * ((0xFF * 0x40) >> 8)) >> 8;   // ~0x3E
    check_in_range("settled vol_l follows master fade to 0x40",
                   faded_lr & 0xFF, exp_fade - 3, exp_fade + 3);
    check_in_range("settled vol_r follows master fade to 0x40",
                   (faded_lr >> 8) & 0xFF, exp_fade - 3, exp_fade + 3);

    // 3) Continue the fade to silence.
    dut->master_vol = 0x00;
    tick(20000);
    check_eq_u32("settled vol_lr follows master fade to 0",
                 vtbl_read(8, VTBL_VOL_LR) & 0xFFFF, 0);

    // 4) Bring the master back — the voice must re-converge upward too.
    dut->master_vol = 0xFF;
    tick(20000);
    check_in_range("settled vol_l recovers when master returns",
                   vtbl_read(8, VTBL_VOL_LR) & 0xFF, exp_full - 3, exp_full + 2);

    // 5) Group fade (gxm = group*master): voice still settled, must follow.
    dut->group_vol_0 = 0x80;
    tick(20000);
    int exp_group = (0xFF * ((0x80 * 0xFF) >> 8)) >> 8;  // ~0x7E
    check_in_range("settled vol_l follows group fade to 0x80",
                   vtbl_read(8, VTBL_VOL_LR) & 0xFF, exp_group - 3, exp_group + 3);
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

// ---------------------------------------------------------------------
// Test 5: explicit_lr_targets
//
// Program a mono constant sample and assert that explicit VOL_TARGET L/R
// bytes survive the full mixer path into sample_data.  This directly
// covers the Duke MultiVoc-style call pattern:
//   alloc/play at volume 0 -> immediately set_vol_lr(voice, L, R).
// The first emitted sample can be silent because VOL_LR starts at 0; with
// VOL_RATE=0 the second and later samples must snap to the requested target.
// ---------------------------------------------------------------------
static void test_explicit_lr_targets(void) {
    printf("test_explicit_lr_targets:\n");

    program_constant_looped_voice(2, 0xFFFF);    // L=255, R=255
    uint32_t center = wait_for_sample(3, "center sample emitted");
    int center_l = (int16_t)(center >> 16);
    int center_r = (int16_t)(center & 0xFFFF);
    check_in_range("center L", center_l, 900, 1100);
    check_in_range("center R", center_r, 900, 1100);

    program_constant_looped_voice(2, 0x00FF);    // L=255, R=0
    uint32_t left = wait_for_sample(3, "hard-left sample emitted");
    int left_l = (int16_t)(left >> 16);
    int left_r = (int16_t)(left & 0xFFFF);
    check_in_range("hard-left L", left_l, 900, 1100);
    check(abs_i16(left_r) <= 2, "hard-left R muted");

    program_constant_looped_voice(2, 0xFF00);    // L=0, R=255
    uint32_t right = wait_for_sample(3, "hard-right sample emitted");
    int right_l = (int16_t)(right >> 16);
    int right_r = (int16_t)(right & 0xFFFF);
    check(abs_i16(right_l) <= 2, "hard-right L muted");
    check_in_range("hard-right R", right_r, 900, 1100);

    program_zero_then_post_target_voice(2, 0x00FF);     // Duke 3D sequence
    uint32_t post = wait_for_sample(40, "post-start set_vol_lr sample emitted");
    int post_l = (int16_t)(post >> 16);
    int post_r = (int16_t)(post & 0xFFFF);
    check_in_range("post-start hard-left L", post_l, 900, 1100);
    check(abs_i16(post_r) <= 2, "post-start hard-left R muted");
}

// ---------------------------------------------------------------------
// Stream-mode (ctrl[3]) tests — the voice-31 stale-replay fix.
//
// A stream voice carries a CPU-published write pointer (VTBL_WPTR).
// The FSM must never fetch at/past it: backlog < STREAM_LOW (256) fades
// the voice's effective volume target to 0 via the existing ramp;
// backlog < STREAM_FLOOR (4) holds position in silence, resuming
// automatically when WPTR advances.
// ---------------------------------------------------------------------

static uint32_t read_pos(int voice) {
    dut->voice_sel_rd = voice;
    tick(4);
    return dut->pos_readback & 0x3FFFFF;
}

// Run until `n` output samples were emitted; returns the largest
// |L|/|R| magnitude seen across them (for poison/fade assertions).
static int run_samples(int n, int *out_seen = nullptr) {
    int seen = 0, max_mag = 0;
    for (int i = 0; i < 400000 && seen < n; i++) {
        tick(1);
        if (dut->sample_wr) {
            int l = abs_i16((int16_t)(dut->sample_data >> 16));
            int r = abs_i16((int16_t)(dut->sample_data & 0xFFFF));
            if (l > max_mag) max_mag = l;
            if (r > max_mag) max_mag = r;
            seen++;
        }
    }
    if (out_seen) *out_seen = seen;
    return max_mag;
}

static void sdram_fill_range(int lo, int hi, uint32_t word) {
    for (int i = lo; i < hi; i++)
        dut->rootp->tb_audio_mixer->sdram_mem[i] = word;
}

// Test 6: the centerpiece — starve → fade → hold → resume, with the
// unwritten ring region poisoned at full scale.  Constant +4096 input
// at full volume mixes to ~255 per channel (accum >>> 4); poison would
// show up around ~2040.  The max_araddr_seen monitor is the strict
// "never fetched past WPTR" assertion.
static void test_stream_hold_resume(void) {
    printf("test_stream_hold_resume:\n");

    reset_dut();

    const int V = 30;
    const uint32_t DATA   = 0x10001000;  // stereo pair L=R=+4096
    const uint32_t POISON = 0x7FFF7FFF;

    sdram_fill_range(0, 512, DATA);      // producer wrote pairs [0, 512)
    sdram_fill_range(512, 1024, POISON); // unwritten tail

    mmio_voice_write(V, VTBL_ADDR,        0);
    mmio_voice_write(V, VTBL_LEN,         1024);   // ring of 1024 stereo pairs
    mmio_voice_write(V, VTBL_RATE,        0x10000);
    mmio_voice_write(V, VTBL_LOOP_START,  0);
    mmio_voice_write(V, VTBL_LOOP_END,    1024);
    mmio_voice_write(V, VTBL_VOL_TARGET,  0xFFFF);
    mmio_voice_write(V, VTBL_VOL_RATE,    4);
    mmio_voice_write(V, VTBL_VOL_LR,      0);
    mmio_voice_write(V, VTBL_WPTR,        512);
    mmio_voice_write(V, VTBL_CTRL, CTRL_ACTIVE | CTRL_STEREO | CTRL_LOOP | CTRL_STREAM);

    // Phase 1: play through the written region into the hold.
    // 512 pairs at rate 1.0 = ~512 samples; run 600 to be past it.
    int mag_play = run_samples(300);
    check_in_range("stream plays written region (post ramp-in mag)", mag_play, 150, 400);

    run_samples(300);                       // walk the rest + fade + hold
    uint32_t held = read_pos(V);
    check_in_range("held position just below WPTR", (int)held, 506, 511);
    check_eq_u32("faded to silence at hold (VOL_LR)", vtbl_read(V, VTBL_VOL_LR) & 0xFFFFu, 0);

    int mag_held = run_samples(64);
    check(mag_held <= 2, "held voice outputs silence");
    uint32_t held2 = read_pos(V);
    check_eq_u32("position frozen while held", held2, held);
    check(dut->max_araddr_seen < 512 * 4,
          "never fetched at/past WPTR (poison untouched)");
    check((dut->voice_active_mask & (1u << V)) != 0, "held stream voice stays active");
    check((dut->voice_end_pending & (1u << V)) == 0, "held stream voice raises no end IRQ");

    // Phase 2: producer refills [512, 1020) and publishes — must resume
    // by itself, ramp back in, and hold again just below the new WPTR.
    sdram_fill_range(512, 1020, DATA);
    mmio_voice_write(V, VTBL_WPTR, 1020);

    int mag_resumed = run_samples(400);
    check_in_range("auto-resume after WPTR advance (mag)", mag_resumed, 150, 400);
    run_samples(300);
    uint32_t held3 = read_pos(V);
    check_in_range("re-held just below new WPTR", (int)held3, 1014, 1019);
    check(dut->max_araddr_seen < 1020 * 4,
          "still never fetched past WPTR after resume");

    // Phase 3: WPTR wraps numerically below pos — modular distance must
    // let the voice play through the ring seam and hold below the new
    // pointer.  [1020,1024) + [0,512) already hold real data.
    sdram_fill_range(1020, 1024, DATA);
    mmio_voice_write(V, VTBL_WPTR, 400);

    int mag_wrap = run_samples(300);
    check_in_range("plays across ring seam after wrapped WPTR (mag)", mag_wrap, 150, 400);
    run_samples(300);
    uint32_t held4 = read_pos(V);
    check_in_range("held below wrapped WPTR", (int)held4, 394, 399);
}

// Test 7: edge cases — empty ring at voice start (must not fetch at
// all), then a full ring (gap = LEN-1) must NOT spuriously hold.
static void test_stream_empty_and_full(void) {
    printf("test_stream_empty_and_full:\n");

    reset_dut();

    const int V = 31;
    sdram_fill_range(0, 1024, 0x7FFF7FFF);   // all poison: nothing is valid yet

    mmio_voice_write(V, VTBL_ADDR,        0);
    mmio_voice_write(V, VTBL_LEN,         1024);
    mmio_voice_write(V, VTBL_RATE,        0x10000);
    mmio_voice_write(V, VTBL_LOOP_START,  0);
    mmio_voice_write(V, VTBL_LOOP_END,    1024);
    mmio_voice_write(V, VTBL_VOL_TARGET,  0xFFFF);
    mmio_voice_write(V, VTBL_VOL_RATE,    4);
    mmio_voice_write(V, VTBL_VOL_LR,      0);
    mmio_voice_write(V, VTBL_WPTR,        0);     // empty: wptr == pos == 0
    mmio_voice_write(V, VTBL_CTRL, CTRL_ACTIVE | CTRL_STEREO | CTRL_LOOP | CTRL_STREAM);

    int mag_empty = run_samples(128);
    check(mag_empty == 0, "empty stream voice is silent");
    check_eq_u32("empty stream voice holds at 0", read_pos(V), 0);
    check_eq_u32("empty stream voice issues NO fetch", dut->max_araddr_seen, 0);

    // Full ring: producer wrote everything but one pair (the firmware's
    // reserve-one-pair invariant): wptr = LEN-1 with pos = 0.
    sdram_fill_range(0, 1023, 0x10001000);
    mmio_voice_write(V, VTBL_WPTR, 1023);
    int mag_full = run_samples(400);
    check_in_range("full ring plays without spurious hold (mag)", mag_full, 150, 400);
    uint32_t pos_full = read_pos(V);
    check(pos_full > 200, "full-ring position advances freely");
}

// Test 7b: stream voice at the firmware-clamped MAXIMUM rate (2.0).
// Position steps by up to 2 (+1 frac carry) per pass, the worst case
// the STREAM_FLOOR=4 margin must absorb: the voice must still hold
// without ever fetching at/past WPTR, and resume cleanly.
static void test_stream_max_rate(void) {
    printf("test_stream_max_rate:\n");

    reset_dut();

    const int V = 29;
    sdram_fill_range(0, 512, 0x10001000);
    sdram_fill_range(512, 1024, 0x7FFF7FFF);   // poison past WPTR

    mmio_voice_write(V, VTBL_ADDR,        0);
    mmio_voice_write(V, VTBL_LEN,         1024);
    mmio_voice_write(V, VTBL_RATE,        0x20000);   // 2.0 — the clamp max
    mmio_voice_write(V, VTBL_LOOP_START,  0);
    mmio_voice_write(V, VTBL_LOOP_END,    1024);
    mmio_voice_write(V, VTBL_VOL_TARGET,  0xFFFF);
    mmio_voice_write(V, VTBL_VOL_RATE,    4);
    mmio_voice_write(V, VTBL_VOL_LR,      0);
    mmio_voice_write(V, VTBL_WPTR,        512);
    mmio_voice_write(V, VTBL_CTRL, CTRL_ACTIVE | CTRL_STEREO | CTRL_LOOP | CTRL_STREAM);

    int mag = run_samples(200);
    check_in_range("rate-2.0 stream plays written region (mag)", mag, 150, 400);
    run_samples(200);                       // walk into the hold
    uint32_t held = read_pos(V);
    check_in_range("rate-2.0 held just below WPTR", (int)held, 504, 511);
    check(dut->max_araddr_seen < 512 * 4,
          "rate-2.0 never fetched at/past WPTR");

    sdram_fill_range(512, 1020, 0x10001000);
    mmio_voice_write(V, VTBL_WPTR, 1020);
    int mag2 = run_samples(300);
    check_in_range("rate-2.0 auto-resume (mag)", mag2, 150, 400);
    check(dut->max_araddr_seen < 1020 * 4,
          "rate-2.0 still never fetched past WPTR after resume");
}

// Test 8: control — a legacy looping voice (ctrl[3]=0) must IGNORE the
// WPTR field entirely and wrap/replay exactly as before (that is the
// pre-fix behavior the rest of the OS relies on for non-stream voices).
static void test_legacy_loop_ignores_wptr(void) {
    printf("test_legacy_loop_ignores_wptr:\n");

    reset_dut();

    const int V = 6;
    sdram_fill_range(0, 1024, 0x10001000);

    mmio_voice_write(V, VTBL_ADDR,        0);
    mmio_voice_write(V, VTBL_LEN,         256);
    mmio_voice_write(V, VTBL_RATE,        0x10000);
    mmio_voice_write(V, VTBL_LOOP_START,  0);
    mmio_voice_write(V, VTBL_LOOP_END,    256);
    mmio_voice_write(V, VTBL_VOL_TARGET,  0xFFFF);
    mmio_voice_write(V, VTBL_VOL_RATE,    0);
    mmio_voice_write(V, VTBL_VOL_LR,      0);
    mmio_voice_write(V, VTBL_WPTR,        8);     // would starve a stream voice
    mmio_voice_write(V, VTBL_CTRL, CTRL_ACTIVE | CTRL_LOOP);   // mono, NO stream

    int mag = run_samples(600);
    check_in_range("legacy loop voice plays past a stale WPTR (mag)", mag, 150, 400);
    check((dut->voice_active_mask & (1u << V)) != 0, "legacy loop voice still active");
    // It must have looped (pos wrapped at 256), i.e. fetched well past
    // WPTR=8 — replay-on-wrap is the legacy contract.
    check(dut->max_araddr_seen >= 200 * 2, "legacy voice fetched past WPTR");
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
    test_master_fade_tracks_settled_voice();

    reset_dut();
    test_voice_end_irq();

    reset_dut();
    test_explicit_lr_targets();

    test_stream_hold_resume();

    test_stream_empty_and_full();

    test_stream_max_rate();

    test_legacy_loop_ignores_wptr();

    printf("\n=== Results: %d passed, %d failed ===\n", passes, fails);

    delete dut;
    return fails == 0 ? 0 : 1;
}
