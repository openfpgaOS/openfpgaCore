/*
 * openfpgaOS Input HAL
 * Unified controller abstraction for Pocket, Dock, and Analogizer (SNAC).
 *
 * The OF_BTN_* button bitmasks and the of_input_state_t struct are
 * defined in api/of_input.h -- the SDK header is the canonical source
 * for both. Per-target HAL implementations are responsible for
 * translating native register layouts into this format. On Pocket the
 * native APF format happens to match OF_BTN_* bit-for-bit (no
 * translation), but a future MiSTer port would remap here.
 */

#ifndef OFOS_INPUT_H
#define OFOS_INPUT_H

#include <stdint.h>
#include "of_input_types.h"

#define INPUT_MAX_PLAYERS   OF_MAX_PLAYERS

/* Initialize input subsystem */
void of_input_init(void);

/* Poll all controllers (call once per frame) */
void of_input_poll(void);

/* Get current state for a player (0 or 1) */
const of_input_state_t *of_input_get_state(int player);

/* Single-player fast path: poll hardware + return P0 state in one call */
void of_input_poll_p0(of_input_state_t *out);

/* Set analog stick dead zone (default 8000).
 * Stick values with absolute value below this threshold are zeroed. */
void of_input_set_deadzone(int16_t deadzone);

/* Check if a button is currently held */
static inline int of_input_is_held(int player, uint32_t mask) {
    extern of_input_state_t of_input_states[INPUT_MAX_PLAYERS];
    return (of_input_states[player].buttons & mask) != 0;
}

/* Check if a button was just pressed this frame */
static inline int of_input_is_pressed(int player, uint32_t mask) {
    extern of_input_state_t of_input_states[INPUT_MAX_PLAYERS];
    return (of_input_states[player].buttons_pressed & mask) != 0;
}

/* Check if a button was just released this frame */
static inline int of_input_is_released(int player, uint32_t mask) {
    extern of_input_state_t of_input_states[INPUT_MAX_PLAYERS];
    return (of_input_states[player].buttons_released & mask) != 0;
}

#endif /* OFOS_INPUT_H */
