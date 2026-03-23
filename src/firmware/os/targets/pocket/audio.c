/*
 * openfpgaOS Audio HAL Implementation
 */

#include "audio.h"
#include "regs.h"

void of_audio_init(void) {
    of_opl_reset();
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

/* ======================================================================
 * Ring buffer for kernel-side audio drain
 * ====================================================================== */

#define AUDIO_RING_SIZE 8192  /* stereo pairs (32KB total) */

static int16_t audio_ring[AUDIO_RING_SIZE * 2];  /* L/R interleaved */
static volatile int ring_read;
static volatile int ring_write;

static int ring_used(void) {
    int used = ring_write - ring_read;
    if (used < 0) used += AUDIO_RING_SIZE;
    return used;
}

int of_audio_enqueue(const int16_t *samples, int count) {
    int enqueued = 0;
    for (int i = 0; i < count; i++) {
        int next = (ring_write + 1) % AUDIO_RING_SIZE;
        if (next == ring_read)
            break;  /* ring full */
        audio_ring[ring_write * 2]     = samples[i * 2];
        audio_ring[ring_write * 2 + 1] = samples[i * 2 + 1];
        ring_write = next;
        enqueued++;
    }
    return enqueued;
}

int of_audio_ring_free(void) {
    return AUDIO_RING_SIZE - 1 - ring_used();
}

void of_audio_drain(void) {
    /* Push as many samples as possible from ring → hardware FIFO */
    while (ring_read != ring_write) {
        if (AUDIO_STATUS & AUDIO_FIFO_FULL)
            break;
        uint32_t left  = (uint16_t)audio_ring[ring_read * 2];
        uint32_t right = (uint16_t)audio_ring[ring_read * 2 + 1];
        AUDIO_SAMPLE = (right << 16) | left;
        ring_read = (ring_read + 1) % AUDIO_RING_SIZE;
    }
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
