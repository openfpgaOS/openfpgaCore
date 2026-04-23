/*
 * openfpgaOS Pocket audio target — honest stereo PCM path on the HW mixer.
 *
 * of_audio_write() pushes interleaved stereo s16 into an SDRAM ring buffer
 * that the hardware mixer plays back on voice 31 (stereo, forward-loop).
 * The ring lives in the reserved 16 KB window at the base of the sample
 * pool (see AUDIO_STREAM_RESERVED_BYTES in hal/mixer.c), which
 * of_mixer_alloc_samples skips past so app sample allocations don't
 * collide with it.
 *
 * The HW mixer runs autonomously off audio_output's dcfifo — CPU is no
 * longer on a sample-accurate deadline.  We only need to keep the ring
 * ahead of the hardware's read pointer, with ~42 ms of slack.
 */

#include "audio.h"
#include "regs.h"
#include "cache.h"
#include "mixer.h"   /* of_mixer_irq_save / restore — SEL race guard */

/* Ring: 2048 stereo pairs = 16 KB = ~42 ms of slack at 48 kHz.  Must
 * match AUDIO_STREAM_RESERVED_BYTES in hal/mixer.c. */
#define AUDIO_RING_PAIRS  2048

/* Interleaved L,R,L,R...  Anchored at the base of the SDRAM sample pool
 * so MIX_VOICE_ADDR can be expressed as offset 0.  The first
 * AUDIO_STREAM_RESERVED_BYTES of the pool are carved off by the mixer
 * HAL's allocator so we own this region exclusively. */
static int16_t  * const audio_ring = (int16_t *)SAMPLE_POOL_BASE;
static uint32_t audio_write_idx;        /* next stereo pair to fill (mod AUDIO_RING_PAIRS) */
static int      audio_voice_active;
static uint32_t audio_voice_rate = 0x10000u;  /* Q16.16: 1.0 = 48 kHz */

#define AUDIO_VOICE  31

static inline uint32_t ring_read_pos(void)
{
    /* pos_int is in stereo-pair units for a stereo voice (HW multiplies
     * by 4 for byte stride), so the gap math below works in pair units. */
    uint32_t mie = of_mixer_irq_save();
    MIX_VOICE_SEL = AUDIO_VOICE;
    uint32_t pos = MIX_VOICE_POS & (AUDIO_RING_PAIRS - 1);
    of_mixer_irq_restore(mie);
    return pos;
}

static void configure_stream_voice(uint32_t rate_fp16)
{
    uint32_t mie = of_mixer_irq_save();
    MIX_VOICE_SEL        = AUDIO_VOICE;
    MIX_VOICE_ADDR       = (uint32_t)audio_ring - SAMPLE_POOL_BASE;   /* = 0 */
    MIX_VOICE_LEN        = AUDIO_RING_PAIRS;
    MIX_VOICE_RATE       = rate_fp16;
    MIX_VOICE_POS_WR     = 0;
    MIX_VOICE_LOOP_START = 0;
    MIX_VOICE_LOOP_END   = AUDIO_RING_PAIRS;
    MIX_VOICE_VOL_LR     = 0xFFFFu;      /* full-scale L+R, snapped */
    MIX_VOICE_VOL_TARGET = 0xFFFFu;
    MIX_VOICE_VOL_RATE   = 0;
    MIX_VOICE_CTRL       = 1u | 2u | 4u; /* active | stereo | loop */
    of_mixer_irq_restore(mie);
    audio_voice_active   = 1;
    audio_voice_rate     = rate_fp16;
}

void of_audio_init(void)
{
    for (int i = 0; i < AUDIO_RING_PAIRS * 2; i++) audio_ring[i] = 0;
    /* Flush the zeroed ring to SDRAM so the HW mixer's first fetch sees
     * silence rather than whatever stale value is in DRAM. */
    of_cache_clean_range(audio_ring, AUDIO_RING_PAIRS * 2 * sizeof(int16_t));
    audio_write_idx = 0;

    /* Stream voice is dormant until the first write; configure_stream_voice
     * activates it on first use. */
    uint32_t mie = of_mixer_irq_save();
    MIX_VOICE_SEL  = AUDIO_VOICE;
    MIX_VOICE_CTRL = 0;
    of_mixer_irq_restore(mie);
    audio_voice_active = 0;

    /* Make sure the HW mixer is globally enabled — boot order may call
     * of_audio_init before the app's of_mixer_init. */
    MIX_CTRL = MIX_CTRL_ENABLE;
}

static inline uint32_t ring_room_pairs(void)
{
    if (!audio_voice_active) return AUDIO_RING_PAIRS;
    uint32_t read_pair = ring_read_pos();
    uint32_t gap = (audio_write_idx + AUDIO_RING_PAIRS - read_pair) & (AUDIO_RING_PAIRS - 1);
    /* Leave one pair unused so wrap is unambiguous. */
    return AUDIO_RING_PAIRS - 1 - gap;
}

int of_audio_write(const int16_t *samples, int count)
{
    if (!samples || count <= 0) return 0;

    if (!audio_voice_active) configure_stream_voice(0x10000u);

    uint32_t room = ring_room_pairs();
    if ((uint32_t)count > room) count = (int)room;
    if (count <= 0) return 0;

    uint32_t idx = audio_write_idx;
    for (int i = 0; i < count; i++) {
        audio_ring[(idx * 2)]     = samples[i * 2];      /* L */
        audio_ring[(idx * 2) + 1] = samples[i * 2 + 1];  /* R */
        idx = (idx + 1) & (AUDIO_RING_PAIRS - 1);
    }
    audio_write_idx = idx;

    /* Push the newly-written pairs out to SDRAM so the HW mixer's next
     * fetch sees them. */
    uint32_t start_pair = (audio_write_idx - (uint32_t)count) & (AUDIO_RING_PAIRS - 1);
    if (start_pair + (uint32_t)count <= AUDIO_RING_PAIRS) {
        of_cache_clean_range(&audio_ring[start_pair * 2],
                             (uint32_t)count * 2 * sizeof(int16_t));
    } else {
        uint32_t head = AUDIO_RING_PAIRS - start_pair;
        of_cache_clean_range(&audio_ring[start_pair * 2],
                             head * 2 * sizeof(int16_t));
        of_cache_clean_range(&audio_ring[0],
                             ((uint32_t)count - head) * 2 * sizeof(int16_t));
    }
    return count;
}

int of_audio_get_free(void)
{
    return (int)ring_room_pairs();
}

/* Streaming wrapper: same ring, reconfigurable source rate for rate
 * conversion in the HW mixer. */
int of_audio_stream_open(int sample_rate)
{
    if (sample_rate <= 0) return -1;
    uint32_t rate = ((uint64_t)sample_rate << 16) / 48000;
    for (int i = 0; i < AUDIO_RING_PAIRS * 2; i++) audio_ring[i] = 0;
    of_cache_clean_range(audio_ring, AUDIO_RING_PAIRS * 2 * sizeof(int16_t));
    audio_write_idx = 0;
    configure_stream_voice(rate);
    return 0;
}

int of_audio_stream_write(const int16_t *samples, int count)
{
    return of_audio_write(samples, count);
}

int of_audio_stream_ready(void)
{
    return of_audio_get_free() >= (AUDIO_RING_PAIRS / 4);
}

void of_audio_stream_close(void)
{
    uint32_t mie = of_mixer_irq_save();
    MIX_VOICE_SEL  = AUDIO_VOICE;
    MIX_VOICE_CTRL = 0;
    of_mixer_irq_restore(mie);
    audio_voice_active = 0;
    audio_write_idx = 0;
}
