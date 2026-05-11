/*
 * openfpgaOS IRQ Dispatcher
 */

#ifndef OFOS_IRQ_H
#define OFOS_IRQ_H

#include <stdint.h>

/* External IRQ source flags (bitmask passed to external callback).
 * Bit 2 (MIX_VOICE) was retired along with the hardware mixer; kept
 * reserved so existing bit positions are stable. */
#define IRQ_SRC_UART_RX     (1 << 0)
#define IRQ_SRC_LINK_RX     (1 << 1)
#define IRQ_SRC_VSYNC       (1 << 3)
#define IRQ_SRC_INPUT       (1 << 4)
#define IRQ_SRC_DATASLOT    (1 << 5)

void of_irq_init(void);
void of_irq_register_external(void (*cb)(uint32_t source));
void of_irq_register_vsync(void (*cb)(void));
void of_irq_register_mixer_end(void (*cb)(uint32_t ended_mask));
void of_irq_register_link_rx(void (*cb)(uint32_t word));

#endif /* OFOS_IRQ_H */
