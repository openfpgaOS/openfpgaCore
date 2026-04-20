// =============================================================================
// tb_audio_main.cpp — Drive the audio_mixer + audio_awe Verilator harness
// =============================================================================
// Test scenarios:
//   1. Single voice basic playback (looped)
//   2. 32-voice polyphonic burst (worst-case CRAM1 cache pressure)
//   3. Voice-stealing pattern (constant note_on rate, exhausts pool)
//   4. Effects sweep: reverb on/off, chorus on/off
// All scenarios capture sample_wr count vs DAC pops and report underruns.
// =============================================================================

#include "Vtb_audio.h"
#include "verilated.h"
#include "verilated_vcd_c.h"
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <cmath>
#include <vector>
#include <string>

// AWE voice-state field offsets (must match audio_awe.v VW_* localparams).
// Word 5 is the SHARED slot: {pan_base[15:0], voice_base_vol[7:0], midi_ch[7:0]}
// (the SDK calls it VW_MIDI_CH_BV_PAN; the mixer's own VOL_TARGET is at a
// different mixer-side field, written via the AWE→mixer mux not voice_state_ram).
enum {
    VW_BASE           = 0,
    VW_LEN            = 1,
    VW_RATE           = 2,
    VW_POS_INT        = 3,
    VW_VOL_LR         = 4,
    VW_MIDI_CH_BV_PAN = 5,    // {pan_base[15:0], voice_base_vol[7:0], midi_ch[7:0]}
    VW_VOL_RATE       = 6,
    VW_LOOP_END       = 7,
    VW_LOOP_START     = 8,
    VW_FILTER_FC      = 9,
    VW_FILTER_Q       = 10,
    VW_CTRL           = 11,
    VW_RAMP0_LEVEL    = 12,
    VW_RAMP0_RATE     = 13,
    VW_RAMP0_TARGET   = 14,
    VW_RAMP0_TIMER    = 15,
    VW_RAMP0_STAGE    = 16,
    VW_RAMP0_ATTACK_R = 17,
    VW_RAMP0_HOLD_T   = 18,
    VW_RAMP0_DECAY_R  = 19,
    VW_RAMP0_SUSTAIN  = 20,
    VW_RAMP0_RELEASE_T= 21,
    VW_LFO0_STATE     = 22,
    VW_LFO0_CFG       = 23,
    VW_LFO0_OUT       = 24,
    VW_LFO1_STATE     = 25,
    VW_LFO1_CFG       = 26,
    VW_LFO1_OUT       = 27,
    VW_PITCH_OFS      = 28,
    VW_FILTER_OFS     = 29,
    VW_INITIAL_FC     = 30,
};

#define CTRL_ACTIVE  (1u << 0)
#define CTRL_LOOP    (1u << 1)
#define CTRL_FMT16   (1u << 2)

#define ENV_DELAY    1
#define ENV_ATTACK   2

static Vtb_audio* g_top = nullptr;
static VerilatedVcdC* g_vcd = nullptr;
static uint64_t g_cycles = 0;

static void tick(int n = 1) {
    for (int i = 0; i < n; i++) {
        g_top->clk = 0; g_top->eval();
        if (g_vcd) g_vcd->dump(g_cycles * 10);
        g_top->clk = 1; g_top->eval();
        if (g_vcd) g_vcd->dump(g_cycles * 10 + 5);
        g_cycles++;
    }
}

static void clear_strobes(void) {
    g_top->cpu_voice_state_wr = 0;
    g_top->cpu_chan_wr        = 0;
    g_top->cpu_mm_wr          = 0;
    g_top->cpu_global_wr      = 0;
    g_top->cpu_note_on        = 0;
    g_top->cpu_note_off       = 0;
    g_top->cpu_voice_stop     = 0;
    g_top->cpu_ramp1_trig     = 0;
    g_top->cpu_mix_voice_wr   = 0;
}

static void awe_voice_state_write(int voice, int word, uint32_t value) {
    g_top->cpu_voice_state_wr    = 1;
    g_top->cpu_voice_state_addr  = ((voice & 0x3F) << 5) | (word & 0x1F);
    g_top->cpu_voice_state_wdata = value;
    tick();
    g_top->cpu_voice_state_wr    = 0;
    tick();   // give AWE a settling cycle between writes
}

static void awe_chan_write(int ch, int word, uint32_t value) {
    g_top->cpu_chan_wr    = 1;
    g_top->cpu_chan_addr  = ((ch & 0x0F) << 2) | (word & 0x3);
    g_top->cpu_chan_wdata = value;
    tick();
    g_top->cpu_chan_wr    = 0;
    tick();
}

static void awe_global_write(int addr, uint32_t value) {
    g_top->cpu_global_wr    = 1;
    g_top->cpu_global_addr  = addr & 0xF;
    g_top->cpu_global_wdata = value;
    tick();
    g_top->cpu_global_wr    = 0;
    tick();
}

static void awe_note_on(int voice) {
    g_top->cpu_note_on       = 1;
    g_top->cpu_note_on_voice = voice & 0x3F;
    tick();
    g_top->cpu_note_on       = 0;
    tick();
}

static void awe_note_off(int voice) {
    g_top->cpu_note_off       = 1;
    g_top->cpu_note_off_voice = voice & 0x3F;
    tick();
    g_top->cpu_note_off       = 0;
    tick();
}

static void awe_voice_stop(int voice) {
    g_top->cpu_voice_stop       = 1;
    g_top->cpu_voice_stop_voice = voice & 0x3F;
    tick();
    g_top->cpu_voice_stop       = 0;
    tick();
}

// Program a voice exactly the way of_awe_voice_load_impl in hal/awe.c does.
// `base` is CRAM1 word address, `len` sample count, `rate` Q16.16 playback rate.
// `voice_base_vol` (0..255) is the per-voice base level used by VOL_COMPOSE.
static void program_voice(int voice, uint32_t base, uint32_t len, uint32_t rate,
                          int loop, uint8_t voice_base_vol, int midi_ch) {
    awe_voice_state_write(voice, VW_BASE,         base);
    awe_voice_state_write(voice, VW_LEN,          len);
    awe_voice_state_write(voice, VW_RATE,         rate);
    awe_voice_state_write(voice, VW_POS_INT,      0);
    awe_voice_state_write(voice, VW_VOL_LR,       0x8080);                 // initial 50% L+R
    // Word 5 = {pan_base[15:0]=0, voice_base_vol[7:0], midi_ch[7:0]}
    awe_voice_state_write(voice, VW_MIDI_CH_BV_PAN,
                          ((uint32_t)0 << 16) | ((uint32_t)voice_base_vol << 8) | (midi_ch & 0xFF));
    awe_voice_state_write(voice, VW_VOL_RATE,     0);
    awe_voice_state_write(voice, VW_LOOP_END,     loop ? len : 0);
    awe_voice_state_write(voice, VW_LOOP_START,   0);
    awe_voice_state_write(voice, VW_FILTER_FC,    65535);                  // wide open
    awe_voice_state_write(voice, VW_FILTER_Q,     (1u << 8) | 8);          // enable + low Q

    // DAHDSR — fast attack, full sustain so voice plays loud and steady
    awe_voice_state_write(voice, VW_RAMP0_LEVEL,   0);
    awe_voice_state_write(voice, VW_RAMP0_RATE,    0x10000);   // attack 1 tick
    awe_voice_state_write(voice, VW_RAMP0_TARGET,  0x10000);   // full level
    awe_voice_state_write(voice, VW_RAMP0_TIMER,   0);
    awe_voice_state_write(voice, VW_RAMP0_STAGE,   ENV_ATTACK);
    awe_voice_state_write(voice, VW_RAMP0_ATTACK_R,0x10000);
    awe_voice_state_write(voice, VW_RAMP0_HOLD_T,  100);
    awe_voice_state_write(voice, VW_RAMP0_DECAY_R, 0x100);
    awe_voice_state_write(voice, VW_RAMP0_SUSTAIN, 0x10000);   // full sustain — voice keeps singing
    awe_voice_state_write(voice, VW_RAMP0_RELEASE_T,1000);

    // LFOs / mod-matrix — zero them so they don't interfere
    awe_voice_state_write(voice, VW_LFO0_STATE,  0);
    awe_voice_state_write(voice, VW_LFO0_CFG,    0);
    awe_voice_state_write(voice, VW_LFO0_OUT,    0);
    awe_voice_state_write(voice, VW_LFO1_STATE,  0);
    awe_voice_state_write(voice, VW_LFO1_CFG,    0);
    awe_voice_state_write(voice, VW_LFO1_OUT,    0);
    awe_voice_state_write(voice, VW_PITCH_OFS,   0);
    awe_voice_state_write(voice, VW_FILTER_OFS,  0);
    awe_voice_state_write(voice, VW_INITIAL_FC,  6900);   // ~440 Hz cutoff baseline

    // Last write = CTRL with active bit.  Loop bit set if requested.
    uint32_t ctrl = CTRL_ACTIVE | CTRL_FMT16 | (loop ? CTRL_LOOP : 0);
    awe_voice_state_write(voice, VW_CTRL, ctrl);
}

// Initialize a MIDI channel's volume/expression/pan in the AWE chan_bank
// so VOL_COMPOSE / SEND_COMPOSE see non-zero values from the first tick.
static void program_channel(int ch, uint8_t volume, uint8_t expr,
                            uint8_t pan, uint8_t reverb_send,
                            uint8_t chorus_send) {
    // chan_bank W0: {ch_vol_combined[31:24], midi_pan_scaled[23:8], sustain[7:0]}
    int vc = (volume * expr) / 127;
    int pn = ((pan - 64) * 500) / 63;
    awe_chan_write(ch, 0,
                   ((uint32_t)(vc & 0xFF) << 24) |
                   ((uint32_t)(pn & 0xFFFF) << 8));
    // W1: {bend[31:16], mod[15:8], _[7:0]}
    awe_chan_write(ch, 1, 0);
    // W2: {brightness[31:24], resonance[23:16], _[15:0]}
    awe_chan_write(ch, 2, 0);
    // W3: {chorus_send[31:24], reverb_send[23:16], _[15:0]}
    awe_chan_write(ch, 3,
                   ((uint32_t)chorus_send << 24) |
                   ((uint32_t)reverb_send << 16));
}

// ── Test scenarios ──────────────────────────────────────────────────────────

struct Stats {
    uint32_t samples_written;
    uint32_t underruns_at_end;
    uint32_t cycles;
    uint32_t active_max;
    uint16_t peak_l, peak_r;
    uint16_t max_delta_l, max_delta_r;
    uint32_t discontinuities;
    uint64_t avg_abs_l, avg_abs_r;     // mean |sample|
};

// Reset the continuity counters at the start of each scenario so we
// measure the run in isolation.
static void reset_counters(void) {
    /* These are output regs in the harness; only an FPGA reset clears
     * them.  For per-test windowing, snapshot at start and diff at end. */
}

static Stats run_for_us(uint32_t us, bool sample_log = false) {
    // 100 MHz clock → 100 cycles per µs
    uint32_t cycles_to_run = us * 100;
    Stats s = {};
    uint32_t start_under   = g_top->underrun_count;
    uint32_t start_disc    = g_top->discontinuity_count;
    uint32_t start_samples = g_top->samples_count;
    uint64_t start_sum_l   = g_top->sum_abs_left;
    uint64_t start_sum_r   = g_top->sum_abs_right;
    uint16_t start_peak_l  = g_top->peak_left_abs;
    uint16_t start_peak_r  = g_top->peak_right_abs;
    uint16_t start_dl      = g_top->max_delta_l;
    uint16_t start_dr      = g_top->max_delta_r;
    uint32_t start_cycles  = g_cycles;
    uint8_t  active_max    = 0;

    bool prev_wr = false;
    uint32_t writes = 0;
    for (uint32_t i = 0; i < cycles_to_run; i++) {
        tick();
        if (g_top->sample_wr && !prev_wr) writes++;
        prev_wr = g_top->sample_wr;
        if (g_top->active_count > active_max) active_max = g_top->active_count;
        if (sample_log && (i % 2083) == 0) {
            printf("  [t=%6.2fms]  fifo=%3u  active=%2u  underruns=%u\n",
                   i / 100000.0, (unsigned)g_top->fifo_level,
                   (unsigned)g_top->active_count,
                   (unsigned)g_top->underrun_count);
        }
    }
    uint32_t n_samples = g_top->samples_count - start_samples;
    s.samples_written  = writes;
    s.underruns_at_end = g_top->underrun_count - start_under;
    s.cycles           = g_cycles - start_cycles;
    s.active_max       = active_max;
    s.peak_l           = g_top->peak_left_abs;     // monotonic, full-run max
    s.peak_r           = g_top->peak_right_abs;
    s.max_delta_l      = g_top->max_delta_l;
    s.max_delta_r      = g_top->max_delta_r;
    s.discontinuities  = g_top->discontinuity_count - start_disc;
    s.avg_abs_l = n_samples ? (g_top->sum_abs_left  - start_sum_l) / n_samples : 0;
    s.avg_abs_r = n_samples ? (g_top->sum_abs_right - start_sum_r) / n_samples : 0;
    (void)start_peak_l; (void)start_peak_r; (void)start_dl; (void)start_dr;
    return s;
}

static void print_stats(const char* name, const Stats& s, uint32_t us_run,
                        uint32_t expected_samples) {
    int32_t delta = (int32_t)s.samples_written - (int32_t)expected_samples;
    printf("[%s]\n", name);
    printf("  duration:        %u µs (%u cycles)\n", us_run, s.cycles);
    printf("  samples written: %u  (expected ~%u, delta %+d)\n",
           s.samples_written, expected_samples, delta);
    printf("  active_max:      %u\n", s.active_max);
    printf("  underruns:       %u %s\n", s.underruns_at_end,
           s.underruns_at_end == 0 ? "✅" : "❌");
    printf("  output level:    L peak=%5u (%.1f%%)  R peak=%5u (%.1f%%)\n",
           s.peak_l, 100.0 * s.peak_l / 32768.0,
           s.peak_r, 100.0 * s.peak_r / 32768.0);
    printf("  output mean|x|:  L %5lu          R %5lu\n",
           (unsigned long)s.avg_abs_l, (unsigned long)s.avg_abs_r);
    printf("  max sample step: L %5u           R %5u\n",
           s.max_delta_l, s.max_delta_r);
    printf("  discontinuities: %u (samples with |Δ|>16384, half-FS) %s\n",
           s.discontinuities, s.discontinuities == 0 ? "✅" : "⚠️");
    printf("\n");
}

static void enable_effects(void) {
    // From mididemo MODE_PLAY init
    awe_global_write(6, 80);    // reverb_wet_level
    awe_global_write(7, 140);   // reverb_feedback
    awe_global_write(8, 48);    // chorus_wet_level
    awe_global_write(9, 60);    // chorus_lfo_rate
    awe_global_write(10, 12);   // chorus_lfo_depth
    awe_global_write(5, 1);     // hw_envelope_enable
}

static void disable_effects(void) {
    awe_global_write(6, 0);
    awe_global_write(7, 0);
    awe_global_write(8, 0);
    awe_global_write(9, 0);
    awe_global_write(10, 0);
    awe_global_write(5, 1);     // keep hw_envelope on (Doom behaviour)
}

// MIDI-cents to fp16 rate: 2^(semitones/12) in Q16.16
static uint32_t pitch_rate(int cents_offset) {
    double mult = pow(2.0, cents_offset / 1200.0);
    return (uint32_t)(0x10000 * mult);
}

static void program_all_channels(uint8_t vol = 100, uint8_t reverb = 80,
                                 uint8_t chorus = 0) {
    for (int ch = 0; ch < 16; ch++)
        program_channel(ch, vol, 127, 64, reverb, chorus);
}

static void scenario_single_note(void) {
    printf("================================================================\n");
    printf("  Scenario 1: Single sustained voice (440Hz-ish), no effects\n");
    printf("================================================================\n");
    disable_effects();
    program_all_channels();
    program_voice(0, 0x100, 2048, 0x10000, /*loop=*/1, /*vbv=*/220, /*ch=*/0);
    awe_note_on(0);
    Stats s = run_for_us(50000);
    print_stats("single_note_no_fx", s, 50000, 2400);
    awe_voice_stop(0);
}

static void scenario_polyphony(int n_voices, bool fx) {
    printf("================================================================\n");
    printf("  Scenario 2: %d voices spread across 2 octaves, fx=%s\n",
           n_voices, fx ? "ON" : "OFF");
    printf("================================================================\n");
    if (fx) enable_effects(); else disable_effects();
    program_all_channels(/*vol=*/100, /*reverb=*/fx ? 80 : 0, /*chorus=*/fx ? 64 : 0);

    for (int v = 0; v < n_voices; v++) {
        // Spread across 2 octaves with chromatic 100-cent steps so the mix
        // is harmonically rich (every voice at a different pitch)
        int cents = -1200 + v * (2400 / (n_voices == 1 ? 1 : (n_voices - 1)));
        program_voice(v,
                      0x100 + v * 0x100,
                      2048 + v * 32,
                      pitch_rate(cents),
                      /*loop=*/1,
                      /*vbv=*/100,
                      /*ch=*/v % 16);
        awe_note_on(v);
    }
    Stats s = run_for_us(100000);
    char name[64];
    snprintf(name, sizeof(name), "%dvoice_fx_%s", n_voices, fx ? "on" : "off");
    print_stats(name, s, 100000, 4800);
    for (int v = 0; v < n_voices; v++) awe_voice_stop(v);
}

static void scenario_voice_stealing(void) {
    printf("================================================================\n");
    printf("  Scenario 3: Voice stealing — 32 voices, fresh note every 2 ms\n");
    printf("================================================================\n");
    enable_effects();
    program_all_channels(100, 80, 64);

    uint32_t total_us         = 500000;       // 0.5 sec
    uint32_t cycles_per_note  = 200000;       // every 2 ms
    uint32_t note_idx         = 0;
    uint32_t under_start      = g_top->underrun_count;
    uint32_t disc_start       = g_top->discontinuity_count;
    uint32_t samples_written  = 0;
    bool     prev_wr          = false;
    uint8_t  active_max       = 0;
    uint32_t cycles_target    = total_us * 100;
    uint32_t samples_start    = g_top->samples_count;

    for (uint32_t i = 0; i < cycles_target; i++) {
        if ((i % cycles_per_note) == 0) {
            int v     = note_idx % 32;
            int cents = (note_idx * 137) % 2400 - 1200;       // pseudo-random pitch
            program_voice(v, 0x100 + v * 0x80, 2048,
                          pitch_rate(cents), 1, 110, v % 16);
            awe_note_on(v);
            note_idx++;
        }
        tick();
        if (g_top->sample_wr && !prev_wr) samples_written++;
        prev_wr = g_top->sample_wr;
        if (g_top->active_count > active_max) active_max = g_top->active_count;
    }

    Stats s = {};
    uint32_t n_samples = g_top->samples_count - samples_start;
    s.samples_written  = samples_written;
    s.underruns_at_end = g_top->underrun_count - under_start;
    s.cycles           = cycles_target;
    s.active_max       = active_max;
    s.peak_l           = g_top->peak_left_abs;
    s.peak_r           = g_top->peak_right_abs;
    s.max_delta_l      = g_top->max_delta_l;
    s.max_delta_r      = g_top->max_delta_r;
    s.discontinuities  = g_top->discontinuity_count - disc_start;
    s.avg_abs_l = n_samples ? g_top->sum_abs_left  / n_samples : 0;
    s.avg_abs_r = n_samples ? g_top->sum_abs_right / n_samples : 0;
    print_stats("voice_stealing_500ms", s, total_us, total_us * 48 / 1000);
    for (int v = 0; v < 32; v++) awe_voice_stop(v);
}

static void scenario_freq_sweep(void) {
    printf("================================================================\n");
    printf("  Scenario 4: Frequency sweep — single voice, sweep 0.25× to 4×\n");
    printf("================================================================\n");
    disable_effects();
    program_all_channels(120, 0, 0);
    program_voice(0, 0x100, 4096, 0x10000, 1, 220, 0);
    awe_note_on(0);
    // Sweep RATE every 5 ms over 200 ms
    uint32_t under_start = g_top->underrun_count;
    uint32_t disc_start  = g_top->discontinuity_count;
    uint32_t samples_start = g_top->samples_count;
    uint8_t  active_max  = 0;
    uint32_t writes      = 0;
    bool     prev_wr     = false;
    const uint32_t total_us = 200000;
    const uint32_t step_cycles = 500000;        // 5 ms steps
    for (uint32_t i = 0; i < total_us * 100; i++) {
        if ((i % step_cycles) == 0) {
            int step  = i / step_cycles;
            int cents = -2400 + step * 200;     // -2 to +2 octaves in 200-cent steps
            awe_voice_state_write(0, VW_RATE, pitch_rate(cents));
        }
        tick();
        if (g_top->sample_wr && !prev_wr) writes++;
        prev_wr = g_top->sample_wr;
        if (g_top->active_count > active_max) active_max = g_top->active_count;
    }
    Stats s = {};
    uint32_t n_samples = g_top->samples_count - samples_start;
    s.samples_written  = writes;
    s.underruns_at_end = g_top->underrun_count - under_start;
    s.cycles           = total_us * 100;
    s.active_max       = active_max;
    s.peak_l           = g_top->peak_left_abs;
    s.peak_r           = g_top->peak_right_abs;
    s.max_delta_l      = g_top->max_delta_l;
    s.max_delta_r      = g_top->max_delta_r;
    s.discontinuities  = g_top->discontinuity_count - disc_start;
    s.avg_abs_l = n_samples ? g_top->sum_abs_left  / n_samples : 0;
    s.avg_abs_r = n_samples ? g_top->sum_abs_right / n_samples : 0;
    print_stats("freq_sweep_4octave", s, total_us, total_us * 48 / 1000);
    awe_voice_stop(0);
}

static void scenario_chord_changes(void) {
    printf("================================================================\n");
    printf("  Scenario 5: Chord changes — 8 voices retrigger every 50 ms\n");
    printf("================================================================\n");
    enable_effects();
    program_all_channels(120, 80, 64);
    static const int chord_cents[3][8] = {
        { -1200, -800, -500, -300,    0,  400,  700, 1200 },   // major spread
        {  -900, -500, -200,  100,  300,  600, 1000, 1500 },   // moves up
        {  -1500,-1000,-600, -200,  100,  500,  900, 1300 },   // moves down
    };
    uint32_t under_start = g_top->underrun_count;
    uint32_t disc_start  = g_top->discontinuity_count;
    uint32_t samples_start = g_top->samples_count;
    uint8_t  active_max  = 0;
    uint32_t writes      = 0;
    bool     prev_wr     = false;
    const uint32_t total_us = 600000;            // 0.6 sec
    const uint32_t change_cycles = 5000000;       // 50 ms per chord change
    int chord = 0;
    for (uint32_t i = 0; i < total_us * 100; i++) {
        if ((i % change_cycles) == 0) {
            for (int v = 0; v < 8; v++) {
                program_voice(v, 0x100 + v * 0x80, 2048,
                              pitch_rate(chord_cents[chord % 3][v]),
                              1, 110, v % 16);
                awe_note_on(v);
            }
            chord++;
        }
        tick();
        if (g_top->sample_wr && !prev_wr) writes++;
        prev_wr = g_top->sample_wr;
        if (g_top->active_count > active_max) active_max = g_top->active_count;
    }
    Stats s = {};
    uint32_t n_samples = g_top->samples_count - samples_start;
    s.samples_written  = writes;
    s.underruns_at_end = g_top->underrun_count - under_start;
    s.cycles           = total_us * 100;
    s.active_max       = active_max;
    s.peak_l           = g_top->peak_left_abs;
    s.peak_r           = g_top->peak_right_abs;
    s.max_delta_l      = g_top->max_delta_l;
    s.max_delta_r      = g_top->max_delta_r;
    s.discontinuities  = g_top->discontinuity_count - disc_start;
    s.avg_abs_l = n_samples ? g_top->sum_abs_left  / n_samples : 0;
    s.avg_abs_r = n_samples ? g_top->sum_abs_right / n_samples : 0;
    print_stats("chord_changes_600ms", s, total_us, total_us * 48 / 1000);
    for (int v = 0; v < 8; v++) awe_voice_stop(v);
}

int main(int argc, char** argv) {
    Verilated::commandArgs(argc, argv);
    g_top = new Vtb_audio;
    Verilated::traceEverOn(true);
    bool want_vcd = false;
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--vcd")) want_vcd = true;
    }
    if (want_vcd) {
        g_vcd = new VerilatedVcdC;
        g_top->trace(g_vcd, 99);
        g_vcd->open("audio_test.vcd");
    }

    // Reset
    g_top->clk = 0;
    g_top->reset_n = 0;
    clear_strobes();
    for (int i = 0; i < 20; i++) tick();
    g_top->reset_n = 1;
    for (int i = 0; i < 20; i++) tick();

    // Init: enable hw_envelope by default (matches Doom + mididemo MODE_PLAY)
    awe_global_write(5, 1);     // hw_envelope_enable

    // Run scenarios
    scenario_single_note();
    scenario_polyphony(8, false);
    scenario_polyphony(16, false);
    scenario_polyphony(32, false);
    scenario_polyphony(8, true);
    scenario_polyphony(16, true);
    scenario_polyphony(32, true);
    scenario_voice_stealing();
    scenario_freq_sweep();
    scenario_chord_changes();

    if (g_vcd) {
        g_vcd->close();
        delete g_vcd;
    }
    delete g_top;
    printf("DONE\n");
    return 0;
}
