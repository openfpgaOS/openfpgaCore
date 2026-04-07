/*
 * openfpgaOS Link Cable HAL (link_lite)
 * IRQ-driven serial link — 1-word TX/RX, software ring buffer in BRAM.
 */

#ifndef OFOS_LINK_H
#define OFOS_LINK_H

#include <stdint.h>

#define LINK_MODE_SLAVE   0
#define LINK_MODE_MASTER  1

/* Initialise link cable. mode: LINK_MODE_MASTER drives SCK, SLAVE listens. */
void of_link_init(int mode);

/* Shut down link cable and deregister IRQ. */
void of_link_stop(void);

/* Send a 32-bit word. Returns 0 on success, -1 if TX busy. */
int of_link_send(uint32_t word);

/* Receive a 32-bit word. Returns 0 and stores in *word, -1 if empty. */
int of_link_recv(uint32_t *word);

/* Number of words available in the RX software buffer. */
int of_link_rx_available(void);

/* 1 if TX is idle and ready for a new word. */
int of_link_tx_ready(void);

/* Register a callback for received words (called from ISR context).
 * Pass NULL to disable. Data is still buffered regardless. */
void of_link_set_recv_callback(void (*cb)(uint32_t word));

#endif /* OFOS_LINK_H */
