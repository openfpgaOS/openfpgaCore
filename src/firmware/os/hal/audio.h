/*
 * openfpgaOS Audio HAL
 * 48 kHz stereo I2S output via hardware FIFO.
 */

#ifndef OFOS_AUDIO_H
#define OFOS_AUDIO_H

#include <stdint.h>

/* Initialize audio subsystem */
void of_audio_init(void);

/* Write stereo sample pairs to the audio FIFO.
 * samples: interleaved L/R 16-bit signed samples
 * count: number of stereo pairs to write
 * Returns number of pairs actually written (limited by FIFO space). */
int of_audio_write(const int16_t *samples, int count);

/* Get number of free entries in audio FIFO */
int of_audio_get_free(void);

/* ======================================================================
 * Streaming Audio (ping-pong double-buffered)
 * ====================================================================== */

/* Open a stream. sample_rate is the input rate (resampled to 48kHz). */
int of_audio_stream_open(int sample_rate);

/* Write interleaved stereo s16 sample pairs.
 * Returns number of stereo pairs actually accepted. */
int of_audio_stream_write(const int16_t *samples, int count);

/* Check if the next write buffer is available (non-blocking). */
int of_audio_stream_ready(void);

/* Stop streaming and release voices. */
void of_audio_stream_close(void);

#endif /* OFOS_AUDIO_H */
