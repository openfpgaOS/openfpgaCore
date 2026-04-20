/*
 * openfpgaOS Hardware Mixer HAL
 * Configures the 32-voice CRAM1 hardware mixer via MMIO registers.
 * All mixing happens in FPGA fabric — zero CPU cost during playback.
 *
 * Hardware features:
 *   - 8-bit stereo volume with log curve and hardware ramp
 *   - 16.16 fixed-point resampling
 *   - Forward and bidirectional looping with LOOP_START/LOOP_END
 *   - 16-bit or 8-bit signed sample format
 *   - Position read-back and write
 *   - Voice-end IRQ (per-voice bitmask, W1C)
 */

#include "mixer.h"
#include "regs.h"
#include "cache.h"

#define MIXER_MAX_VOICES     32
#define MIXER_OUTPUT_RATE    48000
/* Voice 31 is reserved as scratch for of_audio_write() in targets/pocket/audio.c.
 * alloc_voice skips it so the two mechanisms don't collide. */
#define MIXER_SCRATCH_VOICE  31

/* CTRL register bits */
#define CTRL_ACTIVE  (1 << 0)
#define CTRL_LOOP    (1 << 1)
#define CTRL_FMT16   (1 << 2)
#define CTRL_BIDI    (1 << 3)

static int mixer_initialized;
/* 32 voices fit in a single 32-bit MMIO IRQ register.  The SW shadow is
 * kept as uint32_t to match.  The legacy _HI registers are stubbed to 0
 * by the slave at 32 voices and no longer referenced here. */
static uint32_t voice_active_mask;

static inline uint32_t read_irq_pending(void)
{
    return MIX_IRQ_PENDING;
}

static inline void write_irq_clear(uint32_t mask)
{
    if (mask) MIX_IRQ_CLEAR = mask;
}

/* Shadow CTRL per voice — every function that modifies CTRL updates
 * the shadow first, then calls write_ctrl(). */
static uint8_t ctrl_shadow[MIXER_MAX_VOICES];

/* Shadow volume and pan per voice — pan scales the base volume so
 * center pan doesn't halve the output level. */
static uint8_t vol_shadow[MIXER_MAX_VOICES];
static uint8_t pan_shadow[MIXER_MAX_VOICES];

/* Priority per voice for voice stealing (higher = harder to steal) */
static int8_t priority_shadow[MIXER_MAX_VOICES];

/* Volume groups: per-voice group assignment + per-group and master scaling */
#define MIXER_NUM_GROUPS 4
static uint8_t group_shadow[MIXER_MAX_VOICES];        /* group index per voice */
static uint8_t group_vol[MIXER_NUM_GROUPS];            /* 0-255 per group */
static uint8_t master_vol;

/* Brief IRQ-disabled critical section so the IRQ handler doesn't
 * land between read-modify-write of voice_active_mask. */
static inline uint32_t irq_save(void) {
    uint32_t s;
    asm volatile("csrrci %0, mstatus, 8" : "=r"(s));
    return s;
}
static inline void irq_restore(uint32_t s) {
    if (s & (1u << 3))
        asm volatile("csrsi mstatus, 8");
}

/* Called from irq.c when MIX_IRQ_PENDING fires.  Clears the SW shadow
 * mask atomically with the HW W1C so a subsequent alloc_voice doesn't
 * see a leaked stale bit.  Public via mixer_irq_clear_voices().  The
 * mask is uint64_t for ABI stability with prior 48-voice builds; the
 * upper 32 bits are always 0 at 32 voices. */
void mixer_irq_clear_voices(uint64_t mask) {
    voice_active_mask &= ~(uint32_t)mask;
}

/* Lightweight reconciliation — used only by code paths that don't
 * have IRQs enabled (rare).  When IRQs are running normally the
 * shadow stays in sync via mixer_irq_clear_voices(), so this peek
 * is a no-op in steady state. */
static void sync_voice_mask(void) {
    voice_active_mask &= ~read_irq_pending();
}

/* Bounds-only check — like the Amiga's Paula, register writes always
 * go through regardless of DMA state.  Writing to a dead voice just
 * updates BRAM; the FSM ignores it until the voice is reactivated.
 * The old voice_active_mask check silently dropped effect commands
 * (volume, rate, pan, loop) after a sample ended naturally. */
static inline int voice_in_range(int voice) {
    return voice >= 0 && voice < MIXER_MAX_VOICES;
}

static void write_ctrl(int voice) {
    uint8_t ctrl = ctrl_shadow[voice];
    /* Never reactivate a dead voice via a parameter update.
     * Strip ACTIVE for voices not in the software mask — this lets
     * set_loop/set_bidi update BRAM flags without accidentally
     * triggering an inactive→active transition in the RTL. */
    if (!(voice_active_mask & (1u << voice)))
        ctrl &= ~CTRL_ACTIVE;
    MIX_VOICE_CTRL = ctrl;
}

/* Equal-power pan: quarter-cosine/sine, 256 entries.
 * pan_cos[i] = round(cos(i * π / 510) * 255), pan_sin[i] = round(sin(i * π / 510) * 255)
 * At center (128): both ≈ 181 (0.707 × 256). */
static const uint8_t pan_cos[256] = {
    255,255,255,255,255,255,255,255,255,255,255,254,254,254,254,254,
    254,254,253,253,253,253,253,252,252,252,252,251,251,251,251,250,
    250,250,249,249,249,248,248,248,247,247,247,246,246,245,245,244,
    244,243,243,243,242,242,241,241,240,239,239,238,238,237,237,236,
    235,235,234,234,233,232,232,231,230,230,229,228,228,227,226,225,
    225,224,223,222,222,221,220,219,218,218,217,216,215,214,213,213,
    212,211,210,209,208,207,206,205,204,203,203,202,201,200,199,198,
    197,196,195,194,193,192,191,190,188,187,186,185,184,183,182,181,
    180,179,178,176,175,174,173,172,171,169,168,167,166,165,164,162,
    161,160,159,157,156,155,154,152,151,150,149,147,146,145,143,142,
    141,140,138,137,136,134,133,132,130,129,128,126,125,123,122,121,
    119,118,116,115,114,112,111,109,108,107,105,104,102,101, 99, 98,
     96, 95, 94, 92, 91, 89, 88, 86, 85, 83, 82, 80, 79, 77, 76, 74,
     73, 71, 70, 68, 67, 65, 64, 62, 61, 59, 58, 56, 55, 53, 51, 50,
     48, 47, 45, 44, 42, 41, 39, 38, 36, 34, 33, 31, 30, 28, 27, 25,
     24, 22, 20, 19, 17, 16, 14, 13, 11,  9,  8,  6,  5,  3,  2,  0,
};
static const uint8_t pan_sin[256] = {
      0,  2,  3,  5,  6,  8,  9, 11, 13, 14, 16, 17, 19, 20, 22, 24,
     25, 27, 28, 30, 31, 33, 34, 36, 38, 39, 41, 42, 44, 45, 47, 48,
     50, 51, 53, 55, 56, 58, 59, 61, 62, 64, 65, 67, 68, 70, 71, 73,
     74, 76, 77, 79, 80, 82, 83, 85, 86, 88, 89, 91, 92, 94, 95, 96,
     98, 99,101,102,104,105,107,108,109,111,112,114,115,116,118,119,
    121,122,123,125,126,127,129,130,132,133,134,136,137,138,140,141,
    142,143,145,146,147,149,150,151,152,154,155,156,157,159,160,161,
    162,164,165,166,167,168,169,171,172,173,174,175,176,178,179,180,
    181,182,183,184,185,186,187,188,190,191,192,193,194,195,196,197,
    198,199,200,201,202,203,203,204,205,206,207,208,209,210,211,212,
    213,213,214,215,216,217,218,218,219,220,221,222,222,223,224,225,
    225,226,227,228,228,229,230,230,231,232,232,233,234,234,235,235,
    236,237,237,238,238,239,239,240,241,241,242,242,243,243,243,244,
    244,245,245,246,246,247,247,247,248,248,248,249,249,249,250,250,
    250,251,251,251,251,252,252,252,252,253,253,253,253,253,254,254,
    254,254,254,254,254,255,255,255,255,255,255,255,255,255,255,255,
};

/* Compute stereo volume target from voice vol × group vol × master vol + pan.
 * Pan 0=left, 128=center, 255=right. Equal-power curve (constant energy). */
static void apply_vol_pan(int voice)
{
    /* Chain: voice × group × master (all 0-255, result 0-255) */
    int v = vol_shadow[voice];
    v = (v * group_vol[group_shadow[voice]]) / 255;
    v = (v * master_vol) / 255;

    int p = pan_shadow[voice];
    int vol_l = (v * pan_cos[p]) >> 8;
    int vol_r = (v * pan_sin[p]) >> 8;
    MIX_VOICE_VOL_TARGET = ((vol_r & 0xFF) << 8) | (vol_l & 0xFF);
}

void of_mixer_init(int max_voices, int output_rate)
{
    (void)max_voices;
    (void)output_rate;
    for (int i = 0; i < MIXER_MAX_VOICES; i++) {
        MIX_VOICE_SEL = i;
        MIX_VOICE_CTRL = 0;
        ctrl_shadow[i] = 0;
        vol_shadow[i] = 255;
        pan_shadow[i] = 128;
        priority_shadow[i] = 0;
        group_shadow[i] = 0;
    }
    for (int i = 0; i < MIXER_NUM_GROUPS; i++)
        group_vol[i] = 255;
    master_vol = 255;
    /* Clear any pending IRQs */
    MIX_IRQ_CLEAR = 0xFFFFFFFF;
    MIX_CTRL = 1;
    voice_active_mask = 0;
    mixer_initialized = 1;
}

static uint32_t cram1_word_addr(const void *ptr) {
    uint32_t a = (uint32_t)(uintptr_t)ptr;
    uint32_t offset;
    if (a >= CRAM1_UNCACHED && a < CRAM1_UNCACHED + CRAM_SIZE)
        offset = a - CRAM1_UNCACHED;
    else
        offset = a - CRAM1_BASE;
    return offset >> 2;
}

// TODO(audio_review): Voice ownership is fragmented across tracking systems.
// Also, the allocator only protects voice 31. Stream voices 29/30 used in audio.c are unprotected.
/* Allocate a voice, handling priority-based stealing. Returns voice index or -1. */
static int alloc_voice(int priority)
{
    sync_voice_mask();

    /* First pass: find a free voice */
    int voice = -1;
    for (int i = 0; i < MIXER_MAX_VOICES; i++) {
        if (i == MIXER_SCRATCH_VOICE) continue;
        if (!(voice_active_mask & (1u << i))) {
            voice = i;
            break;
        }
    }

    /* Second pass: steal lowest-priority voice if no free voice available */
    if (voice < 0) {
        int lowest_pri = priority;
        for (int i = 0; i < MIXER_MAX_VOICES; i++) {
            if (i == MIXER_SCRATCH_VOICE) continue;
            if (priority_shadow[i] < lowest_pri) {
                lowest_pri = priority_shadow[i];
                voice = i;
            }
        }
        if (voice >= 0) {
            /* Fast hardware fade-out before deactivating to eliminate
             * the click from snapping the sample value to silence.
             * Step 1: set target=0 with fast ramp.
             * Step 2: wait long enough for the ramp to actually complete.
             *         At RATE=8, vol drops from 255 to 0 in 32 sample
             *         periods × 21μs/sample ≈ 670μs.
             * Step 3: deactivate the voice. */
            MIX_VOICE_SEL = voice;
            MIX_VOICE_VOL_TARGET = 0;
            MIX_VOICE_VOL_RATE = 8;

            /* Busy-wait for the ramp to complete. ~700μs at 100MHz =
             * ~70000 cycles. This only runs when stealing a voice, which
             * is rare (only when all 31 voices are busy). */
            for (volatile int w = 0; w < 7000; w++) {
                __asm__ volatile("nop");
            }

            MIX_VOICE_SEL = voice;
            ctrl_shadow[voice] = 0;
            MIX_VOICE_CTRL = 0;
        }
    }
    return voice;
}

/* Common play implementation for both 8-bit and 16-bit samples. */
static int play_internal(const uint8_t *pcm, uint32_t sample_count,
                         uint32_t sample_rate, int priority, int volume,
                         int fmt16)
{
    if (!mixer_initialized || !pcm || sample_count == 0)
        return -1;

    int voice = alloc_voice(priority);
    if (voice < 0) return -1;

    int v = volume & 0xFF;
    uint32_t rate = ((uint64_t)sample_rate << 16) / MIXER_OUTPUT_RATE;
    uint32_t byte_size = fmt16 ? sample_count * 2 : sample_count;

    uint32_t addr = (uint32_t)(uintptr_t)pcm;
    if (addr < CRAM1_UNCACHED || addr >= CRAM1_UNCACHED + CRAM_SIZE)
        of_cache_clean_range((void *)pcm, byte_size);

    MIX_VOICE_SEL = voice;
    MIX_VOICE_ADDR = cram1_word_addr(pcm);
    MIX_VOICE_POS_WR = 0;
    MIX_VOICE_LEN = sample_count;
    MIX_VOICE_RATE = rate;

    vol_shadow[voice] = v;
    pan_shadow[voice] = 128;
    /* Soft fade-in: start at 0, ramp to target.
     * Rate=8 → ramp from 0 to 255 in 32 sample periods ≈ 0.67ms.
     * Eliminates click at sample start without audible attack delay. */
    MIX_VOICE_VOL_LR = 0;
    MIX_VOICE_VOL_TARGET = (v << 8) | v;
    MIX_VOICE_VOL_RATE = 8;

    priority_shadow[voice] = priority;
    ctrl_shadow[voice] = CTRL_ACTIVE | (fmt16 ? CTRL_FMT16 : 0);

    /* Clear stale end-event from a previous play on this voice slot.
     * sync_voice_mask() no longer clears IRQs (it peeks), so an old
     * end bit could still be set.  Without this, poll_ended() would
     * see the stale bit and immediately mark this voice as dead. */
    write_irq_clear(1u << voice);

    /* Set mask BEFORE write_ctrl — write_ctrl strips CTRL_ACTIVE
     * for voices not in voice_active_mask (safety for set_loop/set_bidi).
     * Brief IRQ-disabled critical section so the IRQ handler can't
     * land between the read and write — otherwise a "voice ended"
     * IRQ for an unrelated voice could clear bits that the |=
     * just set, leaking ownership. */
    {
        uint32_t s = irq_save();
        voice_active_mask |= (1u << voice);
        irq_restore(s);
    }
    write_ctrl(voice);
    return voice;
}

int of_mixer_play(const uint8_t *pcm_s16, uint32_t sample_count,
                  uint32_t sample_rate, int priority, int volume)
{
    return play_internal(pcm_s16, sample_count, sample_rate, priority, volume, 1);
}

int of_mixer_play_8bit(const uint8_t *pcm_s8, uint32_t sample_count,
                       uint32_t sample_rate, int priority, int volume)
{
    return play_internal(pcm_s8, sample_count, sample_rate, priority, volume, 0);
}

void of_mixer_retrigger(int voice, const uint8_t *pcm_s16,
                       uint32_t sample_count, uint32_t sample_rate,
                       int volume)
{
    if (voice < 0 || voice >= MIXER_MAX_VOICES || !pcm_s16 || sample_count == 0)
        return;

    int v = volume & 0xFF;
    uint32_t rate = ((uint64_t)sample_rate << 16) / MIXER_OUTPUT_RATE;

    {
        uint32_t a = (uint32_t)(uintptr_t)pcm_s16;
        if (a < CRAM1_UNCACHED || a >= CRAM1_UNCACHED + CRAM_SIZE)
            of_cache_clean_range((void *)pcm_s16, sample_count * 2);
    }

    MIX_VOICE_SEL = voice;

    /* Clear stale voice-end IRQ from previous sample.  Without this,
     * sync_voice_mask() sees the old end event and clears our mask bit,
     * causing set_volume/set_rate/etc. to silently fail (voice_valid()
     * returns false) and of_mixer_play() to steal this voice. */
    write_irq_clear(1u << voice);

    /* Guard LEN: the FSM runs concurrently and checks pos >= len every
     * mix cycle.  Setting LEN to max first prevents the FSM from killing
     * the voice while we update ADDR/POS (it can't reach 4M samples in
     * the few microseconds we need).  Real LEN is written last. */
    MIX_VOICE_LEN = 0x3FFFFF;
    MIX_VOICE_ADDR = cram1_word_addr(pcm_s16);
    MIX_VOICE_POS_WR = 0;
    MIX_VOICE_RATE = rate;
    MIX_VOICE_LEN = sample_count;

    /* Snap to new volume (no ramp on retrigger) */
    vol_shadow[voice] = v;
    pan_shadow[voice] = 128;
    MIX_VOICE_VOL_LR = (v << 8) | v;
    MIX_VOICE_VOL_TARGET = (v << 8) | v;

    /* Re-assert active + fmt16, clear loop (caller sets loop after) */
    ctrl_shadow[voice] = CTRL_ACTIVE | CTRL_FMT16;
    {
        uint32_t s = irq_save();
        voice_active_mask |= (1u << voice);
        irq_restore(s);
    }
    write_ctrl(voice);
}

void of_mixer_stop(int voice)
{
    if (voice >= 0 && voice < MIXER_MAX_VOICES) {
        MIX_VOICE_SEL = voice;
        /* Snap volume to 0 before deactivating to reduce click.
         * The mixer will use vol=0 if it processes this voice before
         * the CTRL=0 write takes effect on the next mix cycle. */
        MIX_VOICE_VOL_LR = 0;
        MIX_VOICE_VOL_TARGET = 0;
        MIX_VOICE_VOL_RATE = 0;  /* instant */
        ctrl_shadow[voice] = 0;
        MIX_VOICE_CTRL = 0;
        voice_active_mask &= ~(1u << voice);
    }
}

void of_mixer_stop_all(void)
{
    for (int i = 0; i < MIXER_MAX_VOICES; i++) {
        MIX_VOICE_SEL = i;
        ctrl_shadow[i] = 0;
        MIX_VOICE_CTRL = 0;
    }
    voice_active_mask = 0;
}

void of_mixer_set_volume(int voice, int volume)
{
    if (!voice_in_range(voice)) return;
    vol_shadow[voice] = volume & 0xFF;
    MIX_VOICE_SEL = voice;
    apply_vol_pan(voice);
}

void of_mixer_set_pan(int voice, int pan)
{
    if (!voice_in_range(voice)) return;
    pan_shadow[voice] = pan & 0xFF;
    MIX_VOICE_SEL = voice;
    apply_vol_pan(voice);
}

int of_mixer_voice_active(int voice)
{
    if (voice < 0 || voice >= MIXER_MAX_VOICES)
        return 0;
    sync_voice_mask();
    return (voice_active_mask & (1u << voice)) ? 1 : 0;
}

/* No-op: hardware mixer runs autonomously */
void of_mixer_pump_auto(void) { }
void of_mixer_pump(void) { }

/* ======================================================================
 * Voice control
 * ====================================================================== */

void of_mixer_set_loop(int voice, int loop_start, int loop_end)
{
    if (!voice_in_range(voice)) return;
    MIX_VOICE_SEL = voice;
    if (loop_start < 0) {
        ctrl_shadow[voice] &= ~(CTRL_LOOP | CTRL_BIDI);
    } else {
        MIX_VOICE_LOOP_START = loop_start;
        if (loop_end > 0)
            MIX_VOICE_LOOP_END = loop_end;
        ctrl_shadow[voice] |= CTRL_LOOP;
    }
    write_ctrl(voice);
}

void of_mixer_set_rate(int voice, int sample_rate_hz)
{
    if (!voice_in_range(voice)) return;
    uint32_t rate = ((uint64_t)sample_rate_hz << 16) / MIXER_OUTPUT_RATE;
    MIX_VOICE_SEL = voice;
    MIX_VOICE_RATE = rate;
}

void of_mixer_set_rate_raw(int voice, uint32_t rate_fp16)
{
    if (!voice_in_range(voice)) return;
    MIX_VOICE_SEL = voice;
    MIX_VOICE_RATE = rate_fp16;
}

void of_mixer_set_vol_lr(int voice, int vol_l, int vol_r)
{
    if (!voice_in_range(voice)) return;
    MIX_VOICE_SEL = voice;
    MIX_VOICE_VOL_TARGET = ((vol_r & 0xFF) << 8) | (vol_l & 0xFF);
}

void of_mixer_set_bidi(int voice, int enable)
{
    if (!voice_in_range(voice)) return;
    MIX_VOICE_SEL = voice;
    if (enable)
        ctrl_shadow[voice] |= CTRL_BIDI;
    else
        ctrl_shadow[voice] &= ~CTRL_BIDI;
    write_ctrl(voice);
}

int of_mixer_get_position(int voice)
{
    if (!voice_in_range(voice)) return 0;
    MIX_VOICE_SEL = voice;
    /* Two-read consistency check: the voice position counter updates
     * asynchronously to CPU reads, so a single read can straddle a
     * carry boundary (e.g. 0x0FFF -> 0x1000) and return a mix of
     * old/new bits. Re-read until two consecutive samples agree. */
    int prev = MIX_VOICE_POS & 0x3FFFFF;
    for (int i = 0; i < 16; i++) {
        int now = MIX_VOICE_POS & 0x3FFFFF;
        if (now == prev) return now;
        prev = now;
    }
    return prev;
}

void of_mixer_set_position(int voice, int sample_offset)
{
    if (!voice_in_range(voice)) return;
    MIX_VOICE_SEL = voice;
    MIX_VOICE_POS_WR = sample_offset;
}

void of_mixer_set_voice(int voice, int sample_rate_hz, int vol_l, int vol_r)
{
    if (!voice_in_range(voice)) return;
    uint32_t rate = ((uint64_t)sample_rate_hz << 16) / MIXER_OUTPUT_RATE;
    MIX_VOICE_SEL = voice;
    MIX_VOICE_RATE = rate;
    MIX_VOICE_VOL_TARGET = ((vol_r & 0xFF) << 8) | (vol_l & 0xFF);
}

void of_mixer_set_voice_raw(int voice, uint32_t rate_fp16, int vol_l, int vol_r)
{
    if (!voice_in_range(voice)) return;
    MIX_VOICE_SEL = voice;
    MIX_VOICE_RATE = rate_fp16;
    MIX_VOICE_VOL_TARGET = ((vol_r & 0xFF) << 8) | (vol_l & 0xFF);
}

void of_mixer_set_volume_ramp(int voice, int rate)
{
    if (!voice_in_range(voice)) return;
    MIX_VOICE_SEL = voice;
    MIX_VOICE_VOL_RATE = rate & 0xFF;
}

uint32_t of_mixer_poll_ended(void)
{
    uint32_t mask = read_irq_pending();
    if (mask) {
        write_irq_clear(mask);
        voice_active_mask &= ~mask;
    }
    return mask;
}

/* ======================================================================
 * Group / master volume
 * ====================================================================== */

void of_mixer_set_group(int voice, int group)
{
    if (!voice_in_range(voice)) return;
    if (group < 0 || group >= MIXER_NUM_GROUPS) return;
    group_shadow[voice] = group;
    MIX_VOICE_SEL = voice;
    apply_vol_pan(voice);
}

void of_mixer_set_group_volume(int group, int volume)
{
    if (group < 0 || group >= MIXER_NUM_GROUPS) return;
    group_vol[group] = volume & 0xFF;
    /* Reapply volume to all active voices in this group */
    for (int i = 0; i < MIXER_MAX_VOICES; i++) {
        if (group_shadow[i] == group && (voice_active_mask & (1u << i))) {
            MIX_VOICE_SEL = i;
            apply_vol_pan(i);
        }
    }
}

void of_mixer_set_master_volume(int volume)
{
    master_vol = volume & 0xFF;
    /* Reapply volume to all active voices */
    for (int i = 0; i < MIXER_MAX_VOICES; i++) {
        if (voice_active_mask & (1u << i)) {
            MIX_VOICE_SEL = i;
            apply_vol_pan(i);
        }
    }
}

// TODO(audio_review): Filter plumbing exists here, but the mixer RTL (audio_mixer.v)
// does not implement it in the sample path. Remove dead filter control paths or implement them.
void of_mixer_set_filter(int voice, int cutoff_q016, int q, int enable)
{
    if (!voice_in_range(voice)) return;
    if (cutoff_q016 < 0)     cutoff_q016 = 0;
    if (cutoff_q016 > 65535) cutoff_q016 = 65535;
    if (q < 0)   q = 0;
    if (q > 255) q = 255;
    MIX_VOICE_SEL       = voice;
    MIX_VOICE_FILTER_FC = (uint32_t)cutoff_q016;
    MIX_VOICE_FILTER_Q  = ((enable ? 1u : 0u) << 8) | (uint32_t)q;
}

/* ======================================================================
 * Sample memory bump allocator
 * ====================================================================== */

static uint32_t sample_pool_head = SAMPLE_POOL_BASE;

void *of_mixer_alloc_samples(uint32_t size)
{
    size = (size + 3) & ~3;
    if (sample_pool_head + size > SAMPLE_POOL_END)
        return (void *)0;
    void *ptr = (void *)sample_pool_head;
    sample_pool_head += size;
    return ptr;
}

void of_mixer_free_samples(void)
{
    sample_pool_head = SAMPLE_POOL_BASE;
}
