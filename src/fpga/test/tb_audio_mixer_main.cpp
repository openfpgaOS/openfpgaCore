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
#include <cstring>
#include <vector>

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
static bool report_perf = std::getenv("AUDIO_PERF_REPORT") != nullptr;
static uint64_t sample_trace = 14695981039346656037ull;
static unsigned sample_count = 0;

static void tick(int n = 1) {
    for (int i = 0; i < n; i++) {
        dut->clk = 0;
        dut->eval();
        sim_time++;
        dut->clk = 1;
        dut->eval();
        sim_time++;
        if (report_perf && dut->sample_wr) {
            sample_trace = (sample_trace ^ sim_time) * 1099511628211ull;
            sample_trace = (sample_trace ^ dut->sample_data) * 1099511628211ull;
            ++sample_count;
        }
    }
}

static void reset_inputs(void) {
    dut->voice_wr             = 0;
    dut->stall_sdram          = 0;
    dut->sdram_latency        = 0;
    dut->mister_output_enable = 0;
    dut->mister_clk_is90      = 0;
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
// layout at MMIO; the FSM-owned backing table uses a four-word stride.
static uint32_t vtbl_read(int voice, int field) {
    int addr = (voice << 4) | field;
    switch (field) {
    case VTBL_POS_INT:
    case VTBL_POS_FRAC:
    case VTBL_VOL_LR:
        return dut->rootp->tb_audio_mixer->dut__DOT__vtbl_fsm_mem[(voice << 2) | (field & 3)];
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

// Exercise every voice through MMIO so compact mutable-table addressing
// cannot alias neighboring voices or POS_INT and VOL_LR. Voices stay inactive;
// queued start-state writes must drain without the sample FSM changing them.
static void test_mutable_fields_all_voices(void) {
    printf("test_mutable_fields_all_voices:\n");
    reset_dut();
    for (int voice = 0; voice < 32; ++voice) {
        mmio_voice_write(voice, VTBL_CTRL, 0);
        mmio_voice_write(voice, VTBL_POS_INT, 0x200000u + voice * 257u);
        mmio_voice_write(voice, VTBL_VOL_LR, 0x8000u + voice * 131u);
    }
    tick(2000);
    bool intact = true;
    for (int voice = 0; voice < 32; ++voice)
        intact &= vtbl_read(voice, VTBL_POS_INT) == 0x200000u + voice * 257u
               && vtbl_read(voice, VTBL_VOL_LR) == 0x8000u + voice * 131u;
    check(intact, "all 32 voices retain independent mutable fields");
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

// Empty streams must not carry their quiet flag into a following ordinary
// voice, including the transition from voice 31 to voice 0 on the next pass.
static void test_stream_quiet_is_per_voice() {
    printf("test_stream_quiet_is_per_voice:\n");
    program_constant_looped_voice(0, 0x4040);
    uint32_t reference = wait_for_sample(64, "ordinary voice settles before empty streams");
    check(reference != 0, "ordinary voice produces nonzero PCM");

    for (int voice : {1, 31}) {
        mmio_voice_write(voice, VTBL_ADDR, 0);
        mmio_voice_write(voice, VTBL_LEN, 32);
        mmio_voice_write(voice, VTBL_RATE, 0x10000);
        mmio_voice_write(voice, VTBL_LOOP_START, 0);
        mmio_voice_write(voice, VTBL_LOOP_END, 32);
        mmio_voice_write(voice, VTBL_WPTR, 0);
        mmio_voice_write(voice, VTBL_VOL_LR, 0);
        mmio_voice_write(voice, VTBL_VOL_TARGET, 0xFFFF);
        mmio_voice_write(voice, VTBL_VOL_RATE, 0);
        mmio_voice_write(voice, VTBL_CTRL, CTRL_ACTIVE | CTRL_LOOP | CTRL_STREAM);
    }
    wait_for_sample(64, "empty streams settle");
    bool exact = true;
    int samples = 0;
    for (int cycles = 0; cycles < 200000 && samples < 128; ++cycles) {
        tick();
        if (dut->sample_wr) {
            exact &= dut->sample_data == reference;
            ++samples;
        }
    }
    check(samples == 128 && exact, "empty streams leave ordinary PCM bit-identical");
}

// A long SDRAM stall prevents the mixer from draining CPU POS/VOL writes.
// More than one full queue of retriggers must eventually reach the voice table.
static void test_queued_writes_under_memory_stall() {
    printf("test_queued_writes_under_memory_stall:\n");
    reset_dut();
    sdram_fill_range(0, 1024, 0x10001000);
    mmio_voice_write(0, VTBL_LEN, 32);
    mmio_voice_write(0, VTBL_LOOP_END, 32);
    mmio_voice_write(0, VTBL_RATE, 65536);
    mmio_voice_write(0, VTBL_CTRL, CTRL_ACTIVE | CTRL_LOOP);
    dut->stall_sdram = 1;
    int waited = 0;
    while (!dut->read_pending && waited++ < 5000) tick();
    check(dut->read_pending, "mixer read held by SDRAM stall");
    int stalled = 0;
    for (int i = 0; i < 80; ++i) {
        int timeout = 0;
        while (!dut->voice_wr_ready && timeout++ < 5000) {
            ++stalled;
            if (stalled == 100) dut->stall_sdram = 0;
            tick();
        }
        check(dut->voice_wr_ready, "queued write can reserve a slot");
        if (!dut->voice_wr_ready) break;
        mmio_voice_write(31, VTBL_POS_INT, 0x100 + i);
    }
    check(stalled >= 100, "full queue backpressures the writer");
    dut->stall_sdram = 0;
    tick(1000);
    check_eq_u32("all queued writes reach inactive voice", vtbl_read(31, VTBL_POS_INT), 0x14F);
}

static void test_rearm_during_sample() {
    printf("test_rearm_during_sample:\n");
    for (int delay : {0, 1, 2, 5, 15, 40}) {
        reset_dut();
        sdram_fill_constant(0x40004000);
        mmio_voice_write(0, VTBL_ADDR, 0);
        mmio_voice_write(0, VTBL_LEN, 1);
        mmio_voice_write(0, VTBL_RATE, 65536);
        mmio_voice_write(0, VTBL_POS_INT, 0);
        mmio_voice_write(0, VTBL_VOL_LR, 0xffff);
        dut->stall_sdram = 1;
        mmio_voice_write(0, VTBL_CTRL, CTRL_ACTIVE);
        int timeout = 0;
        while (!dut->read_pending && timeout++ < 5000) tick();
        check(dut->read_pending, "old one-shot has an outstanding read");

        mmio_voice_write(0, VTBL_CTRL, 0);
        dut->irq_clear = 1;
        dut->irq_clear_wr = 1;
        tick();
        dut->irq_clear_wr = 0;
        mmio_voice_write(0, VTBL_ADDR, 128);
        mmio_voice_write(0, VTBL_LEN, 64);
        mmio_voice_write(0, VTBL_RATE, 65536);
        mmio_voice_write(0, VTBL_POS_INT, 0);
        mmio_voice_write(0, VTBL_LOOP_START, 0);
        mmio_voice_write(0, VTBL_LOOP_END, 64);
        mmio_voice_write(0, VTBL_VOL_LR, 0);
        mmio_voice_write(0, VTBL_VOL_TARGET, 0xffff);
        mmio_voice_write(0, VTBL_VOL_RATE, 0);
        tick(delay);
        mmio_voice_write(0, VTBL_CTRL, CTRL_ACTIVE | CTRL_LOOP);
        dut->stall_sdram = 0;
        tick(2000);
        check_eq_u32("retired sample cannot end the replacement", dut->voice_end_pending, 0);
        check_eq_u32("replacement stays active", dut->voice_active_mask, 1);
        check(vtbl_read(0, VTBL_POS_INT) < 64, "replacement position stays inside its sample");
    }
}

static void test_rearm_phase_sweep() {
    printf("test_rearm_phase_sweep:\n");
    int errors = 0, cases = 0;
    uint64_t states = 0;
    for (int latency : {0, 13, 71}) {
        for (int offset = 0; offset < 96; ++offset) {
            reset_dut();
            sdram_fill_constant(0x20002000);
            dut->sdram_latency = latency;
            mmio_voice_write(0, VTBL_ADDR, 0);
            mmio_voice_write(0, VTBL_LEN, 32);
            mmio_voice_write(0, VTBL_RATE, 65536);
            mmio_voice_write(0, VTBL_POS_INT, 31);
            mmio_voice_write(0, VTBL_VOL_LR, 0x8080);
            mmio_voice_write(0, VTBL_CTRL, CTRL_ACTIVE);
            int timeout = 0;
            auto *tb = dut->rootp->tb_audio_mixer;
            while ((tb->__PVT__dut__DOT__cur_voice != 0 ||
                    tb->__PVT__dut__DOT__state != 3) && timeout++ < 5000) tick();
            if (timeout >= 5000) { ++errors; continue; }
            tick(offset);
            if (tb->__PVT__dut__DOT__cur_voice == 0)
                states |= uint64_t(1) << tb->__PVT__dut__DOT__state;

            mmio_voice_write(0, VTBL_CTRL, 0);
            dut->irq_clear = 1; dut->irq_clear_wr = 1; tick();
            dut->irq_clear_wr = 0;
            mmio_voice_write(0, VTBL_ADDR, 128);
            mmio_voice_write(0, VTBL_LEN, 4);
            mmio_voice_write(0, VTBL_POS_INT, 0);
            mmio_voice_write(0, VTBL_LOOP_START, 0);
            mmio_voice_write(0, VTBL_LOOP_END, 4);
            mmio_voice_write(0, VTBL_VOL_LR, 0);
            mmio_voice_write(0, VTBL_VOL_TARGET, 0xffff);
            mmio_voice_write(0, VTBL_VOL_RATE, 0);
            mmio_voice_write(0, VTBL_CTRL, CTRL_ACTIVE | CTRL_LOOP);
            tick(2500);
            if (dut->voice_end_pending || dut->voice_active_mask != 1 ||
                dut->max_araddr_seen > 132 || vtbl_read(0, VTBL_POS_INT) >= 4) {
                if (errors < 4)
                    printf("  phase failure latency=%d offset=%d irq=%x addr=%x\n",
                           latency, offset, unsigned(dut->voice_end_pending),
                           unsigned(dut->max_araddr_seen));
                ++errors;
            }
            ++cases;
        }
    }
    check(cases == 288 && errors == 0, "288 stop/rearm interleavings preserve DMA bounds and ownership");
    check((states & ((uint64_t(1) << 8) | (uint64_t(1) << 16) | (uint64_t(1) << 28))) ==
                   ((uint64_t(1) << 8) | (uint64_t(1) << 16) | (uint64_t(1) << 28)),
          "sweep reaches parameter capture, outstanding reads and retirement");
}

// Compare the real mixer's buffered PCM with and without SDRAM stalls.
static std::vector<uint32_t> capture_mister_audio(bool is90, bool stalls) {
    reset_dut();
    dut->mister_output_enable = 1;
    dut->mister_clk_is90 = is90;
    for (int i = 0; i < 512; ++i) {
        uint16_t a = i * 64 + 16, b = i * 64 + 48;
        dut->rootp->tb_audio_mixer->sdram_mem[i] = (uint32_t(b) << 16) | a;
    }
    mmio_voice_write(0, VTBL_ADDR, 0);
    mmio_voice_write(0, VTBL_LEN, 1024);
    mmio_voice_write(0, VTBL_RATE, 65536);
    mmio_voice_write(0, VTBL_POS_INT, 0);
    mmio_voice_write(0, VTBL_LOOP_START, 0);
    mmio_voice_write(0, VTBL_LOOP_END, 1024);
    mmio_voice_write(0, VTBL_VOL_TARGET, 0xffff);
    mmio_voice_write(0, VTBL_VOL_RATE, 0);
    mmio_voice_write(0, VTBL_VOL_LR, 0);
    mmio_voice_write(0, VTBL_CTRL, CTRL_ACTIVE | CTRL_LOOP);
    std::vector<uint32_t> samples;
    int last = 0, last_cycle = 0, reads = 0, cadence_errors = 0;
    bool pending = false;
    for (int cycle = 0; cycle < 2000000 && samples.size() < 400; ++cycle) {
        tick();
        if (dut->read_pending && !pending) {
            ++reads;
            dut->sdram_latency = stalls ? (reads % 37 == 0 ? 6000 :
                                           100 + (reads * 317) % 1500) : 0;
        }
        pending = dut->read_pending;
        if (dut->mister_audio_l != last) {
            if (samples.size() > 20) {
                int interval = cycle - last_cycle;
                int low = is90 ? 1875 : 2083;
                if (interval < low || interval > low + 1) ++cadence_errors;
            }
            samples.push_back((uint32_t(dut->mister_audio_l) << 16) | dut->mister_audio_r);
            last = dut->mister_audio_l;
            last_cycle = cycle;
        }
    }
    check(samples.size() == 400, "real mixer produces 400 buffered stereo samples");
    check(cadence_errors == 0, "real mixer output keeps 48 kHz cadence through memory stalls");
    return samples;
}

static void test_mister_paced_mixer() {
    printf("test_mister_paced_mixer:\n");
    for (bool is90 : {false, true}) {
        auto reference = capture_mister_audio(is90, false);
        auto delayed = capture_mister_audio(is90, true);
        for (size_t i = 0; i < reference.size() && i < delayed.size(); ++i) {
            if (reference[i] != delayed[i]) {
                printf("  first PCM mismatch at %zu: %08x vs %08x\n",
                       i, reference[i], delayed[i]);
                break;
            }
        }
        check(reference == delayed, "SDRAM latency leaves buffered PCM bit-identical");
    }
}

// A capacity measurement, not a board timing model. Sweep a fixed additional
// first-beat latency with the mixer free-running; compare the measured work
// per sample against 2083.33 cycles at 100 MHz / 1875 at 90 MHz (48 kHz).
static void measure_throughput() {
    printf("voices,read_delay_cycles,mean_cycles,max_cycles,max_khz_100,max_khz_90\n");
    for (int voices : {1, 8, 16, 20, 28, 32}) {
        for (int delay : {0, 16, 32, 48, 64, 96}) {
            reset_dut();
            sdram_fill_constant(0x10001000);
            dut->sdram_latency = delay;
            for (int v = 0; v < voices; ++v) {
                mmio_voice_write(v, VTBL_ADDR, 0);
                mmio_voice_write(v, VTBL_LEN, 1024);
                mmio_voice_write(v, VTBL_RATE, 65536);
                mmio_voice_write(v, VTBL_LOOP_START, 0);
                mmio_voice_write(v, VTBL_LOOP_END, 1024);
                mmio_voice_write(v, VTBL_VOL_TARGET, 0x2020);
                mmio_voice_write(v, VTBL_VOL_RATE, 0);
                mmio_voice_write(v, VTBL_VOL_LR, 0);
                mmio_voice_write(v, VTBL_CTRL, CTRL_ACTIVE | CTRL_LOOP);
            }
            tick(200000); // drain writes and settle all volume ramps
            int samples = 0, interval = 0, maximum = 0;
            uint64_t total = 0;
            while (!dut->sample_wr) tick();
            while (samples < 2048) {
                tick();
                ++interval;
                if (dut->sample_wr) {
                    total += interval;
                    if (interval > maximum) maximum = interval;
                    interval = 0;
                    ++samples;
                }
            }
            double mean = double(total) / samples;
            printf("%d,%d,%.2f,%d,%.3f,%.3f\n", voices, delay, mean,
                   maximum, 100000.0 / mean, 90000.0 / mean);
        }
    }
}

int main(int argc, char **argv) {
    Verilated::commandArgs(argc, argv);
    dut = new Vtb_audio_mixer;

    if (argc == 2 && std::strcmp(argv[1], "--throughput") == 0) {
        measure_throughput();
        delete dut;
        return 0;
    }

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
    test_stream_quiet_is_per_voice();

    test_stream_max_rate();

    test_legacy_loop_ignores_wptr();

    test_queued_writes_under_memory_stall();
    test_rearm_during_sample();
    test_rearm_phase_sweep();
    test_mister_paced_mixer();
    test_mutable_fields_all_voices();
    // Compare both PCM values and their exact emission cycles across builds.
    if (report_perf) printf("PERF audio samples %u trace %016llx\n", sample_count,
                            (unsigned long long)sample_trace);

    printf("\n=== Results: %d passed, %d failed ===\n", passes, fails);

    delete dut;
    return fails == 0 ? 0 : 1;
}
