/*
 * openfpgaOS SNAC Controller HAL
 * Software-driven SNAC controller interface using hardware shift register.
 * Replaces ~445 ALMs of per-protocol hardware FSMs with firmware drivers.
 */

#ifndef OFOS_SNAC_H
#define OFOS_SNAC_H

#include <stdint.h>
#include "regs.h"

/* SNAC controller state — parsed buttons plus SDK-scale analog axes */
typedef struct {
    uint16_t buttons;       /* Pocket BTN_* format */
    int16_t  joy_lx;        /* Left stick X */
    int16_t  joy_ly;        /* Left stick Y */
    int16_t  joy_rx;        /* Right stick X (PSX analog only) */
    int16_t  joy_ry;        /* Right stick Y */
} snac_controller_t;

/* Initialize SNAC subsystem for the given controller type.
 * Sets up GPIO directions, clock divider, and mode.
 * Call after reading analogizer settings from bridge. */
void snac_init(uint8_t snac_type);

/* Poll the SNAC controller(s). Call once per frame.
 * Reads the hardware shift register and parses protocol data
 * into p1/p2 controller states. */
void snac_poll(void);

/* Get parsed controller state for player 0 or 1 */
const snac_controller_t *snac_get_state(int player);

/* Check if SNAC is currently active */
int snac_is_active(void);

/* ============================================================
 * Low-level shifter helpers
 * ============================================================ */

/* Start a shift transfer and wait for completion.
 * Returns the RX data (shifted-in bits). */
static inline uint32_t snac_xfer(uint32_t tx, int bits, int latch) {
    SNAC_DATA = tx;
    SNAC_CTRL = SNAC_CTRL_START
              | (((bits - 1) & 0x1F) << SNAC_CTRL_BITCNT_SHIFT)
              | (latch ? SNAC_CTRL_LATCH : 0)
              | SNAC_CTRL_ENABLE
              | (SNAC_CTRL & 0x300);  /* preserve mode bits */
    while (SNAC_CTRL & SNAC_CTRL_BUSY)
        ;
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
