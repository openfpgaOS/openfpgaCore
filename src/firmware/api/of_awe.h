/*
 * of_awe.h -- AWE audio coprocessor API for openfpgaOS.
 *
 * AWE owns per-voice control-rate state (envelopes, LFOs, mod matrix,
 * composers) in fabric.  CPU dispatches MIDI / MOD / SFX events and
 * calls awe_voice_load() / awe_voice_trigger() to start a note;
 * thereafter AWE drives the underlying PCM mixer at 1 kHz with no
 * further CPU work.  Per-channel CC writes and globals also flow
 * through AWE.
 *
 * Phase 1 scope (the first checkpoint to ship):
 *   - Register file in fabric is live.
 *   - awe_voice_load() + awe_voice_trigger() emit the same initial
 *     mixer writes (RATE, VOL_L/R, FILTER, sample addresses) the SW
 *     of_smp_voice path produces today.
 *   - No tick-rate processing yet (no envelope advance, no LFO, no
 *     mod matrix).  All non-playback fields in awe_voice_t are stored
 *     in voice-state RAM but unused until later phases land them.
 *
 * Phase 1 is additive: existing of_mixer / of_smp_voice / of_midi paths
 * keep working unchanged.  Apps opt into AWE explicitly.
 */

#ifndef OF_AWE_H
#define OF_AWE_H

#ifdef __cplusplus
extern "C" {
#endif

#include <stdint.h>
#include <stddef.h>

/* ------------------------------------------------------------------ */
/* Constants                                                          */
/* ------------------------------------------------------------------ */

#define OF_AWE_MAX_VOICES   32
#define OF_AWE_NUM_CHANNELS 16
#define OF_AWE_NUM_SEGMENTS  4   /* per ramp */

/* Ramp segment "next" sentinels. */
#define AWE_SEG_NEXT_STAY   0xFE  /* hold this segment forever */
#define AWE_SEG_NEXT_DONE   0xFF  /* end of envelope -- voice retires */

/* Loop modes (must match OFSF_LOOP_* in of_smp_bank.h). */
#define AWE_LOOP_NONE       0
#define AWE_LOOP_FORWARD    1
#define AWE_LOOP_BIDI       3

/* Per-voice interpolation modes. */
#define AWE_INTERP_NONE     0   /* nearest-neighbour, Amiga authenticity */
#define AWE_INTERP_LINEAR   1   /* default for MOD / SFX */
#define AWE_INTERP_CUBIC    2   /* 4-point cubic, MIDI / high quality */

/* LFO waveform select. */
#define AWE_WAVE_TRIANGLE   0
#define AWE_WAVE_SINE       1
#define AWE_WAVE_SQUARE     2
#define AWE_WAVE_SAWTOOTH   3
#define AWE_WAVE_RANDOM     4

/* Filter cutoff "bypass" sentinel.  Setting initial_fc to this value
 * (and clearing all filter-mod scales) gates the SVF out of the voice's
 * signal path.  Same threshold used in the SF2 zone runtime. */
#define AWE_FILTER_BYPASS   13500   /* cents — wide-open + bypass */

/* ------------------------------------------------------------------ */
/* Per-voice config struct -- written to AWE via awe_voice_load()     */
/* ------------------------------------------------------------------ */

/* Ramp segment: target level (Q16.16, 0..0x10000) reached at the given
 * rate (Q16.16 incr/decr per 1 ms tick), or after timer_ticks of delay
 * if rate is 0 (delay/hold stages).  When the level reaches target the
 * ramp advances to segment `next`. */
typedef struct {
    uint32_t target;       /* Q16.16 */
    uint32_t rate;         /* Q16.16 incr/decr per tick (0 = timer stage) */
    uint32_t timer_ticks;  /* used when rate == 0 (delay / hold) */
    uint8_t  next;         /* index into segments, or AWE_SEG_NEXT_* */
    uint8_t  _pad[3];
} awe_segment_t;

/* LFO config.  Triangle is the only waveform required for Phase 4
 * mod-matrix bring-up; others may be deferred per the rev-A scope. */
typedef struct {
    uint32_t rate;         /* Q16.16 phase incr per 1 ms tick */
    uint32_t delay_ticks;  /* start-after-N-ticks */
    uint8_t  waveform;     /* AWE_WAVE_* */
    uint8_t  _pad[3];
} awe_lfo_t;

/* Mod-matrix scales (signed cents per unit of source).  Zero scale
 * means the link contributes nothing -- safe default for unused links. */
typedef struct {
    int16_t lfo0_pitch;    /* MIDI mod_lfo_to_pitch / MOD vibrato */
    int16_t lfo0_filter;   /* MIDI mod_lfo_to_filter */
    int16_t lfo1_pitch;    /* MIDI vib_lfo_to_pitch */
    int16_t ramp1_pitch;   /* MIDI mod_env_to_pitch / MOD portamento */
    int16_t ramp1_filter;  /* MIDI mod_env_to_filter */
    int16_t _pad;
} awe_mm_t;

/* Tagged so of_services.h can forward-declare `struct awe_voice_t`
 * without pulling this whole header in. */
typedef struct awe_voice_t {
    /* Sample playback ----------------------------------------------- */
    const void *base;          /* CPU pointer to start of sample data
                                * in the DMA-target memory the mixer
                                * walks (CRAM1 on Pocket).  Kernel HAL
                                * converts to the mixer's word-address
                                * format internally. */
    uint32_t length;           /* in samples */
    uint32_t loop_start;       /* in samples (relative to base) */
    uint32_t loop_end;         /* in samples (relative to base) */
    uint8_t  loop_mode;        /* AWE_LOOP_* */
    uint8_t  interp_mode;      /* AWE_INTERP_* */
    uint8_t  fmt16;            /* 1 = 16-bit signed, 0 = 8-bit signed */
    uint8_t  _pad_play;

    /* Voice identity ------------------------------------------------ */
    uint8_t  midi_channel;     /* 0..15 — picks the channel-bank entry */
    uint8_t  voice_base_vol;   /* baked velocity × initial_attn (0..255) */
    int16_t  pan_base;         /* SF2 -500..+500 (zone pan) */

    /* Pitch --------------------------------------------------------- */
    uint32_t base_rate;        /* Q16.16 mixer playback rate at root key */

    /* Filter -------------------------------------------------------- */
    int16_t  initial_fc;       /* cents (AWE_FILTER_BYPASS to disable) */
    int16_t  initial_q;        /* centibels */

    /* Ramp 0 -- vol DAHDSR.  Two parallel representations during the
     * migration: the flat OFSF-v3 baked fields (vol_delay_ticks ..
     * vol_release_ticks) that both the SW path and the AWE fabric read,
     * plus the abstract segment[] list that's the forward-compat shape
     * from the design doc.  Phase 3 uses the flat fields exclusively;
     * once the stage-0 (IF_ACTIVE_CHECK) segment iterator lands the
     * struct compiles into segments automatically. */
    uint32_t vol_delay_ticks;
    uint32_t vol_attack_rate;
    uint32_t vol_hold_ticks;
    uint32_t vol_decay_rate;
    uint32_t vol_sustain_level;
    uint32_t vol_release_ticks;
    awe_segment_t ramp0_segs[OF_AWE_NUM_SEGMENTS];

    /* Ramp 1 -- mod env for MIDI / portamento for MOD --------------- */
    awe_segment_t ramp1_segs[OF_AWE_NUM_SEGMENTS];

    /* LFOs ---------------------------------------------------------- */
    awe_lfo_t lfo0;            /* mod LFO */
    awe_lfo_t lfo1;            /* vib LFO */

    /* Mod-matrix scales --------------------------------------------- */
    awe_mm_t mm;

    /* Sends --------------------------------------------------------- */
    uint8_t  reverb_send;      /* 0..255 */
    uint8_t  chorus_send;      /* 0..255 */
    uint8_t  _pad_sends[2];
} awe_voice_t;

/* ------------------------------------------------------------------ */
/* Service-table entry points                                         */
/* ------------------------------------------------------------------ */

#ifndef OF_PC

#include "of_services.h"

/* Stage a voice configuration into AWE's voice-state RAM.  Does not
 * start playback; call awe_voice_trigger() to fire NOTE_ON. */
static inline void of_awe_voice_load(int voice, const awe_voice_t *v) {
    OF_SVC->awe_voice_load(voice, v);
}

/* Strobe NOTE_ON for a previously-loaded voice: AWE composes initial
 * mixer values from the voice state + channel bank + globals and emits
 * RATE / VOL_L/R / FILTER / sample-address writes to the mixer. */
static inline void of_awe_voice_trigger(int voice) {
    OF_SVC->awe_voice_trigger(voice);
}

/* Note-off: schedules RAMP0 release segment.  In Phase 1 this emits a
 * mixer set-volume(0) + voice stop.  Later phases will run the release
 * envelope. */
static inline void of_awe_voice_release(int voice) {
    OF_SVC->awe_voice_release(voice);
}

/* Hard stop a voice -- equivalent to of_mixer_stop. */
static inline void of_awe_voice_stop(int voice) {
    OF_SVC->awe_voice_stop(voice);
}

/* Per-channel CC updates -- write the channel-bank entry that drives
 * subsequent voice-write composers.  In Phase 1, these update the
 * stored channel state but do NOT recompose currently-playing voices
 * (no tick path).  Phase 5+ will recompute on the next tick. */
static inline void of_awe_channel_set_volume(int ch, int vol_0_127) {
    OF_SVC->awe_channel_set_volume(ch, vol_0_127);
}
static inline void of_awe_channel_set_expression(int ch, int expr_0_127) {
    OF_SVC->awe_channel_set_expression(ch, expr_0_127);
}
static inline void of_awe_channel_set_pan(int ch, int pan_0_127) {
    OF_SVC->awe_channel_set_pan(ch, pan_0_127);
}
static inline void of_awe_channel_set_bend(int ch, int bend_signed_8192) {
    OF_SVC->awe_channel_set_bend(ch, bend_signed_8192);
}
static inline void of_awe_channel_set_mod(int ch, int mod_0_127) {
    OF_SVC->awe_channel_set_mod(ch, mod_0_127);
}
static inline void of_awe_channel_set_sustain(int ch, int on_off) {
    OF_SVC->awe_channel_set_sustain(ch, on_off);
}
static inline void of_awe_channel_set_brightness(int ch, int br_0_127) {
    OF_SVC->awe_channel_set_brightness(ch, br_0_127);
}
static inline void of_awe_channel_set_resonance(int ch, int q_0_127) {
    OF_SVC->awe_channel_set_resonance(ch, q_0_127);
}
static inline void of_awe_channel_set_reverb_send(int ch, int send_0_255) {
    OF_SVC->awe_channel_set_reverb_send(ch, send_0_255);
}
static inline void of_awe_channel_set_chorus_send(int ch, int send_0_255) {
    OF_SVC->awe_channel_set_chorus_send(ch, send_0_255);
}

/* Globals. */
static inline void of_awe_set_master_volume(int vol_0_255) {
    OF_SVC->awe_set_master_volume(vol_0_255);
}
static inline void of_awe_set_bend_range(int cents) {
    OF_SVC->awe_set_bend_range(cents);
}

/* Readback: bitmask of which voices currently have ACTIVE=1.  Used by
 * voice-stealing logic (kept on CPU per the design doc). */
static inline uint64_t of_awe_active_mask(void) {
    return OF_SVC->awe_active_mask();
}

/* Phase 2 — free-running 1 kHz tick counter.  Sample at two points
 * and take the delta to verify the sequencer is walking at the right
 * cadence (delta over 1 s should be ~1000).  Wraps every ~50 days. */
static inline uint32_t of_awe_tick_count(void) {
    return OF_SVC->awe_tick_count();
}

/* Phase 3 — global "HW owns the amplitude envelope + VOL_COMPOSE"
 * flag.  When 1, the AWE fabric advances ramp0 each tick and writes
 * VOL_LR on change.  SW paths (of_smp_voice) skip their vol-write
 * side so the two don't stomp.  Flip before note-on events; flipping
 * mid-note is not guaranteed smooth. */
static inline void of_awe_set_hw_envelope(int enabled) {
    OF_SVC->awe_set_hw_envelope(enabled);
}

/* Phase 6a — global reverb bus.  level sets how much of the delayed
 * tap lands in the output (0 = dry, 255 = full wet); feedback is how
 * much of the tap loops back into the delay line for tail length
 * (0 = single slap, ~180 = lush tail, 255 = self-oscillation). */
static inline void of_awe_set_reverb_level(int level) {
    OF_SVC->awe_set_reverb_level(level);
}
static inline void of_awe_set_reverb_feedback(int feedback) {
    OF_SVC->awe_set_reverb_feedback(feedback);
}

/* Phase 6b — global chorus bus.  level = wet/dry mix (0..255),
 * rate = LFO phase increment per sample (Q16, ~120 ≈ 0.1 Hz at
 * 48 kHz output), depth = LFO swing in samples (8..32 typical). */
static inline void of_awe_set_chorus_level(int level) {
    OF_SVC->awe_set_chorus_level(level);
}
static inline void of_awe_set_chorus_rate(int rate) {
    OF_SVC->awe_set_chorus_rate(rate);
}
static inline void of_awe_set_chorus_depth(int depth) {
    OF_SVC->awe_set_chorus_depth(depth);
}

/* Phase 5c — Ramp1 (mod env) trigger.  stage = 2 (ATTACK) restarts
 * level=0 with given rate; stage = 6 (RELEASE) fades from current;
 * stage = 7 (DONE) snaps to 0.  rate is Q16.16 incr per ms tick. */
#define AWE_ENV_ATTACK   2
#define AWE_ENV_RELEASE  6
#define AWE_ENV_DONE     7
static inline void of_awe_ramp1_trigger(int voice, int stage, uint32_t rate) {
    OF_SVC->awe_ramp1_trigger(voice, stage, rate);
}

#else /* OF_PC */

static inline void of_awe_voice_load(int v, const awe_voice_t *p) { (void)v; (void)p; }
static inline void of_awe_voice_trigger(int v) { (void)v; }
static inline void of_awe_voice_release(int v) { (void)v; }
static inline void of_awe_voice_stop(int v)    { (void)v; }
static inline void of_awe_channel_set_volume(int c, int x)     { (void)c; (void)x; }
static inline void of_awe_channel_set_expression(int c, int x) { (void)c; (void)x; }
static inline void of_awe_channel_set_pan(int c, int x)        { (void)c; (void)x; }
static inline void of_awe_channel_set_bend(int c, int x)       { (void)c; (void)x; }
static inline void of_awe_channel_set_mod(int c, int x)        { (void)c; (void)x; }
static inline void of_awe_channel_set_sustain(int c, int x)    { (void)c; (void)x; }
static inline void of_awe_channel_set_brightness(int c, int x) { (void)c; (void)x; }
static inline void of_awe_channel_set_resonance(int c, int x)  { (void)c; (void)x; }
static inline void of_awe_channel_set_reverb_send(int c, int x){ (void)c; (void)x; }
static inline void of_awe_channel_set_chorus_send(int c, int x){ (void)c; (void)x; }
static inline void of_awe_set_master_volume(int v) { (void)v; }
static inline void of_awe_set_bend_range(int c)    { (void)c; }
static inline uint64_t of_awe_active_mask(void)    { return 0; }
static inline uint32_t of_awe_tick_count(void)     { return 0; }
static inline void of_awe_set_hw_envelope(int e)   { (void)e; }
static inline void of_awe_set_reverb_level(int l)   { (void)l; }
static inline void of_awe_set_reverb_feedback(int f){ (void)f; }
static inline void of_awe_set_chorus_level(int l)   { (void)l; }
static inline void of_awe_set_chorus_rate(int r)    { (void)r; }
static inline void of_awe_set_chorus_depth(int d)   { (void)d; }
static inline void of_awe_ramp1_trigger(int v, int s, uint32_t r) { (void)v; (void)s; (void)r; }

#endif /* OF_PC */

#ifdef __cplusplus
}
#endif

#endif /* OF_AWE_H */
