//------------------------------------------------------------------------------
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileType: SOURCE
// SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
//------------------------------------------------------------------------------

/*
 * Host block-device shim for the MiSTer FAT-stack unit tests.
 *
 * REPLACES targets/mister/blockdev.c on the host.  It provides:
 *
 *   - the of_blockdev_* interface (blockdev.h) that file.c/save.c call
 *     indirectly through FatFs, and
 *   - the FatFs diskio glue (disk_status/initialize/read/write/ioctl)
 *
 * backed by pread/pwrite on up to three plain image files (the .vhd images
 * the SDK .mkimage tool produces) — disk 0 = S0 family/legacy, disk 1 = S1
 * instance, disk 2 = S2 borrow, mirroring the hps_bridge multi-disk sector
 * engine.  No DS_* registers, no DMA, no cache discipline — the host has a
 * flat coherent address space, so the firmware's bounce-buffer logic is
 * simply absent here.  The transferred *bytes* are identical to hardware,
 * which is all the file/save HAL semantics depend on.
 *
 * The multidisk capability bit (HPS_STATUS bit 5 on hardware) is modeled as
 * a host flag: of_test_blockdev_set_cap(0) emulates pre-multidisk RTL —
 * disks 1/2 report "not present" no matter what is opened, exactly like
 * hardware where the mount bits are hardwired 0.
 *
 * The test main drives the lifecycle via of_test_blockdev_open_disk() /
 * of_test_blockdev_close_disk() (the disk-0 wrappers keep the original
 * single-image tests unchanged).  "Power cycling" an image (to verify save
 * durability) is just close + reopen on the same path.
 */

#include "blockdev.h"
#include "fatfs/ff.h"
#include "fatfs/diskio.h"

#include <stdint.h>
#include <stdio.h>
#include <fcntl.h>
#include <unistd.h>
#include <sys/stat.h>

#define SECTOR_BYTES 512u

static struct {
    int      fd;
    uint64_t bytes;
    int      readonly;
} disks[OF_BLOCKDEV_DISK_COUNT] = {
    { -1, 0, 0 }, { -1, 0, 0 }, { -1, 0, 0 },
};

static int multidisk_cap;   /* models HPS_STATUS_MULTIDISK_CAP */

static void (*idle_hook)(void);

/* ---- Host lifecycle (test-only) -------------------------------------- */

void of_test_blockdev_set_cap(int cap) {
    multidisk_cap = cap ? 1 : 0;
}

/* Open a disk image; returns 0 on success.  read_only mounts it as a
 * write-protected medium (exercises STA_PROTECT / RES_WRPRT paths). */
int of_test_blockdev_open_disk(unsigned disk, const char *path, int read_only) {
    if (disk >= OF_BLOCKDEV_DISK_COUNT)
        return -1;
    if (disks[disk].fd >= 0) {
        close(disks[disk].fd);
        disks[disk].fd = -1;
    }
    int flags = read_only ? O_RDONLY : O_RDWR;
    int fd = open(path, flags);
    if (fd < 0) {
        perror(path);
        return -1;
    }
    struct stat st;
    if (fstat(fd, &st) != 0) {
        close(fd);
        return -1;
    }
    disks[disk].fd       = fd;
    disks[disk].bytes    = (uint64_t)st.st_size;
    disks[disk].readonly = read_only ? 1 : 0;
    return 0;
}

void of_test_blockdev_close_disk(unsigned disk) {
    if (disk >= OF_BLOCKDEV_DISK_COUNT)
        return;
    if (disks[disk].fd >= 0) {
        close(disks[disk].fd);
        disks[disk].fd = -1;
    }
    disks[disk].bytes    = 0;
    disks[disk].readonly = 0;
}

/* Legacy single-image wrappers (disk 0) — keep the original suite intact. */
int of_test_blockdev_open(const char *path, int read_only) {
    return of_test_blockdev_open_disk(0, path, read_only);
}

void of_test_blockdev_close(void) {
    for (unsigned d = 0; d < OF_BLOCKDEV_DISK_COUNT; d++)
        of_test_blockdev_close_disk(d);
}

/* ---- of_blockdev_* interface ----------------------------------------- */

void of_blockdev_init(void) {
    idle_hook = (void *)0;
    /* Image fds are owned by the test lifecycle; init does not close them. */
}

int of_blockdev_multidisk_cap(void) {
    return multidisk_cap;
}

int of_blockdev_present(uint32_t disk) {
    if (disk >= OF_BLOCKDEV_DISK_COUNT)
        return 0;
    /* Hardware gates disks 1/2 on the capability bit (old RTL hardwires
     * their mount bits to 0); mirror that so cap=0 models old bitstreams. */
    if (disk != 0 && !multidisk_cap)
        return 0;
    return disks[disk].fd >= 0 ? 1 : 0;
}

int of_blockdev_readonly(uint32_t disk) {
    if (disk >= OF_BLOCKDEV_DISK_COUNT)
        return 0;
    return disks[disk].readonly;
}

uint64_t of_blockdev_size(uint32_t disk) {
    return of_blockdev_present(disk) ? disks[disk].bytes : 0;
}

int of_blockdev_read(uint32_t disk, void *buf, uint32_t lba, uint32_t count) {
    if (!of_blockdev_present(disk))
        return -2; /* OF_ERR_NOT_SUPPORTED */
    size_t  bytes = (size_t)count * SECTOR_BYTES;
    off_t   off   = (off_t)lba * SECTOR_BYTES;
    ssize_t n     = pread(disks[disk].fd, buf, bytes, off);
    return n == (ssize_t)bytes ? 0 : -13 /* OF_ERR_IO */;
}

int of_blockdev_write(uint32_t disk, const void *buf, uint32_t lba,
                      uint32_t count) {
    if (!of_blockdev_present(disk))
        return -2;
    if (disks[disk].readonly)
        return -2;
    size_t  bytes = (size_t)count * SECTOR_BYTES;
    off_t   off   = (off_t)lba * SECTOR_BYTES;
    ssize_t n     = pwrite(disks[disk].fd, buf, bytes, off);
    return n == (ssize_t)bytes ? 0 : -13;
}

void of_blockdev_set_idle_hook(void (*hook)(void)) { idle_hook = hook; }
void (*of_blockdev_get_idle_hook(void))(void) { return idle_hook; }

/* Post-boot retry arming (of_file_boot_complete): the host shim has no DS
 * watchdog to retry, but the symbol must exist for file.c, and the flag is
 * observable so tests can assert the boot-complete plumbing fires. */
int of_test_blockdev_retries_enabled;
void of_blockdev_enable_retries(void) {
    of_test_blockdev_retries_enabled = 1;
}

/* ---- FatFs diskio glue ------------------------------------------------ */
/* pdrv = disk index, matching targets/mister/blockdev.c's dispatch. */

DSTATUS disk_status(BYTE pdrv) {
    if (pdrv >= OF_BLOCKDEV_DISK_COUNT || !of_blockdev_present(pdrv))
        return STA_NODISK | STA_NOINIT;
    return of_blockdev_readonly(pdrv) ? STA_PROTECT : 0;
}

DSTATUS disk_initialize(BYTE pdrv) {
    return disk_status(pdrv);
}

DRESULT disk_read(BYTE pdrv, BYTE *buff, LBA_t sector, UINT count) {
    return of_blockdev_read(pdrv, buff, (uint32_t)sector, count) == 0
        ? RES_OK : RES_ERROR;
}

DRESULT disk_write(BYTE pdrv, const BYTE *buff, LBA_t sector, UINT count) {
    if (pdrv < OF_BLOCKDEV_DISK_COUNT && disks[pdrv].readonly)
        return RES_WRPRT;
    return of_blockdev_write(pdrv, buff, (uint32_t)sector, count) == 0
        ? RES_OK : RES_ERROR;
}

DRESULT disk_ioctl(BYTE pdrv, BYTE cmd, void *buff) {
    switch (cmd) {
    case CTRL_SYNC:
        if (pdrv < OF_BLOCKDEV_DISK_COUNT && disks[pdrv].fd >= 0)
            fsync(disks[pdrv].fd);
        return RES_OK;
    case GET_SECTOR_COUNT:
        *(LBA_t *)buff = (LBA_t)(of_blockdev_size(pdrv) / SECTOR_BYTES);
        return RES_OK;
    case GET_SECTOR_SIZE:
        *(WORD *)buff = SECTOR_BYTES;
        return RES_OK;
    case GET_BLOCK_SIZE:
        *(DWORD *)buff = 1;
        return RES_OK;
    default:
        return RES_PARERR;
    }
}
