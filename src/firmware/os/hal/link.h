/*
 * openfpgaOS Link Cable HAL
 * GB/GBC link cable serial communication (256 kHz, full-duplex)
 */

#ifndef OFOS_LINK_H
#define OFOS_LINK_H

#include <stdint.h>

/* Link cable status */
#define LINK_STATUS_CONNECTED   (1 << 0)
#define LINK_STATUS_TX_READY    (1 << 1)
#define LINK_STATUS_RX_READY    (1 << 2)

/* Initialize link cable subsystem */
void of_link_init(void);

/* Send a 32-bit word over the link cable.
 * Returns 0 on success, -1 if TX FIFO is full. */
int of_link_send(uint32_t data);

/* Receive a 32-bit word from the link cable.
 * Returns 0 on success and stores in *data, -1 if no data available. */
int of_link_recv(uint32_t *data);

/* Get link cable status flags */
uint32_t of_link_get_status(void);

/* Check if link cable is connected */
int of_link_is_connected(void);

/* Flush TX FIFO (wait for all pending sends to complete) */
void of_link_flush(void);

#endif /* OFOS_LINK_H */
