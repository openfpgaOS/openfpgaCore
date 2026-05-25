/*
 * openfpgaOS SNAC Controller HAL
 * RndMnkIII's Analogizer/SNAC pinout remains the physical adapter layer.
 * Most protocols use the raw GPIO/shifter interface; PSX can optionally use
 * the autonomous hardware poller, which exposes decoded state and edges.
 */

#ifndef OFOS_SNAC_H
#define OFOS_SNAC_H

#include <stdint.h>
#include "regs.h"

/* SNAC controller state — parsed buttons plus SDK-scale analog axes */
typedef struct {
    uint16_t buttons;       /* Pocket BTN_* format */
    uint16_t buttons_pressed;
    uint16_t buttons_released;
    int16_t  joy_lx;        /* Left stick X */
    int16_t  joy_ly;        /* Left stick Y */
    int16_t  joy_rx;        /* Right stick X (PSX analog only) */
    int16_t  joy_ry;        /* Right stick Y */
} snac_controller_t;

/* Initialize SNAC subsystem for the given controller type.
 * Sets up GPIO directions, clock divider, and mode.
 * Call after reading analogizer settings from bridge. */
void snac_init(uint8_t snac_type);

/* Poll software-driven SNAC controller(s). Hardware-polled PSX modes ignore
 * this because RTL keeps the decoded state current. Apps should use
 * of_input_poll()/of_input_state() instead of calling this directly. */
void snac_poll(void);

/* Poll only when the cached state is older than min_interval_us. */
void snac_poll_if_due(uint32_t min_interval_us);

/* Get parsed controller state for player 0 or 1 */
const snac_controller_t *snac_get_state(int player);

/* Copy parsed controller state and clear latched press/release edges.
 * Apps read through the input HAL, which calls this once per app poll so
 * short SNAC taps survive until the app consumes them. */
snac_controller_t snac_read_state(int player);

/* Acknowledge a decoded-state hardware SNAC IRQ without clearing edges. */
void snac_irq_ack(void);

/* Check if SNAC is currently active */
int snac_is_active(void);

/* ============================================================
 * Low-level shifter helpers
 * ============================================================ */

/* Start a shift transfer and wait for completion.
 * Returns the RX data (shifted-in bits). */
static inline uint32_t snac_xfer(uint32_t tx, int bits, int latch) {
    uint32_t mode = SNAC_CTRL & 0x300;

    for (volatile int i = 0; i < 200000; i++) {
        if ((SNAC_CTRL & SNAC_CTRL_BUSY) == 0)
            break;
    }

    SNAC_DATA = tx;
    SNAC_CTRL = SNAC_CTRL_START
              | (((bits - 1) & 0x1F) << SNAC_CTRL_BITCNT_SHIFT)
              | (latch ? SNAC_CTRL_LATCH : 0)
              | SNAC_CTRL_ENABLE
              | mode;

    for (volatile int i = 0; i < 4096; i++) {
        if (SNAC_CTRL & SNAC_CTRL_BUSY)
            break;
    }
    for (volatile int i = 0; i < 200000; i++) {
        if ((SNAC_CTRL & SNAC_CTRL_BUSY) == 0)
            break;
    }

    return SNAC_DATA;
}

/* Set GPIO pin output values + directions (for PC Engine or manual control) */
static inline void snac_gpio_write(uint8_t out, uint8_t dir) {
    SNAC_GPIO = ((uint32_t)dir << 8) | out;
}

/* Read GPIO pin input values */
static inline uint8_t snac_gpio_read(void) {
    return (uint8_t)(SNAC_GPIO & 0xFF);
}

#endif /* OFOS_SNAC_H */
