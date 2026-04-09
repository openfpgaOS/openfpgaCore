/*
 * shared/uart_poll.inc.c — polled UART helpers
 *
 * Text-included (NOT compiled standalone) by callers that want their
 * own static copy of uart_putc / uart_getc placed in a specific
 * runtime memory region. The includer must define SHARED_ATTR before
 * the include so each function can be tagged with the right section
 * attribute (e.g. .text.boot for the BRAM bootloader, plain .text for
 * the SDRAM kernel).
 *
 * Why polled: the bootloader runs before any IRQ controller is set
 * up, and the kernel's PHDP backend runs with mstatus.MIE masked so
 * the central UART RX dispatcher in irq.c can't drain bytes out from
 * under it. Either way, blocking on a memory-mapped status flag is
 * the only thing we can do.
 *
 * Provided by the includer:
 *   SHARED_ATTR  — section attribute (or empty)
 * Required: regs.h (UART_STATUS, UART_TX_DATA, UART_RX_DATA, UART_TX_RDY,
 * UART_RX_AVAIL).
 */

#ifndef SHARED_ATTR
#  define SHARED_ATTR
#endif

/* Push one byte. Bounded spin so a dead UART can't wedge boot.
 * ~100 µs at 100 MHz; the host's USB-serial bridge is much faster
 * than that, so the loop only ever fires its first iteration when
 * everything is healthy. */
SHARED_ATTR
static void uart_putc(uint8_t c) {
    for (int i = 0; i < 100000; i++) {
        if (UART_STATUS & UART_TX_RDY) {
            UART_TX_DATA = c;
            return;
        }
    }
}

/* Non-blocking byte read. Returns 1 with the byte in *c, or 0 if the
 * RX FIFO is empty. Reading UART_RX_DATA pops the FIFO. */
SHARED_ATTR
static int uart_getc(uint8_t *c) {
    if (UART_STATUS & UART_RX_AVAIL) {
        *c = (uint8_t)(UART_RX_DATA & 0xFF);
        return 1;
    }
    return 0;
}
