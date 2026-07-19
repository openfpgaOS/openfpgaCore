//------------------------------------------------------------------------------
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileType: SOURCE
// SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
//------------------------------------------------------------------------------

/*
 * openfpgaOS HID Mouse Decoder
 * Slot-3 mouse contract parser shared by the per-target input HALs.
 * Callers own the concurrency model: targets that decode from IRQ
 * context wrap decode and read in their own critical sections.
 */

#include "hid_mouse.h"

void hid_mouse_init(hid_mouse_t *m, uint8_t xy_mode) {
    *m = (hid_mouse_t){0};
    m->xy_mode = xy_mode;
}

void hid_mouse_disconnect(hid_mouse_t *m) {
    m->state.present = 0;
    m->state.reserved0 = 0;
    m->state.buttons = 0;
    m->state.buttons_released |= m->prev_buttons;
    m->state.report_counter = 0;
    m->state.dx = 0;
    m->state.dy = 0;
    m->prev_buttons = 0;
    m->prev_report_counter = 0;
    m->report_valid = 0;
}

void hid_mouse_decode(hid_mouse_t *m, uint32_t key, uint32_t joy, uint32_t trig) {
    if ((key >> 28) != OF_INPUT_TYPE_MOUSE) {
        hid_mouse_disconnect(m);
        return;
    }

    uint16_t report_counter = (uint16_t)(key & 0xFFFFu);
    uint16_t buttons = (uint16_t)((joy >> 16) & 0xFFFFu);
    uint16_t x = (uint16_t)(joy & 0xFFFFu);
    uint16_t y = (uint16_t)(trig & 0xFFFFu);

    m->state.present = 1;
    m->state.reserved0 = 0;
    m->state.buttons = buttons;
    m->state.buttons_pressed  |= buttons & (uint16_t)~m->prev_buttons;
    m->state.buttons_released |= (uint16_t)~buttons & m->prev_buttons;
    m->state.report_counter = report_counter;

    /* X/Y are accepted only alongside a counter change: repeats are
     * no-ops, and in accumulator mode a transiently torn CDC read
     * self-heals because the next accepted sample is absolute. */
    if (!m->report_valid) {
        m->prev_x = x;
        m->prev_y = y;
    } else if (report_counter != m->prev_report_counter) {
        if (m->xy_mode == HID_MOUSE_XY_ACCUM) {
            m->state.dx += (int16_t)(uint16_t)(x - m->prev_x);
            m->state.dy += (int16_t)(uint16_t)(y - m->prev_y);
        } else {
            m->state.dx += (int16_t)x;
            m->state.dy += (int16_t)y;
        }
        m->prev_x = x;
        m->prev_y = y;
    }

    m->report_valid = 1;
    m->prev_report_counter = report_counter;
    m->prev_buttons = buttons;
}

void hid_mouse_read(hid_mouse_t *m, of_mouse_state_t *out) {
    if (out)
        *out = m->state;
    m->state.dx = 0;
    m->state.dy = 0;
    m->state.buttons_pressed = 0;
    m->state.buttons_released = 0;
}
