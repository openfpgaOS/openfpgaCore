/*
 * bank_preload.h -- Boot-time SoundFont detection + load.
 *
 * Called by kernel main() after filesystem_init(). Scans the file slot
 * registry for a *.ofsf file, loads it into the persistent audio SDRAM
 * reservation, validates the header, and prints a boot status line. The
 * loaded buffer is exposed via bank_preload_base/size so apps can skip
 * re-loading.
 */

#ifndef OFOS_BANK_PRELOAD_H
#define OFOS_BANK_PRELOAD_H

#include <stdint.h>

/* Detect and load the first *.ofsf in the file slot registry.
 * Returns 0 on success, negative if no bank was found or load failed.
 * Safe to call multiple times; subsequent calls are no-ops. */
int bank_preload(void);

/* Pointer to the preloaded bank in SDRAM, or NULL if none. */
const void *bank_preload_base(void);

/* Byte size of the preloaded bank, or 0. */
uint32_t bank_preload_size(void);

#endif /* OFOS_BANK_PRELOAD_H */
