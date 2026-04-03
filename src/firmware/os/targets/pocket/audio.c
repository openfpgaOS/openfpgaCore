/*
 * openfpgaOS Audio HAL Implementation
 */

#include "audio.h"
#include "regs.h"

void of_audio_init(void) {
    of_opl_reset();
}

/* IRQ handler — called from trap entry for external interrupts */
void irq_handler(void *frame) {
    (void)frame;
}

int of_audio_write(const int16_t *samples, int count) {
    int written = 0;

    for (int i = 0; i < count; i++) {
        if (AUDIO_STATUS & AUDIO_FIFO_FULL)
            break;

        /* Pack L/R 16-bit samples into one 32-bit word */
        uint32_t left  = (uint16_t)samples[i * 2];
        uint32_t right = (uint16_t)samples[i * 2 + 1];
        AUDIO_SAMPLE = (right << 16) | left;
        written++;
    }

    return written;
}

int of_audio_get_free(void) {
    int level = AUDIO_STATUS & AUDIO_FIFO_LEVEL_MASK;
    return AUDIO_FIFO_DEPTH - level;
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
