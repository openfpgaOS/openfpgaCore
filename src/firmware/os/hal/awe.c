/*
 * AWE coprocessor HAL — Phase 1
 *
 * Routes the SDK's awe_* service-table calls into AWE's MMIO register
 * file (regs.h: AWE_*).  Phase 1 the CPU pre-composes initial mixer
 * values (rate, vol_l/r, cutoff_q016) using the same arithmetic the
 * SW voice engine (of_smp_voice.c) uses today, packs them into the
 * voice-state RAM, and strobes NOTE_ON.  The audio_awe FSM then emits
 * 9 mixer writes that program the underlying mixer voice slot.
 *
 * Per-tick processing (envelope advance, LFO, mod matrix) lands in
 * Phases 2-5 — at which point the CPU stops doing the compose and
 * AWE's fabric composers take over.
 *
 * Channel CC writes update the AWE channel bank but do NOT recompose
 * currently-playing voices in Phase 1 (no tick path).
 *
 * The HAL must not call any service-table function pointer through
 * ECALL — it runs in kernel context and ECALL would nest-trap.  Use
 * the local of_smp_tables helpers (linked into the kernel) for math.
 */

#include "../../api/of_awe.h"
#include "../../api/of_smp_tables.h"
#include "regs.h"

#include <stdint.h>
#include <stddef.h>

#define AWE_NUM_CHANNELS 16
#define AWE_MAX_VOICES   32

/* Voice state word offsets — must match audio_awe.v's NOTE_ON FSM.
 * CTRL is W11 (last) so the voice doesn't activate until the whole
 * word range W0..W10 is programmed. */
#define VW_BASE        0
#define VW_LEN         1
#define VW_RATE        2
#define VW_POS_INT     3
#define VW_VOL_LR      4
#define VW_VOL_TARGET  5
#define VW_VOL_RATE    6
#define VW_LOOP_END    7
#define VW_LOOP_START  8
#define VW_FILTER_FC   9
#define VW_FILTER_Q   10
#define VW_CTRL       11
/* Phase 3 RAMP0 state */
#define VW_RAMP0_LEVEL    12
#define VW_RAMP0_RATE     13
#define VW_RAMP0_TARGET   14
#define VW_RAMP0_TIMER    15
#define VW_RAMP0_STAGE    16
#define VW_RAMP0_ATTACK_R 17
#define VW_RAMP0_HOLD_T   18
#define VW_RAMP0_DECAY_R  19
#define VW_RAMP0_SUSTAIN  20
#define VW_RAMP0_RELEASE_T 21
/* Phase 4 LFO + mod-matrix slots */
#define VW_LFO0_STATE     22
#define VW_LFO0_CONFIG    23
#define VW_LFO0_OUTPUT    24
#define VW_LFO1_STATE     25
#define VW_LFO1_CONFIG    26
#define VW_LFO1_OUTPUT    27
#define VW_PITCH_OFS      28
#define VW_FILTER_OFS     29
#define VW_INITIAL_FC     30   /* raw SF2 fc cents — FILTER_COMPOSE (Phase 5b) */

/* Mixer CTRL word bits (must match audio_mixer.v's voice table CTRL). */
#define CTRL_ACTIVE   (1u << 0)
#define CTRL_LOOP     (1u << 1)
#define CTRL_FMT16    (1u << 2)
#define CTRL_BIDI     (1u << 3)

/* Per-channel state shadow.  Writes to of_awe_channel_set_* hit AWE's
 * channel bank AND this shadow so the HAL can recompute voice initial
 * values without reading back from MMIO. */
typedef struct {
    uint8_t  vol;          /* CC7  0..127 */
    uint8_t  expr;         /* CC11 0..127 */
    uint8_t  pan;          /* CC10 0..127 (64=center) */
    int16_t  bend;         /* signed -8192..+8191 */
    uint8_t  mod;          /* CC1  0..127 */
    uint8_t  sustain;      /* CC64 0/1 */
    uint8_t  brightness;   /* CC74 0..127 */
    uint8_t  resonance;    /* CC71 0..127 */
    uint8_t  reverb_send;  /* CC91 0..255 */
    uint8_t  chorus_send;  /* CC93 0..255 */
} chan_state_t;

static chan_state_t chans[AWE_NUM_CHANNELS];
static uint8_t      master_vol = 255;
static int16_t      bend_range_cents = 200;

/* ------------------------------------------------------------------ */
/* Voice-state RAM helpers                                            */
/* ------------------------------------------------------------------ */

static inline void vstate_set_cursor(int voice, int word)
{
    AWE_VOICE_LOAD_ADDR = ((uint32_t)(voice & 0x3F) << 5) | (word & 0x1F);
}

static inline void vstate_write(uint32_t v) { AWE_VOICE_LOAD_DATA = v; }

static inline void chan_set_cursor(int ch, int word)
{
    AWE_CHAN_LOAD_ADDR = ((uint32_t)(ch & 0xF) << 2) | (word & 0x3);
}

static inline void chan_write(uint32_t v) { AWE_CHAN_LOAD_DATA = v; }

/* ------------------------------------------------------------------ */
/* Compose helpers — match of_smp_voice.c arithmetic                  */
/* ------------------------------------------------------------------ */

/* Pan-split: SF2 pan -500..+500 + MIDI pan 0..127 (64=center) → L/R */
static void compose_vol_lr(uint8_t voice_base_vol, int16_t pan_base,
                           const chan_state_t *ch,
                           uint8_t *out_l, uint8_t *out_r)
{
    /* (vol × ch_vol × ch_expr × master) >> shifts, all 0..255 result */
    int32_t vol = voice_base_vol;
    int32_t cvol = ((int32_t)ch->vol * ch->expr) / 127;
    vol = (vol * cvol) >> 7;
    vol = (vol * master_vol) >> 8;
    if (vol > 255) vol = 255;
    if (vol < 0)   vol = 0;

    int midi_pan = ((int)ch->pan - 64) * 500 / 63;
    int pan = (int)pan_base + midi_pan;
    if (pan < -500) pan = -500;
    if (pan >  500) pan =  500;

    int l, r;
    if (pan <= 0) {
        l = vol;
        r = (vol * (500 + pan)) / 500;
    } else {
        l = (vol * (500 - pan)) / 500;
        r = vol;
    }
    *out_l = (uint8_t)l;
    *out_r = (uint8_t)r;
}

/* Filter cutoff cents → Q0.16 mixer cutoff (matches filter_update). */
static uint16_t compose_filter_cutoff(int16_t fc_cents)
{
    if (fc_cents < 1500)  fc_cents = 1500;
    if (fc_cents > 13500) fc_cents = 13500;
    uint32_t fc_mult = smp_cents_to_multiplier(fc_cents);
    /* fc_hz = 8.176 * 2^(fc/1200); cutoff_hw = (fc_hz / 24000) * 65535
     * 8.176 * 65536 / 24000 ≈ 22.36 → integer 22 */
    uint32_t cutoff = (uint32_t)(((uint64_t)fc_mult * 22) >> 16);
    if (cutoff > 65535) cutoff = 65535;
    return (uint16_t)cutoff;
}

/* Filter Q (centibels) + channel resonance → 0..255 damping. */
static uint8_t compose_filter_q(int16_t initial_q_cb, uint8_t ch_resonance)
{
    int16_t q16 = initial_q_cb + (int16_t)(ch_resonance * 8);
    if (q16 > 960) q16 = 960;
    int q_hw = 255 - (q16 * 255 / 960);
    if (q_hw < 8) q_hw = 8;
    return (uint8_t)q_hw;
}

/* ------------------------------------------------------------------ */
/* Service-table entry points                                         */
/* ------------------------------------------------------------------ */

void of_awe_voice_load_impl(int voice, const struct awe_voice_t *v)
{
    if (voice < 0 || voice >= AWE_MAX_VOICES || !v) return;

    const chan_state_t *ch = &chans[v->midi_channel & 0x0F];

    /* Compose Phase-1 mixer-init values from the per-zone struct +
     * channel state.  In Phase 5+ these go away — the fabric does it. */
    uint32_t mixer_rate = v->base_rate;     /* no bend/mod yet */

    uint8_t vol_l, vol_r;
    compose_vol_lr(v->voice_base_vol, v->pan_base, ch, &vol_l, &vol_r);
    uint32_t vol_lr = ((uint32_t)vol_r << 8) | vol_l;

    int filter_enable = (v->initial_fc < AWE_FILTER_BYPASS);
    uint16_t cutoff = filter_enable
        ? compose_filter_cutoff(v->initial_fc) : 65535;
    uint8_t  q_hw   = filter_enable
        ? compose_filter_q(v->initial_q, ch->resonance) : 8;

    /* CTRL: active + loop (if looping) + fmt16 + bidi */
    uint32_t ctrl = CTRL_ACTIVE;
    if (v->loop_mode == AWE_LOOP_FORWARD || v->loop_mode == AWE_LOOP_BIDI)
        ctrl |= CTRL_LOOP;
    if (v->loop_mode == AWE_LOOP_BIDI)
        ctrl |= CTRL_BIDI;
    if (v->fmt16)
        ctrl |= CTRL_FMT16;

    /* Convert the SDK's CPU pointer to the mixer's CRAM1 word address
     * (32-bit-word units, matching audio_mixer.v's voice base_addr).
     * Same arithmetic as hal/mixer.c:cram1_word_addr. */
    uint32_t base_va = (uint32_t)(uintptr_t)v->base;
    uint32_t base_offset;
    if (base_va >= CRAM1_UNCACHED && base_va < CRAM1_UNCACHED + CRAM_SIZE)
        base_offset = base_va - CRAM1_UNCACHED;
    else
        base_offset = base_va - CRAM1_BASE;
    uint32_t base_word = base_offset >> 2;

    /* Sequential write through the windowed interface.  Cursor auto-
     * increments after each AWE_VOICE_LOAD_DATA write so we set ADDR
     * once at W0 and write 12 words back-to-back.  Order matches the
     * FSM's emit order — CTRL is last so the voice doesn't activate
     * until everything else is programmed.
     *
     * VOL_TARGET == VOL_LR + VOL_RATE = 0: when vol_rate == 0 the
     * mixer "snaps" vol_lr to vol_target every sample; matching them
     * makes the snap a no-op and the voice plays at the requested
     * level.  Without these the voice is silenced by the previous
     * tenant's stale target (typically 0 from voice_force_off). */
    vstate_set_cursor(voice, VW_BASE);
    vstate_write(base_word);                        /* W0  BASE */
    vstate_write(v->length);                        /* W1  LEN */
    vstate_write(mixer_rate);                       /* W2  RATE */
    vstate_write(0);                                /* W3  POS_INT (start at sample 0) */
    vstate_write(vol_lr);                           /* W4  VOL_LR */
    vstate_write(vol_lr);                           /* W5  VOL_TARGET = VOL_LR */
    vstate_write(0);                                /* W6  VOL_RATE = 0 (instant) */
    vstate_write(v->loop_end);                      /* W7  LOOP_END */
    vstate_write(v->loop_start);                    /* W8  LOOP_START */
    vstate_write((uint32_t)cutoff);                 /* W9  FILTER_FC */
    vstate_write(((uint32_t)(filter_enable ? 1 : 0) << 8)
                 | (uint32_t)q_hw);                 /* W10 FILTER_Q */
    vstate_write(ctrl);                             /* W11 CTRL (last) */

    /* --------------------------------------------------------------
     * Phase 3: RAMP0 (volume envelope) state + baked DAHDSR params.
     * Populated unconditionally — when AWE_HW_ENVELOPE is off, the
     * fabric sub-FSM short-circuits and the words stay unread.  This
     * keeps awe_voice_load's wire format stable across the SW/HW
     * flip.
     *
     * Initial working state depends on whether the zone has a
     * non-zero delay: start in ENV_DELAY with the timer loaded, else
     * jump straight to ENV_ATTACK with rate + target preloaded.
     * -------------------------------------------------------------- */
    {
        uint32_t level0, rate0, target0, timer0;
        uint32_t stage0;
        if (v->vol_delay_ticks > 0) {
            level0  = 0;
            rate0   = 0;
            target0 = 0;
            timer0  = v->vol_delay_ticks;
            stage0  = 1;  /* ENV_DELAY */
        } else {
            level0  = 0;
            rate0   = v->vol_attack_rate;
            target0 = 0x00010000;
            timer0  = 0;
            stage0  = 2;  /* ENV_ATTACK */
        }
        vstate_set_cursor(voice, 12);
        vstate_write(level0);                          /* W12 level */
        vstate_write(rate0);                           /* W13 cur_rate */
        vstate_write(target0);                         /* W14 cur_target */
        vstate_write(timer0);                          /* W15 cur_timer */
        vstate_write(stage0);                          /* W16 stage */
        vstate_write(v->vol_attack_rate);              /* W17 */
        vstate_write(v->vol_hold_ticks);               /* W18 */
        vstate_write(v->vol_decay_rate);               /* W19 */
        vstate_write(v->vol_sustain_level);            /* W20 */

        /* Phase 3c: fabric has no divider.  Pre-bake the release-rate
         * reciprocal so NOTE_OFF_SERVICE can compute
         *   rate = (level × inv_release) >> 16
         * in a single 17×16 DSP multiply instead of a runtime divide.
         * inv_release = 0x10000 / release_ticks (Q16 unsigned).
         * release_ticks == 0 collapses to a one-tick release. */
        {
            uint32_t inv_release;
            if (v->vol_release_ticks == 0)
                inv_release = 0x10000u;       /* collapse: 1 tick */
            else
                inv_release = 0x10000u / v->vol_release_ticks;
            if (inv_release == 0) inv_release = 1;  /* clamp minimum */
            vstate_write(inv_release);                 /* W21 (reciprocal) */
        }
    }

    /* --------------------------------------------------------------
     * Phase 4: LFO state + mod-matrix scales.
     *
     * W22..W27 hold the two LFOs' {phase,delay_rem}, {rate,wave},
     * and cached output words.  Phase is zeroed at note-load; rate
     * is taken as the low 16 bits of awe_lfo_t.rate (Q16 phase
     * increment per 1 ms tick).  Delay_ticks is clamped to 16 bits
     * (covers up to ~65 sec of LFO delay — more than any real zone).
     *
     * W28 / W29 are the mod-matrix output cache (PITCH_OFS,
     * FILTER_OFS).  Zero them so stage 7/8 composers see a clean
     * slate on the first tick after note-on.
     * -------------------------------------------------------------- */
    {
        uint32_t lfo0_delay = v->lfo0.delay_ticks;
        uint32_t lfo1_delay = v->lfo1.delay_ticks;
        if (lfo0_delay > 0xFFFFu) lfo0_delay = 0xFFFFu;
        if (lfo1_delay > 0xFFFFu) lfo1_delay = 0xFFFFu;

        vstate_set_cursor(voice, VW_LFO0_STATE);
        vstate_write((lfo0_delay << 16) | 0u);          /* W22 state */
        vstate_write(((uint32_t)(v->lfo0.waveform & 7u) << 16)
                     | (v->lfo0.rate & 0xFFFFu));        /* W23 cfg */
        vstate_write(0);                                  /* W24 out cache */
        vstate_write((lfo1_delay << 16) | 0u);          /* W25 state */
        vstate_write(((uint32_t)(v->lfo1.waveform & 7u) << 16)
                     | (v->lfo1.rate & 0xFFFFu));        /* W26 cfg */
        vstate_write(0);                                  /* W27 out cache */
        vstate_write(0);                                  /* W28 PITCH_OFS */
        vstate_write(0);                                  /* W29 FILTER_OFS */
        vstate_write((uint32_t)(int32_t)v->initial_fc);   /* W30 INITIAL_FC cents (sign-ext) */
    }

    /* Mod-matrix scales — sidecar register file.  CPU writes one
     * scale per MMIO poke; field id matches awe_mm_t member order. */
    {
        AWE_MM_WRITE = ((uint32_t)(uint16_t)v->mm.lfo0_pitch  << 16)
                     | (0u << 8) | (uint32_t)(voice & 0x3F);
        AWE_MM_WRITE = ((uint32_t)(uint16_t)v->mm.lfo0_filter << 16)
                     | (1u << 8) | (uint32_t)(voice & 0x3F);
        AWE_MM_WRITE = ((uint32_t)(uint16_t)v->mm.lfo1_pitch  << 16)
                     | (2u << 8) | (uint32_t)(voice & 0x3F);
        AWE_MM_WRITE = ((uint32_t)(uint16_t)v->mm.ramp1_pitch << 16)
                     | (3u << 8) | (uint32_t)(voice & 0x3F);
        AWE_MM_WRITE = ((uint32_t)(uint16_t)v->mm.ramp1_filter << 16)
                     | (4u << 8) | (uint32_t)(voice & 0x3F);
    }
}

void of_awe_voice_trigger_impl(int voice)
{
    if (voice < 0 || voice >= AWE_MAX_VOICES) return;
    AWE_NOTE_ON = (uint32_t)(voice & 0x3F);
}

void of_awe_voice_release_impl(int voice)
{
    if (voice < 0 || voice >= AWE_MAX_VOICES) return;
    /* Phase 1: AWE has no envelope yet, so release == stop.  Mark the
     * voice inactive and let the mixer's ramp handle the audible fade
     * via the existing VOL_TARGET / VOL_RATE path.  Phase 5+ replaces
     * this with a real RAMP0 release segment. */
    AWE_NOTE_OFF = (uint32_t)(voice & 0x3F);
}

/* Phase 5c — Ramp1 (mod env) trigger.  Two-write protocol; the fabric
 * latches the rate first, then the trigger commits {voice, stage} into
 * ramp1_meta_ram.  ATTACK resets level to 0; RELEASE keeps the current
 * level; DONE snaps to 0 and stops. */
void of_awe_ramp1_trigger_impl(int voice, int stage, uint32_t rate)
{
    if (voice < 0 || voice >= AWE_MAX_VOICES) return;
    AWE_RAMP1_RATE    = rate;
    AWE_RAMP1_TRIGGER = ((uint32_t)(stage & 0xF) << 8) | (uint32_t)(voice & 0x3F);
}

void of_awe_voice_stop_impl(int voice)
{
    if (voice < 0 || voice >= AWE_MAX_VOICES) return;
    AWE_VOICE_STOP = (uint32_t)(voice & 0x3F);
    /* Hard stop: also clear the mixer's CTRL.active bit so DMA halts
     * immediately.  Phase 1 does this through the existing periph
     * mixer-write path because AWE itself has no "stop" emit yet. */
    MIX_VOICE_SEL  = voice;
    MIX_VOICE_CTRL = 0;
}

/* Channel-bank packing — Phase 3b precomputes the two expensive SW
 * divides (/127 for vol×expr, /63 for midi-pan scaling) on the CPU so
 * the fabric VOL_COMPOSE sees only multiplies.  CC writes happen at
 * event rate (tens of Hz aggregate), so the CPU-side divide is free;
 * it avoids a fabric divider that would sit on the 100 MHz critical
 * path per voice per tick.
 *
 * Layout matches what audio_awe.v reads in stage 15:
 *   W0: {ch_vol_combined[31:24], midi_pan_scaled[23:8], sustain[7:0]}
 *        where ch_vol_combined = (vol × expr) / 127           (0..127)
 *        and   midi_pan_scaled = ((pan-64) × 500) / 63       (-508..+500)
 *   W1: {bend[31:16], mod[15:8], _[7:0]}
 *   W2: {brightness[31:24], resonance[23:16], _[15:0]}
 *   W3: {chorus_send[31:24], reverb_send[23:16], _[15:0]} */
static void chan_push(int ch)
{
    const chan_state_t *c = &chans[ch & 0x0F];
    int ch_vol_combined  = ((int)c->vol * (int)c->expr) / 127;   /* 0..127 */
    int midi_pan_scaled  = (((int)c->pan - 64) * 500) / 63;       /* -508..+500 */

    chan_set_cursor(ch, 0);
    chan_write(((uint32_t)(ch_vol_combined & 0xFF) << 24)
             | ((uint32_t)(midi_pan_scaled & 0xFFFF) << 8)
             |  (uint32_t)c->sustain);
    chan_write(((uint32_t)(uint16_t)c->bend << 16) | ((uint32_t)c->mod << 8));
    chan_write(((uint32_t)c->brightness << 24) | ((uint32_t)c->resonance << 16));
    chan_write(((uint32_t)c->chorus_send << 24) | ((uint32_t)c->reverb_send << 16));
}

void of_awe_channel_set_volume_impl(int ch, int v) {
    if (ch < 0 || ch >= AWE_NUM_CHANNELS) return;
    chans[ch].vol = (uint8_t)(v & 0x7F); chan_push(ch);
}
void of_awe_channel_set_expression_impl(int ch, int v) {
    if (ch < 0 || ch >= AWE_NUM_CHANNELS) return;
    chans[ch].expr = (uint8_t)(v & 0x7F); chan_push(ch);
}
void of_awe_channel_set_pan_impl(int ch, int v) {
    if (ch < 0 || ch >= AWE_NUM_CHANNELS) return;
    chans[ch].pan = (uint8_t)(v & 0x7F); chan_push(ch);
}
void of_awe_channel_set_bend_impl(int ch, int v) {
    if (ch < 0 || ch >= AWE_NUM_CHANNELS) return;
    chans[ch].bend = (int16_t)v; chan_push(ch);
}
void of_awe_channel_set_mod_impl(int ch, int v) {
    if (ch < 0 || ch >= AWE_NUM_CHANNELS) return;
    chans[ch].mod = (uint8_t)(v & 0x7F); chan_push(ch);
}
void of_awe_channel_set_sustain_impl(int ch, int on) {
    if (ch < 0 || ch >= AWE_NUM_CHANNELS) return;
    chans[ch].sustain = on ? 1 : 0; chan_push(ch);
}
void of_awe_channel_set_brightness_impl(int ch, int v) {
    if (ch < 0 || ch >= AWE_NUM_CHANNELS) return;
    chans[ch].brightness = (uint8_t)(v & 0x7F); chan_push(ch);
}
void of_awe_channel_set_resonance_impl(int ch, int v) {
    if (ch < 0 || ch >= AWE_NUM_CHANNELS) return;
    chans[ch].resonance = (uint8_t)(v & 0x7F); chan_push(ch);
}
void of_awe_channel_set_reverb_send_impl(int ch, int v) {
    if (ch < 0 || ch >= AWE_NUM_CHANNELS) return;
    chans[ch].reverb_send = (uint8_t)(v & 0xFF); chan_push(ch);
}
void of_awe_channel_set_chorus_send_impl(int ch, int v) {
    if (ch < 0 || ch >= AWE_NUM_CHANNELS) return;
    chans[ch].chorus_send = (uint8_t)(v & 0xFF); chan_push(ch);
}

void of_awe_set_master_volume_impl(int v) {
    if (v < 0)   v = 0;
    if (v > 255) v = 255;
    master_vol = (uint8_t)v;
    AWE_MASTER_VOL = (uint32_t)master_vol;
}

void of_awe_set_bend_range_impl(int cents) {
    bend_range_cents = (int16_t)cents;
    AWE_BEND_RANGE = (uint32_t)cents;
}

uint64_t of_awe_active_mask_impl(void) {
    uint32_t lo = AWE_ACTIVE_MASK_LO;
    uint32_t hi = AWE_ACTIVE_MASK_HI;
    return ((uint64_t)hi << 32) | lo;
}

uint32_t of_awe_tick_count_impl(void) {
    return AWE_TICK_COUNT;
}

static int g_awe_hw_envelope;

void of_awe_set_hw_envelope_impl(int enabled) {
    g_awe_hw_envelope = enabled ? 1 : 0;
    /* Global addr 5 = hw_envelope_enable bit per audio_awe.v. */
    AWE_HW_ENVELOPE = (uint32_t)g_awe_hw_envelope;
}

int of_awe_hw_envelope_enabled(void) {
    return g_awe_hw_envelope;
}

void of_awe_set_reverb_level_impl(int level) {
    if (level < 0)   level = 0;
    if (level > 255) level = 255;
    AWE_REVERB_LEVEL = (uint32_t)level;
}

void of_awe_set_reverb_feedback_impl(int feedback) {
    if (feedback < 0)   feedback = 0;
    if (feedback > 255) feedback = 255;
    AWE_REVERB_FEEDBACK = (uint32_t)feedback;
}

void of_awe_set_chorus_level_impl(int level) {
    if (level < 0)   level = 0;
    if (level > 255) level = 255;
    AWE_CHORUS_LEVEL = (uint32_t)level;
}

void of_awe_set_chorus_rate_impl(int rate) {
    if (rate < 0)     rate = 0;
    if (rate > 65535) rate = 65535;
    AWE_CHORUS_RATE = (uint32_t)rate;
}

void of_awe_set_chorus_depth_impl(int depth) {
    if (depth < 0)   depth = 0;
    if (depth > 255) depth = 255;
    AWE_CHORUS_DEPTH = (uint32_t)depth;
}

void awe_init(void)
{
    /* Defaults matching of_midi_init / smp_voice_init expectations. */
    for (int i = 0; i < AWE_NUM_CHANNELS; i++) {
        chans[i].vol         = 100;
        chans[i].expr        = 127;
        chans[i].pan         = 64;
        chans[i].bend        = 0;
        chans[i].mod         = 0;
        chans[i].sustain     = 0;
        chans[i].brightness  = 64;
        chans[i].resonance   = 0;
        chans[i].reverb_send = 0;
        chans[i].chorus_send = 0;
        chan_push(i);
    }
    master_vol = 255;
    AWE_MASTER_VOL = master_vol;
    AWE_BEND_RANGE = (uint32_t)bend_range_cents;
}
