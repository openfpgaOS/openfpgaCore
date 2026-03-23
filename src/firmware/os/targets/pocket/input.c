/*
 * openfpgaOS Input HAL Implementation
 * Reads from both Pocket APF controllers and Analogizer SNAC
 */

#include "input.h"
#include "regs.h"

/* Global state accessible via inline functions in input.h */
of_input_state_t of_input_states[INPUT_MAX_PLAYERS];

static uint32_t prev_buttons[INPUT_MAX_PLAYERS];
static int16_t stick_deadzone = 8000;

static inline int16_t apply_deadzone(int16_t val) {
    return (val > -stick_deadzone && val < stick_deadzone) ? 0 : val;
}

void of_input_init(void) {
    for (int i = 0; i < INPUT_MAX_PLAYERS; i++) {
        of_input_states[i] = (of_input_state_t){0};
        prev_buttons[i] = 0;
    }
}

void of_input_poll(void) {
    /* Player 1 */
    uint32_t keys1 = CONT1_KEY;
    uint32_t joy1  = CONT1_JOY;
    uint32_t trig1 = CONT1_TRIG;

    of_input_states[0].buttons_pressed  = keys1 & ~prev_buttons[0];
    of_input_states[0].buttons_released = ~keys1 & prev_buttons[0];
    of_input_states[0].buttons = keys1;
    prev_buttons[0] = keys1;

    /* Joystick: extract signed 8-bit values, apply dead zone */
    of_input_states[0].joy_lx = apply_deadzone((int8_t)(joy1 & 0xFF));
    of_input_states[0].joy_ly = apply_deadzone((int8_t)((joy1 >> 8) & 0xFF));
    of_input_states[0].joy_rx = apply_deadzone((int8_t)((joy1 >> 16) & 0xFF));
    of_input_states[0].joy_ry = apply_deadzone((int8_t)((joy1 >> 24) & 0xFF));

    /* Triggers */
    of_input_states[0].trigger_l = trig1 & 0xFFFF;
    of_input_states[0].trigger_r = (trig1 >> 16) & 0xFFFF;

    /* Player 2 */
    uint32_t keys2 = CONT2_KEY;
    uint32_t joy2  = CONT2_JOY;
    uint32_t trig2 = CONT2_TRIG;

    of_input_states[1].buttons_pressed  = keys2 & ~prev_buttons[1];
    of_input_states[1].buttons_released = ~keys2 & prev_buttons[1];
    of_input_states[1].buttons = keys2;
    prev_buttons[1] = keys2;

    of_input_states[1].joy_lx = apply_deadzone((int8_t)(joy2 & 0xFF));
    of_input_states[1].joy_ly = apply_deadzone((int8_t)((joy2 >> 8) & 0xFF));
    of_input_states[1].joy_rx = apply_deadzone((int8_t)((joy2 >> 16) & 0xFF));
    of_input_states[1].joy_ry = apply_deadzone((int8_t)((joy2 >> 24) & 0xFF));

    of_input_states[1].trigger_l = trig2 & 0xFFFF;
    of_input_states[1].trigger_r = (trig2 >> 16) & 0xFFFF;
}

const of_input_state_t *of_input_get_state(int player) {
    if (player < 0 || player >= INPUT_MAX_PLAYERS)
        return &of_input_states[0];
    return &of_input_states[player];
}

void of_input_set_deadzone(int16_t deadzone) {
    if (deadzone < 0) deadzone = -deadzone;
    stick_deadzone = deadzone;
}
