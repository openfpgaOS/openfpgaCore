/*
 * openfpgaOS Audio Mixer
 * Multi-voice PCM mixer with 16.16 fixed-point resampling and
 * linear interpolation. Mixes unsigned 8-bit PCM to stereo int16
 * output via the audio FIFO.
 *
 * Ported from PocketDukeNukem/pocket_audio.c
 */

#include "mixer.h"
#include "audio.h"
#include "regs.h"
#include <string.h>

#define MIXER_MAX_VOICES    8
#define MIX_BUF_FRAMES      512

typedef struct {
    const uint8_t *data;    /* pointer to raw PCM samples (8-bit unsigned) */
    uint32_t length;        /* total PCM samples */
    uint32_t pos;           /* current position (16.16 fixed point) */
    uint32_t rate;          /* playback rate (16.16 fixed point step) */
    int      volume;        /* 0-255 */
    int      pan;           /* 0=left, 128=center, 255=right */
    int      active;
    int      priority;
} mixer_voice_t;

static mixer_voice_t voices[MIXER_MAX_VOICES];
static int32_t mix_acc[MIX_BUF_FRAMES * 2];    /* 32-bit accumulator (stereo) */
static int16_t mix_buf[MIX_BUF_FRAMES * 2];    /* stereo output */
static int mixer_num_voices;
static int mixer_output_rate;
static int mixer_initialized;

void of_mixer_init(int max_voices, int output_rate)
{
    of_audio_init();
    memset(voices, 0, sizeof(voices));
    mixer_num_voices = (max_voices > MIXER_MAX_VOICES) ? MIXER_MAX_VOICES : max_voices;
    if (mixer_num_voices < 1) mixer_num_voices = 1;
    mixer_output_rate = output_rate;
    mixer_initialized = 1;
}

int of_mixer_play(const uint8_t *pcm_u8, uint32_t sample_count,
                  uint32_t sample_rate, int priority, int volume)
{
    if (!mixer_initialized) return -1;
    if (!pcm_u8 || sample_count == 0) return -1;

    /* Find a free voice or steal lowest priority */
    int best = -1;
    int best_pri = priority + 1;
    for (int i = 0; i < mixer_num_voices; i++) {
        if (!voices[i].active) { best = i; break; }
        if (voices[i].priority < best_pri) {
            best_pri = voices[i].priority;
            best = i;
        }
    }
    if (best < 0) return -1;

    /* Set up voice */
    mixer_voice_t *v = &voices[best];
    v->data = pcm_u8;
    v->length = sample_count;
    v->pos = 0;
    v->rate = (sample_rate << 16) / (uint32_t)mixer_output_rate;
    v->volume = (volume > 255) ? 255 : (volume < 0) ? 0 : volume;
    v->pan = 128;  /* center */
    v->active = 1;
    v->priority = priority;

    return best;
}

void of_mixer_stop(int voice)
{
    if (voice >= 0 && voice < mixer_num_voices)
        voices[voice].active = 0;
}

void of_mixer_stop_all(void)
{
    for (int i = 0; i < mixer_num_voices; i++)
        voices[i].active = 0;
}

void of_mixer_set_volume(int voice, int volume)
{
    if (voice >= 0 && voice < mixer_num_voices) {
        if (volume > 255) volume = 255;
        if (volume < 0) volume = 0;
        voices[voice].volume = volume;
    }
}

void of_mixer_set_pan(int voice, int pan)
{
    if (voice >= 0 && voice < mixer_num_voices) {
        if (pan > 255) pan = 255;
        if (pan < 0) pan = 0;
        voices[voice].pan = pan;
    }
}

int of_mixer_voice_active(int voice)
{
    if (voice >= 0 && voice < mixer_num_voices)
        return voices[voice].active;
    return 0;
}

/* Auto-pump interval: ~10ms at 100MHz = 1,000,000 cycles.
 * Any kernel code path can call of_mixer_pump_auto() cheaply —
 * it only mixes if enough time has passed since the last pump. */
#define MIXER_PUMP_INTERVAL (CPU_FREQ_HZ / 100)  /* ~10ms */
static uint64_t last_pump_cycles;

void of_mixer_pump_auto(void)
{
    if (!mixer_initialized) return;
    uint64_t now = read_cycles();
    if (now - last_pump_cycles < MIXER_PUMP_INTERVAL) return;
    last_pump_cycles = now;
    of_mixer_pump();
}

void of_mixer_pump(void)
{
    if (!mixer_initialized) return;

    int free_pairs = of_audio_get_free();
    if (free_pairs <= 0) return;
    if (free_pairs > MIX_BUF_FRAMES)
        free_pairs = MIX_BUF_FRAMES;

    /* Clear 32-bit accumulator */
    memset(mix_acc, 0, free_pairs * 2 * sizeof(int32_t));

    /* Mix active voices with linear interpolation */
    for (int v = 0; v < mixer_num_voices; v++) {
        mixer_voice_t *voice = &voices[v];
        if (!voice->active) continue;

        int vol = voice->volume;
        int pan = voice->pan;
        /* Pre-compute L/R gains: pan 0=full left, 128=center, 255=full right */
        int gain_l = (255 - pan) * 2;  /* 0-510 */
        int gain_r = pan * 2;          /* 0-510 */
        if (gain_l > 255) gain_l = 255;
        if (gain_r > 255) gain_r = 255;

        for (int i = 0; i < free_pairs; i++) {
            uint32_t idx = voice->pos >> 16;
            uint32_t frac = voice->pos & 0xFFFF;

            if (idx >= voice->length) {
                voice->active = 0;
                break;
            }

            /* Linear interpolation between adjacent samples */
            int s0 = (int)voice->data[idx] - 128;
            int s1 = (idx + 1 < voice->length) ? (int)voice->data[idx + 1] - 128 : s0;
            int sample = s0 + (((s1 - s0) * (int)frac) >> 16);

            int32_t mono = sample * vol;

            mix_acc[i * 2]     += (mono * gain_l) >> 8;
            mix_acc[i * 2 + 1] += (mono * gain_r) >> 8;

            voice->pos += voice->rate;
        }
    }

    /* Clamp to int16 */
    for (int i = 0; i < free_pairs * 2; i++) {
        int32_t s = mix_acc[i];
        if (s > 32767) s = 32767;
        if (s < -32768) s = -32768;
        mix_buf[i] = (int16_t)s;
    }

    of_audio_write(mix_buf, free_pairs);
}
