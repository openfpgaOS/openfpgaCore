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
#define MIXER_SCRATCH_VOICE  31

/* CTRL register bits */
#define CTRL_ACTIVE  (1 << 0)
#define CTRL_LOOP    (1 << 1)
#define CTRL_FMT16   (1 << 2)
#define CTRL_BIDI    (1 << 3)

static int mixer_initialized;
static uint32_t voice_active_mask;

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

/* Sync software voice tracking with hardware voice-end events */
static void sync_voice_mask(void) {
    uint32_t ended = MIX_IRQ_PENDING;
    if (ended) {
        MIX_IRQ_CLEAR = ended;
        voice_active_mask &= ~ended;
    }
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

/* Compute stereo volume target from voice vol × group vol × master vol + pan.
 * Pan 0=left, 128=center, 255=right. At center, both channels = full volume. */
static void apply_vol_pan(int voice)
{
    /* Chain: voice × group × master (all 0-255, result 0-255) */
    int v = vol_shadow[voice];
    v = (v * group_vol[group_shadow[voice]]) / 255;
    v = (v * master_vol) / 255;

    int p = pan_shadow[voice];
    int vol_l = (p < 128) ? v : (v * (255 - p)) / 127;
    int vol_r = (p > 128) ? v : (v * p) / 128;
    if (vol_l > 255) vol_l = 255;
    if (vol_r > 255) vol_r = 255;
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

/* Allocate a voice, handling priority-based stealing. Returns voice index or -1. */
static int alloc_voice(int priority)
{
    sync_voice_mask();

    /* First pass: find a free voice */
    int voice = -1;
    for (int i = 0; i < MIXER_SCRATCH_VOICE; i++) {
        if (!(voice_active_mask & (1u << i))) {
            voice = i;
            break;
        }
    }

    /* Second pass: steal lowest-priority voice if no free voice available */
    if (voice < 0) {
        int lowest_pri = priority;
        for (int i = 0; i < MIXER_SCRATCH_VOICE; i++) {
            if (priority_shadow[i] < lowest_pri) {
                lowest_pri = priority_shadow[i];
                voice = i;
            }
        }
        if (voice >= 0) {
            MIX_VOICE_SEL = voice;
            MIX_VOICE_VOL_LR = 0;
            MIX_VOICE_VOL_TARGET = 0;
            MIX_VOICE_VOL_RATE = 0;
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

    of_cache_clean_range((void *)pcm, byte_size);

    MIX_VOICE_SEL = voice;
    MIX_VOICE_ADDR = cram1_word_addr(pcm);
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

    /* Set mask BEFORE write_ctrl — write_ctrl strips CTRL_ACTIVE
     * for voices not in voice_active_mask (safety for set_loop/set_bidi). */
    voice_active_mask |= (1u << voice);
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

    of_cache_clean_range((void *)pcm_s16, sample_count * 2);

    MIX_VOICE_SEL = voice;

    /* Clear stale voice-end IRQ from previous sample.  Without this,
     * sync_voice_mask() sees the old end event and clears our mask bit,
     * causing set_volume/set_rate/etc. to silently fail (voice_valid()
     * returns false) and of_mixer_play() to steal this voice. */
    MIX_IRQ_CLEAR = (1u << voice);

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
    voice_active_mask |= (1u << voice);
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
    return MIX_VOICE_POS & 0x3FFFFF;
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

void of_mixer_set_vol_rate(int voice, int rate)
{
    if (!voice_in_range(voice)) return;
    MIX_VOICE_SEL = voice;
    MIX_VOICE_VOL_RATE = rate & 0xFF;
}

uint32_t of_mixer_poll_ended(void)
{
    uint32_t mask = MIX_IRQ_PENDING;
    if (mask) {
        MIX_IRQ_CLEAR = mask;
        /* Update software tracking */
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

/* ======================================================================
 * Sample memory bump allocator
 * ====================================================================== */

/* Uncached alias (0x39) reduces cache pollution for large sample uploads,
 * but the CPU may still cache these writes (PMA not fully enforced).
 * of_mixer_play() does an explicit D-cache clean before starting a voice. */
#define SAMPLE_POOL_BASE  (CRAM1_UNCACHED + 0x00400000)
#define SAMPLE_POOL_END   (CRAM1_UNCACHED + 0x00F00000)

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
