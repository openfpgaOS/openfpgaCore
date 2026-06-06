//------------------------------------------------------------------------------
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileType: SOURCE
// SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
//------------------------------------------------------------------------------

/*
 * Host-only declarations exported by the test shims (blockdev_host.c,
 * stub/stub.c) for use by test_main.c.
 */

#ifndef OF_TEST_SUPPORT_H
#define OF_TEST_SUPPORT_H

#include <stdint.h>

/* blockdev_host.c — disk-image lifecycle. */
int  of_test_blockdev_open(const char *path, int read_only);
void of_test_blockdev_close(void);

/* stub/stub.c — fake MMIO poke (used to exercise HPS register paths). */
void of_test_mmio_set(uint32_t addr, uint32_t value);

#endif /* OF_TEST_SUPPORT_H */
