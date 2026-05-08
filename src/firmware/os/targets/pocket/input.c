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

static of_keyboard_state_t keyboard_state;
static of_mouse_state_t mouse_state;
static uint32_t prev_buttons[INPUT_MAX_PLAYERS];
static uint32_t prev_keyboard_keys[OF_KEYBOARD_WORDS];
static uint16_t prev_keyboard_modifiers;
static uint16_t prev_mouse_buttons;
static uint16_t prev_mouse_report_counter;
static int mouse_report_valid;
static int16_t stick_deadzone = 8000;
static int input_hub_present;
static volatile uint32_t input_hw_last_event_lo;
static volatile uint32_t input_hw_last_event_hi;
static volatile uint32_t input_hw_dropped_events;

static inline int16_t apply_deadzone(int16_t val) {
    return (val > -stick_deadzone && val < stick_deadzone) ? 0 : val;
}

static inline int16_t input_axis_from_u8_centered(uint8_t raw) {
    return (int16_t)(((int16_t)raw - 128) * 256);
}

static int input_probe_hub(void) {
    INPUT_IRQ_MASK = 0x5u;

    uint32_t mask = INPUT_IRQ_MASK & 0xFu;
    uint32_t info0 = INPUT_SLOT_INFO(0);
    uint32_t info1 = INPUT_SLOT_INFO(1);
    uint32_t info2 = INPUT_SLOT_INFO(2);
    uint32_t info3 = INPUT_SLOT_INFO(3);

    INPUT_IRQ_MASK = 0;

    if (mask != 0x5u)
        return 0;

    if (!(info0 & INPUT_SLOT_INFO_PRESENT) ||
        !(info1 & INPUT_SLOT_INFO_PRESENT) ||
        !(info2 & INPUT_SLOT_INFO_PRESENT) ||
        !(info3 & INPUT_SLOT_INFO_PRESENT))
        return 0;

    if (!(info0 & INPUT_SLOT_INFO_IRQ_EN) ||
         (info1 & INPUT_SLOT_INFO_IRQ_EN) ||
        !(info2 & INPUT_SLOT_INFO_IRQ_EN) ||
         (info3 & INPUT_SLOT_INFO_IRQ_EN))
        return 0;

    return 1;
}

void of_input_init(void) {
    for (int i = 0; i < INPUT_MAX_PLAYERS; i++) {
        of_input_states[i] = (of_input_state_t){0};
        prev_buttons[i] = 0;
    }
    keyboard_state = (of_keyboard_state_t){0};
    mouse_state = (of_mouse_state_t){0};
    prev_keyboard_modifiers = 0;
    prev_mouse_buttons = 0;
    prev_mouse_report_counter = 0;
    mouse_report_valid = 0;
    for (int i = 0; i < (int)OF_KEYBOARD_WORDS; i++)
        prev_keyboard_keys[i] = 0;

    /* Newer bitstreams expose an input hub at 0x40000100.  Probe the
     * coherent register set, not just one writable echo, so an app/runtime
     * mismatch keeps using the legacy CONT1/2 registers instead of a dead
     * extended page. */
    input_hub_present = input_probe_hub();
    if (input_hub_present) {
        INPUT_IRQ_MASK = 0xFu;
        INPUT_IRQ_CLEAR = INPUT_IRQ_CLEAR_FIFO | INPUT_IRQ_CLEAR_OVERFLOW;
        IRQ_MASK |= IRQ_MASK_INPUT;
    }
}

/* Read APF Pocket controller into temporary state */
static inline void read_apf(int player, uint32_t *keys, uint32_t *joy, uint32_t *trig) {
    if (input_hub_present && player >= 0 && player < 4) {
        *keys = INPUT_SLOT_KEY(player);
        *joy  = INPUT_SLOT_JOY(player);
        *trig = INPUT_SLOT_TRIG(player);
        if (player == 0 && !(*keys | *joy | *trig)) {
            uint32_t legacy_keys = CONT1_KEY;
            uint32_t legacy_joy = CONT1_JOY;
            uint32_t legacy_trig = CONT1_TRIG;
            if (legacy_keys | legacy_joy | legacy_trig) {
                *keys = legacy_keys;
                *joy = legacy_joy;
                *trig = legacy_trig;
            }
        } else if (player == 1 && !(*keys | *joy | *trig)) {
            uint32_t legacy_keys = CONT2_KEY;
            uint32_t legacy_joy = CONT2_JOY;
            uint32_t legacy_trig = CONT2_TRIG;
            if (legacy_keys | legacy_joy | legacy_trig) {
                *keys = legacy_keys;
                *joy = legacy_joy;
                *trig = legacy_trig;
            }
        }
    } else if (player == 0) {
        *keys = CONT1_KEY;
        *joy  = CONT1_JOY;
        *trig = CONT1_TRIG;
    } else {
        *keys = CONT2_KEY;
        *joy  = CONT2_JOY;
        *trig = CONT2_TRIG;
    }
}

void of_input_irq_service(void) {
    if (!input_hub_present)
        return;

    /* Drain bounded hardware records.  DATA0 is the low word; DATA1 is
     * the high word and pops the FIFO in RTL.  The current v1 pipeline
     * still computes app-visible pressed/released edges from of_input_poll()
     * so this ISR does not mutate of_input_states[]. */
    for (int i = 0; i < 64; i++) {
        uint32_t status = INPUT_STATUS;
        if (!(status & INPUT_STATUS_PENDING))
            break;

        if (status & INPUT_STATUS_FIFO_EMPTY) {
            if (status & INPUT_STATUS_OVERFLOW) {
                input_hw_dropped_events++;
                INPUT_IRQ_CLEAR = INPUT_IRQ_CLEAR_OVERFLOW;
            }
            break;
        }

        input_hw_last_event_lo = INPUT_FIFO_DATA0;
        input_hw_last_event_hi = INPUT_FIFO_DATA1;
    }
}

static inline uint8_t apf_input_type(uint32_t key) {
    return (uint8_t)(key >> 28);
}

/* Fill input state from raw values */
static void fill_state(int player, uint32_t keys, int16_t lx, int16_t ly,
                       int16_t rx, int16_t ry, uint32_t trig) {
    keys &= 0xFFFFu;
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

static void fill_apf_state(int player, uint32_t keys, uint32_t joy,
                           uint32_t trig) {
    uint8_t type = apf_input_type(keys);
    int has_analog = (type == OF_INPUT_TYPE_GAMEPAD_ANALOG) ||
                     (type == OF_INPUT_TYPE_NONE && joy != 0);

    fill_state(player, keys,
               has_analog ? input_axis_from_u8_centered(joy & 0xFF) : 0,
               has_analog ? input_axis_from_u8_centered((joy >> 8) & 0xFF) : 0,
               has_analog ? input_axis_from_u8_centered((joy >> 16) & 0xFF) : 0,
               has_analog ? input_axis_from_u8_centered((joy >> 24) & 0xFF) : 0,
               trig);
}

static int apf_has_activity(uint32_t keys, uint32_t joy, uint32_t trig) {
    return (keys & 0xFFFFu) || joy || trig;
}

static int snac_has_activity(const snac_controller_t *sc) {
    return sc->buttons || sc->joy_lx || sc->joy_ly ||
           sc->joy_rx || sc->joy_ry;
}

static int should_rescue_p1_apf(const snac_controller_t *sc,
                                uint32_t apf_keys,
                                uint32_t apf_joy,
                                uint32_t apf_trig) {
    return !snac_has_activity(sc) &&
           apf_has_activity(apf_keys, apf_joy, apf_trig);
}

static void keyboard_disconnect(void) {
    keyboard_state.present = 0;
    keyboard_state.reserved0 = 0;
    keyboard_state.modifiers = 0;
    keyboard_state.modifiers_pressed = 0;
    keyboard_state.modifiers_released = prev_keyboard_modifiers;
    prev_keyboard_modifiers = 0;

    for (int i = 0; i < (int)OF_KEYBOARD_WORDS; i++) {
        keyboard_state.keys[i] = 0;
        keyboard_state.keys_pressed[i] = 0;
        keyboard_state.keys_released[i] = prev_keyboard_keys[i];
        prev_keyboard_keys[i] = 0;
    }
    for (int i = 0; i < (int)OF_KEYBOARD_REPORT_KEYS; i++)
        keyboard_state.report_keys[i] = 0;
}

static void decode_keyboard(uint32_t key, uint32_t joy, uint32_t trig) {
    if (apf_input_type(key) != OF_INPUT_TYPE_KEYBOARD) {
        keyboard_disconnect();
        return;
    }

    uint32_t curr_keys[OF_KEYBOARD_WORDS] = {0};
    uint8_t report_keys[OF_KEYBOARD_REPORT_KEYS];

    report_keys[0] = (uint8_t)((joy >> 24) & 0xFFu);
    report_keys[1] = (uint8_t)((joy >> 16) & 0xFFu);
    report_keys[2] = (uint8_t)((joy >> 8) & 0xFFu);
    report_keys[3] = (uint8_t)(joy & 0xFFu);
    report_keys[4] = (uint8_t)((trig >> 8) & 0xFFu);
    report_keys[5] = (uint8_t)(trig & 0xFFu);

    for (int i = 0; i < (int)OF_KEYBOARD_REPORT_KEYS; i++) {
        uint8_t usage = report_keys[i];
        if (usage != 0)
            curr_keys[usage >> 5] |= 1u << (usage & 31);
        keyboard_state.report_keys[i] = usage;
    }

    uint16_t modifiers = (uint16_t)(key & 0xFFFFu);

    keyboard_state.present = 1;
    keyboard_state.reserved0 = 0;
    keyboard_state.modifiers = modifiers;
    keyboard_state.modifiers_pressed = modifiers & (uint16_t)~prev_keyboard_modifiers;
    keyboard_state.modifiers_released = (uint16_t)~modifiers & prev_keyboard_modifiers;
    prev_keyboard_modifiers = modifiers;

    for (int i = 0; i < (int)OF_KEYBOARD_WORDS; i++) {
        keyboard_state.keys[i] = curr_keys[i];
        keyboard_state.keys_pressed[i] = curr_keys[i] & ~prev_keyboard_keys[i];
        keyboard_state.keys_released[i] = ~curr_keys[i] & prev_keyboard_keys[i];
        prev_keyboard_keys[i] = curr_keys[i];
    }
}

static void mouse_disconnect(void) {
    mouse_state.present = 0;
    mouse_state.reserved0 = 0;
    mouse_state.buttons = 0;
    mouse_state.buttons_pressed = 0;
    mouse_state.buttons_released = prev_mouse_buttons;
    mouse_state.report_counter = 0;
    mouse_state.dx = 0;
    mouse_state.dy = 0;
    prev_mouse_buttons = 0;
    prev_mouse_report_counter = 0;
    mouse_report_valid = 0;
}

static void decode_mouse(uint32_t key, uint32_t joy, uint32_t trig) {
    if (apf_input_type(key) != OF_INPUT_TYPE_MOUSE) {
        mouse_disconnect();
        return;
    }

    uint16_t report_counter = (uint16_t)(key & 0xFFFFu);
    uint16_t buttons = (uint16_t)((joy >> 16) & 0xFFFFu);

    mouse_state.present = 1;
    mouse_state.reserved0 = 0;
    mouse_state.buttons = buttons;
    mouse_state.buttons_pressed = buttons & (uint16_t)~prev_mouse_buttons;
    mouse_state.buttons_released = (uint16_t)~buttons & prev_mouse_buttons;
    mouse_state.report_counter = report_counter;

    if (mouse_report_valid && report_counter != prev_mouse_report_counter) {
        mouse_state.dx += (int16_t)(joy & 0xFFFFu);
        mouse_state.dy += (int16_t)(trig & 0xFFFFu);
    }

    mouse_report_valid = 1;
    prev_mouse_report_counter = report_counter;
    prev_mouse_buttons = buttons;
}

static void poll_hid_slots(void) {
    if (!input_hub_present) {
        keyboard_disconnect();
        mouse_disconnect();
        return;
    }

    uint32_t key, joy, trig;
    read_apf(2, &key, &joy, &trig);
    decode_keyboard(key, joy, trig);

    read_apf(3, &key, &joy, &trig);
    decode_mouse(key, joy, trig);
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
            if (should_rescue_p1_apf(sc, apf1_keys, apf1_joy, apf1_trig))
                fill_apf_state(0, apf1_keys, apf1_joy, apf1_trig);
            else
                fill_state(0, sc->buttons, sc->joy_lx, sc->joy_ly,
                           sc->joy_rx, sc->joy_ry, 0);
            fill_apf_state(1, apf2_keys, apf2_joy, apf2_trig);
            break;
        case 1: /* APF→P1, SNAC→P2 */
            fill_apf_state(0, apf1_keys, apf1_joy, apf1_trig);
            fill_state(1, sc->buttons, sc->joy_lx, sc->joy_ly,
                       sc->joy_rx, sc->joy_ry, 0);
            break;
        case 2: { /* SNAC P1→P1, SNAC P2→P2 */
            const snac_controller_t *sc2 = snac_get_state(1);
            if (should_rescue_p1_apf(sc, apf1_keys, apf1_joy, apf1_trig))
                fill_apf_state(0, apf1_keys, apf1_joy, apf1_trig);
            else
                fill_state(0, sc->buttons, sc->joy_lx, sc->joy_ly,
                           sc->joy_rx, sc->joy_ry, 0);
            fill_state(1, sc2->buttons, sc2->joy_lx, sc2->joy_ly,
                       sc2->joy_rx, sc2->joy_ry, 0);
            break;
        }
        case 3: { /* SNAC P1→P2, SNAC P2→P1 */
            const snac_controller_t *sc2 = snac_get_state(1);
            if (should_rescue_p1_apf(sc2, apf1_keys, apf1_joy, apf1_trig))
                fill_apf_state(0, apf1_keys, apf1_joy, apf1_trig);
            else
                fill_state(0, sc2->buttons, sc2->joy_lx, sc2->joy_ly,
                           sc2->joy_rx, sc2->joy_ry, 0);
            fill_state(1, sc->buttons, sc->joy_lx, sc->joy_ly,
                       sc->joy_rx, sc->joy_ry, 0);
            break;
        }
        }
        poll_hid_slots();
        return;
    }

    uint32_t keys1, joy1, trig1;
    read_apf(0, &keys1, &joy1, &trig1);
    fill_apf_state(0, keys1, joy1, trig1);

    uint32_t keys2, joy2, trig2;
    read_apf(1, &keys2, &joy2, &trig2);
    fill_apf_state(1, keys2, joy2, trig2);

    poll_hid_slots();
}

void of_input_poll_p0(of_input_state_t *out) {
    if (snac_is_active()) {
        snac_poll();
        const of_analogizer_state_t *anlg = of_analogizer_get_state();
        uint8_t assignment = anlg->snac_assignment & 0x3;

        if (assignment == 1) {
            uint32_t keys, joy, trig;
            read_apf(0, &keys, &joy, &trig);
            fill_apf_state(0, keys, joy, trig);
        } else {
            const snac_controller_t *sc =
                snac_get_state((assignment == 3) ? 1 : 0);
            uint32_t keys, joy, trig;
            read_apf(0, &keys, &joy, &trig);
            if (should_rescue_p1_apf(sc, keys, joy, trig))
                fill_apf_state(0, keys, joy, trig);
            else
                fill_state(0, sc->buttons, sc->joy_lx, sc->joy_ly,
                           sc->joy_rx, sc->joy_ry, 0);
        }

        poll_hid_slots();
        *out = of_input_states[0];
        return;
    }

    uint32_t keys, joy, trig;
    read_apf(0, &keys, &joy, &trig);
    fill_apf_state(0, keys, joy, trig);

    poll_hid_slots();
    *out = of_input_states[0];
}

const of_input_state_t *of_input_get_state(int player) {
    if (player < 0 || player >= INPUT_MAX_PLAYERS)
        return &of_input_states[0];
    return &of_input_states[player];
}

const of_keyboard_state_t *of_input_get_keyboard_state(void) {
    return &keyboard_state;
}

void of_input_read_mouse_state(of_mouse_state_t *out) {
    if (out)
        *out = mouse_state;
    mouse_state.dx = 0;
    mouse_state.dy = 0;
}

void of_input_set_deadzone(int16_t deadzone) {
    if (deadzone < 0) deadzone = -deadzone;
    stick_deadzone = deadzone;
}
