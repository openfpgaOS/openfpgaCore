//------------------------------------------------------------------------------
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileType: SOURCE
// SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
//------------------------------------------------------------------------------

/*
 * Host-test stubs: fake MMIO table + terminal printf.
 *
 * of_test_mmio() backs REG32(addr) with a tiny fixed table.  Only the HPS
 * status block (HPS_STATUS @ 0x49000000, HPS_BOOT_LEN @ 0x4900000C) is read
 * by the FAT stack under test, and only on the boot-slot (id 1) path.  We
 * deliberately leave HPS_STATUS_BOOT_LOADED clear so boot_slot_read() bails
 * with OF_ERR_NOT_SUPPORTED instead of memcpy'ing from a target-physical
 * address that does not map in the host process.  All cells default to 0, and
 * unknown addresses share a single scratch cell so stray writes are harmless.
 */

#include <stdint.h>
#include <stdio.h>
#include <stdarg.h>
#include <stdlib.h>

/* ---- Fake MMIO ------------------------------------------------------- */

#define MMIO_CELLS 32

static struct {
    uint32_t addr;
    uint32_t value;
    int      used;
} mmio[MMIO_CELLS];

static uint32_t mmio_scratch;   /* shared sink for overflow addresses */

volatile uint32_t *of_test_mmio(uint32_t addr) {
    for (int i = 0; i < MMIO_CELLS; i++) {
        if (mmio[i].used && mmio[i].addr == addr)
            return &mmio[i].value;
    }
    for (int i = 0; i < MMIO_CELLS; i++) {
        if (!mmio[i].used) {
            mmio[i].used  = 1;
            mmio[i].addr  = addr;
            mmio[i].value = 0;
            return &mmio[i].value;
        }
    }
    return &mmio_scratch;
}

/* Test-side accessor so the harness can poke HPS registers if it ever wants
 * to exercise the boot-slot path explicitly. */
void of_test_mmio_set(uint32_t addr, uint32_t value) {
    *of_test_mmio(addr) = value;
}

/* ---- Terminal printf ------------------------------------------------- */
/* Silent by default; set OF_TEST_VERBOSE=1 in the environment to see the
 * firmware's diagnostic chatter (FAT mount failures etc.). */

void of_term_printf(const char *fmt, ...) {
    static int checked, verbose;
    if (!checked) {
        const char *v = getenv("OF_TEST_VERBOSE");
        verbose = (v && v[0] && v[0] != '0');
        checked = 1;
    }
    if (!verbose)
        return;
    va_list ap;
    va_start(ap, fmt);
    vfprintf(stderr, fmt, ap);
    va_end(ap);
}
