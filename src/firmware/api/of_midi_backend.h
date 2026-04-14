/*
 * of_midi_backend.h -- MIDI synthesis backend interface
 *
 * The of_midi front-end parses Standard MIDI Files and dispatches
 * synthesis events through a pluggable backend.
 *
 *   backend_smp  -- sample-based synthesis via hardware mixer + SVF LPF
 *
 * Apps call of_midi_play() and get the sample backend automatically.
 * of_midi_set_backend() allows custom backends.
 */

#ifndef OF_MIDI_BACKEND_H
#define OF_MIDI_BACKEND_H

#ifdef __cplusplus
extern "C" {
#endif

#include <stdint.h>

/* Per-MIDI-channel state shared between front-end and backend.
 * Owned and written by the front-end; backends read only. */
typedef struct {
    uint8_t  program[16];
    uint8_t  volume[16];    /* CC7, 0-127 */
    uint8_t  pan[16];       /* CC10, 0-127 (64=center) */
    int16_t  bend[16];      /* -8192..+8191 */
    int      master_volume; /* 0-255 */
} midi_channel_state_t;

typedef struct midi_backend_ops {
    /* One-time init (called from of_midi_init). ch_state points to the
     * front-end's shared channel state -- valid for the backend's
     * entire lifetime. */
    void (*init)(const midi_channel_state_t *ch_state);
    void (*reset)(void);

    /* Note events */
    void (*note_on)(int ch, int note, int vel);
    void (*note_off)(int ch, int note);

    /* Channel messages */
    void (*program_change)(int ch, int program);
    void (*control_change)(int ch, int cc, int value);
    void (*pitch_bend)(int ch, int value);  /* raw -8192..+8191 */

    /* Bulk silence */
    void (*all_notes_off)(int ch);
    void (*all_sound_off)(void);

    /* Periodic tick (called from of_midi_pump with elapsed microseconds).
     * OPL3 backend ignores this; sample backend uses it for envelopes. */
    void (*pump)(uint32_t elapsed_us);

    /* Master volume (0-255) changed */
    void (*set_volume)(int volume);

    /* Bank loading (OPL3: WOPL bank pointer; SMP: ignored) */
    void (*load_bank)(const uint8_t *bank);
    const uint8_t *(*builtin_bank)(void);
} midi_backend_ops_t;

#ifdef __cplusplus
}
#endif

#endif /* OF_MIDI_BACKEND_H */
