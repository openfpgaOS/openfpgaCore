//------------------------------------------------------------------------------
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileType: SOURCE
// SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
//------------------------------------------------------------------------------

/*
 * openfpgaOS Input HAL Implementation
 * Reads from Pocket APF controllers and Analogizer SNAC.
 * Hardware-polled SNAC protocols latch decoded state/edges in RTL.  Older
 * software-polled SNAC protocols are refreshed opportunistically and cached
 * by the SNAC driver so games do not synchronously clock adapters in their
 * frame loop.
 */

#include "input.h"
#include "regs.h"
#include "snac.h"
#include "analogizer.h"
#include "hid_mouse.h"

/* Global state accessible via inline functions in input.h */
of_input_state_t of_input_states[INPUT_MAX_PLAYERS];

static of_keyboard_state_t keyboard_state;
static hid_mouse_t hid_mouse;
static uint32_t prev_buttons[INPUT_MAX_PLAYERS];
static uint32_t prev_keyboard_keys[OF_KEYBOARD_WORDS];
static uint16_t prev_keyboard_modifiers;
static int16_t stick_deadzone = 8000;
static int input_hub_present;
static volatile uint32_t input_hw_last_event_lo;
static volatile uint32_t input_hw_last_event_hi;
static volatile uint32_t input_hw_dropped_events;

#define SNAC_INPUT_POLL_INTERVAL_US 2000u

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

static snac_controller_t read_snac_cached(int player)
{
    snac_controller_t out;
    uint32_t irq = input_irq_save_local();
    out = snac_read_state(player);
    input_irq_restore_local(irq);
    return out;
}

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
    hid_mouse_init(&hid_mouse, HID_MOUSE_XY_DELTA);
    prev_keyboard_modifiers = 0;
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
    if (!input_hub_present) {
        snac_irq_ack();
        return;
    }

    snac_irq_ack();

    /* Drain bounded hardware records.  DATA0 is the low word; DATA1 is
     * the high word and pops the FIFO in RTL.  The current v1 pipeline
     * still computes app-visible pressed/released edges from of_input_poll()
     * so this ISR does not mutate of_input_states[]. */
    int mouse_event = 0;
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

        uint32_t lo = INPUT_FIFO_DATA0;
        uint32_t hi = INPUT_FIFO_DATA1;
        input_hw_last_event_lo = lo;
        input_hw_last_event_hi = hi;
        if (INPUT_EVENT_TYPE(hi) == INPUT_EVENT_SLOT_CHANGE &&
            INPUT_EVENT_SLOT(hi) == 3u)
            mouse_event = 1;
    }

    /* Slot-3 change: decode the mouse report here so per-report deltas
     * survive multiple hardware reports per frame.  Change records carry
     * no payload, so coalesced events yield one decode of the newest
     * report; the counter dedup keeps the poll-path fallback idempotent.
     * Runs with interrupts already disabled (trap context). */
    if (mouse_event) {
        uint32_t key, joy, trig;
        read_apf(3, &key, &joy, &trig);
        hid_mouse_decode(&hid_mouse, key, joy, trig);
    }
}

void of_input_vblank_service(void) {
    if (snac_is_active())
        snac_poll_if_due(SNAC_INPUT_POLL_INTERVAL_US);
}

static inline uint8_t apf_input_type(uint32_t key) {
    return (uint8_t)(key >> 28);
}

/* Fill input state from raw values */
static void fill_state_edges(int player, uint32_t keys, int16_t lx, int16_t ly,
                             int16_t rx, int16_t ry, uint32_t trig,
                             uint32_t extra_pressed,
                             uint32_t extra_released) {
    keys &= 0xFFFFu;
    of_input_states[player].buttons_pressed  =
        (keys & ~prev_buttons[player]) | (extra_pressed & 0xFFFFu);
    of_input_states[player].buttons_released =
        (~keys & prev_buttons[player]) | (extra_released & 0xFFFFu);
    of_input_states[player].buttons = keys;
    prev_buttons[player] = keys;

    of_input_states[player].joy_lx = apply_deadzone(lx);
    of_input_states[player].joy_ly = apply_deadzone(ly);
    of_input_states[player].joy_rx = apply_deadzone(rx);
    of_input_states[player].joy_ry = apply_deadzone(ry);
    of_input_states[player].trigger_l = trig & 0xFFFF;
    of_input_states[player].trigger_r = (trig >> 16) & 0xFFFF;
}

static void fill_state(int player, uint32_t keys, int16_t lx, int16_t ly,
                       int16_t rx, int16_t ry, uint32_t trig) {
    fill_state_edges(player, keys, lx, ly, rx, ry, trig, 0, 0);
}

typedef struct {
    int16_t lx, ly;
    int16_t rx, ry;
} input_axes_t;

static input_axes_t apf_axes_from_raw(uint32_t keys, uint32_t joy) {
    uint8_t type = apf_input_type(keys);
    int has_analog = (type == OF_INPUT_TYPE_GAMEPAD_ANALOG) ||
                     (type == OF_INPUT_TYPE_NONE && joy != 0);

    if (!has_analog)
        return (input_axes_t){0};

    return (input_axes_t){
        .lx = input_axis_from_u8_centered(joy & 0xFF),
        .ly = input_axis_from_u8_centered((joy >> 8) & 0xFF),
        .rx = input_axis_from_u8_centered((joy >> 16) & 0xFF),
        .ry = input_axis_from_u8_centered((joy >> 24) & 0xFF),
    };
}

static void fill_apf_state(int player, uint32_t keys, uint32_t joy,
                           uint32_t trig) {
    input_axes_t axes = apf_axes_from_raw(keys, joy);
    fill_state(player, keys, axes.lx, axes.ly, axes.rx, axes.ry, trig);
}

static inline int axis_active(int16_t axis) {
    return apply_deadzone(axis) != 0;
}

static void fill_apf_snac_state(int player, uint32_t keys, uint32_t joy,
                                uint32_t trig,
                                const snac_controller_t *sc) {
    input_axes_t axes = apf_axes_from_raw(keys, joy);
    uint32_t buttons = keys & 0xFFFFu;

    if (sc) {
        buttons |= sc->buttons;
        if (axis_active(sc->joy_lx)) axes.lx = sc->joy_lx;
        if (axis_active(sc->joy_ly)) axes.ly = sc->joy_ly;
        if (axis_active(sc->joy_rx)) axes.rx = sc->joy_rx;
        if (axis_active(sc->joy_ry)) axes.ry = sc->joy_ry;
    }

    fill_state_edges(player, buttons, axes.lx, axes.ly, axes.rx, axes.ry, trig,
                     sc ? sc->buttons_pressed : 0,
                     sc ? sc->buttons_released : 0);
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

static void poll_hid_slots(void) {
    if (!input_hub_present) {
        keyboard_disconnect();
        hid_mouse_disconnect(&hid_mouse);
        return;
    }

    uint32_t key, joy, trig;
    read_apf(2, &key, &joy, &trig);
    decode_keyboard(key, joy, trig);

    /* Poll-path fallback for the IRQ decode.  The register read and the
     * decode must share one critical section: an IRQ decode of a newer
     * report in between would let this stale snapshot re-pass the
     * counter dedup and double-count its delta. */
    uint32_t irq = input_irq_save_local();
    read_apf(3, &key, &joy, &trig);
    hid_mouse_decode(&hid_mouse, key, joy, trig);
    input_irq_restore_local(irq);
}

void of_input_poll(void) {
    of_analogizer_refresh();

    if (snac_is_active()) {
        /* Keep Pocket/Dock APF input live, then overlay SNAC on the
         * configured player slot.  This allows built-in controls and
         * dock controllers to remain usable while Analogizer/SNAC is on. */
        snac_poll_if_due(SNAC_INPUT_POLL_INTERVAL_US);
        snac_controller_t sc0 = read_snac_cached(0);

        const of_analogizer_state_t *anlg = of_analogizer_get_state();
        uint8_t assignment = anlg->snac_assignment & 0x3;

        uint32_t apf1_keys, apf1_joy, apf1_trig;
        uint32_t apf2_keys, apf2_joy, apf2_trig;
        read_apf(0, &apf1_keys, &apf1_joy, &apf1_trig);
        read_apf(1, &apf2_keys, &apf2_joy, &apf2_trig);

        switch (assignment) {
        default:
        case 0: /* SNAC->P1, APF P1/P2 remain active */
            fill_apf_snac_state(0, apf1_keys, apf1_joy, apf1_trig, &sc0);
            fill_apf_state(1, apf2_keys, apf2_joy, apf2_trig);
            break;
        case 1: /* SNAC->P2, APF P1/P2 remain active */
            fill_apf_state(0, apf1_keys, apf1_joy, apf1_trig);
            fill_apf_snac_state(1, apf2_keys, apf2_joy, apf2_trig, &sc0);
            break;
        case 2: { /* SNAC P1->P1, SNAC P2->P2; APF remains active */
            snac_controller_t sc1 = read_snac_cached(1);
            fill_apf_snac_state(0, apf1_keys, apf1_joy, apf1_trig, &sc0);
            fill_apf_snac_state(1, apf2_keys, apf2_joy, apf2_trig, &sc1);
            break;
        }
        case 3: { /* SNAC P1->P2, SNAC P2->P1; APF remains active */
            snac_controller_t sc1 = read_snac_cached(1);
            fill_apf_snac_state(0, apf1_keys, apf1_joy, apf1_trig, &sc1);
            fill_apf_snac_state(1, apf2_keys, apf2_joy, apf2_trig, &sc0);
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
    of_analogizer_refresh();

    if (snac_is_active()) {
        snac_poll_if_due(SNAC_INPUT_POLL_INTERVAL_US);
        const of_analogizer_state_t *anlg = of_analogizer_get_state();
        uint8_t assignment = anlg->snac_assignment & 0x3;
        uint32_t keys, joy, trig;
        read_apf(0, &keys, &joy, &trig);

        if (assignment == 1) {
            fill_apf_state(0, keys, joy, trig);
        } else {
            snac_controller_t sc = read_snac_cached((assignment == 3) ? 1 : 0);
            fill_apf_snac_state(0, keys, joy, trig, &sc);
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

int of_input_is_docked(void) {
    /* Slot 1's built-in-buttons type is only reportable handheld; docked,
     * the bridge reports the paired controller type or 0x0 when nothing
     * is paired (same invariant video.c's vrr_fixed_rate_sink relies on).
     * read_apf covers both the input-hub and legacy CONT1 register paths. */
    uint32_t keys, joy, trig;
    read_apf(0, &keys, &joy, &trig);
    return apf_input_type(keys) != OF_INPUT_TYPE_POCKET;
}

void of_input_read_mouse_state(of_mouse_state_t *out) {
    uint32_t irq = input_irq_save_local();
    hid_mouse_read(&hid_mouse, out);
    input_irq_restore_local(irq);
}

void of_input_set_deadzone(int16_t deadzone) {
    if (deadzone < 0) deadzone = -deadzone;
    stick_deadzone = deadzone;
}
