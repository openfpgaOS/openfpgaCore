/*
 * openfpgaOS Audio HAL Implementation
 */

#include "audio.h"
#include "regs.h"
#include "cache.h"

/* Forward declarations — used by of_audio_init */
static int audio_buf_idx;
static int audio_ever_written;

void of_audio_init(void) {
    /* Enable hardware mixer (voices start inactive) */
    MIX_CTRL = 1;
    audio_buf_idx = 0;
    audio_ever_written = 0;
}

/* Ping-pong scratch buffers in CRAM1 for of_audio_write(). */
#define AUDIO_SCRATCH_CRAM1   (CRAM1_UNCACHED + 0x00F00000)  /* Last 1MB of CRAM1 */
#define AUDIO_SCRATCH_HALF    (256 * 1024)                    /* 256K samples per buffer */
#define AUDIO_SCRATCH_VOICE   31

int of_audio_write(const int16_t *samples, int count) {
    if (count <= 0 || !samples) return 0;
    if (count > (int)AUDIO_SCRATCH_HALF) count = AUDIO_SCRATCH_HALF;

    /* Wait for previous buffer to finish if the scratch voice is still playing
     * from the buffer we're about to overwrite. */
    MIX_VOICE_SEL = AUDIO_SCRATCH_VOICE;
    uint32_t scratch_addr = AUDIO_SCRATCH_CRAM1 + audio_buf_idx * AUDIO_SCRATCH_HALF * 2;

    /* Copy mono samples directly into CRAM1 scratch */
    volatile int16_t *dest = (volatile int16_t *)scratch_addr;
    for (int i = 0; i < count; i++)
        dest[i] = samples[i];

    /* Flush D-cache — uncached alias may still be cached */
    of_cache_clean_range((void *)scratch_addr, count * 2);

    /* Play via mixer voice 31 */
    MIX_VOICE_SEL = AUDIO_SCRATCH_VOICE;
    MIX_VOICE_ADDR = (scratch_addr - CRAM1_UNCACHED) >> 2;
    MIX_VOICE_LEN = count;
    MIX_VOICE_RATE = 0x10000;  /* 1:1 (48kHz native) */
    MIX_VOICE_VOL_LR = 0xFFFF;       /* full volume L+R */
    MIX_VOICE_VOL_TARGET = 0xFFFF;
    MIX_VOICE_VOL_RATE = 0;          /* instant */
    MIX_VOICE_CTRL = 1 | (1 << 2);   /* active + fmt16 */

    /* Swap to other half for next write */
    audio_buf_idx ^= 1;
    audio_ever_written = 1;

    return count;
}

int of_audio_get_free(void) {
    if (!audio_ever_written)
        return AUDIO_SCRATCH_HALF;  /* never written — always free */
    if (MIX_IRQ_PENDING & (1u << AUDIO_SCRATCH_VOICE))
        return AUDIO_SCRATCH_HALF;  /* voice finished */
    return 0;  /* still playing */
}


/* ======================================================================
 * Streaming audio (ping-pong voices 29+30)
 * ====================================================================== */

#define STREAM_VOICE_A    29
#define STREAM_VOICE_B    30
#define STREAM_BUF_SIZE   (32 * 1024)   /* 32K samples per half (~0.67s at 48kHz) */
#define STREAM_BUF_BASE   (CRAM1_UNCACHED + 0x00E00000)  /* Before scratch area */

static int stream_active;
static int stream_write_buf;   /* 0 or 1: which buffer is being filled */
static int stream_writes_done; /* how many writes completed (0,1,2+) */
static uint32_t stream_rate;

int of_audio_stream_open(int sample_rate) {
    if (stream_active) return -1;

    stream_rate = ((uint64_t)sample_rate << 16) / 48000;
    stream_write_buf = 0;
    stream_writes_done = 0;
    stream_active = 1;

    /* Stop both stream voices */
    MIX_VOICE_SEL = STREAM_VOICE_A;
    MIX_VOICE_CTRL = 0;
    MIX_VOICE_SEL = STREAM_VOICE_B;
    MIX_VOICE_CTRL = 0;

    /* Clear their IRQ bits */
    MIX_IRQ_CLEAR = (1u << STREAM_VOICE_A) | (1u << STREAM_VOICE_B);

    return 0;
}

static void stream_start_voice(int voice, int buf_idx) {
    uint32_t addr = STREAM_BUF_BASE + buf_idx * STREAM_BUF_SIZE * 2;
    MIX_VOICE_SEL = voice;
    MIX_VOICE_ADDR = (addr - CRAM1_UNCACHED) >> 2;
    MIX_VOICE_LEN = STREAM_BUF_SIZE;
    MIX_VOICE_RATE = stream_rate;
    MIX_VOICE_VOL_LR = 0xFFFF;
    MIX_VOICE_VOL_TARGET = 0xFFFF;
    MIX_VOICE_VOL_RATE = 0;
    MIX_VOICE_CTRL = (1 << 2) | 1;  /* active + fmt16 */
}

int of_audio_stream_write(const int16_t *samples, int count) {
    if (!stream_active || !samples || count <= 0) return 0;
    if (count > (int)STREAM_BUF_SIZE) count = STREAM_BUF_SIZE;

    uint32_t addr = STREAM_BUF_BASE + stream_write_buf * STREAM_BUF_SIZE * 2;
    volatile int16_t *dest = (volatile int16_t *)addr;
    for (int i = 0; i < count; i++)
        dest[i] = samples[i];

    /* Pad remainder with silence if short */
    for (int i = count; i < (int)STREAM_BUF_SIZE; i++)
        dest[i] = 0;

    of_cache_clean_range((void *)addr, STREAM_BUF_SIZE * 2);

    /* Start the voice for this buffer */
    int voice = (stream_write_buf == 0) ? STREAM_VOICE_A : STREAM_VOICE_B;
    MIX_IRQ_CLEAR = (1u << voice);
    stream_start_voice(voice, stream_write_buf);

    stream_write_buf ^= 1;
    stream_writes_done++;
    return count;
}

int of_audio_stream_ready(void) {
    if (!stream_active) return 1;
    /* First two writes always ready (filling both ping-pong buffers) */
    if (stream_writes_done < 2) return 1;
    /* After that, check if the target buffer's voice has finished */
    int voice = (stream_write_buf == 0) ? STREAM_VOICE_A : STREAM_VOICE_B;
    return (MIX_IRQ_PENDING & (1u << voice)) ? 1 : 0;
}

void of_audio_stream_close(void) {
    if (!stream_active) return;
    MIX_VOICE_SEL = STREAM_VOICE_A;
    MIX_VOICE_CTRL = 0;
    MIX_VOICE_SEL = STREAM_VOICE_B;
    MIX_VOICE_CTRL = 0;
    MIX_IRQ_CLEAR = (1u << STREAM_VOICE_A) | (1u << STREAM_VOICE_B);
    stream_active = 0;
}

