//------------------------------------------------------------------------------
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileType: SOURCE
// SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
//------------------------------------------------------------------------------

/*
 * Host-test replacement for hal/cache.h.
 *
 * The host has a coherent flat address space, so every cache-maintenance
 * primitive collapses to a no-op.  Signatures mirror hal/cache.h exactly so
 * the firmware translation units type-check unchanged.  (file.c/save.c only
 * reference these transitively; blockdev.c — which uses them directly — is
 * replaced by blockdev_host.c on the host, but the no-ops keep the header
 * usable everywhere.)
 */

#ifndef OFOS_CACHE_H
#define OFOS_CACHE_H

#include <stdint.h>

static inline void of_cache_init(void) {}
static inline void of_cache_inval_range(void *addr, uint32_t size) { (void)addr; (void)size; }
static inline void of_cache_clean_range(void *addr, uint32_t size) { (void)addr; (void)size; }
static inline void of_cache_flush_range(void *addr, uint32_t size) { (void)addr; (void)size; }
static inline void of_cache_flush_dcache(void) {}
static inline void of_cache_invalidate_icache(void) {}
static inline void of_cache_flush(void) {}
static inline uint32_t of_cache_guard_skips(void) { return 0; }

#endif /* OFOS_CACHE_H */
