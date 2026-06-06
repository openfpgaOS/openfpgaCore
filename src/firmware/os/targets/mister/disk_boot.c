//------------------------------------------------------------------------------
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileType: SOURCE
// SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
//------------------------------------------------------------------------------

/*
 * openfpgaOS Disk HAL — boot-ROM backend stub (MiSTer)
 *
 * The Pocket's boot ROM exposes a UART/PHDP disk channel for host-attached
 * development; MiSTer has no equivalent (the HPS delivers boot.rom by
 * ioctl and all file I/O goes through the mounted disk image).  Probe
 * always fails, so of_disk_init() falls through to of_disk_bridge — the
 * FatFs/sector-engine backend in file.c.
 */

#include "disk.h"

static int boot_probe(void) {
    return 0;
}

static int boot_read(uint32_t slot_id, uint32_t slot_offset,
                     void *dest, uint32_t length) {
    (void)slot_id; (void)slot_offset; (void)dest; (void)length;
    return -1;
}

static long boot_size(uint32_t slot_id) {
    (void)slot_id;
    return -1;
}

const of_disk_driver_t of_disk_boot = {
    .name  = "none",
    .probe = boot_probe,
    .read  = boot_read,
    .size  = boot_size,
};
