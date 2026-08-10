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
 * The USB mouse rides input-hub slot 3 (INPUT_SLOT_KEY/JOY/TRIG(3)) with
 * the unified layout — KEY[31:28]=5, KEY[15:0]=report counter,
 * JOY[31:16]=buttons — except X/Y are free-running wrapping accumulators
 * the RTL encoder adds each packet's delta into, so pure polling is
 * lossless with no hub IRQ.  No probe: a bitstream without the encoder
 * reads type nibble 0 and the mouse simply stays absent.
 *
 * The USB keyboard rides input-hub slot 2 (INPUT_SLOT_KEY/JOY/TRIG(2)) as a
 * HID BOOT REPORT — KEY[31:28]=4, KEY[7:0]=modifier bitmap, JOY/TRIG=the six
 * rollover usage IDs — which is byte-for-byte the Pocket dock keyboard layout,
 * so decode_keyboard() below is the pocket decoder verbatim.  hps_keyboard.v
 * does the PS/2 set-2 → HID translation and the rollover bookkeeping in RTL,
 * because slot 2 is a shared contract carrying HID usage IDs and must not mean
 * something different per target.  No probe, same as the mouse: a bitstream
 * without the encoder reads type nibble 0 and the keyboard stays absent.
 *
 * No input hub IRQs or SNAC on MiSTer v1.
 */

#include "input.h"
#include "regs.h"
#include "hid_mouse.h"

of_input_state_t of_input_states[INPUT_MAX_PLAYERS];

static of_keyboard_state_t keyboard_state;
static hid_mouse_t hid_mouse;
static uint32_t prev_buttons[INPUT_MAX_PLAYERS];
static uint32_t prev_keyboard_keys[OF_KEYBOARD_WORDS];
static uint16_t prev_keyboard_modifiers;
static int16_t stick_deadzone = 8000;

void of_input_init(void) {
    for (int i = 0; i < INPUT_MAX_PLAYERS; i++) {
        of_input_states[i] = (of_input_state_t){0};
        prev_buttons[i] = 0;
    }
    keyboard_state = (of_keyboard_state_t){0};
    prev_keyboard_modifiers = 0;
    for (int i = 0; i < (int)OF_KEYBOARD_WORDS; i++)
        prev_keyboard_keys[i] = 0;
    hid_mouse_init(&hid_mouse, HID_MOUSE_XY_ACCUM);
}

void of_input_irq_service(void) {
    /* No input hub IRQs on MiSTer. */
}

/* Nothing here decodes from IRQ context, but the HAL permits apps to
 * read mouse state from IRQ callbacks, so the decode/consume pairs get
 * the same critical sections as the pocket implementation.  Nesting is
 * safe: the inner restore re-writes the already-cleared MIE. */
static inline uint32_t input_irq_save_local(void)
{
    uint32_t prev;
    __asm__ volatile("csrrci %0, mstatus, 0x8"
                     : "=r"(prev) :: "memory");
    return prev & 0x8u;
}

static inline void input_irq_restore_local(uint32_t prev)
{
    if (prev)
        __asm__ volatile("csrrsi zero, mstatus, 0x8" ::: "memory");
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

static inline uint8_t apf_input_type(uint32_t key) {
    return (uint8_t)(key >> 28);
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

/* Slot 2 carries a HID boot report in the Pocket dock's layout; this is the
 * pocket decoder unchanged.  See the file header for why the PS/2 → HID
 * translation happens in RTL rather than here. */
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

static void poll_keyboard_slot(void) {
    uint32_t irq = input_irq_save_local();
    decode_keyboard(INPUT_SLOT_KEY(2), INPUT_SLOT_JOY(2), INPUT_SLOT_TRIG(2));
    input_irq_restore_local(irq);
}

static void poll_mouse_slot(void) {
    uint32_t irq = input_irq_save_local();
    hid_mouse_decode(&hid_mouse, INPUT_SLOT_KEY(3), INPUT_SLOT_JOY(3),
                     INPUT_SLOT_TRIG(3));
    input_irq_restore_local(irq);
}

void of_input_poll(void) {
    fill_state(0, CONT1_KEY, CONT1_JOY, CONT1_TRIG);
    fill_state(1, CONT2_KEY, CONT2_JOY, CONT2_TRIG);
    poll_keyboard_slot();
    poll_mouse_slot();
}

void of_input_poll_p0(of_input_state_t *out) {
    fill_state(0, CONT1_KEY, CONT1_JOY, CONT1_TRIG);
    poll_keyboard_slot();
    poll_mouse_slot();
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

int of_input_is_docked(void) {
    /* MiSTer always drives an external display with detached
     * controllers -- permanently in the docked/console posture. */
    return 1;
}

void of_input_read_mouse_state(of_mouse_state_t *out) {
    /* Refresh the slot here too: with no hub IRQ the accumulators only
     * advance when someone decodes them, and pure state-getter apps may
     * never call of_input_poll().  Counter dedup keeps this idempotent
     * with the poll path. */
    uint32_t irq = input_irq_save_local();
    poll_mouse_slot();
    hid_mouse_read(&hid_mouse, out);
    input_irq_restore_local(irq);
}

void of_input_set_deadzone(int16_t deadzone) {
    if (deadzone < 0) deadzone = -deadzone;
    stick_deadzone = deadzone;
}
