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

/* blockdev_host.c — disk-image lifecycle.  Disk indices mirror hardware:
 * 0 = S0 family/legacy, 1 = S1 instance, 2 = S2 borrow. */
int  of_test_blockdev_open_disk(unsigned disk, const char *path, int read_only);
void of_test_blockdev_close_disk(unsigned disk);
/* Legacy single-image wrappers (disk 0; close drops ALL disks). */
int  of_test_blockdev_open(const char *path, int read_only);
void of_test_blockdev_close(void);
/* Model the RTL multidisk capability (HPS_STATUS bit 5): 0 = old RTL
 * (disks 1/2 never present), 1 = multidisk-capable bitstream. */
void of_test_blockdev_set_cap(int cap);

/* stub/stub.c — fake MMIO poke (used to exercise HPS register paths). */
void of_test_mmio_set(uint32_t addr, uint32_t value);

/* blockdev_host.c — observable state of the post-boot retry arming
 * (the of_file_boot_complete -> of_blockdev_enable_retries plumbing). */
extern int of_test_blockdev_retries_enabled;

#endif /* OF_TEST_SUPPORT_H */
