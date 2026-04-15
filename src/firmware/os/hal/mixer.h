/*
 * openfpgaOS Hardware Mixer HAL
 *
 * 48-voice hardware PCM mixer with stereo 8-bit volume,
 * 16.16 fixed-point resampling, forward/bidi looping,
 * 16-bit and 8-bit signed sample support.
 */

#ifndef OFOS_MIXER_H
#define OFOS_MIXER_H

#include <stdint.h>

void of_mixer_init(int max_voices, int output_rate);

/* Play 16-bit signed mono PCM from CRAM1. Returns voice index or -1. */
int of_mixer_play(const uint8_t *pcm_s16, uint32_t sample_count,
                  uint32_t sample_rate, int priority, int volume);

/* Play 8-bit signed mono PCM from CRAM1. Returns voice index or -1. */
int of_mixer_play_8bit(const uint8_t *pcm_s8, uint32_t sample_count,
                       uint32_t sample_rate, int priority, int volume);

/* Retrigger an existing voice with new sample data (no stop/start gap).
 * Redirects ADDR/LEN/POS in-place — eliminates click on note retrigger. */
void of_mixer_retrigger(int voice, const uint8_t *pcm_s16,
                        uint32_t sample_count, uint32_t sample_rate,
                        int volume);

void of_mixer_stop(int voice);
void of_mixer_stop_all(void);

/* Mono volume (0-255), sets both L and R equally */
void of_mixer_set_volume(int voice, int volume);

/* Pan (0=left, 128=center, 255=right) */
void of_mixer_set_pan(int voice, int pan);

/* Per-voice active check (software-tracked) */
int of_mixer_voice_active(int voice);

/* No-op: hardware mixer runs autonomously */
void of_mixer_pump(void);
void of_mixer_pump_auto(void);

/* Loop control. loop_start=-1 disables. loop_end sets LOOP_END register. */
void of_mixer_set_loop(int voice, int loop_start, int loop_end);

/* Playback rate from Hz (kernel computes 16.16 fixed-point) */
void of_mixer_set_rate(int voice, int sample_rate_hz);

/* Playback rate as raw 16.16 fixed-point (no division) */
void of_mixer_set_rate_raw(int voice, uint32_t rate_fp16);

/* Independent stereo volume (0-255 each) */
void of_mixer_set_vol_lr(int voice, int vol_l, int vol_r);

/* Bidirectional (ping-pong) loop enable/disable */
void of_mixer_set_bidi(int voice, int enable);

/* Position read (integer sample index) */
int of_mixer_get_position(int voice);

/* Position write (sample offset) */
void of_mixer_set_position(int voice, int sample_offset);

/* Rate (Hz) + stereo volume in one call */
void of_mixer_set_voice(int voice, int sample_rate_hz, int vol_l, int vol_r);

/* Rate (16.16 raw) + stereo volume in one call */
void of_mixer_set_voice_raw(int voice, uint32_t rate_fp16, int vol_l, int vol_r);

/* Volume ramp speed (0=instant, 1=fast ~5ms, higher=slower) */
void of_mixer_set_volume_ramp(int voice, int rate);

/* Poll for ended voices. Returns bitmask of voices that finished
 * since last poll. Clears the hardware IRQ bits and updates
 * software voice tracking. */
uint32_t of_mixer_poll_ended(void);

/* Group / master volume */
#define OF_MIXER_GROUP_SFX   0
#define OF_MIXER_GROUP_MUSIC 1
#define OF_MIXER_GROUP_VOICE 2
#define OF_MIXER_GROUP_AUX   3

void of_mixer_set_group(int voice, int group);
void of_mixer_set_group_volume(int group, int volume);
void of_mixer_set_master_volume(int volume);

/* Per-voice SVF low-pass filter.
 *   cutoff_q016 : Q0.16 SVF cutoff coefficient (raw register value).
 *                 65535 ≈ wide-open, lower values close it down.
 *   q           : 0..255 resonance (lower = more damping in HW).
 *   enable      : 0 = bypass, 1 = filter active. */
void of_mixer_set_filter(int voice, int cutoff_q016, int q, int enable);

/* Sample memory bump allocator */
void *of_mixer_alloc_samples(uint32_t size);
void of_mixer_free_samples(void);

#endif /* OFOS_MIXER_H */
