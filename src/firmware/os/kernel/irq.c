/*
 * openfpgaOS IRQ Dispatcher
 * Central interrupt handler — dispatches timer and external IRQs
 * to registered callbacks.
 */

#include "irq.h"
#include "../hal/regs.h"

/* Timer ISR callback — lives in syscall.c (handles timer_callback + sigalrm) */
extern void timer_isr_callback(void);

/* External IRQ callback */
static void (*external_cb)(uint32_t source);

void of_irq_init(void) {
    external_cb = 0;
}

void of_irq_register_external(void (*cb)(uint32_t source)) {
    external_cb = cb;
}

/*
 * irq_handler — called from assembly trap entry for all hardware interrupts.
 * Dispatches based on mcause code: 7 = machine timer, 11 = machine external.
 */
void irq_handler(void *frame) {
    uint32_t *f = (uint32_t *)frame;
    uint32_t mcause = f[33];  /* offset 132 / 4 */
    uint32_t code = mcause & 0x7FFFFFFF;

    if (code == 7) {
        /* Machine timer interrupt — clear pending, call callback */
        TIMER_CTRL = TIMER_CTRL_ENABLE | TIMER_CTRL_W1C_IRQ;
        timer_isr_callback();
    }

    if (code == 11) {
        /* Machine external interrupt — link cable, UART RX, mixer voice-end */
        if (external_cb) {
            /* Build source bitmask for the callback */
            uint32_t source = 0;
            /* UART RX: check if FIFO has data */
            if (UART_STATUS & UART_RX_AVAIL)
                source |= IRQ_SRC_UART_RX;
            /* Link and mixer sources can be added as needed */
            external_cb(source);
        }
    }
}
