//------------------------------------------------------------------------------
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileType: SOURCE
// SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
//------------------------------------------------------------------------------

/*
 * openfpgaOS Timer HAL
 * Hardware cycle counter based timing
 */

#ifndef OFOS_TIMER_H
#define OFOS_TIMER_H

#include <stdint.h>

/* Initialize timer subsystem */
void of_timer_init(void);

/* Get current time in microseconds (wraps at ~4295 seconds for 32-bit) */
uint32_t of_timer_get_us(void);

/* Get current time in milliseconds */
uint32_t of_timer_get_ms(void);

/* Get seconds since boot (with fractional nanoseconds in *ns_out if non-NULL) */
uint32_t of_timer_get_seconds(uint32_t *ns_out);

/* Busy-wait for the given number of microseconds */
void of_timer_delay_us(uint32_t us);

/* Busy-wait for the given number of milliseconds */
void of_timer_delay_ms(uint32_t ms);

#endif /* OFOS_TIMER_H */
