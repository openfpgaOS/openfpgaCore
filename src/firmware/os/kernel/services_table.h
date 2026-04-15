/*
 * services_table.h -- OS services table (kernel side)
 *
 * The kernel populates a static of_services_table struct in BSS at boot
 * and exposes its address via services_table_get(). The pointer is then
 * handed to apps through an auxv tag (AT_OF_SVC), so apps never compile
 * in any BRAM-specific address.
 */

#ifndef SERVICES_TABLE_H
#define SERVICES_TABLE_H

#include <stdint.h>

struct of_services_table;

void services_table_init(void);
const struct of_services_table *services_table_get(void);

/* Publish the boot-time preloaded SoundFont to apps. Called by
 * bank_preload() after a successful load; leaves the fields NULL/0
 * otherwise. */
void services_table_set_smp_bank(const void *base, uint32_t size);

#endif /* SERVICES_TABLE_H */
