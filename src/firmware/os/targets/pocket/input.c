/*
 * openfpgaOS Input HAL Implementation
 * Reads from Pocket APF controllers and Analogizer SNAC.
 * When SNAC is active, polls the software SNAC driver and merges
 * the result into the input state per the assignment mapping.
 */

#include "input.h"
#include "regs.h"
#include "snac.h"
#include "analogizer.h"

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

/* Read APF Pocket controller into temporary state */
static inline void read_apf(int player, uint32_t *keys, uint32_t *joy, uint32_t *trig) {
    if (player == 0) {
        *keys = CONT1_KEY;
        *joy  = CONT1_JOY;
        *trig = CONT1_TRIG;
    } else {
        *keys = CONT2_KEY;
        *joy  = CONT2_JOY;
        *trig = CONT2_TRIG;
    }
}

/* Fill input state from raw values */
static void fill_state(int player, uint32_t keys, int8_t lx, int8_t ly,
                        int8_t rx, int8_t ry, uint32_t trig) {
    of_input_states[player].buttons_pressed  = keys & ~prev_buttons[player];
    of_input_states[player].buttons_released = ~keys & prev_buttons[player];
    of_input_states[player].buttons = keys;
    prev_buttons[player] = keys;

    of_input_states[player].joy_lx = apply_deadzone(lx);
    of_input_states[player].joy_ly = apply_deadzone(ly);
    of_input_states[player].joy_rx = apply_deadzone(rx);
    of_input_states[player].joy_ry = apply_deadzone(ry);
    of_input_states[player].trigger_l = trig & 0xFFFF;
    of_input_states[player].trigger_r = (trig >> 16) & 0xFFFF;
}

void of_input_poll(void) {
    if (snac_is_active()) {
        /* Poll SNAC hardware shift register */
        snac_poll();
        const snac_controller_t *sc = snac_get_state(0);

        const of_analogizer_state_t *anlg = of_analogizer_get_state();
        uint8_t assignment = anlg->snac_assignment & 0x3;

        uint32_t apf1_keys, apf1_joy, apf1_trig;
        uint32_t apf2_keys, apf2_joy, apf2_trig;
        read_apf(0, &apf1_keys, &apf1_joy, &apf1_trig);
        read_apf(1, &apf2_keys, &apf2_joy, &apf2_trig);

        switch (assignment) {
        case 0: /* SNAC→P1, APF→P2 */
            fill_state(0, sc->buttons, sc->joy_lx, sc->joy_ly,
                       sc->joy_rx, sc->joy_ry, 0);
            fill_state(1, apf2_keys,
                       (int8_t)(apf2_joy & 0xFF),
                       (int8_t)((apf2_joy >> 8) & 0xFF),
                       (int8_t)((apf2_joy >> 16) & 0xFF),
                       (int8_t)((apf2_joy >> 24) & 0xFF),
                       apf2_trig);
            break;
        case 1: /* APF→P1, SNAC→P2 */
            fill_state(0, apf1_keys,
                       (int8_t)(apf1_joy & 0xFF),
                       (int8_t)((apf1_joy >> 8) & 0xFF),
                       (int8_t)((apf1_joy >> 16) & 0xFF),
                       (int8_t)((apf1_joy >> 24) & 0xFF),
                       apf1_trig);
            fill_state(1, sc->buttons, sc->joy_lx, sc->joy_ly,
                       sc->joy_rx, sc->joy_ry, 0);
            break;
        case 2: { /* SNAC P1→P1, SNAC P2→P2 */
            const snac_controller_t *sc2 = snac_get_state(1);
            fill_state(0, sc->buttons, sc->joy_lx, sc->joy_ly,
                       sc->joy_rx, sc->joy_ry, 0);
            fill_state(1, sc2->buttons, sc2->joy_lx, sc2->joy_ly,
                       sc2->joy_rx, sc2->joy_ry, 0);
            break;
        }
        case 3: { /* SNAC P1→P2, SNAC P2→P1 */
            const snac_controller_t *sc2 = snac_get_state(1);
            fill_state(0, sc2->buttons, sc2->joy_lx, sc2->joy_ly,
                       sc2->joy_rx, sc2->joy_ry, 0);
            fill_state(1, sc->buttons, sc->joy_lx, sc->joy_ly,
                       sc->joy_rx, sc->joy_ry, 0);
            break;
        }
        }
        return;
    }

    /* Standard path: read APF Pocket controllers directly */
    /* Player 1 */
    uint32_t keys1 = CONT1_KEY;
    uint32_t joy1  = CONT1_JOY;
    uint32_t trig1 = CONT1_TRIG;

    of_input_states[0].buttons_pressed  = keys1 & ~prev_buttons[0];
    of_input_states[0].buttons_released = ~keys1 & prev_buttons[0];
    of_input_states[0].buttons = keys1;
    prev_buttons[0] = keys1;

    of_input_states[0].joy_lx = apply_deadzone((int8_t)(joy1 & 0xFF));
    of_input_states[0].joy_ly = apply_deadzone((int8_t)((joy1 >> 8) & 0xFF));
    of_input_states[0].joy_rx = apply_deadzone((int8_t)((joy1 >> 16) & 0xFF));
    of_input_states[0].joy_ry = apply_deadzone((int8_t)((joy1 >> 24) & 0xFF));

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

void of_input_poll_p0(of_input_state_t *out) {
    if (snac_is_active()) {
        snac_poll();
        const snac_controller_t *sc = snac_get_state(0);
        fill_state(0, sc->buttons, sc->joy_lx, sc->joy_ly,
                   sc->joy_rx, sc->joy_ry, 0);
        *out = of_input_states[0];
        return;
    }

    uint32_t keys = CONT1_KEY;
    uint32_t joy  = CONT1_JOY;
    uint32_t trig = CONT1_TRIG;

    of_input_states[0].buttons_pressed  = keys & ~prev_buttons[0];
    of_input_states[0].buttons_released = ~keys & prev_buttons[0];
    of_input_states[0].buttons = keys;
    prev_buttons[0] = keys;

    of_input_states[0].joy_lx = apply_deadzone((int8_t)(joy & 0xFF));
    of_input_states[0].joy_ly = apply_deadzone((int8_t)((joy >> 8) & 0xFF));
    of_input_states[0].joy_rx = apply_deadzone((int8_t)((joy >> 16) & 0xFF));
    of_input_states[0].joy_ry = apply_deadzone((int8_t)((joy >> 24) & 0xFF));

    of_input_states[0].trigger_l = trig & 0xFFFF;
    of_input_states[0].trigger_r = (trig >> 16) & 0xFFFF;

    *out = of_input_states[0];
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
