/* Exercise the real timer HAL at production and experimental clock rates. */
#include <assert.h>
#include <stdint.h>
#include <stdio.h>

#define OFOS_FILE_H
#define OFOS_REGS_H
static uint32_t frequency;
static uint64_t cycles;
#define CPU_FREQ_HZ frequency
static uint64_t read_cycles(void) { return cycles; }
static void of_check_shutdown(void) { ++cycles; }

#ifndef TIMER_SOURCE
#define TIMER_SOURCE "../../src/firmware/os/targets/pocket/timer.c"
#endif
#include TIMER_SOURCE

int main(void)
{
    const uint32_t rates[] = {100000000, 96000000, 90000000, 83000000};
    const uint32_t seconds[] = {0, 1, 123456, UINT32_MAX};
    for (unsigned f = 0; f < sizeof(rates) / sizeof(*rates); ++f) {
        frequency = rates[f];
        for (unsigned s = 0; s < sizeof(seconds) / sizeof(*seconds); ++s) {
            for (unsigned quarter = 0; quarter < 4; ++quarter) {
                cycles = (uint64_t)seconds[s] * frequency + quarter * (frequency / 4);
                uint32_t ns = UINT32_MAX;
                assert(of_timer_get_seconds(&ns) == seconds[s]);
                assert(ns == quarter * 250000000u);
                assert(of_timer_get_seconds(NULL) == seconds[s]);
                assert(of_timer_get_us() == (uint32_t)((uint64_t)seconds[s] * 1000000 + quarter * 250000));
                assert(of_timer_get_ms() == (uint32_t)((uint64_t)seconds[s] * 1000 + quarter * 250));
            }
        }
        cycles = frequency - 1;
        uint32_t ns;
        assert(of_timer_get_seconds(&ns) == 0);
        assert(ns == (uint32_t)(((uint64_t)frequency - 1) * 1000000000 / frequency));
        ++cycles;
        assert(of_timer_get_seconds(&ns) == 1 && ns == 0);
    }
    puts("PASS: timer seconds, fractions, rollover and runtime 83/90/96/100 MHz clocks");
    return 0;
}
