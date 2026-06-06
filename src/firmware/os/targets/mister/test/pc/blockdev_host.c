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
 * backed by pread/pwrite on a plain image file (the .vhd produced by the SDK
 * .mkimage tool).  No DS_* registers, no DMA, no cache discipline — the host
 * has a flat coherent address space, so the firmware's bounce-buffer logic is
 * simply absent here.  The transferred *bytes* are identical to hardware,
 * which is all the file/save HAL semantics depend on.
 *
 * The test main drives the lifecycle via of_test_blockdev_open() /
 * of_test_blockdev_close().  "Power cycling" the image (to verify save
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

static int      img_fd = -1;
static uint64_t img_bytes;
static int      img_readonly;
static void   (*idle_hook)(void);

/* ---- Host lifecycle (test-only) -------------------------------------- */

/* Open the disk image; returns 0 on success.  read_only mounts it as a
 * write-protected medium (exercises STA_PROTECT / RES_WRPRT paths). */
int of_test_blockdev_open(const char *path, int read_only) {
    if (img_fd >= 0) {
        close(img_fd);
        img_fd = -1;
    }
    int flags = read_only ? O_RDONLY : O_RDWR;
    img_fd = open(path, flags);
    if (img_fd < 0) {
        perror(path);
        return -1;
    }
    struct stat st;
    if (fstat(img_fd, &st) != 0) {
        close(img_fd);
        img_fd = -1;
        return -1;
    }
    img_bytes    = (uint64_t)st.st_size;
    img_readonly = read_only ? 1 : 0;
    return 0;
}

void of_test_blockdev_close(void) {
    if (img_fd >= 0) {
        close(img_fd);
        img_fd = -1;
    }
    img_bytes    = 0;
    img_readonly = 0;
}

/* ---- of_blockdev_* interface ----------------------------------------- */

void of_blockdev_init(void) {
    idle_hook = (void *)0;
    /* Image fd is owned by the test lifecycle; init does not close it. */
}

int of_blockdev_present(void) {
    return img_fd >= 0 ? 1 : 0;
}

int of_blockdev_readonly(void) {
    return img_readonly;
}

uint64_t of_blockdev_size(void) {
    return of_blockdev_present() ? img_bytes : 0;
}

int of_blockdev_read(void *buf, uint32_t lba, uint32_t count) {
    if (img_fd < 0)
        return -2; /* OF_ERR_NOT_SUPPORTED */
    size_t  bytes = (size_t)count * SECTOR_BYTES;
    off_t   off   = (off_t)lba * SECTOR_BYTES;
    ssize_t n     = pread(img_fd, buf, bytes, off);
    return n == (ssize_t)bytes ? 0 : -13 /* OF_ERR_IO */;
}

int of_blockdev_write(const void *buf, uint32_t lba, uint32_t count) {
    if (img_fd < 0)
        return -2;
    if (img_readonly)
        return -2;
    size_t  bytes = (size_t)count * SECTOR_BYTES;
    off_t   off   = (off_t)lba * SECTOR_BYTES;
    ssize_t n     = pwrite(img_fd, buf, bytes, off);
    return n == (ssize_t)bytes ? 0 : -13;
}

void of_blockdev_set_idle_hook(void (*hook)(void)) { idle_hook = hook; }
void (*of_blockdev_get_idle_hook(void))(void) { return idle_hook; }

/* ---- FatFs diskio glue ----------------------------------------------- */

DSTATUS disk_status(BYTE pdrv) {
    (void)pdrv;
    if (img_fd < 0)
        return STA_NODISK | STA_NOINIT;
    return img_readonly ? STA_PROTECT : 0;
}

DSTATUS disk_initialize(BYTE pdrv) {
    return disk_status(pdrv);
}

DRESULT disk_read(BYTE pdrv, BYTE *buff, LBA_t sector, UINT count) {
    (void)pdrv;
    return of_blockdev_read(buff, (uint32_t)sector, count) == 0 ? RES_OK : RES_ERROR;
}

DRESULT disk_write(BYTE pdrv, const BYTE *buff, LBA_t sector, UINT count) {
    (void)pdrv;
    if (img_readonly)
        return RES_WRPRT;
    return of_blockdev_write(buff, (uint32_t)sector, count) == 0 ? RES_OK : RES_ERROR;
}

DRESULT disk_ioctl(BYTE pdrv, BYTE cmd, void *buff) {
    (void)pdrv;
    switch (cmd) {
    case CTRL_SYNC:
        if (img_fd >= 0)
            fsync(img_fd);
        return RES_OK;
    case GET_SECTOR_COUNT:
        *(LBA_t *)buff = (LBA_t)(img_bytes / SECTOR_BYTES);
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
