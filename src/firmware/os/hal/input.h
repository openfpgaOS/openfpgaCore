/*
 * openfpgaOS Input HAL
 * Unified controller abstraction for Pocket, Dock, and Analogizer (SNAC)
 */

#ifndef OFOS_INPUT_H
#define OFOS_INPUT_H

#include <stdint.h>

#define INPUT_MAX_PLAYERS   2

typedef struct {
    uint32_t buttons;           /* Button bitmask (BTN_* from regs.h) */
    uint32_t buttons_pressed;   /* Newly pressed since last poll */
    uint32_t buttons_released;  /* Newly released since last poll */
    int16_t  joy_lx, joy_ly;   /* Left stick (-128 to 127) */
    int16_t  joy_rx, joy_ry;   /* Right stick (-128 to 127) */
    uint16_t trigger_l;         /* Left analog trigger (0-65535) */
    uint16_t trigger_r;         /* Right analog trigger (0-65535) */
} of_input_state_t;

/* Initialize input subsystem */
void of_input_init(void);

/* Poll all controllers (call once per frame) */
void of_input_poll(void);

/* Get current state for a player (0 or 1) */
const of_input_state_t *of_input_get_state(int player);

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
