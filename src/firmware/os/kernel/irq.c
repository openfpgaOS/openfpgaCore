/*
 * openfpgaOS IRQ Dispatcher
 * Central interrupt handler — dispatches timer and external IRQs
 * to registered callbacks.
 */

#include "irq.h"
#include "../hal/regs.h"
#include "../hal/input.h"
#include "../hal/file.h"

/* Timer ISR callback — lives in syscall.c (handles timer_callback + sigalrm) */
extern void timer_isr_callback(void);
extern void of_video_vsync_irq_service(void);

/* Terminal printf — declared here to avoid dragging all of terminal.h. */
extern void of_term_printf(const char *fmt, ...);

/* IRQ callbacks */
static void (*external_cb)(uint32_t source);
static void (*vsync_cb)(void);
static void (*link_rx_cb)(uint32_t word);

void of_irq_init(void) {
    external_cb = 0;
    vsync_cb = 0;
    link_rx_cb = 0;
    IRQ_MASK = 0;
    INPUT_IRQ_MASK = 0;
    TIMER_CTRL = 0;
}

void of_irq_enable_cpu(void) {
    uint32_t mie = (1u << 7) | (1u << 11);  /* MTIE | MEIE */
    __asm__ volatile("csrw mie, %0" :: "r"(mie) : "memory");
    __asm__ volatile("csrsi mstatus, 0x8" ::: "memory");
}

void of_irq_register_external(void (*cb)(uint32_t source)) {
    external_cb = cb;
}

void of_irq_register_vsync(void (*cb)(void)) {
    vsync_cb = cb;
    IRQ_MASK |= IRQ_MASK_VSYNC;
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
        /* Machine timer interrupt — clear pending and run the app
         * callback.  The HW mixer runs autonomously off audio_output's
         * dcfifo, so no audio work happens here. */
        TIMER_CTRL = TIMER_CTRL_ENABLE | TIMER_CTRL_W1C_IRQ;

        of_check_shutdown();
        timer_isr_callback();
    }

    if (code == 11) {
        /* Machine external interrupt — UART RX, link, vsync, input,
         * data-slot completion. */
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

        if (INPUT_STATUS & INPUT_STATUS_PENDING) {
            of_input_irq_service();
            source |= IRQ_SRC_INPUT;
        }

        if (DS_STATUS & DS_STATUS_IRQ_PENDING) {
            of_file_async_irq_service();
            source |= IRQ_SRC_DATASLOT;
        }

        /* Dispatch: dedicated callbacks */
        if ((source & IRQ_SRC_LINK_RX) && link_rx_cb) {
            uint32_t word = LINK_RX_DATA;  /* read clears rx_ready + IRQ */
            link_rx_cb(word);
        }

        uint32_t dispatch_source = source;
        if (source & IRQ_SRC_VSYNC) {
            of_video_vsync_irq_service();
            of_input_vblank_service();
            if (vsync_cb)
                vsync_cb();
            else
                dispatch_source &= ~IRQ_SRC_VSYNC;
        }

        if (external_cb && dispatch_source)
            external_cb(dispatch_source);
    }
}
