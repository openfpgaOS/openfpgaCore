//------------------------------------------------------------------------------
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileType: SOURCE
// SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
//------------------------------------------------------------------------------

/*
 * openfpgaOS Input HAL
 * Unified controller abstraction for Pocket, Dock, and Analogizer (SNAC).
 *
 * The OF_BTN_* button bitmasks and the of_input_state_t struct are
 * defined in api/of_input.h -- the SDK header is the canonical source
 * for both. Per-target HAL implementations are responsible for
 * translating native register layouts into this format. On Pocket the
 * low 16 APF button bits match OF_BTN_* bit-for-bit; APF report type
 * metadata and dock keyboard/mouse reports are decoded separately.
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

/* Service input-hub IRQs.  Drains raw hardware change records; target
 * implementations may use this to feed a future event queue. */
void of_input_irq_service(void);

/* Service input sources sampled from display IRQ context.  On Pocket this
 * opportunistically refreshes software-driven SNAC protocols; autonomous
 * hardware-polled SNAC protocols raise input IRQs instead. */
void of_input_vblank_service(void);

/* Get current state for a player (0 or 1) */
const of_input_state_t *of_input_get_state(int player);

/* Get current dock keyboard state.  Keyboard reports use USB HID usage IDs. */
const of_keyboard_state_t *of_input_get_keyboard_state(void);

/* Read mouse state and consume the relative movement and button edge
 * masks accumulated since the previous read.  buttons itself remains
 * level-based like controller state. */
void of_input_read_mouse_state(of_mouse_state_t *out);

/* Nonzero while running on an external display with detached controllers.
 * Pocket: the APF cont1 report type -- built-in buttons (0x1) are only
 * reportable handheld; docked, slot 1 carries the paired controller type
 * (or 0x0 with nothing paired).  MiSTer: always 1.  Live state. */
int of_input_is_docked(void);

/* Single-player fast path: poll hardware + return P0 state in one call */
void of_input_poll_p0(of_input_state_t *out);

/* Set analog stick dead zone (default 8000).
 * Stick values with absolute value below this threshold are zeroed. */
void of_input_set_deadzone(int16_t deadzone);

/* Check if a button was just pressed this frame */
static inline int of_input_is_pressed(int player, uint32_t mask) {
    extern of_input_state_t of_input_states[INPUT_MAX_PLAYERS];
    return (of_input_states[player].buttons_pressed & mask) != 0;
}

#endif /* OFOS_INPUT_H */
