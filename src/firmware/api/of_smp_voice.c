/*
 * of_smp_voice.c -- Sample voice engine: 48-voice polyphony with
 *                   DAHDSR envelopes, dual LFOs, and pitch bend.
 */

#include "include/of_smp_voice.h"
#include "include/of_smp_bank.h"
#include "include/of_smp_tables.h"
#include "include/of_mixer.h"
#include "include/of_cache.h"
#include "include/of_timer.h"
#include "include/of_services.h"
#include "include/of_awe.h"
#include <string.h>

/* ------------------------------------------------------------------ */
/* Static state                                                       */
/* ------------------------------------------------------------------ */

static smp_voice_t voices[SMP_MAX_VOICES];
static uint32_t tick_counter;

/* ------------------------------------------------------------------ */
/* AWE backend bookkeeping                                            */
/* ------------------------------------------------------------------ */
/* When awe_backend_enabled is non-zero, note_on / note_off / CC hooks
 * bypass the SW voice engine and drive the AWE coprocessor directly.
 * The fabric takes over per-tick work (envelope, LFO, mod-matrix,
 * compose); the CPU only has to parse MIDI + push note events. */
#define AWE_SLOTS 32

static int      awe_backend_enabled;
static uint8_t  awe_slot_used   [AWE_SLOTS];   /* 1 = slot holds a note-on */
static uint8_t  awe_slot_ch     [AWE_SLOTS];
static uint8_t  awe_slot_note   [AWE_SLOTS];
static uint8_t  awe_slot_sustain[AWE_SLOTS];   /* 1 = release deferred by CC64 */
static uint8_t  awe_slot_excl   [AWE_SLOTS];   /* SF2 exclusive_class — drum cut-off */
static uint32_t awe_slot_age    [AWE_SLOTS];
/* Phase 5c — remember the zone's mod-env release ticks for awe_note_off.
 * Stored as ticks (not rate) because the SDK doesn't know the current
 * env level at note-off; we approximate the rate as 0x10000 / ticks
 * (assuming the env was at full peak) which is correct for the typical
 * case of note-off after attack completes. */
static uint32_t awe_slot_mod_release_t[AWE_SLOTS];

/* ------------------------------------------------------------------ */
/* Tick-cost probe (Task #10)                                         */
/* ------------------------------------------------------------------ */
/* NOTE: VexRiscv here does not expose rdcycle to user mode, so we use
 * OF_SVC->timer_get_us() (direct service-table call — NOT the ecall
 * of_time_us(), which would nest-trap when smp_voice_tick runs from
 * the MIDI timer ISR). Stats are in microseconds. */

static uint32_t tick_us_max;
static uint32_t tick_us_last;
static uint32_t tick_spike_count;
static uint32_t tick_stat_count;
static uint8_t  tick_active_peak;
static uint8_t  tick_stage_sustain;
static uint8_t  tick_stage_release;
static uint8_t  tick_stage_decay;
static uint8_t  tick_sustain_held;
static uint8_t  tick_ch_active[16];

/* A/B/C instrumentation counters — see smp_tick_stats_t for descriptions.
 * These are incremented at actual HW-write sites (post-cache) and from
 * smp_voice_tick_record_pump(), then snapshotted by get_stats and zeroed
 * by reset_stats. */
static uint32_t stat_filter_writes;
static uint32_t stat_rate_writes;
static uint32_t stat_vol_writes;
static uint32_t stat_pump_count;
static uint32_t stat_pump_interval_max_us;
static uint32_t stat_pump_interval_min_us = 0xFFFFFFFFu;
static uint32_t stat_pump_burst_count;
static uint32_t stat_pump_budget_exceeded;
static uint16_t stat_cutoff_delta_max;

/* 2 ms budget = 500 Hz tick rate. */
#define SMP_TICK_SPIKE_US  2000u

/* ------------------------------------------------------------------ */
/* Mixer-write trace (OF_TRACE_MIXER_WRITES)                          */
/* ------------------------------------------------------------------ */

#ifdef OF_TRACE_MIXER_WRITES

/* ~640 KB ring at 20 B/entry.  Enough to hold ~0.7 s of a dense drum
 * MIDI with every rate/vol/filter write captured, at which point the
 * ring wraps.  Short-clip bit-identical diffs are the intended use
 * case; the stats counters remain for aggregate checks on long runs. */
#define SMP_TRACE_CAP 32768u

static smp_mixer_trace_entry_t smp_trace_buf[SMP_TRACE_CAP];
static uint32_t smp_trace_next;   /* next write index in ring */
static uint32_t smp_trace_total;  /* monotonic entries recorded (wraps never) */

static void smp_mixer_trace_log(uint8_t op, int voice,
                                uint32_t a0, uint32_t a1, uint32_t a2)
{
    uint32_t idx = smp_trace_next;
    smp_trace_next = (idx + 1u) % SMP_TRACE_CAP;

    smp_mixer_trace_entry_t *e = &smp_trace_buf[idx];
    e->seq   = smp_trace_total++;
    e->op    = op;
    e->voice = (uint8_t)voice;
    e->_pad  = 0;
    e->arg0  = a0;
    e->arg1  = a1;
    e->arg2  = a2;
}

#define SMP_TRACE(op, v, a, b, c) smp_mixer_trace_log((op), (v), (a), (b), (c))

void smp_mixer_trace_reset(void)
{
    smp_trace_next  = 0;
    smp_trace_total = 0;
}

uint32_t smp_mixer_trace_total(void) { return smp_trace_total; }

uint32_t smp_mixer_trace_dump(smp_mixer_trace_entry_t *out, uint32_t max)
{
    if (!out || max == 0) return 0;

    uint32_t total = smp_trace_total;
    uint32_t count = total > SMP_TRACE_CAP ? SMP_TRACE_CAP : total;
    if (count > max) count = max;

    uint32_t start = total > SMP_TRACE_CAP ? smp_trace_next : 0;
    for (uint32_t i = 0; i < count; i++) {
        out[i] = smp_trace_buf[(start + i) % SMP_TRACE_CAP];
    }
    return count;
}

#else  /* !OF_TRACE_MIXER_WRITES */

#define SMP_TRACE(op, v, a, b, c) ((void)0)

void     smp_mixer_trace_reset(void)                                   { }
uint32_t smp_mixer_trace_total(void)                                   { return 0; }
uint32_t smp_mixer_trace_dump(smp_mixer_trace_entry_t *out, uint32_t max)
{
    (void)out; (void)max;
    return 0;
}

#endif /* OF_TRACE_MIXER_WRITES */

void smp_voice_tick_get_stats(smp_tick_stats_t *out)
{
    if (!out) return;
    /* Note: field is named cycles_* for ABI stability but holds microseconds. */
    out->cycles_max    = tick_us_max;
    out->cycles_last   = tick_us_last;
    out->spike_count   = tick_spike_count;
    out->tick_count    = tick_stat_count;
    out->active_peak   = tick_active_peak;
    out->stage_sustain = tick_stage_sustain;
    out->stage_release = tick_stage_release;
    out->stage_decay   = tick_stage_decay;
    out->sustain_held  = tick_sustain_held;
    for (int i = 0; i < 16; i++)
        out->ch_active[i] = tick_ch_active[i];

    out->filter_writes         = stat_filter_writes;
    out->rate_writes           = stat_rate_writes;
    out->vol_writes            = stat_vol_writes;
    out->pump_count            = stat_pump_count;
    out->pump_interval_max_us  = stat_pump_interval_max_us;
    out->pump_interval_min_us  = stat_pump_interval_min_us;
    out->pump_burst_count      = stat_pump_burst_count;
    out->pump_budget_exceeded  = stat_pump_budget_exceeded;
    out->cutoff_delta_max      = stat_cutoff_delta_max;
}

void smp_voice_tick_reset_stats(void)
{
    tick_us_max      = 0;
    tick_spike_count = 0;
    tick_stat_count  = 0;
    tick_active_peak = 0;

    stat_filter_writes        = 0;
    stat_rate_writes          = 0;
    stat_vol_writes           = 0;
    stat_pump_count           = 0;
    stat_pump_interval_max_us = 0;
    stat_pump_interval_min_us = 0xFFFFFFFFu;
    stat_pump_burst_count     = 0;
    stat_pump_budget_exceeded = 0;
    stat_cutoff_delta_max     = 0;
}

void smp_voice_tick_record_pump(uint32_t elapsed_us, int ticks_fired,
                                int budget_exceeded)
{
    stat_pump_count++;
    if (elapsed_us > stat_pump_interval_max_us)
        stat_pump_interval_max_us = elapsed_us;
    if (elapsed_us < stat_pump_interval_min_us)
        stat_pump_interval_min_us = elapsed_us;
    if (ticks_fired > 1) stat_pump_burst_count++;
    if (budget_exceeded) stat_pump_budget_exceeded++;
}

/* Per-channel state (16 MIDI channels) */
static int ch_volume[16];       /* CC7  (0-127) */
static int ch_expression[16];   /* CC11 (0-127) */
static int ch_pan[16];          /* CC10 (0-127, 64=center) */
static int ch_bend[16];         /* -8192..+8191 */
static int ch_mod_depth[16];    /* CC1  (0-127) */
static int ch_sustain[16];      /* CC64 on/off */
static int ch_brightness[16];   /* CC74 (0-127) */
static int ch_resonance[16];    /* CC71 (0-127) */
static int ch_reverb_send[16];  /* CC91 (0-127), default 40 = GM tasteful default */
static int ch_chorus_send[16];  /* CC93 (0-127), default 0 */
static int master_vol = 255;

/* Cached mixer state to avoid redundant CDC writes */
static uint32_t prev_rate[SMP_MAX_VOICES];
static int      prev_vol_l[SMP_MAX_VOICES];
static int      prev_vol_r[SMP_MAX_VOICES];

/* Voices pending steal (waiting for hardware fade-out) */
#define STEAL_PENDING -2

/* Minimum envelope level before we consider it done */
#define ENV_FLOOR 0x100

/* Pitch bend range in cents (standard: +/-2 semitones) */
#define BEND_RANGE_CENTS 200

/* ------------------------------------------------------------------ */
/* Fixed-point helpers                                                */
/* ------------------------------------------------------------------ */

static int32_t triangle_wave(int32_t phase)
{
    /* phase is Q16.16 wrapping at 0x10000.
     * Output: -0x10000 .. +0x10000 (Q16.16 signed) */
    phase &= 0xFFFF;
    if (phase < 0x4000)
        return (phase << 2);                    /* 0..0x10000 */
    else if (phase < 0xC000)
        return 0x20000 - (phase << 2);          /* 0x10000..-0x10000 */
    else
        return (phase << 2) - 0x40000;          /* -0x10000..0 */
}

/* ------------------------------------------------------------------ */
/* Envelope helpers                                                   */
/* ------------------------------------------------------------------ */

/* All SF2-unit conversions are pre-baked into the OFSF v3 zone
 * (ticks, per-tick rates, and Q16.16 sustain levels) by sf2_to_ofsf.
 * env_init / env_advance just copy those baked fields into the per-voice
 * env_state_t; the arithmetic is identical to what this code used to do
 * at every transition but happens once offline instead of per voice. */

static void env_init(env_state_t *e, uint32_t delay_ticks,
                     uint32_t attack_rate)
{
    e->level = 0;
    e->target = 0;

    if (delay_ticks > 0) {
        e->stage = ENV_DELAY;
        e->timer = (int32_t)delay_ticks;
        e->rate = 0;
        return;
    }

    e->stage = ENV_ATTACK;
    e->rate = (int32_t)attack_rate;
    e->target = 0x10000;
    e->timer = 0;
}

static void env_advance(env_state_t *e, const ofsf_zone_t *z, int is_vol)
{
    switch (e->stage) {
    case ENV_OFF:
    case ENV_DONE:
        return;

    case ENV_DELAY:
        if (--e->timer <= 0) {
            uint32_t atk_rate = is_vol ? z->vol_attack_rate : z->mod_attack_rate;
            e->stage = ENV_ATTACK;
            e->rate = (int32_t)atk_rate;
            e->target = 0x10000;
        }
        return;

    case ENV_ATTACK:
        e->level += e->rate;
        if (e->level >= 0x10000) {
            e->level = 0x10000;
            uint32_t hold_ticks = is_vol ? z->vol_hold_ticks : z->mod_hold_ticks;
            if (hold_ticks > 0) {
                e->stage = ENV_HOLD;
                e->timer = (int32_t)hold_ticks;
                e->rate = 0;
            } else {
                goto start_decay;
            }
        }
        return;

    case ENV_HOLD:
        if (--e->timer <= 0) {
start_decay: ;
            uint32_t sus_level  = is_vol ? z->vol_sustain_level : z->mod_sustain_level;
            uint32_t decay_rate = is_vol ? z->vol_decay_rate    : z->mod_decay_rate;
            e->stage = ENV_DECAY;
            e->target = (int32_t)sus_level;
            e->rate = (int32_t)decay_rate;
        }
        return;

    case ENV_DECAY:
        e->level -= e->rate;
        if (e->level <= e->target) {
            e->level = e->target;
            e->stage = ENV_SUSTAIN;
            e->rate = 0;
        }
        return;

    case ENV_SUSTAIN:
        return;

    case ENV_RELEASE:
        e->level -= e->rate;
        if (e->level <= ENV_FLOOR) {
            e->level = 0;
            e->stage = ENV_DONE;
        }
        return;
    }
}

static void env_start_release(env_state_t *e, uint32_t release_ticks)
{
    if (e->stage == ENV_OFF || e->stage == ENV_DONE)
        return;

    if (release_ticks < 1) release_ticks = 1;

    e->stage = ENV_RELEASE;
    e->target = 0;
    e->rate = e->level / (int32_t)release_ticks;
    if (e->rate < 1) e->rate = 1;
}

/* ------------------------------------------------------------------ */
/* LFO helpers                                                        */
/* ------------------------------------------------------------------ */

static void lfo_init(lfo_state_t *l, uint32_t delay_ticks, uint32_t rate)
{
    /* Delay ticks and per-tick phase increment are pre-baked in the
     * OFSF v3 zone (smp_timecents_to_ticks + smp_lfo_freq_cents_to_rate
     * applied offline by sf2_to_ofsf).  No conversion needed here. */
    l->phase = 0;
    l->delay_ticks = (int32_t)delay_ticks;
    l->rate = (int32_t)rate;
    if (l->rate < 1) l->rate = 1;
}

static int32_t lfo_advance(lfo_state_t *l)
{
    if (l->delay_ticks > 0) {
        l->delay_ticks--;
        return 0;
    }
    l->phase += l->rate;
    l->phase &= 0xFFFF;
    return triangle_wave(l->phase);
}

/* ------------------------------------------------------------------ */
/* Voice allocation                                                   */
/* ------------------------------------------------------------------ */

static void voice_force_off(int idx);
static void voice_cleanup_stolen(void);

/* Reclaim a slot for immediate reuse: free the hardware mixer voice and
 * mark the slot inactive.  voice_alloc's steal passes call this so the
 * caller (smp_voice_note_on) can write fresh state without leaking the
 * previous hardware voice. */
static void voice_reclaim(int idx)
{
    smp_voice_t *v = &voices[idx];
    if (v->mixer_voice >= 0)
        of_mixer_stop(v->mixer_voice);
    v->mixer_voice = -1;
    v->active = 0;
}

static int voice_alloc(void)
{
    /* Pass 1: find a free slot */
    for (int i = 0; i < SMP_MAX_VOICES; i++) {
        if (!voices[i].active)
            return i;
    }

    /* Pass 2: steal ENV_DONE (oldest first).  Skip STEAL_PENDING slots —
     * voice_cleanup_stolen owns those and will free them next tick. */
    int best = -1;
    uint32_t best_age = UINT32_MAX;
    for (int i = 0; i < SMP_MAX_VOICES; i++) {
        if (voices[i].active != STEAL_PENDING &&
            voices[i].vol_env.stage == ENV_DONE && voices[i].age < best_age) {
            best = i;
            best_age = voices[i].age;
        }
    }
    if (best >= 0) {
        voice_reclaim(best);
        return best;
    }

    /* Pass 3: steal the quietest ENV_RELEASE voice (lowest envelope
     * level).  Stealing by audible-level minimizes the click from the
     * hard stop in voice_reclaim — a voice already near-silent fades
     * to zero with no perceptible discontinuity.  "Oldest by age" was
     * a poor proxy because per-zone release rates differ widely (a
     * drum with 50 ms release started 2 s ago is silent; a piano with
     * 3 s release started 2 s ago is still loud). */
    int32_t best_level = INT32_MAX;
    for (int i = 0; i < SMP_MAX_VOICES; i++) {
        if (voices[i].active != STEAL_PENDING &&
            voices[i].vol_env.stage == ENV_RELEASE &&
            voices[i].vol_env.level < best_level) {
            best = i;
            best_level = voices[i].vol_env.level;
        }
    }
    if (best >= 0) {
        voice_reclaim(best);
        return best;
    }

    /* Pass 4: steal the quietest voice of any stage (last resort).
     * Same rationale as pass 3 — steal whatever is least audible. */
    best_level = INT32_MAX;
    for (int i = 0; i < SMP_MAX_VOICES; i++) {
        if (voices[i].active != STEAL_PENDING &&
            voices[i].vol_env.level < best_level) {
            best = i;
            best_level = voices[i].vol_env.level;
        }
    }
    if (best >= 0)
        voice_reclaim(best);

    return best;
}

/* Schedule a voice for shutdown without reusing its slot.  Used by
 * kill_exclusive_class — the new note allocates a fresh slot and the
 * old one fades out via voice_cleanup_stolen on the next tick. */
static void voice_force_off(int idx)
{
    smp_voice_t *v = &voices[idx];
    if (v->mixer_voice >= 0) {
        of_mixer_set_vol_lr(v->mixer_voice, 0, 0);
        SMP_TRACE(SMP_TRACE_OP_VOL_LR, v->mixer_voice, 0, 0, 0);
        of_mixer_set_volume_ramp(v->mixer_voice, 4);
    }
    v->active = STEAL_PENDING;
}

static void voice_cleanup_stolen(void)
{
    for (int i = 0; i < SMP_MAX_VOICES; i++) {
        if (voices[i].active == STEAL_PENDING) {
            if (voices[i].mixer_voice >= 0)
                of_mixer_stop(voices[i].mixer_voice);
            voices[i].active = 0;
            voices[i].mixer_voice = -1;
        }
    }
}

/* ------------------------------------------------------------------ */
/* Exclusive class                                                    */
/* ------------------------------------------------------------------ */

static void kill_exclusive_class(int midi_ch, uint8_t excl_class)
{
    if (excl_class == 0) return;
    for (int i = 0; i < SMP_MAX_VOICES; i++) {
        smp_voice_t *v = &voices[i];
        if (v->active && v->active != STEAL_PENDING &&
            v->midi_ch == midi_ch && v->zone &&
            v->zone->exclusive_class == excl_class) {
            voice_force_off(i);
        }
    }
}

/* ------------------------------------------------------------------ */
/* Volume / pan / pitch computation                                   */
/* ------------------------------------------------------------------ */

static void compute_vol_lr(smp_voice_t *v, int *out_l, int *out_r)
{
    /* env_vol: Q16.16 -> 0..256 */
    int32_t env_vol = v->vol_env.level >> 8;
    if (env_vol > 255) env_vol = 255;
    if (env_vol < 0)   env_vol = 0;

    /* channel volume: (CC7 * CC11) / 127 -> 0..127 */
    int ch = v->midi_ch;
    int ch_vol_combined = (ch_volume[ch] * ch_expression[ch]) / 127;

    /* Design-doc compose: VOICE_BASE_VOL × RAMP0_LEVEL × CH_VOL × CH_EXPR × MASTER.
     * voice_base_vol (0..255) = (vel_scale × initial_attn_scale) >> 8, baked at
     * note-on so this function does one less multiply per tick AND matches the
     * AWE fabric's compose arithmetic verbatim (Phase 3 bit-identical). */
    int32_t vol = env_vol;
    vol = (vol * v->voice_base_vol) >> 8;
    vol = (vol * ch_vol_combined) >> 7;
    vol = (vol * master_vol) >> 8;
    if (vol > 255) vol = 255;
    if (vol < 0)   vol = 0;

    /* Pan: zone pan + channel pan.
     * Zone pan: -500..+500 (SF2 units, -500=full left, +500=full right)
     * Channel CC10: 0..127 (64=center)
     * Combined pan: -500..+500 range */
    int zone_pan = v->zone ? v->zone->pan : 0;
    int midi_pan = ((ch_pan[ch] - 64) * 500) / 63;
    int pan = zone_pan + midi_pan;
    if (pan < -500) pan = -500;
    if (pan > 500)  pan = 500;

    /* Convert pan to L/R scaling.
     * pan -500: L=vol, R=0
     * pan    0: L=vol, R=vol
     * pan +500: L=0,   R=vol */
    if (pan <= 0) {
        *out_l = vol;
        *out_r = (vol * (500 + pan)) / 500;
    } else {
        *out_l = (vol * (500 - pan)) / 500;
        *out_r = vol;
    }
}

/* Recompute and (if changed) write the filter state for a voice.
 *
 * Called at 1 kHz (mid-tick + end-tick) rather than 500 Hz because the HW
 * filter cutoff snaps instantly — each cents-level change produces a small
 * SVF state-variable transient.  Doubling the update rate halves the jump
 * size per write and reduces audible "breakup" on effect-heavy patches
 * (synth leads, pads, brass) where mod_lfo_to_filter or mod_env_to_filter
 * sweep the cutoff continuously.
 *
 * Skip the HW write when the integer-rounded HW cutoff value is unchanged:
 * cents-level jitter often collapses to the same Q0.16 HW number, and a
 * redundant write still perturbs the filter on the HW side. */
static void filter_update(smp_voice_t *v)
{
    const ofsf_zone_t *z = v->zone;
    if (!z) return;
    if (v->mixer_voice < 0) return;
    if (z->initial_fc >= 13500 &&
        z->mod_lfo_to_filter == 0 &&
        z->mod_env_to_filter == 0)
        return;  /* filter bypassed at note-on */

    int32_t fc = z->initial_fc;

    if (z->mod_lfo_to_filter != 0) {
        int32_t lfo_out = triangle_wave(v->mod_lfo.phase);
        fc += (lfo_out * z->mod_lfo_to_filter) >> 16;
    }
    if (z->mod_env_to_filter != 0) {
        fc += ((int64_t)v->mod_env.level * z->mod_env_to_filter) >> 16;
    }
    fc += ((int32_t)ch_brightness[v->midi_ch] - 64) * 75;

    if (fc < 1500)  fc = 1500;
    if (fc > 13500) fc = 13500;

    int16_t fc16 = (int16_t)fc;
    int16_t q16  = z->initial_q + (int16_t)(ch_resonance[v->midi_ch] * 8);
    if (q16 > 960) q16 = 960;

    if (fc16 == v->cur_filter_fc && q16 == v->cur_filter_q)
        return;
    v->cur_filter_fc = fc16;
    v->cur_filter_q  = q16;

    /* Convert cents to Q0.16 normalized frequency for hardware.
     * fc_hz = 8.176 * 2^(fc_cents/1200)
     * cutoff_hw = (fc_hz / 24000) * 65535  (Nyquist = 24 kHz)
     * 8.176 Hz * 65536 / 24000 ≈ 22.36 → use 22 as integer approx */
    uint32_t fc_mult = smp_cents_to_multiplier(fc16);
    uint32_t cutoff_hw = (uint32_t)(((uint64_t)fc_mult * 22) >> 16);
    if (cutoff_hw > 65535) cutoff_hw = 65535;

    /* SVF q is damping (higher = less resonance), SF2 Q is resonance
     * gain (higher = more resonance). Invert. */
    int q_hw = 255 - (q16 * 255 / 960);
    if (q_hw < 8) q_hw = 8;  /* prevent self-oscillation */

    if ((uint16_t)cutoff_hw == v->cur_cutoff_hw)
        return;  /* HW cutoff unchanged — avoid redundant write */

    /* Track the largest single-tick cutoff jump across all voices since
     * the last stats reset.  Big jumps correlate with audible SVF
     * state-variable transients. */
    uint16_t new_hw = (uint16_t)cutoff_hw;
    uint16_t delta  = (new_hw > v->cur_cutoff_hw)
                        ? (uint16_t)(new_hw - v->cur_cutoff_hw)
                        : (uint16_t)(v->cur_cutoff_hw - new_hw);
    if (delta > stat_cutoff_delta_max) stat_cutoff_delta_max = delta;
    v->cur_cutoff_hw = new_hw;

    of_mixer_set_filter(v->mixer_voice, (int)cutoff_hw, q_hw, 1);
    SMP_TRACE(SMP_TRACE_OP_FILTER, v->mixer_voice,
              (uint32_t)cutoff_hw, (uint32_t)q_hw, 1u);
    stat_filter_writes++;
}

static uint32_t compute_pitch(smp_voice_t *v)
{
    int ch = v->midi_ch;
    int32_t cents_offset = 0;

    /* Pitch bend */
    cents_offset += ((int32_t)ch_bend[ch] * BEND_RANGE_CENTS) / 8192;

    /* Vibrato LFO */
    if (v->zone && v->zone->vib_lfo_to_pitch != 0) {
        int32_t lfo_out = triangle_wave(v->vib_lfo.phase);
        cents_offset += (lfo_out * v->zone->vib_lfo_to_pitch) >> 16;
    }

    /* Mod LFO to pitch */
    if (v->zone && v->zone->mod_lfo_to_pitch != 0) {
        int32_t lfo_out = triangle_wave(v->mod_lfo.phase);
        int32_t depth = v->zone->mod_lfo_to_pitch;
        /* Scale by CC1 mod wheel */
        depth = (depth * ch_mod_depth[ch]) / 127;
        cents_offset += (lfo_out * depth) >> 16;
    }

    /* Mod envelope to pitch */
    if (v->zone && v->zone->mod_env_to_pitch != 0) {
        cents_offset += ((int64_t)v->mod_env.level * v->zone->mod_env_to_pitch) >> 16;
    }

    if (cents_offset == 0)
        return v->base_rate_fp16;
    if (cents_offset > 12000) cents_offset = 12000;
    if (cents_offset < -12000) cents_offset = -12000;

    uint32_t mult = smp_cents_to_multiplier(cents_offset);
    return (uint32_t)(((uint64_t)v->base_rate_fp16 * mult) >> 16);
}

/* ------------------------------------------------------------------ */
/* AWE backend — note_on / note_off path                              */
/* ------------------------------------------------------------------ */

/* Sync awe_slot_used against the fabric's active_mask.  Fabric drops
 * bits on envelope DONE, but our bookkeeping only notices lazily.
 * Calling this at the top of note_on / note_off keeps the slot state
 * accurate so the voice stealer doesn't unnecessarily reclaim a slot
 * that just retired silently. */
static void awe_sync_active(void)
{
    uint64_t mask = of_awe_active_mask();
    for (int i = 0; i < AWE_SLOTS; i++) {
        if (awe_slot_used[i] && !((mask >> i) & 1ull))
            awe_slot_used[i] = 0;
    }
}

/* SF2 exclusive_class enforcement.  When a new note in a non-zero
 * class arrives on a channel, kill any voice already playing in the
 * same class on the same channel.  This is the mechanism that makes
 * a closed hi-hat cut off an open hi-hat instead of overlapping. */
static void awe_kill_exclusive_class(int midi_ch, uint8_t excl_class)
{
    if (excl_class == 0) return;
    for (int i = 0; i < AWE_SLOTS; i++) {
        if (!awe_slot_used[i])               continue;
        if (awe_slot_ch[i]   != midi_ch)     continue;
        if (awe_slot_excl[i] != excl_class)  continue;
        of_awe_voice_stop(i);
        awe_slot_used[i] = 0;
    }
}

static int awe_voice_alloc(void)
{
    /* 1) Prefer a slot that the HAL has never touched. */
    for (int i = 0; i < AWE_SLOTS; i++)
        if (!awe_slot_used[i]) return i;

    /* 2) Reclaim any slot whose envelope has run to completion in the
     *    fabric (awe_active_mask is owned by the coprocessor and drops
     *    the bit once the voice retires). */
    uint64_t mask = of_awe_active_mask();
    for (int i = 0; i < AWE_SLOTS; i++)
        if (!((mask >> i) & 1ull)) {
            awe_slot_used[i] = 0;
            return i;
        }

    /* 3) Steal the oldest.  Matches the SW voice-stealer's preference
     *    for long-lived notes. */
    int      oldest     = 0;
    uint32_t oldest_age = awe_slot_age[0];
    for (int i = 1; i < AWE_SLOTS; i++) {
        if (awe_slot_age[i] < oldest_age) {
            oldest_age = awe_slot_age[i];
            oldest     = i;
        }
    }
    of_awe_voice_stop(oldest);
    awe_slot_used[oldest] = 0;
    return oldest;
}

static int awe_note_on(const ofsf_zone_t *zone, int midi_ch, int note,
                       int velocity, const void *sample_base)
{
    const ofsf_header_t *hdr = of_smp_bank_get();
    if (!hdr || !sample_base) return -1;

    awe_sync_active();
    awe_kill_exclusive_class(midi_ch, zone->exclusive_class);

    int slot = awe_voice_alloc();
    if (slot < 0) return -1;

    awe_voice_t v;
    memset(&v, 0, sizeof(v));
    v.base         = (const uint8_t *)sample_base + zone->sample_offset;
    v.length       = zone->sample_length;
    v.loop_start   = zone->loop_start;
    v.loop_end     = zone->loop_end;
    v.loop_mode    = zone->loop_mode;
    v.interp_mode  = AWE_INTERP_LINEAR;
    v.fmt16        = 1;
    v.midi_channel = (uint8_t)midi_ch;
    v.pan_base     = zone->pan;
    v.initial_fc   = zone->initial_fc;
    v.initial_q    = zone->initial_q;

    /* voice_base_vol = (vel_scale × initial_attn_scale) >> 8 — same
     * formula smp_voice_note_on uses so audio amplitude matches. */
    {
        int vel_scale = (velocity * 2) + 1;
        if (vel_scale > 255) vel_scale = 255;
        int attn = zone->initial_attn_scale;
        int bv   = (vel_scale * attn) >> 8;
        if (bv > 255) bv = 255;
        v.voice_base_vol = (uint8_t)bv;
    }

    /* base_rate = (sr/48000) × 2^(pitch_cents/1200) in Q16.16.  Mirrors
     * the SW compute in smp_voice_note_on for bit-identical pitch. */
    {
        uint32_t sr        = hdr->sample_rate;
        uint32_t base_fp16 = OF_MIXER_RATE_FP16(sr);
        int32_t  cents     = ((int32_t)note - (int32_t)zone->root_key) * 100
                           + (int32_t)zone->coarse_tune * 100
                           + (int32_t)zone->fine_tune;
        uint32_t mult = smp_cents_to_multiplier(cents);
        v.base_rate = (uint32_t)(((uint64_t)base_fp16 * mult) >> 16);
    }

    /* DAHDSR */
    v.vol_delay_ticks   = zone->vol_delay_ticks;
    v.vol_attack_rate   = zone->vol_attack_rate;
    v.vol_hold_ticks    = zone->vol_hold_ticks;
    v.vol_decay_rate    = zone->vol_decay_rate;
    v.vol_sustain_level = zone->vol_sustain_level;
    v.vol_release_ticks = zone->vol_release_ticks;

    /* Phase 4 LFOs + mod matrix */
    v.lfo0.rate        = zone->mod_lfo_rate;
    v.lfo0.delay_ticks = zone->mod_lfo_delay_ticks;
    v.lfo0.waveform    = AWE_WAVE_TRIANGLE;
    v.lfo1.rate        = zone->vib_lfo_rate;
    v.lfo1.delay_ticks = zone->vib_lfo_delay_ticks;
    v.lfo1.waveform    = AWE_WAVE_TRIANGLE;
    v.mm.lfo0_pitch    = zone->mod_lfo_to_pitch;
    v.mm.lfo0_filter   = zone->mod_lfo_to_filter;
    v.mm.lfo1_pitch    = zone->vib_lfo_to_pitch;
    /* mm.ramp1_pitch / ramp1_filter deferred to Phase 5c (RAMP1_STEP
     * not wired yet; Phase 4 fabric ignores these fields). */

    /* Sends — fabric SEND_COMPOSE reads chan_bank, not voice state, so
     * keep these synced via channel writes elsewhere.  Storing them on
     * the voice for ABI completeness. */
    {
        int rs = (ch_reverb_send[midi_ch] * 255) / 127;
        int cs = (ch_chorus_send[midi_ch] * 255) / 127;
        if (rs > 255) rs = 255;
        if (cs > 255) cs = 255;
        v.reverb_send = (uint8_t)rs;
        v.chorus_send = (uint8_t)cs;
    }

    of_awe_voice_load(slot, &v);
    of_awe_voice_trigger(slot);

    /* Phase 5c — fire RAMP1 attack from the zone's mod-env attack rate.
     * If the zone has no mod env (rate==0), skip the trigger so the
     * level stays at 0 and the mod matrix contributes nothing. */
    if (zone->mod_attack_rate > 0) {
        of_awe_ramp1_trigger(slot, AWE_ENV_ATTACK, zone->mod_attack_rate);
    }

    awe_slot_used   [slot] = 1;
    awe_slot_ch     [slot] = (uint8_t)midi_ch;
    awe_slot_note   [slot] = (uint8_t)note;
    awe_slot_sustain[slot] = 0;
    awe_slot_excl   [slot] = zone->exclusive_class;
    awe_slot_age    [slot] = tick_counter;
    awe_slot_mod_release_t[slot] = zone->mod_release_ticks;
    return slot;
}

static void awe_note_off(int midi_ch, int note)
{
    awe_sync_active();   /* clear bits the fabric retired */
    for (int i = 0; i < AWE_SLOTS; i++) {
        if (!awe_slot_used[i])            continue;
        if (awe_slot_ch[i]   != midi_ch)  continue;
        if (awe_slot_note[i] != note)     continue;

        if (ch_sustain[midi_ch]) {
            awe_slot_sustain[i] = 1;      /* hold until CC64 drops */
        } else {
            of_awe_voice_release(i);
            /* Phase 5c — start mod-env RELEASE in lockstep with vol-env.
             * Approximate rate as full-peak / ticks (see comment on
             * awe_slot_mod_release_t).  Skip if zone has no mod env. */
            if (awe_slot_mod_release_t[i] > 0) {
                uint32_t rate = 0x10000u / awe_slot_mod_release_t[i];
                if (rate < 1) rate = 1;
                of_awe_ramp1_trigger(i, AWE_ENV_RELEASE, rate);
            }
            awe_slot_used[i] = 0;
        }
    }
}

static void awe_release_sustain(int midi_ch)
{
    for (int i = 0; i < AWE_SLOTS; i++) {
        if (!awe_slot_used[i])           continue;
        if (awe_slot_ch[i] != midi_ch)   continue;
        if (!awe_slot_sustain[i])        continue;
        of_awe_voice_release(i);
        /* Phase 5c — sustain pedal lift fires the deferred mod release. */
        if (awe_slot_mod_release_t[i] > 0) {
            uint32_t rate = 0x10000u / awe_slot_mod_release_t[i];
            if (rate < 1) rate = 1;
            of_awe_ramp1_trigger(i, AWE_ENV_RELEASE, rate);
        }
        awe_slot_sustain[i] = 0;
        awe_slot_used[i]    = 0;
    }
}

static void awe_all_off(int midi_ch)
{
    for (int i = 0; i < AWE_SLOTS; i++) {
        if (!awe_slot_used[i])         continue;
        if (awe_slot_ch[i] != midi_ch) continue;
        of_awe_voice_stop(i);
        awe_slot_used[i] = 0;
    }
}

static void awe_all_off_global(void)
{
    for (int i = 0; i < AWE_SLOTS; i++) {
        if (!awe_slot_used[i]) continue;
        of_awe_voice_stop(i);
        awe_slot_used[i] = 0;
    }
}

void smp_voice_enable_awe_backend(int on)
{
    awe_backend_enabled = on ? 1 : 0;
    if (on) {
        /* HW envelope advance must be on for any of the fabric
         * per-tick work to run.  The services_table_init() left this
         * off so the SW path isn't double-driven. */
        of_awe_set_hw_envelope(1);
        memset(awe_slot_used,    0, sizeof(awe_slot_used));
        memset(awe_slot_sustain, 0, sizeof(awe_slot_sustain));
        /* Push current per-channel send state into the AWE chan_bank so
         * SEND_COMPOSE has non-zero sends from the first tick.  Without
         * this seed, reverb/chorus stay silent until a CC91/CC93 arrives
         * — a MIDI file that never sends them (or sends them only at
         * the start, before the backend was enabled) would play dry. */
        for (int i = 0; i < 16; i++) {
            int rs = (ch_reverb_send[i] * 255) / 127;
            int cs = (ch_chorus_send[i] * 255) / 127;
            of_awe_channel_set_reverb_send(i, rs);
            of_awe_channel_set_chorus_send(i, cs);
        }
    } else {
        of_awe_set_hw_envelope(0);
        awe_all_off_global();
    }
}

int smp_voice_awe_backend_enabled(void)
{
    return awe_backend_enabled;
}

/* ------------------------------------------------------------------ */
/* Public API                                                         */
/* ------------------------------------------------------------------ */

void smp_voice_init(void)
{
    for (int i = 0; i < SMP_MAX_VOICES; i++) {
        voices[i].active = 0;
        voices[i].mixer_voice = -1;
    }

    for (int i = 0; i < 16; i++) {
        ch_volume[i]     = 100;
        ch_expression[i] = 127;
        ch_pan[i]        = 64;
        ch_bend[i]       = 0;
        ch_mod_depth[i]  = 0;
        ch_sustain[i]    = 0;
        ch_brightness[i] = 64;
        ch_resonance[i]  = 0;
        ch_reverb_send[i] = 40;   /* GM tasteful default ~31 % wet */
        ch_chorus_send[i] = 0;
    }

    master_vol = 255;
    tick_counter = 0;
}

int smp_voice_note_on(const ofsf_zone_t *zone, int midi_ch, int note,
                      int velocity, const void *sample_base)
{
    if (!zone || midi_ch < 0 || midi_ch > 15 || velocity <= 0)
        return -1;

    if (awe_backend_enabled)
        return awe_note_on(zone, midi_ch, note, velocity, sample_base);

    kill_exclusive_class(midi_ch, zone->exclusive_class);

    int idx = voice_alloc();
    if (idx < 0)
        return -1;

    smp_voice_t *v = &voices[idx];

    v->active = 1;
    v->zone = zone;
    v->midi_ch = (uint8_t)midi_ch;
    v->note = (uint8_t)note;
    v->velocity = (uint8_t)velocity;
    v->sustain_held = 0;
    v->mixer_voice = -1;
    v->age = tick_counter;

    /* Pre-bake voice_base_vol = (vel_scale × initial_attn_scale) >> 8.
     * One u8 field now replaces the two multiplies the old compute_vol_lr
     * did per tick, and matches the awe_voice_t.voice_base_vol the AWE
     * fabric reads from voice-state RAM (Phase 3 onward). */
    {
        int vel_scale = (velocity * 2) + 1;
        if (vel_scale > 255) vel_scale = 255;
        int attn_scale = zone ? zone->initial_attn_scale : 255;
        int bv = (vel_scale * attn_scale) >> 8;
        if (bv > 255) bv = 255;
        v->voice_base_vol = (uint8_t)bv;
    }

    /* Compute base playback rate:
     * rate = (sample_rate / 48000) * 2^((note - root + coarse)*100 + fine) / 1200)
     * We split into the sample_rate ratio and the pitch offset. */
    const ofsf_header_t *hdr = of_smp_bank_get();
    uint32_t sr = hdr ? hdr->sample_rate : 44100;
    uint32_t base_fp16 = OF_MIXER_RATE_FP16(sr);

    int32_t total_cents = ((int32_t)note - (int32_t)zone->root_key) * 100
                        + (int32_t)zone->coarse_tune * 100
                        + (int32_t)zone->fine_tune;
    uint32_t pitch_mult = smp_cents_to_multiplier(total_cents);
    v->base_rate_fp16 = (uint32_t)(((uint64_t)base_fp16 * pitch_mult) >> 16);

    /* Compute sample address.
     * sample_base points to start of sample blob in CRAM1.
     * sample_offset is bytes from blob start.
     * CRAM1 uses word addressing but samples are 16-bit, so
     * the word address = base + offset/2. */
    const uint8_t *sample_ptr = (const uint8_t *)sample_base
                              + zone->sample_offset;

    int mhv = of_mixer_play(sample_ptr, zone->sample_length, sr, 0, 200);
    if (mhv < 0) { v->active = 0; return -1; }
    v->mixer_voice = mhv;
    of_mixer_set_rate_raw(mhv, v->base_rate_fp16);
    SMP_TRACE(SMP_TRACE_OP_RATE, mhv, v->base_rate_fp16, 0, 0);
    stat_rate_writes++;

    /* Loop setup */
    if (zone->loop_mode == OFSF_LOOP_FORWARD || zone->loop_mode == OFSF_LOOP_BIDI) {
        of_mixer_set_loop(mhv, zone->loop_start, zone->loop_end);
        if (zone->loop_mode == OFSF_LOOP_BIDI)
            of_mixer_set_bidi(mhv, 1);
        /* Looping voice: let the envelope decide when it ends. */
        v->sample_ticks_remaining = 0;
    } else {
        /* One-shot: compute how many software ticks (500 Hz) until the
         * sample walks off its natural end.
         *
         *   samples consumed per 48 kHz output tick = base_rate_fp16 / 65536
         *   seconds to finish = sample_length * 65536 / (base_rate_fp16 * 48000)
         *   SW ticks (500 Hz) = sample_length * 65536 / (base_rate_fp16 * 96)
         *
         * Adds 20 % headroom so modest pitch bends (drums: typically none)
         * don't truncate audible content. */
        if (v->base_rate_fp16 > 0 && zone->sample_length > 0) {
            uint64_t num = (uint64_t)zone->sample_length * 65536u * 6u;
            uint64_t den = (uint64_t)v->base_rate_fp16 * 96u * 5u;
            uint64_t ticks = num / (den ? den : 1);
            if (ticks < 1) ticks = 1;
            if (ticks > 0x7FFFFFFFu) ticks = 0x7FFFFFFFu;
            v->sample_ticks_remaining = (int32_t)ticks;
        } else {
            v->sample_ticks_remaining = 0;
        }
    }

    of_mixer_set_group(mhv, OF_MIXER_GROUP_MUSIC);

    /* Initialize envelopes and LFOs from pre-baked OFSF v3 fields. */
    env_init(&v->vol_env, zone->vol_delay_ticks, zone->vol_attack_rate);
    env_init(&v->mod_env, zone->mod_delay_ticks, zone->mod_attack_rate);

    lfo_init(&v->mod_lfo, zone->mod_lfo_delay_ticks, zone->mod_lfo_rate);
    lfo_init(&v->vib_lfo, zone->vib_lfo_delay_ticks, zone->vib_lfo_rate);

    /* Initial filter state.
     *
     * CRITICAL: of_mixer_play does NOT reset the per-voice filter
     * registers (FILTER_FC / FILTER_Q), so a reused voice slot inherits
     * whatever cutoff/Q/enable the previous note left behind.  Without
     * an explicit write here, a new zone with no filter requirement
     * would play through a stale low-pass filter from an unrelated
     * sample, producing audibly wrong timbres on stolen voices.
     *
     * Program the filter explicitly:
     *   - If the zone needs filtering (cutoff below wide-open, or any
     *     modulation source), enable with the zone's initial cutoff/Q
     *     converted to the hardware Q0.16 / 0..255 representation —
     *     same math as the per-tick filter update below.
     *   - Otherwise disable the filter so the voice runs unfiltered. */
    v->cur_filter_fc = zone->initial_fc;
    v->cur_filter_q  = zone->initial_q;
    {
        int need_filter = (zone->initial_fc < 13500) ||
                          (zone->mod_lfo_to_filter != 0) ||
                          (zone->mod_env_to_filter != 0);
        if (need_filter) {
            int32_t fc = zone->initial_fc;
            if (fc < 1500)  fc = 1500;
            if (fc > 13500) fc = 13500;
            int16_t q16 = zone->initial_q + (int16_t)(ch_resonance[midi_ch] * 8);
            if (q16 > 960) q16 = 960;
            uint32_t fc_mult = smp_cents_to_multiplier(fc);
            uint32_t cutoff_hw = (uint32_t)(((uint64_t)fc_mult * 22) >> 16);
            if (cutoff_hw > 65535) cutoff_hw = 65535;
            int q_hw = 255 - (q16 * 255 / 960);
            if (q_hw < 8) q_hw = 8;
            of_mixer_set_filter(mhv, (int)cutoff_hw, q_hw, 1);
            SMP_TRACE(SMP_TRACE_OP_FILTER, mhv,
                      (uint32_t)cutoff_hw, (uint32_t)q_hw, 1u);
            stat_filter_writes++;
            /* Cache the fc we actually wrote so tick() only rewrites on
             * a real change. */
            v->cur_filter_fc = (int16_t)fc;
            v->cur_filter_q  = q16;
            v->cur_cutoff_hw = (uint16_t)cutoff_hw;
        } else {
            /* Bypass the filter — enable=0 ensures a prior voice's
             * cutoff doesn't bleed into this one. */
            of_mixer_set_filter(mhv, 65535, 8, 0);
            SMP_TRACE(SMP_TRACE_OP_FILTER, mhv, 65535u, 8u, 0u);
            stat_filter_writes++;
            v->cur_cutoff_hw = 65535;
        }
    }

    /* Advance the envelope one tick so the level is non-zero before
     * the ISR runs — otherwise the ISR writes volume 0 immediately. */
    env_advance(&v->vol_env, zone, 1);

    int vl, vr;
    compute_vol_lr(v, &vl, &vr);
    of_mixer_set_vol_lr(mhv, vl, vr);
    SMP_TRACE(SMP_TRACE_OP_VOL_LR, mhv, (uint32_t)vl, (uint32_t)vr, 0);
    stat_vol_writes++;
    prev_vol_l[idx] = vl;
    prev_vol_r[idx] = vr;
    prev_rate[idx]  = v->base_rate_fp16;

    return idx;
}

void smp_voice_note_off(int midi_ch, int note)
{
    if (awe_backend_enabled) {
        awe_note_off(midi_ch, note);
        return;
    }
    for (int i = 0; i < SMP_MAX_VOICES; i++) {
        smp_voice_t *v = &voices[i];
        if (!v->active || v->active == STEAL_PENDING)
            continue;
        if (v->midi_ch != midi_ch || v->note != note)
            continue;

        if (ch_sustain[midi_ch]) {
            v->sustain_held = 1;
        } else {
            env_start_release(&v->vol_env, v->zone->vol_release_ticks);
            env_start_release(&v->mod_env, v->zone->mod_release_ticks);
        }
    }
}

void smp_voice_tick(void)
{
    /* AWE backend takes over per-tick work (envelope / LFO / mod-matrix
     * / VOL_COMPOSE / PITCH_COMPOSE) in fabric.  Only bump the counter
     * so the voice-stealer's age heuristic still advances; the MIDI
     * pump remains the sole CPU audio workload. */
    if (awe_backend_enabled) {
        tick_counter++;
        return;
    }

    uint32_t _probe_t0 = OF_SVC->timer_get_us();
    uint8_t  _probe_active = 0;
    uint8_t  _probe_sustain = 0;
    uint8_t  _probe_release = 0;
    uint8_t  _probe_decay = 0;
    uint8_t  _probe_held = 0;
    uint8_t  _probe_ch[16] = {0};

    tick_counter++;
    voice_cleanup_stolen();

    for (int i = 0; i < SMP_MAX_VOICES; i++) {
        smp_voice_t *v = &voices[i];
        if (!v->active || v->active == STEAL_PENDING)
            continue;
        _probe_active++;
        if (v->vol_env.stage == ENV_SUSTAIN) _probe_sustain++;
        else if (v->vol_env.stage == ENV_RELEASE) _probe_release++;
        else if (v->vol_env.stage == ENV_DECAY) _probe_decay++;
        if (v->sustain_held) _probe_held++;
        if (v->midi_ch < 16) _probe_ch[v->midi_ch]++;

        const ofsf_zone_t *z = v->zone;

        /* Natural sample-end check for one-shots.  When the sample has
         * played to its end the mixer is already emitting silence, so we
         * can force-DONE without any audible click and reclaim the slot
         * immediately — otherwise the envelope's long SUSTAIN parks the
         * voice (especially SF2 drum zones with very long vol_sustain)
         * and fills all 28 soft voices during dense drum tracks. */
        if (v->sample_ticks_remaining > 0) {
            if (--v->sample_ticks_remaining == 0) {
                v->vol_env.stage = ENV_DONE;
                v->vol_env.level = 0;
            }
        }

        /* Envelopes and LFOs advance twice per outer tick (effective
         * 1 kHz), but we must also sample pitch at 1 kHz — writing RATE
         * only once per outer tick produces an audible 2 ms staircase
         * on fast pitch modulation (short mod_env_to_pitch sweeps, fast
         * vibratos, snappy pitch bends).  HW RATE snaps (no ramp), so
         * we interleave a mid-tick compute_pitch between the two halves
         * of the advance sequence.  Volume is handled only at the end
         * because the mixer already ramps between VOL writes. */
        env_advance(&v->vol_env, z, 1);
        env_advance(&v->mod_env, z, 0);
        lfo_advance(&v->mod_lfo);
        lfo_advance(&v->vib_lfo);

        /* Mid-tick pitch update (1 kHz sampling) */
        if (v->mixer_voice >= 0 && v->vol_env.stage != ENV_DONE) {
            uint32_t rate_mid = compute_pitch(v);
            if (rate_mid != prev_rate[i]) {
                of_mixer_set_rate_raw(v->mixer_voice, rate_mid);
                SMP_TRACE(SMP_TRACE_OP_RATE, v->mixer_voice, rate_mid, 0, 0);
                prev_rate[i] = rate_mid;
                stat_rate_writes++;
            }
            /* Mid-tick filter update — run at 1 kHz (matches pitch) so
             * cents-level cutoff sweeps from mod_lfo_to_filter /
             * mod_env_to_filter change in smaller steps and produce
             * smaller SVF state-variable transients. */
            filter_update(v);
        }

        env_advance(&v->vol_env, z, 1);
        env_advance(&v->mod_env, z, 0);
        lfo_advance(&v->mod_lfo);
        lfo_advance(&v->vib_lfo);

        if (v->vol_env.stage == ENV_DONE) {
            if (v->mixer_voice >= 0)
                of_mixer_stop(v->mixer_voice);
            v->active = 0;
            v->mixer_voice = -1;
            continue;
        }

        int vl, vr;
        compute_vol_lr(v, &vl, &vr);
        uint32_t rate = compute_pitch(v);
        if (vl != prev_vol_l[i] || vr != prev_vol_r[i] ||
            rate != prev_rate[i]) {
            of_mixer_set_voice_raw(v->mixer_voice, rate, vl, vr);
            SMP_TRACE(SMP_TRACE_OP_VOICE_RAW, v->mixer_voice,
                      rate, (uint32_t)vl, (uint32_t)vr);
            /* set_voice_raw coalesces rate + vol; count each independently
             * changed field so the stats reflect the underlying load. */
            if (rate != prev_rate[i]) stat_rate_writes++;
            if (vl != prev_vol_l[i] || vr != prev_vol_r[i]) stat_vol_writes++;
            prev_vol_l[i] = vl;
            prev_vol_r[i] = vr;
            prev_rate[i] = rate;
        }

        /* End-tick filter update (second half of the 1 kHz sampling). */
        filter_update(v);
    }

    uint32_t _probe_dt = OF_SVC->timer_get_us() - _probe_t0;
    tick_us_last = _probe_dt;
    if (_probe_dt > tick_us_max) tick_us_max = _probe_dt;
    if (_probe_dt > SMP_TICK_SPIKE_US) tick_spike_count++;
    if (_probe_active > tick_active_peak) tick_active_peak = _probe_active;
    tick_stage_sustain = _probe_sustain;
    tick_stage_release = _probe_release;
    tick_stage_decay   = _probe_decay;
    tick_sustain_held  = _probe_held;
    for (int i = 0; i < 16; i++)
        tick_ch_active[i] = _probe_ch[i];
    tick_stat_count++;
}

void smp_voice_update_volume(int midi_ch, int volume, int expression)
{
    if (midi_ch < 0 || midi_ch > 15) return;
    ch_volume[midi_ch]     = volume;
    ch_expression[midi_ch] = expression;
    if (awe_backend_enabled) {
        of_awe_channel_set_volume    (midi_ch, volume);
        of_awe_channel_set_expression(midi_ch, expression);
    }
}

void smp_voice_update_pan(int midi_ch, int pan)
{
    if (midi_ch < 0 || midi_ch > 15) return;
    ch_pan[midi_ch] = pan;
    if (awe_backend_enabled)
        of_awe_channel_set_pan(midi_ch, pan);
}

void smp_voice_update_bend(int midi_ch, int bend)
{
    if (midi_ch < 0 || midi_ch > 15) return;
    ch_bend[midi_ch] = bend;
    if (awe_backend_enabled)
        of_awe_channel_set_bend(midi_ch, bend);
}

void smp_voice_update_mod(int midi_ch, int mod_depth)
{
    if (midi_ch < 0 || midi_ch > 15) return;
    ch_mod_depth[midi_ch] = mod_depth;
    if (awe_backend_enabled)
        of_awe_channel_set_mod(midi_ch, mod_depth);
}

void smp_voice_update_sustain(int midi_ch, int sustain_on)
{
    if (midi_ch < 0 || midi_ch > 15) return;
    ch_sustain[midi_ch] = sustain_on;

    if (awe_backend_enabled) {
        of_awe_channel_set_sustain(midi_ch, sustain_on);
        if (!sustain_on)
            awe_release_sustain(midi_ch);
        return;
    }

    if (!sustain_on) {
        for (int i = 0; i < SMP_MAX_VOICES; i++) {
            smp_voice_t *v = &voices[i];
            if (v->active && v->active != STEAL_PENDING &&
                v->midi_ch == midi_ch && v->sustain_held) {
                v->sustain_held = 0;
                env_start_release(&v->vol_env, v->zone->vol_release_ticks);
                env_start_release(&v->mod_env, v->zone->mod_release_ticks);
            }
        }
    }
}

void smp_voice_update_filter(int midi_ch, int brightness, int resonance)
{
    if (midi_ch < 0 || midi_ch > 15) return;
    ch_brightness[midi_ch] = brightness;
    ch_resonance[midi_ch]  = resonance;
    if (awe_backend_enabled) {
        of_awe_channel_set_brightness(midi_ch, brightness);
        of_awe_channel_set_resonance (midi_ch, resonance);
    }
}

void smp_voice_update_reverb_send(int midi_ch, int send_0_127)
{
    if (midi_ch < 0 || midi_ch > 15) return;
    if (send_0_127 < 0)   send_0_127 = 0;
    if (send_0_127 > 127) send_0_127 = 127;
    ch_reverb_send[midi_ch] = send_0_127;
    if (awe_backend_enabled) {
        int s = (send_0_127 * 255) / 127;
        of_awe_channel_set_reverb_send(midi_ch, s);
    }
}

void smp_voice_update_chorus_send(int midi_ch, int send_0_127)
{
    if (midi_ch < 0 || midi_ch > 15) return;
    if (send_0_127 < 0)   send_0_127 = 0;
    if (send_0_127 > 127) send_0_127 = 127;
    ch_chorus_send[midi_ch] = send_0_127;
    if (awe_backend_enabled) {
        int s = (send_0_127 * 255) / 127;
        of_awe_channel_set_chorus_send(midi_ch, s);
    }
}

void smp_voice_all_off(int midi_ch)
{
    if (awe_backend_enabled) {
        awe_all_off(midi_ch);
        return;
    }
    for (int i = 0; i < SMP_MAX_VOICES; i++) {
        smp_voice_t *v = &voices[i];
        if (v->active && v->active != STEAL_PENDING && v->midi_ch == midi_ch) {
            if (v->mixer_voice >= 0)
                of_mixer_stop(v->mixer_voice);
            v->active = 0;
            v->mixer_voice = -1;
        }
    }
}

void smp_voice_all_off_global(void)
{
    if (awe_backend_enabled) {
        awe_all_off_global();
        return;
    }
    for (int i = 0; i < SMP_MAX_VOICES; i++) {
        smp_voice_t *v = &voices[i];
        if (v->active) {
            if (v->mixer_voice >= 0)
                of_mixer_stop(v->mixer_voice);
            v->active = 0;
            v->mixer_voice = -1;
        }
    }
}

void smp_voice_set_master_volume(int vol)
{
    if (vol < 0)   vol = 0;
    if (vol > 255) vol = 255;
    master_vol = vol;
}
