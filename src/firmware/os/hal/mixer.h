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

#endif /* OFOS_MIXER_H */
