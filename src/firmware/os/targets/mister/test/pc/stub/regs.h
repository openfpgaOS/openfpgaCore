//------------------------------------------------------------------------------
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileType: SOURCE
// SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
//------------------------------------------------------------------------------

/*
 * Host-test replacement for hal/regs.h.
 *
 * file.c / save.c / hps_regs.h consume only a small slice of the real
 * register header: a handful of memory-map base macros (used purely as
 * arithmetic to derive pointer constants that the *tested* code paths never
 * actually dereference on the host), the DMA chunk size, the OF_TARGET_*
 * platform constants, fence(), and REG32() — the latter only through
 * hps_regs.h for HPS_STATUS / HPS_BOOT_LEN.
 *
 * Strategy:
 *   - Pull the real targets/mister/target_platform.h (pure #defines, host
 *     safe) and api/of_caps.h for the OF_TARGET_* / OF_PLATFORM_* values, so
 *     the slot→path map, capacities and chunk sizes are byte-identical to
 *     hardware.
 *   - Provide fence() as a no-op (the rv32 "fence" asm won't assemble on x86).
 *   - Route REG32(addr) to a fake MMIO table (of_test_mmio in stub.c) so
 *     HPS_STATUS / HPS_BOOT_LEN reads are well-defined; everything defaults 0,
 *     which keeps the boot-slot (id 1) path disabled — exactly what we want,
 *     because boot_slot_read would otherwise memcpy from a target-physical
 *     address that does not exist in the host process.
 *
 * This file is FIRST on the include path, so it shadows hal/regs.h for the
 * firmware translation units pulled into the host harness.
 */

#ifndef OFOS_REGS_H
#define OFOS_REGS_H

#include <stdint.h>
#include "of_caps.h"            /* OF_PLATFORM_MISTER */
#include "target_platform.h"    /* OF_TARGET_* (host-safe, pure macros) */

/* ---- Fake MMIO ------------------------------------------------------- */
/* Returns a pointer into a host-side table keyed by the target MMIO byte
 * address.  Defined in stub.c.  Defaults to a zero cell. */
volatile uint32_t *of_test_mmio(uint32_t addr);

#define REG32(addr)     (*of_test_mmio((uint32_t)(addr)))

/* ---- Memory map (arithmetic-only; not dereferenced by tested paths) -- */
#define SDRAM_BASE          OF_TARGET_SDRAM_BASE
#define SDRAM_SIZE          OF_TARGET_SDRAM_SIZE
#define SDRAM_UNCACHED_BASE OF_TARGET_SDRAM_UNCACHED_BASE

#define CRAM0_BASE          OF_TARGET_CRAM0_BASE
#define CRAM_SIZE           OF_TARGET_CRAM_SIZE
#define CRAM0_BRIDGE        OF_TARGET_CRAM0_BRIDGE

#define DMA_CHUNK_SIZE      OF_TARGET_DMA_CHUNK_SIZE

/* ---- Inline helpers -------------------------------------------------- */
static inline void fence(void) { /* no-op on host */ }

#endif /* OFOS_REGS_H */
