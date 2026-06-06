//------------------------------------------------------------------------------
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileType: SOURCE
// SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
//------------------------------------------------------------------------------

/*
 * MiSTer block-device layer — 512-byte sector access to the OSD-mounted
 * disk image through the hps_bridge sector engine.
 *
 * This is the single owner of the DS_* data-slot registers on MiSTer
 * (there is no APF bridge).  FatFs's diskio glue lives in blockdev.c;
 * file.c and save.c sit above it through ff.h.
 *
 * On the PC test harness (OF_PC_TEST) the same interface is provided by
 * a pread/pwrite file-backed shim instead — see test/pc/blockdev_host.c.
 */

#ifndef OFOS_MISTER_BLOCKDEV_H
#define OFOS_MISTER_BLOCKDEV_H

#include <stdint.h>

/* Reset internal state; safe to call before the image is mounted. */
void of_blockdev_init(void);

/* 1 if a disk image is currently mounted (live HPS status). */
int of_blockdev_present(void);

/* 1 if the image is mounted read-only. */
int of_blockdev_readonly(void);

/* Mounted image size in bytes (0 when not mounted). */
uint64_t of_blockdev_size(void);

/* Raw sector access.  count sectors of 512 bytes; buf may be anywhere in
 * SDRAM/BRAM — alignment and cache maintenance are handled internally.
 * Returns 0 on success, negative OF_ERR_* on failure. */
int of_blockdev_read(void *buf, uint32_t lba, uint32_t count);
int of_blockdev_write(const void *buf, uint32_t lba, uint32_t count);

/* Idle hook called during sector waits (audio pump etc.).  file.c's
 * of_file_set_idle_hook delegates here — blockdev owns the wait loops. */
void of_blockdev_set_idle_hook(void (*hook)(void));
void (*of_blockdev_get_idle_hook(void))(void);

#endif /* OFOS_MISTER_BLOCKDEV_H */
