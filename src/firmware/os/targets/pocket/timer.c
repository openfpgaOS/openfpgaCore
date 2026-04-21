/*
 * openfpgaOS Timer HAL Implementation
 */

#include "timer.h"
#include "file.h"
#include "regs.h"

void of_timer_init(void) {
    /* No initialization required */
}

uint32_t of_timer_get_us(void) {
    /* 100 MHz = 100 cycles per microsecond */
    return (uint32_t)(read_cycles() / (CPU_FREQ_HZ / 1000000));
}

uint32_t of_timer_get_ms(void) {
    return (uint32_t)(read_cycles() / (CPU_FREQ_HZ / 1000));
}

uint32_t of_timer_get_seconds(uint32_t *ns_out) {
    uint64_t cycles = read_cycles();
    uint32_t sec = (uint32_t)(cycles / CPU_FREQ_HZ);
    if (ns_out) {
        uint64_t rem = cycles % CPU_FREQ_HZ;
        *ns_out = (uint32_t)(rem * 10);  /* 10ns per cycle at 100MHz */
    }
    return sec;
}

/* swmixer_tick lives in the OS image; declare here so we can pump it
 * from the busy-wait without pulling all of swmixer.h into this TU. */
extern void swmixer_tick(void);

void of_timer_delay_us(uint32_t us) {
    uint64_t target = read_cycles() + (uint64_t)us * (CPU_FREQ_HZ / 1000000);

    /* usleep arrives through the ecall trap handler with mstatus.MIE=0.
     * If we spin here with IRQs masked the 1 kHz timer ISR can't fire,
     * swmixer_tick stops, and software-mixer voices freeze for the whole
     * delay — audible as voices never ending and position reads stuck at
     * 0.  Re-enabling MIE here is tempting but unsafe: start.S's trap
     * entry unconditionally resets sp to _stack_top before allocating
     * a new trap frame, so a nested IRQ would overwrite the outer
     * syscall's trap frame and corrupt the saved mepc on mret.
     *
     * Safer: pump swmixer_tick directly from the busy-wait.  The tick
     * is self-pacing — it checks the DMA ring gap at entry and
     * early-returns once the producer has caught up to ~ring-size, so
     * calling it more often than 1 kHz doesn't overproduce or advance
     * voices faster than real time. */
    const uint64_t cycles_per_ms = CPU_FREQ_HZ / 1000u;
    uint64_t next_tick = read_cycles() + cycles_per_ms;

    while (read_cycles() < target) {
        of_check_shutdown();
        if (read_cycles() >= next_tick) {
            swmixer_tick();
            next_tick += cycles_per_ms;
        }
    }
}

void of_timer_delay_ms(uint32_t ms) {
    of_timer_delay_us(ms * 1000);
}
