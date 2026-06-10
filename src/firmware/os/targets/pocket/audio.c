//------------------------------------------------------------------------------
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileType: SOURCE
// SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
//------------------------------------------------------------------------------

/*
 * openfpgaOS Pocket audio target — honest stereo PCM path on the HW mixer.
 *
 * of_audio_write() pushes interleaved stereo s16 into an SDRAM ring buffer
 * that the hardware mixer plays back on voice 31 (stereo, forward-loop).
 * The ring lives in a 16 KB persistent audio reservation carved by
 * mixer.c before the app starts.
 *
 * Cache discipline: audio_ring uses the UNCACHED SDRAM alias.  Each
 * CPU store goes straight through p_axi to SDRAM and stalls on its AXI
 * B-response, so by the time of_audio_write() returns, the sample is
 * visible to the HW mixer's next fetch.  Cached writes + cbo.clean
 * (the natural-looking pattern) are not safe here: cbo.clean is
 * unreliable on this VexiiRiscv config, and even cbo.flush only
 * settles d_axi's write FIFO over millisecond timescales — too slow
 * for the mixer's sub-ms reads.  See hal/cache.c's commentary.
 *
 * The HW mixer runs autonomously off audio_output's dcfifo — CPU is no
 * longer on a sample-accurate deadline.  We only need to keep the ring
 * ahead of the hardware's read pointer, with ~42 ms of slack.
 */

#include "audio.h"
#include "regs.h"
#include "mixer.h"
#include "target_platform.h"

/* The SW music ring's depth is derived from its SDRAM reservation, so there is
 * ONE knob (OF_TARGET_AUDIO_STREAM_SIZE in target_platform.h) rather than a
 * separately-hardcoded pair count. 4 bytes per stereo pair. Default reservation
 * 64 KB => 16384 pairs => ~341 ms at 48 kHz. Apps software-mix music into this
 * ring from the main loop; sizing it to outlast the worst single-thread stall
 * (heavy frames, MPQ/bzip2 loads) keeps the autonomous HW mixer from replaying
 * its tail. Music latency through the ring is irrelevant and SFX bypass it. */
/* (int) so comparisons against int loop counters stay signed-clean, matching
 * the original plain-int literal. */
#define AUDIO_RING_PAIRS  ((int)(OF_TARGET_AUDIO_STREAM_SIZE / 4u))
_Static_assert((AUDIO_RING_PAIRS & (AUDIO_RING_PAIRS - 1)) == 0,
               "AUDIO_RING_PAIRS must be a power of two (ring index uses a mask)");

/* Interleaved L,R,L,R... via the uncached SDRAM alias (see file header
 * for why).  HW mixer's AXI master sees the same physical SDRAM
 * regardless of which CPU alias we write through.  Addressed as one
 * 32-bit word per stereo pair ({hi=R, lo=L} little-endian — the same
 * memory image as adjacent L,R int16 stores) so each pair costs ONE
 * uncached store's B-response stall instead of two. */
static volatile uint32_t *audio_ring;
static uint32_t audio_write_idx;        /* next stereo pair to fill (mod AUDIO_RING_PAIRS) */
static int      audio_voice_active;
static uint32_t audio_voice_rate = 0x10000u;  /* Q16.16: 1.0 = 48 kHz */

#define AUDIO_VOICE  31

static inline void ensure_audio_ring(void)
{
    if (!audio_ring)
        audio_ring = (volatile uint32_t *)(uintptr_t)of_mixer_stream_uncached_base();
}

static inline uint32_t ring_read_pos(void)
{
    /* pos_int is in stereo-pair units for a stereo voice (HW multiplies
     * by 4 for byte stride), so the gap math below works in pair units.
     * Flat MMIO addressing — no SEL latch, no race with the mixer ISR. */
    return MIX_VOICE_POS(AUDIO_VOICE) & (AUDIO_RING_PAIRS - 1);
}

static void configure_stream_voice(uint32_t rate_fp16)
{
    ensure_audio_ring();
    MIX_VOICE_ADDR(AUDIO_VOICE)       = of_mixer_stream_base();
    MIX_VOICE_LEN(AUDIO_VOICE)        = AUDIO_RING_PAIRS;
    MIX_VOICE_RATE(AUDIO_VOICE)       = rate_fp16;
    MIX_VOICE_POS_WR(AUDIO_VOICE)     = 0;
    MIX_VOICE_LOOP_START(AUDIO_VOICE) = 0;
    MIX_VOICE_LOOP_END(AUDIO_VOICE)   = AUDIO_RING_PAIRS;
    MIX_VOICE_VOL_LR(AUDIO_VOICE)     = 0xFFFFu;      /* full-scale L+R, snapped */
    MIX_VOICE_VOL_TARGET(AUDIO_VOICE) = 0xFFFFu;
    MIX_VOICE_VOL_RATE(AUDIO_VOICE)   = 0;
    MIX_VOICE_CTRL(AUDIO_VOICE)       = 1u | 2u | 4u; /* active | stereo | loop */
    audio_voice_active   = 1;
    audio_voice_rate     = rate_fp16;
}

void of_audio_init(void)
{
    ensure_audio_ring();
    /* Uncached writes — each store stalls on its B-response, so by
     * the loop's end the ring is fully zeroed in SDRAM.  No flush. */
    for (int i = 0; i < AUDIO_RING_PAIRS; i++) audio_ring[i] = 0;
    audio_write_idx = 0;

    /* Stream voice is dormant until the first write; configure_stream_voice
     * activates it on first use. */
    MIX_VOICE_CTRL(AUDIO_VOICE) = 0;
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
    ensure_audio_ring();

    if (!audio_voice_active) configure_stream_voice(0x10000u);

    uint32_t room = ring_room_pairs();
    if ((uint32_t)count > room) count = (int)room;
    if (count <= 0) return 0;

    /* Uncached writes — visible to the HW mixer immediately on each
     * store's B-response.  No post-write flush needed. */
    uint32_t idx = audio_write_idx;
    for (int i = 0; i < count; i++) {
        audio_ring[idx] = ((uint32_t)(uint16_t)samples[i * 2 + 1] << 16)  /* R */
                        | (uint16_t)samples[i * 2];                       /* L */
        idx = (idx + 1) & (AUDIO_RING_PAIRS - 1);
    }
    audio_write_idx = idx;
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
    ensure_audio_ring();
    uint32_t rate = ((uint64_t)sample_rate << 16) / 48000;
    /* Uncached zeroing — same rationale as of_audio_init. */
    for (int i = 0; i < AUDIO_RING_PAIRS; i++) audio_ring[i] = 0;
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
    MIX_VOICE_CTRL(AUDIO_VOICE) = 0;
    audio_voice_active = 0;
    audio_write_idx = 0;
}
