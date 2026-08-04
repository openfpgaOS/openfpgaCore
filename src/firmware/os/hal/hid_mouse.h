//------------------------------------------------------------------------------
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileType: SOURCE
// SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
//------------------------------------------------------------------------------

/*
 * openfpgaOS HID Mouse Decoder
 * Target-agnostic parser for the input-hub slot 3 mouse contract:
 *   KEY[31:28] = OF_INPUT_TYPE_MOUSE, KEY[15:0] = report counter,
 *   JOY[31:16] = buttons, JOY[15:0] = X, TRIG[15:0] = Y
 *   (signed 16-bit, positive Y is down per HID convention).
 * The X/Y field semantics differ per target: the Pocket APF dock packs
 * TWO consecutive samples' int8 deltas per 16-bit field ({a<<8 | b}; the
 * report's true delta is (int8)a + (int8)b -- decoding the field as one
 * int16 turns every negative low-byte sample into a phantom +256, a
 * down-right drift), MiSTer's encoder exposes free-running wrapping
 * accumulators (delta = (int16_t)(now - prev), lossless under pure
 * polling).  All modes dedup on the report counter, so decoding the same
 * register snapshot twice (IRQ path + poll fallback) is idempotent.
 */

#ifndef OFOS_HID_MOUSE_H
#define OFOS_HID_MOUSE_H

#include <stdint.h>
#include "of_input_types.h"

#define HID_MOUSE_XY_DELTA  0u  /* X/Y are int16 per-report deltas */
#define HID_MOUSE_XY_ACCUM  1u  /* X/Y are wrapping accumulators (MiSTer) */
#define HID_MOUSE_XY_PAIR8  2u  /* X/Y pack two int8 sample deltas (Pocket dock) */

/* App-visible state plus decode context.  state.dx/dy AND the
 * pressed/released edge masks accumulate across reports until a
 * consuming hid_mouse_read(), so a press+release pair that both land
 * between two app reads still surfaces both edges. */
typedef struct {
    of_mouse_state_t state;
    uint16_t prev_buttons;
    uint16_t prev_report_counter;
    uint16_t prev_x, prev_y;
    uint16_t added_x, added_y;  /* DELTA mode: payload credited for prev_report_counter */
    uint8_t  report_valid;
    uint8_t  xy_mode;
} hid_mouse_t;

void hid_mouse_init(hid_mouse_t *m, uint8_t xy_mode);

/* Decode one slot-3 register snapshot.  A non-mouse type nibble
 * disconnects, so callers feed the raw registers unconditionally. */
void hid_mouse_decode(hid_mouse_t *m, uint32_t key, uint32_t joy, uint32_t trig);

void hid_mouse_disconnect(hid_mouse_t *m);

/* Copy state and consume the accumulated deltas + button edge masks. */
void hid_mouse_read(hid_mouse_t *m, of_mouse_state_t *out);

#endif /* OFOS_HID_MOUSE_H */
