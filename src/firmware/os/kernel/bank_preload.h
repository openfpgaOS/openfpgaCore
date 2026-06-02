//------------------------------------------------------------------------------
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileType: SOURCE
// SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
//------------------------------------------------------------------------------

/*
 * bank_preload.h -- Boot-time SoundFont detection + load.
 *
 * Called by kernel main() after filesystem_init(). Scans the file slot
 * registry for a *.ofsf file, loads it into the persistent audio SDRAM
 * reservation, validates the header, and prints a boot status line. The
 * preloaded bank is exposed to apps via the services table
 * (OF_SVC->smp_bank_preload_base / smp_bank_preload_size).
 */

#ifndef OFOS_BANK_PRELOAD_H
#define OFOS_BANK_PRELOAD_H

#include <stdint.h>

/* Detect and load the first *.ofsf in the file slot registry.
 * Returns 0 on success, negative if no bank was found or load failed.
 * Safe to call multiple times; subsequent calls are no-ops. */
int bank_preload(void);

#endif /* OFOS_BANK_PRELOAD_H */
