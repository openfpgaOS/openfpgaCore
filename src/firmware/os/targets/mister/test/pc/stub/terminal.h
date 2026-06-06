//------------------------------------------------------------------------------
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileType: SOURCE
// SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
//------------------------------------------------------------------------------

/*
 * Host-test replacement for hal/terminal.h.
 *
 * file.c / save.c / (host) blockdev only use of_term_printf for diagnostics.
 * Map it to a printf-style wrapper implemented in stub.c so the firmware's
 * printf-format attribute keeps catching mistakes, while output can be
 * silenced via the OF_TEST_VERBOSE switch.  The remaining terminal entry
 * points are declared as no-op-able shims for completeness but are unused.
 */

#ifndef OFOS_TERMINAL_H
#define OFOS_TERMINAL_H

#include <stdint.h>

void of_term_printf(const char *fmt, ...) __attribute__((format(printf, 1, 2)));

/* Unused by the FAT stack under test, but declared so any incidental
 * reference still links. */
static inline void of_term_init(void) {}
static inline void of_term_putchar(char c) { (void)c; }
static inline void of_term_puts(const char *s) { (void)s; }

#endif /* OFOS_TERMINAL_H */
