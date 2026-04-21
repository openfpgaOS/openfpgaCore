/*
 * openfpgaOS software block mixer
 * -------------------------------
 * Replaces the retired hardware `audio_mixer.v`.  The CPU mixes up to
 * SWMIXER_MAX_VOICES voices in fixed-size blocks (one tick = one block),
 * saturates to s16, and stores stereo pairs into a ring in cached SDRAM.
 * audio_dma.v (AXI M4 on the SDRAM arbiter) streams the ring into the
 * audio_output dcfifo at 48 kHz.
 *
 *   block size  = 48 stereo samples   (1 ms at 48 kHz)
 *   tick rate   = 1 kHz               (matches of_smp_voice envelope tick)
 *   ring depth  = 4096 stereo entries (~85 ms producer-consumer slack)
 *   output FIFO = 1024 stereo entries (~21 ms slack)
 *
 * Layout:
 *   - Voice state is held in plain C structs, owned by the CPU.
 *   - Sample data lives in the SDRAM sample pool as signed PCM,
 *     read via the D-cache.  (v2 arch: moved from CRAM1 to SDRAM.)
 *   - DMA ring lives in SDRAM, written cached + cbo.clean flushed.
 *   - of_mixer_* HAL sits on top of this engine (see hal/mixer.c).
 *
 * The tick is driven from irq.c's timer ISR; every fire pushes one block.
 */

#ifndef OFOS_SWMIXER_H
#define OFOS_SWMIXER_H

#include <stdint.h>

/* Voice pool cut 32→16 so the inner loop runs half as long when the
 * SF2 pump fires a dense passage.  Matches audio.md's target of
 * "16 SF2 voices".  of_mixer_play's voice-stealer handles the cap.
 * Doom's renderer needs the CPU back. */
/* 16 voices is the nominal SF2 target but the ISR cost scales linearly
 * with voice count.  On this 100 MHz RV32 with the software mixer,
 * 16 active voices pushes the 1 kHz ISR to ~85 % CPU and starves Doom's
 * renderer.  Halving polyphony to 8 caps peak ISR ≈ 45 %; music loses
 * some density but melodies + bass remain intact, and Doom gets back
 * enough CPU to render the full demo loop smoothly. */
#define SWMIXER_MAX_VOICES    8
#define SWMIXER_OUTPUT_RATE   48000
#define SWMIXER_BLOCK_SAMPLES 48

/* Loop modes (match OFSF_LOOP_* values used by of_smp_voice / of_smp_bank). */
#define SWMIXER_LOOP_NONE     0
#define SWMIXER_LOOP_FORWARD  1
#define SWMIXER_LOOP_BIDI     2

typedef struct {
    /* Sample data */
    const void *sample;     /* CPU pointer to PCM blob in SDRAM sample pool */
    uint32_t    len;        /* total sample count */
    uint32_t    loop_start; /* sample index */
    uint32_t    loop_end;   /* sample index (exclusive) */

    /* Playback cursor (Q16.16 within len) */
    uint32_t pos_int;
    uint16_t pos_frac;

    /* Rate in Q16.16, where 0x10000 = unity (source rate == 48 kHz) */
    uint32_t rate_fp16;

    /* Current and target volumes, 0..255 per channel.  vol_*_cur ramps
     * toward vol_*_tgt at vol_rate per output sample (0 = instant). */
    uint8_t vol_l_cur, vol_r_cur;
    uint8_t vol_l_tgt, vol_r_tgt;
    uint8_t vol_rate;

    /* Flags */
    uint8_t active     : 1;
    uint8_t fmt16      : 1;  /* 1 = s16 source, 0 = s8 source */
    uint8_t stereo     : 1;  /* 1 = interleaved {L,R} source */
    uint8_t loop_mode  : 2;  /* SWMIXER_LOOP_* */
    uint8_t dir_rev    : 1;  /* BIDI direction: 1=reverse, 0=forward */
    uint8_t end_pending: 1;  /* voice reached end since last poll */
    uint8_t _pad       : 1;
} swmixer_voice_t;

void swmixer_init(void);

/* Programs the SDRAM → audio_output DMA (audio_dma.v) with a 16 KB ring
 * at a fixed SDRAM offset (see DMA_RING_ADDR in swmixer.c).  The DMA
 * enable is deferred to the first swmixer_tick() call (post app-load,
 * bridge is free). */
void swmixer_dma_start(void);

/* Mix one 48-sample block into the SDRAM DMA ring.  Called from the
 * 1 kHz timer ISR — the DMA's ~85 ms of buffering lets the ISR be
 * delayed freely without audio glitches, and the ring lives in cached
 * SDRAM so each block is ~48 L1 stores + 3 cbo.cleans. */
void swmixer_tick(void);

/* Current producer-consumer gap in stereo pairs.  Exposed so the main-
 * thread pump loop knows how much to mix to reach a target buffer
 * fullness before yielding back to the renderer. */
uint32_t swmixer_ring_gap(void);

/* Direct access for HAL bindings.  Returns NULL if idx is out of range. */
swmixer_voice_t *swmixer_voice(int idx);

/* Bitmask of voices that ended since the last call.  W1C semantics. */
uint32_t swmixer_poll_ended(void);

/* Convenience: stop a voice cleanly (snaps vol to 0 and deactivates). */
void swmixer_stop(int idx);
void swmixer_stop_all(void);

#endif /* OFOS_SWMIXER_H */
