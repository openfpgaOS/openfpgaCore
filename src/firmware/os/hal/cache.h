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

/* DMA copy: src→dst, cache-coherent. For large SDRAM-to-SDRAM transfers.
 * Flushes D-cache before DMA to ensure src data is in SDRAM,
 * invalidates dst lines after DMA so CPU reads fresh data.
 * len must be word-aligned (multiple of 4). */
void dma_copy(void *dst, const void *src, uint32_t len);

/* DMA fill: fills dst with 32-bit value, cache-coherent.
 * len must be word-aligned. */
void dma_fill(void *dst, uint32_t value, uint32_t len);

/* Async DMA copy: starts transfer, returns immediately.
 * Caller MUST call dma_wait() before accessing dst. */
void dma_copy_async(void *dst, const void *src, uint32_t len);

/* Async DMA fill: starts transfer, returns immediately.
 * Caller MUST call dma_wait() before accessing dst. */
void dma_fill_async(void *dst, uint32_t value, uint32_t len);

#endif /* OFOS_CACHE_H */
