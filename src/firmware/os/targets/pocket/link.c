/*
 * openfpgaOS Link Cable HAL Implementation
 * GB/GBC link cable serial via MMIO registers at 0x4D000000
 */

#include "link.h"
#include "regs.h"

/* Link register offsets (from link_mmio.v analysis) */
#define LINK_REG_STATUS     0   /* Status register */
#define LINK_REG_TX_DATA    1   /* TX data register */
#define LINK_REG_RX_DATA    2   /* RX data register */
#define LINK_REG_CONTROL    3   /* Control register */

void of_link_init(void) {
    /* Reset link state */
    LINK_REG(LINK_REG_CONTROL) = 0;
}

int of_link_send(uint32_t data) {
    uint32_t status = LINK_REG(LINK_REG_STATUS);
    if (!(status & LINK_STATUS_TX_READY))
        return -1;

    LINK_REG(LINK_REG_TX_DATA) = data;
    return 0;
}

int of_link_recv(uint32_t *data) {
    uint32_t status = LINK_REG(LINK_REG_STATUS);
    if (!(status & LINK_STATUS_RX_READY))
        return -1;

    *data = LINK_REG(LINK_REG_RX_DATA);
    return 0;
}

uint32_t of_link_get_status(void) {
    return LINK_REG(LINK_REG_STATUS);
}

int of_link_is_connected(void) {
    return (LINK_REG(LINK_REG_STATUS) & LINK_STATUS_CONNECTED) != 0;
}

void of_link_flush(void) {
    /* Wait until TX FIFO is empty */
    while (!(LINK_REG(LINK_REG_STATUS) & LINK_STATUS_TX_READY)) {}
}
