/*
 * of_midi_smp.c -- Sample-based MIDI backend
 *
 * Implements midi_backend_ops_t using the hardware PCM mixer with
 * per-voice SVF LPF. Zone lookups come from the loaded .ofsf bank;
 * envelopes and LFOs are driven by the 1 kHz voice engine tick.
 */

#include "include/of_midi_smp.h"
#include "include/of_midi_backend.h"
#include "include/of_smp_bank.h"
#include "include/of_smp_voice.h"
#include "include/of_mixer.h"
#include "include/of_timer.h"

static const midi_channel_state_t *ch;
static int smp_inited;
static uint32_t tick_accum_us;

/* CC state not in the shared channel state (backend-specific) */
static uint8_t ch_expression[16];   /* CC11 */
static uint8_t ch_sustain[16];      /* CC64 */
static uint8_t ch_mod_wheel[16];    /* CC1 */
static uint8_t ch_brightness[16];   /* CC74 */
static uint8_t ch_resonance[16];    /* CC71 */

static void smp_init(const midi_channel_state_t *ch_state)
{
    ch = ch_state;
    smp_voice_init();

    for (int i = 0; i < 16; i++) {
        ch_expression[i] = 127;
        ch_sustain[i] = 0;
        ch_mod_wheel[i] = 0;
        ch_brightness[i] = 64;
        ch_resonance[i] = 64;
    }

    tick_accum_us = 0;
    smp_inited = 1;
}

static void smp_reset(void)
{
    smp_voice_all_off_global();
    for (int i = 0; i < 16; i++) {
        ch_expression[i] = 127;
        ch_sustain[i] = 0;
        ch_mod_wheel[i] = 0;
        ch_brightness[i] = 64;
        ch_resonance[i] = 64;
    }
    tick_accum_us = 0;
}

static void smp_note_on(int midi_ch, int note, int vel)
{
    const ofsf_zone_t *zones[4];
    int bank = (midi_ch == 9) ? 128 : 0;
    int program = ch->program[midi_ch];
    int n = of_smp_zone_lookup(bank, program, note, vel, zones, 4);

    const void *sbase = of_smp_bank_sample_base();
    for (int i = 0; i < n; i++)
        smp_voice_note_on(zones[i], midi_ch, note, vel, sbase);
}

static void smp_note_off(int midi_ch, int note)
{
    smp_voice_note_off(midi_ch, note);
}

static void smp_program_change(int midi_ch, int program)
{
    (void)midi_ch;
    (void)program;
    /* Program is stored in front-end ch_state; backend reads it on note-on */
}

static void smp_control_change(int midi_ch, int cc, int value)
{
    switch (cc) {
    case 1: /* Mod wheel */
        ch_mod_wheel[midi_ch] = (uint8_t)value;
        smp_voice_update_mod(midi_ch, value);
        break;
    case 7: /* Volume -- stored in front-end, pass combined vol to engine */
        smp_voice_update_volume(midi_ch, ch->volume[midi_ch], ch_expression[midi_ch]);
        break;
    case 10: /* Pan -- stored in front-end */
        smp_voice_update_pan(midi_ch, ch->pan[midi_ch]);
        break;
    case 11: /* Expression */
        ch_expression[midi_ch] = (uint8_t)value;
        smp_voice_update_volume(midi_ch, ch->volume[midi_ch], ch_expression[midi_ch]);
        break;
    case 64: /* Sustain pedal */
        ch_sustain[midi_ch] = (uint8_t)value;
        smp_voice_update_sustain(midi_ch, value >= 64);
        break;
    case 71: /* Resonance */
        ch_resonance[midi_ch] = (uint8_t)value;
        smp_voice_update_filter(midi_ch, ch_brightness[midi_ch], value);
        break;
    case 74: /* Brightness (filter cutoff) */
        ch_brightness[midi_ch] = (uint8_t)value;
        smp_voice_update_filter(midi_ch, value, ch_resonance[midi_ch]);
        break;
    case 120: /* All Sound Off */
        smp_voice_all_off(midi_ch);
        break;
    case 121: /* Reset All Controllers */
        ch_expression[midi_ch] = 127;
        ch_sustain[midi_ch] = 0;
        ch_mod_wheel[midi_ch] = 0;
        ch_brightness[midi_ch] = 64;
        ch_resonance[midi_ch] = 64;
        smp_voice_update_sustain(midi_ch, 0);
        break;
    case 123: /* All Notes Off */
        smp_voice_all_off(midi_ch);
        break;
    }
}

static void smp_pitch_bend(int midi_ch, int value)
{
    smp_voice_update_bend(midi_ch, value);
}

static void smp_all_notes_off(int midi_ch)
{
    smp_voice_all_off(midi_ch);
}

static void smp_all_sound_off(void)
{
    smp_voice_all_off_global();
}

static void smp_pump(uint32_t elapsed_us)
{
    if (!smp_inited)
        return;

    tick_accum_us += elapsed_us;
    while (tick_accum_us >= 1000) {
        smp_voice_tick();
        tick_accum_us -= 1000;
    }
}

static void smp_set_volume(int volume)
{
    smp_voice_set_master_volume(volume);
}

static void smp_load_bank(const uint8_t *bank)
{
    (void)bank; /* Sample backend loads .ofsf via of_smp_bank_load() */
}

static const uint8_t *smp_builtin_bank(void)
{
    return 0; /* No built-in bank; must load .ofsf from SD */
}

static const midi_backend_ops_t smp_ops = {
    .init           = smp_init,
    .reset          = smp_reset,
    .note_on        = smp_note_on,
    .note_off       = smp_note_off,
    .program_change = smp_program_change,
    .control_change = smp_control_change,
    .pitch_bend     = smp_pitch_bend,
    .all_notes_off  = smp_all_notes_off,
    .all_sound_off  = smp_all_sound_off,
    .pump           = smp_pump,
    .set_volume     = smp_set_volume,
    .load_bank      = smp_load_bank,
    .builtin_bank   = smp_builtin_bank,
};

const midi_backend_ops_t *midi_backend_smp(void)
{
    return &smp_ops;
}
