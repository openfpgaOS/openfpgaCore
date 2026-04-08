/*
 * caps_table.h -- Capability descriptor (kernel side)
 *
 * The kernel populates a static of_capabilities struct in BSS at boot
 * and exposes its address via caps_table_get(). The pointer is then
 * handed to apps through an auxv tag (AT_OF_CAPS), so apps never
 * compile in any BRAM-specific address.
 */

#ifndef CAPS_TABLE_H
#define CAPS_TABLE_H

#include <stdint.h>

struct of_capabilities;

void caps_table_init(uintptr_t heap_base);
const struct of_capabilities *caps_table_get(void);

#endif /* CAPS_TABLE_H */
