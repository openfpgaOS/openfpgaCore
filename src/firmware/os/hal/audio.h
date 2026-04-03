/*
 * openfpgaOS Audio HAL
 * 48kHz stereo I2S output via hardware FIFO + OPL (YMF262 OPL3) FM synthesizer
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
 * OPL (YMF262 OPL3) FM Synthesizer
 * ====================================================================== */

/* Write a value to an OPL register.
 * Registers 0x00-0xFF target bank 0, 0x100-0x1FF target bank 1.
 * CPU stalls automatically during write timing. */
void of_opl_write(uint16_t reg, uint8_t val);

/* Reset OPL (clear all registers) */
void of_opl_reset(void);

#endif /* OFOS_AUDIO_H */
