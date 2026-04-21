/*
 * openfpgaOS IRQ Dispatcher
 * Central interrupt handler — dispatches timer and external IRQs
 * to registered callbacks.
 */

#include "irq.h"
#include "../hal/regs.h"

/* Timer ISR callback — lives in syscall.c (handles timer_callback + sigalrm) */
extern void timer_isr_callback(void);

/* Software mixer tick — produces one 48-sample block and pushes it to
 * the audio FIFO.  Fires from the 1 kHz timer ISR.  Declared here to
 * avoid dragging all of swmixer.h into the kernel IRQ TU. */
extern void swmixer_tick(void);

/* IRQ callbacks */
static void (*external_cb)(uint32_t source);
static void (*vsync_cb)(void);
static void (*link_rx_cb)(uint32_t word);

void of_irq_init(void) {
    external_cb = 0;
    vsync_cb = 0;
    link_rx_cb = 0;
}

void of_irq_register_external(void (*cb)(uint32_t source)) {
    external_cb = cb;
}

void of_irq_register_vsync(void (*cb)(void)) {
    vsync_cb = cb;
    if (cb)
        IRQ_MASK |= IRQ_MASK_VSYNC;
    else
        IRQ_MASK &= ~IRQ_MASK_VSYNC;
}

/* Voice-end IRQ retired with the hardware mixer.  Ended voices are now
 * reported synchronously via of_mixer_poll_ended(); the registration
 * call is preserved for ABI but accepts and drops the callback. */
void of_irq_register_mixer_end(void (*cb)(uint32_t ended_mask)) {
    (void)cb;
}

void of_irq_register_link_rx(void (*cb)(uint32_t word)) {
    link_rx_cb = cb;
    if (cb)
        IRQ_MASK |= IRQ_MASK_LINK;
    else
        IRQ_MASK &= ~IRQ_MASK_LINK;
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
        /* Machine timer interrupt — clear pending, mix + run app
         * callback.  swmixer_tick writes 48 stereo pairs into the
         * SDRAM DMA ring and cbo.cleans the three cache lines; the
         * audio_dma AXI M4 master streams from there to the dcfifo. */
        TIMER_CTRL = TIMER_CTRL_ENABLE | TIMER_CTRL_W1C_IRQ;
        swmixer_tick();
        timer_isr_callback();
    }

    if (code == 11) {
        /* Machine external interrupt — UART RX, link, vsync. */
        uint32_t source = 0;

        if (UART_STATUS & UART_RX_AVAIL)
            source |= IRQ_SRC_UART_RX;

        /* Link: rx_ready flag (cleared by reading RX_DATA) */
        if (LINK_STATUS & LINK_STATUS_RX_READY)
            source |= IRQ_SRC_LINK_RX;

        /* Vsync: check pending, W1C clear */
        if (VSYNC_IRQ_PENDING) {
            VSYNC_IRQ_PENDING = 1;  /* W1C */
            source |= IRQ_SRC_VSYNC;
        }

        /* Dispatch: dedicated callbacks */
        if ((source & IRQ_SRC_LINK_RX) && link_rx_cb) {
            uint32_t word = LINK_RX_DATA;  /* read clears rx_ready + IRQ */
            link_rx_cb(word);
        }

        if ((source & IRQ_SRC_VSYNC) && vsync_cb)
            vsync_cb();

        if (external_cb)
            external_cb(source);
    }
}
