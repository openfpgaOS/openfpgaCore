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
 * The ring lives in a persistent audio reservation (OF_TARGET_AUDIO_
 * STREAM_SIZE bytes) carved by mixer.c before the app starts.
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
 * ahead of the hardware's read pointer.
 *
 * Stream mode (audio_mixer.v ctrl[3] + MIX_VOICE_WPTR): voice 31 is a
 * true FIFO.  of_audio_write publishes the write pointer after each
 * fill; the hardware never fetches at/past it.  If the CPU stalls (a
 * blocking SD read, a long compute frame) the voice plays out its
 * backlog, fades to silence via its volume ramp, holds position, and
 * resumes by itself on the next write — it can no longer loop stale
 * ring contents, no matter how long the stall.
 */

#include "audio.h"
#include "regs.h"
#include "mixer.h"
#include "target_platform.h"

/* The SW music ring's depth is derived from its SDRAM reservation, so there is
 * ONE knob (OF_TARGET_AUDIO_STREAM_SIZE in target_platform.h) rather than a
 * separately-hardcoded pair count. 4 bytes per stereo pair (pocket default
 * 256 KB => 65536 pairs => ~1.37 s at 48 kHz). Apps software-mix music into
 * this ring from the main loop; the depth is how long music keeps playing
 * through a single-thread stall before the stream-mode fade. Music latency
 * through the ring is irrelevant and SFX bypass it. */
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

#define AUDIO_VOICE  31

static inline void ensure_audio_ring(void)
{
    if (!audio_ring) {
        audio_ring = (volatile uint32_t *)(uintptr_t)of_mixer_stream_uncached_base();
        /* One-time zero at first use.  Re-zeroing on every init/stop is
         * unnecessary under stream mode (the voice never fetches at or
         * past WPTR, so stale contents are unreachable) and would cost
         * ~65536 B-response-stalled stores (~tens of ms) per call. */
        for (int i = 0; i < AUDIO_RING_PAIRS; i++) audio_ring[i] = 0;
    }
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
    /* Stream mode: start empty (WPTR == pos == 0) — the voice holds in
     * silence until the first of_audio_write publishes data, instead of
     * playing whatever the ring held.  VOL_RATE=4 gives the ~1.3 ms ramp
     * the hardware uses to fade out just before a starved hold and back
     * in on resume (also declicks app volume writes). */
    MIX_VOICE_WPTR(AUDIO_VOICE)       = 0;
    MIX_VOICE_VOL_LR(AUDIO_VOICE)     = 0xFFFFu;      /* full-scale L+R */
    MIX_VOICE_VOL_TARGET(AUDIO_VOICE) = 0xFFFFu;
    /* The raw VOL_TARGET write above bypasses mixer.c's dedupe shadow;
     * invalidate it or a post-fade restart leaves shadow==0 and the NEXT
     * of_mixer_set_volume(31, 0) fade is silently skipped. */
    of_mixer_vol_target_invalidate(AUDIO_VOICE);
    MIX_VOICE_VOL_RATE(AUDIO_VOICE)   = 4;
    MIX_VOICE_CTRL(AUDIO_VOICE)       = 1u | 2u | 4u | 8u; /* active | stereo | loop | stream */
    audio_voice_active   = 1;
}

void of_audio_init(void)
{
    /* Deactivate FIRST so teardown never races a live voice.  No ring
     * zeroing: stream mode makes contents at/past WPTR unreachable, and
     * configure_stream_voice resets POS/WPTR before reactivating.
     * (ensure_audio_ring zeroes once at first allocation.) */
    MIX_VOICE_CTRL(AUDIO_VOICE) = 0;
    audio_voice_active = 0;
    ensure_audio_ring();
    audio_write_idx = 0;

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
    /* Publish the new frontier to the stream voice.  Ordering is free:
     * every data store above is uncached and stalled on its B-response,
     * so the samples are in SDRAM before this MMIO store issues.  The
     * voice never reads at/past WPTR, which is what makes a stalled
     * producer yield silence-and-hold instead of a stale replay loop. */
    MIX_VOICE_WPTR(AUDIO_VOICE) = idx;
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
    /* Stream mode's hold floor (4 pairs of backlog) is sized for rates
     * up to 2.0 — clamp so a fast source can't step past the floor
     * (sources above 96 kHz play pitch-shifted; none exist today). */
    if (rate > 0x20000u) rate = 0x20000u;
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
