//------------------------------------------------------------------------------
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileType: SOURCE
// SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
//------------------------------------------------------------------------------

/*
 * MiSTer block-device layer — 512-byte sector access to the OSD-mounted
 * disk images through the hps_bridge sector engine.
 *
 * This is the single owner of the DS_* data-slot registers on MiSTer
 * (there is no APF bridge).  FatFs's diskio glue lives in blockdev.c;
 * file.c and save.c sit above it through ff.h.
 *
 * Multi-disk: up to three images (FatFs pdrv = disk index = DS_SLOT_ID):
 *   disk 0 — S0 family library / the legacy single all-in-one image
 *   disk 1 — S1 writable instance volume (os.ini, saves, config)
 *   disk 2 — S2 borrow library
 * Disks 1/2 exist only when the RTL advertises HPS_STATUS_MULTIDISK_CAP;
 * on older RTL of_blockdev_present(1/2) is always 0 and DS_SLOT_ID stays 0
 * on the wire, so pre-multidisk bitstreams behave exactly as before.
 *
 * On the PC test harness the same interface is provided by a pread/pwrite
 * file-backed shim instead — see test/pc/blockdev_host.c.
 */

#ifndef OFOS_MISTER_BLOCKDEV_H
#define OFOS_MISTER_BLOCKDEV_H

#include <stdint.h>

/* Disk indices understood by the sector engine (DS_SLOT_ID low 4 bits). */
#define OF_BLOCKDEV_DISK_COUNT 3u

/* Reset internal state; safe to call before any image is mounted. */
void of_blockdev_init(void);

/* 1 if the RTL supports the multi-disk DS_SLOT_ID contract (live HPS
 * status bit 5; reads 0 on pre-multidisk bitstreams). */
int of_blockdev_multidisk_cap(void);

/* 1 if disk image `disk` (0..2) is currently mounted (live HPS status).
 * Disks 1/2 report 0 unless the multidisk capability is present. */
int of_blockdev_present(uint32_t disk);

/* 1 if disk image `disk` is mounted read-only. */
int of_blockdev_readonly(uint32_t disk);

/* Mounted image size in bytes (0 when not mounted). */
uint64_t of_blockdev_size(uint32_t disk);

/* Raw sector access.  count sectors of 512 bytes; buf may be anywhere in
 * SDRAM/BRAM — alignment and cache maintenance are handled internally.
 * Returns 0 on success, negative OF_ERR_* on failure. */
int of_blockdev_read(uint32_t disk, void *buf, uint32_t lba, uint32_t count);
int of_blockdev_write(uint32_t disk, const void *buf, uint32_t lba,
                      uint32_t count);

/* Idle hook called during sector waits (audio pump etc.).  file.c's
 * of_file_set_idle_hook delegates here — blockdev owns the wait loops. */
void of_blockdev_set_idle_hook(void (*hook)(void));
void (*of_blockdev_get_idle_hook(void))(void);

/* Arm the DS ERR_TIMEOUT retry loop.  Called ONCE by the kernel (through
 * of_file_boot_complete) when boot is finished: during init/probe a
 * not-yet-responding HPS must fail fast so boot proceeds (retrying there
 * blank-screens the core for minutes); after boot the same error means
 * "host busy — e.g. the OSD is open" and the op is re-dispatched. */
void of_blockdev_enable_retries(void);

/* Debug: total sector-op re-dispatches after the RTL watchdog's transient
 * ERR_TIMEOUT (the HPS stops servicing sd_rd/sd_wr while the OSD is open).
 * Defined in blockdev.c; grows monotonically since boot. */
extern int of_blockdev_timeout_retries;

#endif /* OFOS_MISTER_BLOCKDEV_H */
