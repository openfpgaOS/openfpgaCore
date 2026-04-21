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

void of_timer_delay_us(uint32_t us) {
    uint64_t target = read_cycles() + (uint64_t)us * (CPU_FREQ_HZ / 1000000);

    /* usleep arrives through the ecall trap handler, which entered with
     * mstatus.MIE=0 per RISC-V trap semantics.  If we spin here with MIE
     * still masked, the 1 kHz timer ISR can't fire → swmixer_tick never
     * runs → software-mixer voice state is frozen for the whole delay.
     * Audible as: voices never reach their end, position reads stay at
     * 0, and polyphony effectively degrades to "one voice forever".
     * Re-enable MIE for the duration of the spin, save the prior bit so
     * we restore it afterwards.  Nested IRQs are safe: the trap handler
     * re-saves mepc/mcause into a fresh frame on every entry. */
    uint32_t prev;
    __asm__ volatile("csrrsi %0, mstatus, 0x8" : "=r"(prev));

    while (read_cycles() < target) {
        of_check_shutdown();
    }

    if (!(prev & 0x8))
        __asm__ volatile("csrci mstatus, 0x8");
}

void of_timer_delay_ms(uint32_t ms) {
    of_timer_delay_us(ms * 1000);
}
