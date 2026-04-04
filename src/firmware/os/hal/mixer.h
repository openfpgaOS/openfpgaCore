/*
 * openfpgaOS Audio Mixer HAL
 * Multi-voice PCM mixer with 16.16 fixed-point resampling
 */

#ifndef OFOS_MIXER_H
#define OFOS_MIXER_H

#include <stdint.h>

/* Initialize the mixer with the given number of voices and output rate.
 * max_voices is clamped to 8. */
void of_mixer_init(int max_voices, int output_rate);

/* Play a sound. Returns voice index or -1 on failure.
 * pcm_u8: unsigned 8-bit PCM samples
 * sample_count: number of samples
 * sample_rate: source sample rate in Hz
 * priority: higher = harder to steal
 * volume: 0-255 */
int of_mixer_play(const uint8_t *pcm_u8, uint32_t sample_count,
                  uint32_t sample_rate, int priority, int volume);

/* Stop a specific voice */
void of_mixer_stop(int voice);

/* Stop all voices */
void of_mixer_stop_all(void);

/* Set volume for a voice (0-255) */
void of_mixer_set_volume(int voice, int volume);

/* Set pan for a voice (0=left, 128=center, 255=right) */
void of_mixer_set_pan(int voice, int pan);

/* Check if a voice is currently active */
int of_mixer_voice_active(int voice);

/* Mix active voices and write to audio FIFO */
void of_mixer_pump(void);

/* Time-gated pump — only mixes if >=10ms since last pump.
 * Cheap to call from any kernel code path. */
void of_mixer_pump_auto(void);

/* Set loop points. loop_start=-1 disables looping. */
void of_mixer_set_loop(int voice, int loop_start, int loop_end);

/* Set playback rate from Hz (kernel computes 16.16 fixed-point). */
void of_mixer_set_rate(int voice, int sample_rate_hz);

/* Set playback rate directly as 16.16 fixed-point (no conversion). */
void of_mixer_set_rate_raw(int voice, uint32_t rate_fp16);

/* Set left/right volume independently (0-255 each). */
void of_mixer_set_vol_lr(int voice, int vol_l, int vol_r);

/* Enable/disable bidirectional (ping-pong) looping. */
void of_mixer_set_bidi(int voice, int enable);

/* Read current playback position (integer sample index). */
int of_mixer_get_position(int voice);

/* Set playback position (sample offset). */
void of_mixer_set_position(int voice, int sample_offset);

/* Set rate (Hz) + stereo volume in one call — tracker tick hot path. */
void of_mixer_set_voice(int voice, int sample_rate_hz, int vol_l, int vol_r);

/* Set rate (16.16 raw) + stereo volume in one call — zero-division hot path. */
void of_mixer_set_voice_raw(int voice, uint32_t rate_fp16, int vol_l, int vol_r);

/* Allocate sample memory from CRAM1 pool (bump allocator).
 * Returns pointer to 4-byte aligned CRAM1 buffer, or NULL if full.
 * Pass the returned pointer to of_mixer_play(). */
void *of_mixer_alloc_samples(uint32_t size);

/* Reset sample allocator — frees all sample memory. */
void of_mixer_free_samples(void);

#endif /* OFOS_MIXER_H */
