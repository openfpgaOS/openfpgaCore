//------------------------------------------------------------------------------
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileType: SOURCE
// SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
//------------------------------------------------------------------------------

/*
 * openfpgaOS Input HAL Implementation — MiSTer
 *
 * hps_bridge.v translates MiSTer joystick_0/1 into the APF register
 * contract the shared code expects:
 *
 *   CONT1_KEY/CONT2_KEY  [15:0] buttons in OF_BTN_* order (dpad transposed
 *                        from MiSTer's R,L,D,U to OF's U,D,L,R in RTL;
 *                        CONF_STR J1 order makes bits 4+ line up 1:1)
 *                        [31:28] type nibble = 0 (analog inferred from JOY)
 *   CONT1_JOY/CONT2_JOY  {ry,rx,ly,lx} unsigned-centered bytes (RTL XORs
 *                        MiSTer's signed axes with 0x80)
 *   CONT1_TRIG           0 (no analog triggers on MiSTer pads)
 *
 * No input hub, SNAC, dock keyboard or mouse on MiSTer v1 — the HID state
 * objects exist but always read disconnected.  USB keyboards via hps_io
 * ps2_key are a follow-up.
 */

#include "input.h"
#include "regs.h"

of_input_state_t of_input_states[INPUT_MAX_PLAYERS];

static of_keyboard_state_t keyboard_state;
static of_mouse_state_t mouse_state;
static uint32_t prev_buttons[INPUT_MAX_PLAYERS];
static int16_t stick_deadzone = 8000;

void of_input_init(void) {
    for (int i = 0; i < INPUT_MAX_PLAYERS; i++) {
        of_input_states[i] = (of_input_state_t){0};
        prev_buttons[i] = 0;
    }
    keyboard_state = (of_keyboard_state_t){0};
    mouse_state = (of_mouse_state_t){0};
}

void of_input_irq_service(void) {
    /* No input hub IRQs on MiSTer. */
}

void of_input_vblank_service(void) {
    /* No software-polled adapters on MiSTer. */
}

static inline int16_t apply_deadzone(int16_t val) {
    return (val > -stick_deadzone && val < stick_deadzone) ? 0 : val;
}

static inline int16_t input_axis_from_u8_centered(uint8_t raw) {
    return (int16_t)(((int16_t)raw - 128) * 256);
}

static void fill_state(int player, uint32_t keys, uint32_t joy, uint32_t trig) {
    uint32_t buttons = keys & 0xFFFFu;

    of_input_states[player].buttons_pressed  = buttons & ~prev_buttons[player];
    of_input_states[player].buttons_released = ~buttons & prev_buttons[player];
    of_input_states[player].buttons = buttons;
    prev_buttons[player] = buttons;

    if (joy != 0) {
        of_input_states[player].joy_lx =
            apply_deadzone(input_axis_from_u8_centered(joy & 0xFF));
        of_input_states[player].joy_ly =
            apply_deadzone(input_axis_from_u8_centered((joy >> 8) & 0xFF));
        of_input_states[player].joy_rx =
            apply_deadzone(input_axis_from_u8_centered((joy >> 16) & 0xFF));
        of_input_states[player].joy_ry =
            apply_deadzone(input_axis_from_u8_centered((joy >> 24) & 0xFF));
    } else {
        of_input_states[player].joy_lx = 0;
        of_input_states[player].joy_ly = 0;
        of_input_states[player].joy_rx = 0;
        of_input_states[player].joy_ry = 0;
    }

    of_input_states[player].trigger_l = trig & 0xFFFF;
    of_input_states[player].trigger_r = (trig >> 16) & 0xFFFF;
}

void of_input_poll(void) {
    fill_state(0, CONT1_KEY, CONT1_JOY, CONT1_TRIG);
    fill_state(1, CONT2_KEY, CONT2_JOY, CONT2_TRIG);
}

void of_input_poll_p0(of_input_state_t *out) {
    fill_state(0, CONT1_KEY, CONT1_JOY, CONT1_TRIG);
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
