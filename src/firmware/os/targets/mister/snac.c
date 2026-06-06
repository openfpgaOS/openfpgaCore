//------------------------------------------------------------------------------
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileType: SOURCE
// SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
//------------------------------------------------------------------------------

/*
 * openfpgaOS SNAC HAL — MiSTer stub
 *
 * No SNAC adapter port on MiSTer (controllers arrive through hps_io
 * joysticks).  All entry points are inert; snac_is_active() == 0 keeps the
 * shared input paths on the plain controller registers.
 */

#include "snac.h"

void snac_init(uint8_t snac_type) {
    (void)snac_type;
}

void snac_poll(void) {}

void snac_poll_if_due(uint32_t min_interval_us) {
    (void)min_interval_us;
}

snac_controller_t snac_read_state(int player) {
    (void)player;
    return (snac_controller_t){0};
}

void snac_irq_ack(void) {}

int snac_is_active(void) {
    return 0;
}
