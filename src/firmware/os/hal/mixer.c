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

static inline int voice_valid(int voice) {
    return voice >= 0 && voice < MIXER_MAX_VOICES &&
           (voice_active_mask & (1u << voice));
}

static void write_ctrl(int voice, int vol_8bit) {
    int hwvol = (vol_8bit >> 4) & 0xF;
    MIX_VOICE_CTRL = (hwvol << 4) | ctrl_shadow[voice];
}

void of_mixer_init(int max_voices, int output_rate)
{
    (void)max_voices;
    (void)output_rate;
    for (int i = 0; i < MIXER_MAX_VOICES; i++) {
        MIX_VOICE_SEL = i;
        MIX_VOICE_CTRL = 0;
        ctrl_shadow[i] = 0;
    }
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

int of_mixer_play(const uint8_t *pcm_s16, uint32_t sample_count,
                  uint32_t sample_rate, int priority, int volume)
{
    (void)priority;
    if (!mixer_initialized || !pcm_s16 || sample_count == 0)
        return -1;

    int voice = -1;
    for (int i = 0; i < MIXER_SCRATCH_VOICE; i++) {
        if (!(voice_active_mask & (1u << i))) {
            voice = i;
            break;
        }
    }
    if (voice < 0) return -1;

    int v = volume & 0xFF;
    uint32_t rate = ((uint64_t)sample_rate << 16) / MIXER_OUTPUT_RATE;

    MIX_VOICE_SEL = voice;
    MIX_VOICE_ADDR = cram1_word_addr(pcm_s16);
    MIX_VOICE_LEN = sample_count;
    MIX_VOICE_RATE = rate;

    /* Set initial volume directly (no ramp on play) */
    MIX_VOICE_VOL_LR = (v << 8) | v;
    MIX_VOICE_VOL_TARGET = (v << 8) | v;
    MIX_VOICE_VOL_RATE = 1;  /* default: fast ramp for subsequent changes */

    ctrl_shadow[voice] = CTRL_ACTIVE | CTRL_FMT16;
    write_ctrl(voice, v);

    voice_active_mask |= (1u << voice);
    return voice;
}

void of_mixer_stop(int voice)
{
    if (voice >= 0 && voice < MIXER_MAX_VOICES) {
        MIX_VOICE_SEL = voice;
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
    if (!voice_valid(voice)) return;
    int v = volume & 0xFF;
    MIX_VOICE_SEL = voice;
    MIX_VOICE_VOL_TARGET = (v << 8) | v;
    write_ctrl(voice, v);
}

void of_mixer_set_pan(int voice, int pan)
{
    if (!voice_valid(voice)) return;
    int vol_l = 255 - pan;
    int vol_r = pan;
    MIX_VOICE_SEL = voice;
    MIX_VOICE_VOL_TARGET = ((vol_r & 0xFF) << 8) | (vol_l & 0xFF);
    write_ctrl(voice, (vol_l + vol_r) >> 1);
}

int of_mixer_voice_active(int voice)
{
    if (voice < 0 || voice >= MIXER_MAX_VOICES)
        return 0;
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
    if (!voice_valid(voice)) return;
    MIX_VOICE_SEL = voice;
    if (loop_start < 0) {
        ctrl_shadow[voice] &= ~(CTRL_LOOP | CTRL_BIDI);
    } else {
        MIX_VOICE_LOOP_START = loop_start;
        if (loop_end > 0)
            MIX_VOICE_LOOP_END = loop_end;
        ctrl_shadow[voice] |= CTRL_LOOP;
    }
    write_ctrl(voice, 0xFF);
}

void of_mixer_set_rate(int voice, int sample_rate_hz)
{
    if (!voice_valid(voice)) return;
    uint32_t rate = ((uint64_t)sample_rate_hz << 16) / MIXER_OUTPUT_RATE;
    MIX_VOICE_SEL = voice;
    MIX_VOICE_RATE = rate;
}

void of_mixer_set_rate_raw(int voice, uint32_t rate_fp16)
{
    if (!voice_valid(voice)) return;
    MIX_VOICE_SEL = voice;
    MIX_VOICE_RATE = rate_fp16;
}

void of_mixer_set_vol_lr(int voice, int vol_l, int vol_r)
{
    if (!voice_valid(voice)) return;
    MIX_VOICE_SEL = voice;
    MIX_VOICE_VOL_TARGET = ((vol_r & 0xFF) << 8) | (vol_l & 0xFF);
}

void of_mixer_set_bidi(int voice, int enable)
{
    if (!voice_valid(voice)) return;
    MIX_VOICE_SEL = voice;
    if (enable)
        ctrl_shadow[voice] |= CTRL_BIDI;
    else
        ctrl_shadow[voice] &= ~CTRL_BIDI;
    write_ctrl(voice, 0xFF);
}

int of_mixer_get_position(int voice)
{
    if (!voice_valid(voice)) return 0;
    MIX_VOICE_SEL = voice;
    return MIX_VOICE_POS & 0x3FFFFF;
}

void of_mixer_set_position(int voice, int sample_offset)
{
    if (!voice_valid(voice)) return;
    MIX_VOICE_SEL = voice;
    MIX_VOICE_POS_WR = sample_offset;
}

void of_mixer_set_voice(int voice, int sample_rate_hz, int vol_l, int vol_r)
{
    if (!voice_valid(voice)) return;
    uint32_t rate = ((uint64_t)sample_rate_hz << 16) / MIXER_OUTPUT_RATE;
    MIX_VOICE_SEL = voice;
    MIX_VOICE_RATE = rate;
    MIX_VOICE_VOL_TARGET = ((vol_r & 0xFF) << 8) | (vol_l & 0xFF);
}

void of_mixer_set_voice_raw(int voice, uint32_t rate_fp16, int vol_l, int vol_r)
{
    if (!voice_valid(voice)) return;
    MIX_VOICE_SEL = voice;
    MIX_VOICE_RATE = rate_fp16;
    MIX_VOICE_VOL_TARGET = ((vol_r & 0xFF) << 8) | (vol_l & 0xFF);
}

void of_mixer_set_vol_rate(int voice, int rate)
{
    if (!voice_valid(voice)) return;
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
 * Sample memory bump allocator
 * ====================================================================== */

#define SAMPLE_POOL_BASE  (CRAM1_BASE + 0x00400000)
#define SAMPLE_POOL_END   (CRAM1_BASE + 0x00F00000)

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
