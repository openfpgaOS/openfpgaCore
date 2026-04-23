/*
 * openfpgaOS mixer HAL — thin facade over the HW audio_mixer (v2).
 *
 * Hardware fetches samples from the SDRAM sample pool (base 0x13700000),
 * does 2-tap linear interp + per-channel volume ramp, mixes 32 voices,
 * and drives audio_output's dcfifo at 48 kHz.  The public of_mixer_* API
 * is unchanged from the old swmixer-backed HAL so existing apps link.
 *
 * Programming model (see audio_mixer.v + axi_periph_slave.v):
 *   MIX_VOICE_SEL      = <voice>        // latches target voice index
 *   MIX_VOICE_<field>  = <value>        // writes the corresponding VTBL slot
 *   MIX_VOICE_POS      read-only        // returns pos_int for selected voice
 *   MIX_IRQ_PENDING    = W1C bitmap     // one bit per retired one-shot voice
 *
 * MIX_VOICE_SEL is shared between the write path and the pos readback, so
 * a concurrent caller (interrupt context + main thread) can interleave
 * and read the wrong voice.  Firmware convention: call of_mixer_* only
 * from the main thread.
 */

#include "mixer.h"
#include "regs.h"
#include "cache.h"

extern void of_term_printf(const char *fmt, ...);

/* MIX_VOICE_SEL is shared between the write path and the pos readback;
 * the 1 kHz timer ISR runs of_midi_pump which mid-sequence writes SEL as
 * part of smp_voice_tick / note-event dispatch.  Every SEL + field pair
 * below is wrapped in of_mixer_irq_save / restore (from mixer.h) so a
 * preempting ISR can't steer a main-thread field write to the wrong
 * voice.  Root cause of the "voice pos stuck at ~51" symptom. */
#define mixer_irq_save    of_mixer_irq_save
#define mixer_irq_restore of_mixer_irq_restore

#define MIXER_MAX_VOICES     32
#define MIXER_OUTPUT_RATE    48000

/* Reserve voice 31 for the pocket `of_audio_*` stereo stream so SFX
 * playback never steals it.  audio.c programs voice 31 directly. */
#define MIXER_SCRATCH_VOICE  31

/* Shadow state the HW doesn't read back. */
static uint32_t active_shadow;                    /* bit i = voice i active */
static uint8_t  ctrl_shadow[MIXER_MAX_VOICES];    /* {loop[2], stereo[1], active[0]} */
static int8_t   priority_shadow[MIXER_MAX_VOICES];
static uint8_t  vol_shadow[MIXER_MAX_VOICES];     /* master-scaled 0..255 */
static uint8_t  pan_shadow[MIXER_MAX_VOICES];     /* 0=L, 128=C, 255=R */
static uint8_t  group_shadow[MIXER_MAX_VOICES];

#define MIXER_NUM_GROUPS 4
static uint8_t group_vol[MIXER_NUM_GROUPS];
static uint8_t master_vol;

static int mixer_initialized;

/* Equal-power pan: quarter-cosine/sine, 256 entries.
 * pan_cos[i] ~= cos(i*pi/510)*255, pan_sin[i] ~= sin(i*pi/510)*255. */
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

static inline int voice_in_range(int voice)
{
    return voice >= 0 && voice < MIXER_MAX_VOICES;
}

/* Sample pool pointer -> byte offset the HW expects in MIX_VOICE_ADDR. */
static inline uint32_t sample_offset(const void *p)
{
    return (uint32_t)p - SAMPLE_POOL_BASE;
}

/* Push current vol_shadow * group * master * pan to VOICE_VOL_TARGET for
 * this voice.  The HW ramps from VOL_LR toward VOL_TARGET at VOL_RATE; we
 * never touch VOL_LR except on (re)trigger to give a soft start. */
static void apply_vol_pan(int voice)
{
    int v = vol_shadow[voice];
    v = (v * group_vol[group_shadow[voice]]) / 255;
    v = (v * master_vol) / 255;
    int p = pan_shadow[voice];
    int vol_l = (v * pan_cos[p]) >> 8;
    int vol_r = (v * pan_sin[p]) >> 8;

    uint32_t mie = mixer_irq_save();
    MIX_VOICE_SEL = voice;
    MIX_VOICE_VOL_TARGET = ((vol_r & 0xFF) << 8) | (vol_l & 0xFF);
    mixer_irq_restore(mie);
}

static inline void write_ctrl(int voice, uint8_t ctrl)
{
    ctrl_shadow[voice] = ctrl;
    if (ctrl & 1u) active_shadow |=  (1u << voice);
    else           active_shadow &= ~(1u << voice);
    uint32_t mie = mixer_irq_save();
    MIX_VOICE_SEL  = voice;
    MIX_VOICE_CTRL = ctrl;
    mixer_irq_restore(mie);
}

void of_mixer_init(int max_voices, int output_rate)
{
    (void)max_voices;
    (void)output_rate;

    /* Deactivate every voice and clear pending IRQ bits. */
    for (int i = 0; i < MIXER_MAX_VOICES; i++) {
        vol_shadow[i]      = 255;
        pan_shadow[i]      = 128;
        priority_shadow[i] = 0;
        group_shadow[i]    = 0;
        ctrl_shadow[i]     = 0;
        uint32_t mie = mixer_irq_save();
        MIX_VOICE_SEL        = i;
        MIX_VOICE_CTRL       = 0;
        MIX_VOICE_VOL_LR     = 0;
        MIX_VOICE_VOL_TARGET = 0;
        MIX_VOICE_VOL_RATE   = 0;
        mixer_irq_restore(mie);
    }
    for (int i = 0; i < MIXER_NUM_GROUPS; i++) group_vol[i] = 255;
    master_vol     = 255;
    active_shadow  = 0;
    MIX_IRQ_CLEAR  = 0xFFFFFFFFu;
    MIX_CTRL       = MIX_CTRL_ENABLE;
    mixer_initialized = 1;
}

static int alloc_voice(int priority)
{
    for (int i = 0; i < MIXER_MAX_VOICES; i++) {
        if (i == MIXER_SCRATCH_VOICE) continue;
        if (!(active_shadow & (1u << i))) return i;
    }
    int victim = -1;
    int lowest = priority;
    for (int i = 0; i < MIXER_MAX_VOICES; i++) {
        if (i == MIXER_SCRATCH_VOICE) continue;
        if (priority_shadow[i] < lowest) {
            lowest = priority_shadow[i];
            victim = i;
        }
    }
    /* Re-programming in play_internal overrides any fade-out we could
     * schedule here, so stealing is a hard cut.  The new voice's
     * VOL_LR=0 -> ramp-to-target at VOL_RATE=8 covers the start click. */
    return victim;
}

static int play_internal(const void *pcm, uint32_t sample_count,
                         uint32_t sample_rate, int priority, int volume,
                         int fmt16)
{
    if (!mixer_initialized || !pcm || sample_count == 0) return -1;
    if (!fmt16) return -1;   /* HW v2 is 16-bit only */
    int voice = alloc_voice(priority);
    if (voice < 0) return -1;

    extern volatile uint32_t play_counter_diag;
    play_counter_diag++;

    /* DIAG: log any play with suspiciously short length. */
    if (sample_count < 200) {
        static int short_diag = 0;
        if (short_diag < 20) {
            of_term_printf("[SHORT play v=%d len=%u sr=%u]\n",
                           voice, (unsigned)sample_count, (unsigned)sample_rate);
            short_diag++;
        }
    }

    uint32_t rate = ((uint64_t)sample_rate << 16) / MIXER_OUTPUT_RATE;

    /* Force the sample data out to SDRAM before the HW mixer reads it.
     * The mixer fetches directly from SDRAM on its own AXI master,
     * bypassing the CPU D-cache; any CPU stores are still dirty in L1
     * until we force writeback.  Use cbo.flush (writeback + invalidate)
     * rather than cbo.clean — the bank_preload path showed that on this
     * VexiiRiscv config, only flush reliably moves data to SDRAM for
     * the mixer's AXI read path to see. */
    of_cache_flush_range((void *)pcm, sample_count * sizeof(int16_t));

    uint32_t mie = mixer_irq_save();
    MIX_VOICE_SEL        = voice;
    MIX_VOICE_ADDR       = sample_offset(pcm);
    MIX_VOICE_LEN        = sample_count;
    MIX_VOICE_RATE       = rate;
    MIX_VOICE_POS_WR     = 0;
    MIX_VOICE_LOOP_START = 0;
    MIX_VOICE_LOOP_END   = sample_count;
    MIX_VOICE_VOL_LR     = 0;      /* start silent */
    MIX_VOICE_VOL_RATE   = 8;      /* ~0.67 ms ramp to target */
    mixer_irq_restore(mie);

    vol_shadow[voice]      = volume & 0xFF;
    pan_shadow[voice]      = 128;
    priority_shadow[voice] = (int8_t)priority;
    apply_vol_pan(voice);

    write_ctrl(voice, 1u);   /* active | mono | no-loop */
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
    (void)pcm_s8; (void)sample_count; (void)sample_rate;
    (void)priority; (void)volume;
    return -1;   /* deferred — see audio_mixer.v header */
}

void of_mixer_retrigger(int voice, const uint8_t *pcm_s16,
                        uint32_t sample_count, uint32_t sample_rate,
                        int volume)
{
    if (!voice_in_range(voice) || !pcm_s16 || sample_count == 0) return;
    uint32_t rate = ((uint64_t)sample_rate << 16) / MIXER_OUTPUT_RATE;
    int v = volume & 0xFF;

    /* Same SDRAM flush as play_internal — see comment there. */
    of_cache_flush_range((void *)pcm_s16, sample_count * sizeof(int16_t));

    uint32_t mie = mixer_irq_save();
    MIX_VOICE_SEL        = voice;
    MIX_VOICE_ADDR       = sample_offset(pcm_s16);
    MIX_VOICE_LEN        = sample_count;
    MIX_VOICE_RATE       = rate;
    MIX_VOICE_POS_WR     = 0;
    MIX_VOICE_LOOP_START = 0;
    MIX_VOICE_LOOP_END   = sample_count;
    MIX_VOICE_VOL_LR     = (uint32_t)((v << 8) | v);   /* snap, no fade */
    MIX_VOICE_VOL_RATE   = 0;
    mixer_irq_restore(mie);

    vol_shadow[voice] = v;
    pan_shadow[voice] = 128;
    apply_vol_pan(voice);

    write_ctrl(voice, 1u);   /* active | mono | no-loop */
}

void of_mixer_stop(int voice)
{
    if (!voice_in_range(voice)) return;
    uint32_t mie = mixer_irq_save();
    MIX_VOICE_SEL    = voice;
    MIX_VOICE_VOL_LR = 0;
    mixer_irq_restore(mie);
    write_ctrl(voice, 0);
}

void of_mixer_stop_all(void)
{
    uint32_t mie = mixer_irq_save();
    for (int i = 0; i < MIXER_MAX_VOICES; i++) {
        MIX_VOICE_SEL    = i;
        MIX_VOICE_VOL_LR = 0;
        MIX_VOICE_CTRL   = 0;
        ctrl_shadow[i]   = 0;
    }
    active_shadow = 0;
    mixer_irq_restore(mie);
}

void of_mixer_set_volume(int voice, int volume)
{
    if (!voice_in_range(voice)) return;
    vol_shadow[voice] = volume & 0xFF;
    apply_vol_pan(voice);
}

void of_mixer_set_pan(int voice, int pan)
{
    if (!voice_in_range(voice)) return;
    pan_shadow[voice] = pan & 0xFF;
    apply_vol_pan(voice);
}

int of_mixer_voice_active(int voice)
{
    if (!voice_in_range(voice)) return 0;
    return (active_shadow >> voice) & 1u;
}

/* Hardware mixer runs autonomously — no CPU pump needed.  The ABI
 * entries stay so older app binaries continue to link. */
void of_mixer_pump(void)      {}
void of_mixer_pump_auto(void) {}

void of_mixer_set_loop(int voice, int loop_start, int loop_end)
{
    if (!voice_in_range(voice)) return;

    /* DIAG: report first 10 loop settings to see if loop_end is ~51. */
    {
        static int loop_diag = 0;
        if (loop_diag < 10) {
            of_term_printf("[setloop v=%d ls=%d le=%d]\n",
                           voice, loop_start, loop_end);
            loop_diag++;
        }
    }

    uint8_t cur = ctrl_shadow[voice];
    if (loop_start < 0) {
        write_ctrl(voice, cur & ~4u);   /* clear loop bit */
    } else {
        uint32_t mie = mixer_irq_save();
        MIX_VOICE_SEL        = voice;
        MIX_VOICE_LOOP_START = (uint32_t)loop_start;
        if (loop_end > 0) MIX_VOICE_LOOP_END = (uint32_t)loop_end;
        mixer_irq_restore(mie);
        write_ctrl(voice, cur | 4u);    /* set loop bit */
    }
}

void of_mixer_set_rate(int voice, int sample_rate_hz)
{
    if (!voice_in_range(voice)) return;
    uint32_t rate = ((uint64_t)sample_rate_hz << 16) / MIXER_OUTPUT_RATE;
    uint32_t mie = mixer_irq_save();
    MIX_VOICE_SEL  = voice;
    MIX_VOICE_RATE = rate;
    mixer_irq_restore(mie);
}

void of_mixer_set_rate_raw(int voice, uint32_t rate_fp16)
{
    if (!voice_in_range(voice)) return;
    uint32_t mie = mixer_irq_save();
    MIX_VOICE_SEL  = voice;
    MIX_VOICE_RATE = rate_fp16;
    mixer_irq_restore(mie);
}

void of_mixer_set_vol_lr(int voice, int vol_l, int vol_r)
{
    if (!voice_in_range(voice)) return;
    uint32_t mie = mixer_irq_save();
    MIX_VOICE_SEL        = voice;
    MIX_VOICE_VOL_TARGET = ((vol_r & 0xFF) << 8) | (vol_l & 0xFF);
    mixer_irq_restore(mie);
}

/* Bidi loop deferred in HW v2 — log via no-op, forward-loop still works. */
void of_mixer_set_bidi(int voice, int enable)
{
    (void)voice; (void)enable;
}

int of_mixer_get_position(int voice)
{
    if (!voice_in_range(voice)) return 0;
    uint32_t mie = mixer_irq_save();
    MIX_VOICE_SEL = voice;
    int pos = (int)(MIX_VOICE_POS & 0x3FFFFFu);
    mixer_irq_restore(mie);
    return pos;
}

void of_mixer_set_position(int voice, int sample_offset_idx)
{
    if (!voice_in_range(voice)) return;
    uint32_t mie = mixer_irq_save();
    MIX_VOICE_SEL    = voice;
    MIX_VOICE_POS_WR = (uint32_t)sample_offset_idx;
    mixer_irq_restore(mie);
}

void of_mixer_set_voice(int voice, int sample_rate_hz, int vol_l, int vol_r)
{
    if (!voice_in_range(voice)) return;
    uint32_t rate = ((uint64_t)sample_rate_hz << 16) / MIXER_OUTPUT_RATE;
    uint32_t mie = mixer_irq_save();
    MIX_VOICE_SEL        = voice;
    MIX_VOICE_RATE       = rate;
    MIX_VOICE_VOL_TARGET = ((vol_r & 0xFF) << 8) | (vol_l & 0xFF);
    mixer_irq_restore(mie);
}

void of_mixer_set_voice_raw(int voice, uint32_t rate_fp16, int vol_l, int vol_r)
{
    if (!voice_in_range(voice)) return;
    uint32_t mie = mixer_irq_save();
    MIX_VOICE_SEL        = voice;
    MIX_VOICE_RATE       = rate_fp16;
    MIX_VOICE_VOL_TARGET = ((vol_r & 0xFF) << 8) | (vol_l & 0xFF);
    mixer_irq_restore(mie);
}

void of_mixer_set_volume_ramp(int voice, int rate)
{
    if (!voice_in_range(voice)) return;
    uint32_t mie = mixer_irq_save();
    MIX_VOICE_SEL      = voice;
    MIX_VOICE_VOL_RATE = (uint32_t)(rate & 0xFF);
    mixer_irq_restore(mie);
}

uint32_t of_mixer_poll_ended(void)
{
    uint32_t ended = MIX_IRQ_PENDING;
    if (ended) {
        MIX_IRQ_CLEAR  = ended;
        active_shadow &= ~ended;
        for (int i = 0; i < MIXER_MAX_VOICES; i++)
            if (ended & (1u << i)) ctrl_shadow[i] = 0;
    }
    return ended;
}

void of_mixer_set_group(int voice, int group)
{
    if (!voice_in_range(voice)) return;
    if (group < 0 || group >= MIXER_NUM_GROUPS) return;
    group_shadow[voice] = (uint8_t)group;
    apply_vol_pan(voice);
}

void of_mixer_set_group_volume(int group, int volume)
{
    if (group < 0 || group >= MIXER_NUM_GROUPS) return;
    group_vol[group] = volume & 0xFF;
    for (int i = 0; i < MIXER_MAX_VOICES; i++) {
        if ((group_shadow[i] == group) && (active_shadow & (1u << i)))
            apply_vol_pan(i);
    }
}

void of_mixer_set_master_volume(int volume)
{
    master_vol = volume & 0xFF;
    for (int i = 0; i < MIXER_MAX_VOICES; i++) {
        if (active_shadow & (1u << i)) apply_vol_pan(i);
    }
}

/* Filter surface — the HW mixer v2 doesn't implement per-voice SVF; kept
 * as a no-op so older app binaries link cleanly. */
void of_mixer_set_filter(int voice, int cutoff_q016, int q, int enable)
{
    (void)voice; (void)cutoff_q016; (void)q; (void)enable;
}

/* Sample memory bump allocator — backed by the SDRAM sample pool at
 * OF_TARGET_SAMPLE_BASE.  Apps pass the returned pointer back into
 * of_mixer_play*; sample_offset() converts to the byte offset the HW
 * expects in MIX_VOICE_ADDR.  The first AUDIO_STREAM_RESERVED_BYTES of
 * the pool are reserved for the of_audio stereo stream ring (owned by
 * targets/pocket/audio.c), so the head starts past that window. */
#define AUDIO_STREAM_RESERVED_BYTES  0x4000u   /* 16 KB: 2048 stereo pairs */
static uint32_t sample_pool_head = SAMPLE_POOL_BASE + AUDIO_STREAM_RESERVED_BYTES;

void *of_mixer_alloc_samples(uint32_t size)
{
    size = (size + 3) & ~3u;
    if (sample_pool_head + size > SAMPLE_POOL_END) return (void *)0;
    void *ptr = (void *)sample_pool_head;
    sample_pool_head += size;
    return ptr;
}

void of_mixer_free_samples(void)
{
    sample_pool_head = SAMPLE_POOL_BASE + AUDIO_STREAM_RESERVED_BYTES;
}
