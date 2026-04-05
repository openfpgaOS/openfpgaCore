/*
 * openfpgaOS IRQ Dispatcher
 */

#ifndef OFOS_IRQ_H
#define OFOS_IRQ_H

#include <stdint.h>

/* External IRQ source flags (bitmask passed to external callback) */
#define IRQ_SRC_UART_RX     (1 << 0)
#define IRQ_SRC_LINK_RX     (1 << 1)
#define IRQ_SRC_MIX_VOICE   (1 << 2)

void of_irq_init(void);
void of_irq_register_external(void (*cb)(uint32_t source));

#endif /* OFOS_IRQ_H */
