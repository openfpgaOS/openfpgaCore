//------------------------------------------------------------------------------
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileType: SOURCE
// SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
//------------------------------------------------------------------------------

/*
 * memtest.h -- boot-time memory integrity check
 *
 * Catches the physical-layer core/os mismatches that no register
 * handshake can: SDRAM/CRAM controller interleave changes, DDIO DQ
 * capture-timing regressions, and timing-marginal cores.  All of those
 * present as scrambled memory (garbled console text, apps trapping on
 * every instruction) with no other diagnostic.
 */

#ifndef OF_HAL_MEMTEST_H
#define OF_HAL_MEMTEST_H

#include <stdint.h>

/* Run the boot memory check.  Returns 0 on pass; on failure prints one
 * detail line (region, address, wrote/got) via of_term and returns -1.
 * Call between of_init_early() and of_init_late() (after the contract
 * handshake): it writes to the app SDRAM window and the CRAM0 DMA
 * staging windows, which carry no traffic only at that point. */
int of_memtest_boot(void);

#endif /* OF_HAL_MEMTEST_H */
