//------------------------------------------------------------------------------
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileType: SOURCE
// SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
//------------------------------------------------------------------------------

/*
 * link.c — IRQ-driven link cable driver for link_lite hardware.
 *
 * Software ring buffer replaces the old hardware FIFOs.
 * ISR is in BRAM (OF_FASTTEXT) for zero cache thrashing.
 */

#include "../hal/regs.h"
#include "../kernel/irq.h"
#include <stdint.h>

#define LINK_RX_BUF_SIZE  256   /* must be power of 2 */
#define LINK_RX_BUF_MASK  (LINK_RX_BUF_SIZE - 1)

/* Software RX ring buffer — lives in BRAM for fast ISR access */
static uint32_t __attribute__((section(".app_fastdata")))
    rx_buf[LINK_RX_BUF_SIZE];
static volatile uint32_t __attribute__((section(".app_fastdata")))
    rx_head;  /* written by ISR */
static volatile uint32_t __attribute__((section(".app_fastdata")))
    rx_tail;  /* read by main thread */

/* TX state */
static volatile int tx_busy;

/* App callback — called from ISR context on each received word.
 * NULL = no callback, app polls of_link_recv() instead. */
static void (*app_rx_cb)(uint32_t word);

/* ---- ISR (runs in BRAM, no cache pressure) ---- */

__attribute__((section(".app_fasttext")))
static void link_rx_isr(uint32_t word) {
    uint32_t next = (rx_head + 1) & LINK_RX_BUF_MASK;
    if (next != rx_tail) {
        rx_buf[rx_head] = word;
        rx_head = next;
    }
    tx_busy = 0;
    if (app_rx_cb)
        app_rx_cb(word);
}

/* ---- Public API ---- */

void of_link_init(int master) {
    rx_head = 0;
    rx_tail = 0;
    tx_busy = 0;
    app_rx_cb = 0;

    LINK_CTRL = 2;  /* reset */
    LINK_CTRL = (master ? 5 : 1);  /* enable + master bit */

    of_irq_register_link_rx(link_rx_isr);
}

int of_link_send(uint32_t word) {
    if (tx_busy) return -1;
    tx_busy = 1;
    LINK_TX_DATA = word;
    return 0;
}

int of_link_recv(uint32_t *word) {
    if (rx_head == rx_tail) return -1;
    *word = rx_buf[rx_tail];
    rx_tail = (rx_tail + 1) & LINK_RX_BUF_MASK;
    return 0;
}

int of_link_rx_available(void) {
    return (rx_head - rx_tail) & LINK_RX_BUF_MASK;
}
