/*
 * openfpgaOS Audio HAL Implementation
 */

#include "audio.h"
#include "regs.h"

void of_audio_init(void) {
    of_opl_reset();
    /* Enable hardware mixer (voices start inactive) */
    MIX_CTRL = 1;


}

/* IRQ handler moved to kernel/irq.c */

/* Scratch buffer in CRAM1 for of_audio_write() convenience wrapper. */
#define AUDIO_SCRATCH_CRAM1   (CRAM1_UNCACHED + 0x00F00000)  /* Last 1MB of CRAM1 */
#define AUDIO_SCRATCH_VOICE   31
#define AUDIO_SCRATCH_MAX     (256 * 1024)

int of_audio_write(const int16_t *samples, int count) {
    if (count <= 0 || !samples) return 0;
    if (count > (int)(AUDIO_SCRATCH_MAX)) count = AUDIO_SCRATCH_MAX;

    /* Convert stereo interleaved to mono (average L+R) into CRAM1 scratch */
    volatile int16_t *dest = (volatile int16_t *)AUDIO_SCRATCH_CRAM1;
    for (int i = 0; i < count; i++)
        dest[i] = (int16_t)(((int32_t)samples[i * 2] + samples[i * 2 + 1]) >> 1);

    /* Play via mixer voice 31 */
    MIX_VOICE_SEL = AUDIO_SCRATCH_VOICE;
    MIX_VOICE_ADDR = (AUDIO_SCRATCH_CRAM1 - CRAM1_UNCACHED) >> 2;
    MIX_VOICE_LEN = count;
    MIX_VOICE_RATE = 0x10000;  /* 1:1 (48kHz native) */
    MIX_VOICE_CTRL = (15 << 4) | 1;  /* vol=15 (full), active=1 */

    return count;
}

int of_audio_get_free(void) {
    /* Always report space available — mixer handles buffering */
    return AUDIO_SCRATCH_MAX;
}


void of_opl_write(uint16_t reg, uint8_t val) {
    if (reg & 0x100) {
        /* Bank 1 */
        OPL_ADDR2 = reg & 0xFF;
        OPL_DATA2 = val;
    } else {
        /* Bank 0 */
        OPL_ADDR = reg;
        OPL_DATA = val;
    }
}

void of_opl_reset(void) {
    /* Clear all OPL registers (both banks) */
    for (int i = 0; i < 256; i++) {
        of_opl_write(i, 0);
        of_opl_write(0x100 + i, 0);
    }
}
