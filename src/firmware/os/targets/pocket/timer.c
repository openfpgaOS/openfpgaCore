//------------------------------------------------------------------------------
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileType: SOURCE
// SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
//------------------------------------------------------------------------------

/*
 * openfpgaOS Timer HAL Implementation
 */

#include "timer.h"
#include "file.h"
#include "regs.h"

void of_timer_init(void) {
    /* No initialization required */
}

/* Low 32 bits of (cycles / d) for a divisor below 2^16, using three 32-bit
 * hardware divides instead of the 64-bit __udivdi3 libcall.
 *
 * Schoolbook long division in 16-bit digits.  Only the low 32 quotient bits
 * are wanted, so the high word contributes just its remainder; each partial
 * dividend is (remainder << 16 | digit) < d * 2^16, which fits 32 bits and
 * yields a 16-bit quotient digit.  Exact -- bit-identical to the truncated
 * 64-bit quotient, wrap included, which callers rely on for delta maths.
 * Stateless, so it is safe from the timer ISR (the MIDI pump) and the main
 * thread at once. */
static inline uint32_t div_cycles_small(uint64_t cycles, uint32_t d) {
    uint32_t hi = (uint32_t)(cycles >> 32);
    uint32_t lo = (uint32_t)cycles;
    uint32_t r  = hi % d;
    uint32_t t1 = (r << 16) | (lo >> 16);
    uint32_t q1 = t1 / d;
    uint32_t t0 = ((t1 - q1 * d) << 16) | (lo & 0xFFFFu);
    return (q1 << 16) + t0 / d;
}

uint32_t of_timer_get_us(void) {
    /* Use the clock reported by the core, including the 90 MHz fallback. */
    uint32_t d = CPU_FREQ_HZ / 1000000;
    uint64_t cycles = read_cycles();
    if (d - 1u < 0xFFFFu)                       /* 1 <= d < 2^16 */
        return div_cycles_small(cycles, d);
    return (uint32_t)(cycles / d);
}

uint32_t of_timer_get_ms(void) {
    /* cycles-per-ms is 90000/100000 -- too wide for the 16-bit digit
     * divide, but it is a multiple of 8 for every whole-MHz clock, and
     * floor(floor(c / 8) / m) == floor(c / (8 * m)) exactly. */
    uint32_t d = CPU_FREQ_HZ / 1000;
    uint64_t cycles = read_cycles();
    if ((d & 7u) == 0 && (d >> 3) - 1u < 0xFFFFu)
        return div_cycles_small(cycles >> 3, d >> 3);
    return (uint32_t)(cycles / d);
}

uint32_t of_timer_get_seconds(uint32_t *ns_out) {
    uint64_t cycles = read_cycles();
    uint32_t sec = (uint32_t)(cycles / CPU_FREQ_HZ);
    if (ns_out) {
        uint64_t rem = cycles % CPU_FREQ_HZ;
        *ns_out = (uint32_t)((rem * 1000000000ull) / CPU_FREQ_HZ);
    }
    return sec;
}

void of_timer_delay_us(uint32_t us) {
    uint64_t target = read_cycles() + (uint64_t)us * (CPU_FREQ_HZ / 1000000);

    /* Software mixer retired in hw-mixer v2 phase 5+6 — the busy-wait no
     * longer needs to pump swmixer_tick() during delays.  The hardware
     * mixer keeps producing samples on its own FSM, independent of any
     * CPU-side tick.  Left as a plain spin with a shutdown check so the
     * Pocket can still exit the loop early on a system-reset request. */
    while (read_cycles() < target) {
        of_check_shutdown();
    }
}

void of_timer_delay_ms(uint32_t ms) {
    of_timer_delay_us(ms * 1000);
}
