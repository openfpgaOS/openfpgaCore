//------------------------------------------------------------------------------
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileType: SOURCE
// SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
//------------------------------------------------------------------------------

/*
 * openfpgaOS Cache Management HAL
 * Cache management for DMA coherency (conflict eviction)
 */

#ifndef OFOS_CACHE_H
#define OFOS_CACHE_H

#include <stdint.h>

/* Initialize cache subsystem */
void of_cache_init(void);

/* Invalidate D-cache lines for a range (after DMA writes to memory).
 * Discards stale cached data so CPU sees fresh DMA results. */
void of_cache_inval_range(void *addr, uint32_t size);

/* Write-back D-cache lines for a range (before peripheral reads memory).
 * Ensures dirty data is visible in physical SDRAM. */
void of_cache_clean_range(void *addr, uint32_t size);

/* Write-back + invalidate D-cache lines for a range. */
void of_cache_flush_range(void *addr, uint32_t size);

/* Flush entire D-cache (expensive — prefer range-based operations). */
void of_cache_flush_dcache(void);

/* Invalidate I-cache. Required after loading code via DMA.
 * Issues fence + fence.i. */
void of_cache_invalidate_icache(void);

/* Full cache flush: D-cache writeback + I-cache invalidate.
 * Use before jumping to newly loaded code. */
void of_cache_flush(void);

/* Count of cbo ranges that fell (partly) outside the cacheable SDRAM window
 * and were clamped/skipped by the address guard.  Normally 0; a nonzero,
 * growing value means out-of-range pointers are reaching the cache ops —
 * the signature of clk_ram_controller-domain bit corruption (see cache.c).
 * Surfacing this somewhere visible (debug reg / console) flags the marginal
 * timing instead of silently masking it. */
uint32_t of_cache_guard_skips(void);

#endif /* OFOS_CACHE_H */
