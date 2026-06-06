//------------------------------------------------------------------------------
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileType: SOURCE
// SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
//------------------------------------------------------------------------------

/*
 * MiSTer block-device implementation + FatFs diskio glue.
 *
 * Owns the DS_* data-slot registers (the hps_bridge sector engine) and the
 * idle hook pumped during sector waits.  Three transfer strategies:
 *
 *   READ,  aligned dest:  inval-range → DMA direct into the buffer → inval
 *   READ,  other dest:    DMA into scratch → inval → memcpy (cached view)
 *   WRITE, any source:    4-aligned: clean-range → DMA from the buffer
 *                         else: memcpy to cached scratch → clean → DMA
 *
 * The bounce path goes through the CACHED view of the scratch window with
 * explicit maintenance — a memcpy from the uncached alias would issue one
 * uncached word read per 4 bytes (128 round-trips per sector); through the
 * cache it's a handful of line-fill bursts.  The discipline that keeps
 * this safe: scratch lines are only ever dirtied by the write-bounce
 * memcpy and are cleaned before the engine reads them, so no dirty
 * victim can land on top of DMA data; the read-bounce invalidate after
 * DMA discards any stale/speculative lines before the copy.
 *
 * Cache-line discipline for direct transfers mirrors pocket/file.c's
 * bridge_read_impl: a DMA into cached SDRAM is bracketed by invalidates
 * so dirty victim lines can't overwrite DMA data and stale lines can't
 * mask it.
 *
 * The sector engine addresses the image with a 32-bit BYTE offset
 * (DS_SLOT_OFFSET), so the device is architecturally capped at 4 GiB —
 * both transfer entry points bound-check, and GET_SECTOR_COUNT clamps
 * what FatFs is told, so an oversized .vhd degrades to "first 4 GiB
 * visible" instead of silent offset wraparound.
 */

#include "blockdev.h"
#include "hps_regs.h"
#include "cache.h"
#include "of_error.h"
#include "terminal.h"
#include "fatfs/ff.h"
#include "fatfs/diskio.h"
#include <string.h>

#define SECTOR_BYTES        512u
#define SECTOR_TIMEOUT      200000000u  /* ~2 s at 100 MHz */
#define CACHE_LINE          64u
#define SCRATCH_SECTORS     (OF_TARGET_CRAM0_DMA_CHUNK_SIZE / SECTOR_BYTES)

/* Scratch window, cached view (see file header for the discipline). */
#define SCRATCH_CACHED      (SDRAM_BASE + OF_TARGET_CRAM0_BRIDGE + \
                             OF_TARGET_CRAM0_SCRATCH_OFFSET)
#define SCRATCH_DMA_ADDR    (OF_TARGET_CRAM0_BRIDGE + OF_TARGET_CRAM0_SCRATCH_OFFSET)

/* 32-bit byte-offset engine limit (see file header). */
static int range_addressable(uint32_t lba, uint32_t count) {
    return ((uint64_t)lba + count) * SECTOR_BYTES <= 0x100000000ull;
}

static void (*idle_hook)(void);
static int op_count;

void of_blockdev_set_idle_hook(void (*hook)(void)) { idle_hook = hook; }
void (*of_blockdev_get_idle_hook(void))(void) { return idle_hook; }

void of_blockdev_init(void) {
    idle_hook = (void *)0;
    op_count = 0;
}

int of_blockdev_present(void) {
    return (HPS_STATUS & HPS_STATUS_IMG_MOUNTED) ? 1 : 0;
}

int of_blockdev_readonly(void) {
    return (HPS_STATUS & HPS_STATUS_IMG_READONLY) ? 1 : 0;
}

uint64_t of_blockdev_size(void) {
    if (!of_blockdev_present())
        return 0;
    uint32_t hi1, lo, hi2;
    do {
        hi1 = HPS_IMG_SIZE_HI;
        lo  = HPS_IMG_SIZE_LO;
        hi2 = HPS_IMG_SIZE_HI;
    } while (hi1 != hi2);
    return ((uint64_t)hi1 << 32) | lo;
}

/* Suppress the hook while MIE is clear (inside a trap) — same rationale as
 * pocket/file.c call_idle_hook, minus the CSR save/restore: blockdev waits
 * never nest ecalls, but hooks may, so the trap CSRs still need protecting. */
static void call_idle_hook(void) {
    if (!idle_hook) return;

    uint32_t mstatus;
    __asm__ volatile("csrr %0, mstatus" : "=r"(mstatus));
    if ((mstatus & 0x8u) == 0)
        return;

    uint32_t saved_mepc, saved_mcause, saved_mtval;
    __asm__ volatile("csrr %0, mepc"   : "=r"(saved_mepc));
    __asm__ volatile("csrr %0, mcause" : "=r"(saved_mcause));
    __asm__ volatile("csrr %0, mtval"  : "=r"(saved_mtval));

    idle_hook();

    __asm__ volatile("csrw mepc, %0"   :: "r"(saved_mepc));
    __asm__ volatile("csrw mcause, %0" :: "r"(saved_mcause));
    __asm__ volatile("csrw mtval, %0"  :: "r"(saved_mtval));
}

/* Wait for ACK → DONE → READY → WR_IDLE, mirroring pocket file_wait_complete.
 * Same DS_STATUS semantics; hps_bridge implements the identical contract. */
static int sector_wait_complete(void) {
    uint32_t timeout;

    op_count++;

    timeout = SECTOR_TIMEOUT;
    while (!(DS_STATUS & DS_STATUS_ACK)) {
        if (--timeout == 0) {
            of_term_printf("[blk ACK timeout #%d st=%02x]\n",
                           op_count, DS_STATUS & 0x7F);
            return OF_ERR_TIMEOUT;
        }
        if ((timeout & 0x3FF) == 0)
            call_idle_hook();
    }

    timeout = SECTOR_TIMEOUT;
    while (!(DS_STATUS & DS_STATUS_DONE)) {
        if (--timeout == 0) {
            of_term_printf("[blk DONE timeout #%d st=%02x]\n",
                           op_count, DS_STATUS & 0x7F);
            return OF_ERR_TIMEOUT;
        }
        if ((timeout & 0x3FF) == 0)
            call_idle_hook();
    }

    uint32_t err = (DS_STATUS & DS_STATUS_ERR_MASK) >> DS_STATUS_ERR_SHIFT;
    if (err) {
        DS_STATUS = DS_STATUS_IRQ_PENDING;
        return -((int)err);
    }

    timeout = SECTOR_TIMEOUT;
    while ((DS_STATUS & (DS_STATUS_READY | DS_STATUS_WR_IDLE))
           != (DS_STATUS_READY | DS_STATUS_WR_IDLE)) {
        if (--timeout == 0) {
            of_term_printf("[blk IDLE timeout #%d st=%02x]\n",
                           op_count, DS_STATUS & 0x7F);
            return OF_ERR_TIMEOUT;
        }
    }

    DS_STATUS = DS_STATUS_IRQ_PENDING;
    return 0;
}

/* Issue one sector-engine command.  dma_addr is an SDRAM byte offset. */
static int sector_cmd(uint32_t cmd, uint32_t lba, uint32_t dma_addr,
                      uint32_t bytes) {
    uint32_t wait = SECTOR_TIMEOUT;
    while ((DS_STATUS & (DS_STATUS_READY | DS_STATUS_WR_IDLE))
           != (DS_STATUS_READY | DS_STATUS_WR_IDLE)) {
        if (--wait == 0) return OF_ERR_TIMEOUT;
    }

    DS_SLOT_ID     = 0;                 /* raw disk device */
    DS_SLOT_OFFSET = lba * SECTOR_BYTES;
    DS_BRIDGE_ADDR = dma_addr;
    DS_LENGTH      = bytes;
    fence();
    DS_COMMAND     = cmd;
    fence();

    return sector_wait_complete();
}

static int addr_in_cached_sdram(uint32_t addr, uint32_t length) {
    return addr >= SDRAM_BASE && length <= SDRAM_SIZE &&
           addr <= SDRAM_BASE + SDRAM_SIZE - length;
}

static int addr_in_uncached_sdram(uint32_t addr, uint32_t length) {
    return addr >= SDRAM_UNCACHED_BASE && length <= SDRAM_SIZE &&
           addr <= SDRAM_UNCACHED_BASE + SDRAM_SIZE - length;
}

int of_blockdev_read(void *buf, uint32_t lba, uint32_t count) {
    if (!of_blockdev_present())
        return OF_ERR_NOT_SUPPORTED;
    if (!range_addressable(lba, count))
        return OF_ERR_BAD_RANGE;

    uint8_t *dst = (uint8_t *)buf;
    uint32_t addr = (uintptr_t)buf;
    uint32_t total = count * SECTOR_BYTES;
    int cached  = addr_in_cached_sdram(addr, total);
    int uncached = addr_in_uncached_sdram(addr, total);

    /* Direct DMA: SDRAM destination, cache-line aligned (uncached alias
     * destinations only need 4-byte alignment for the AXI write master). */
    int direct = (cached && (addr & (CACHE_LINE - 1u)) == 0) ||
                 (uncached && (addr & 3u) == 0);

    uint32_t done = 0;
    while (done < total) {
        uint32_t chunk = total - done;
        if (chunk > OF_TARGET_CRAM0_DMA_CHUNK_SIZE)
            chunk = OF_TARGET_CRAM0_DMA_CHUNK_SIZE;

        if (direct) {
            uint32_t cur = addr + done;
            uint32_t dma = cached ? (cur - SDRAM_BASE)
                                  : (cur - SDRAM_UNCACHED_BASE);
            if (cached)
                of_cache_inval_range(dst + done, chunk);
            int rc = sector_cmd(DS_CMD_READ, lba + done / SECTOR_BYTES,
                                dma, chunk);
            if (rc < 0)
                return rc;
            if (cached)
                of_cache_inval_range(dst + done, chunk);
        } else {
            int rc = sector_cmd(DS_CMD_READ, lba + done / SECTOR_BYTES,
                                SCRATCH_DMA_ADDR, chunk);
            if (rc < 0)
                return rc;
            /* Discard stale/speculative scratch lines, then copy through
             * the cache (line-fill bursts, not per-word uncached reads). */
            of_cache_inval_range((void *)SCRATCH_CACHED, chunk);
            memcpy(dst + done, (const void *)SCRATCH_CACHED, chunk);
        }

        done += chunk;
    }

    return 0;
}

int of_blockdev_write(const void *buf, uint32_t lba, uint32_t count) {
    if (!of_blockdev_present())
        return OF_ERR_NOT_SUPPORTED;
    if (of_blockdev_readonly())
        return OF_ERR_NOT_SUPPORTED;
    if (!range_addressable(lba, count))
        return OF_ERR_BAD_RANGE;

    const uint8_t *src = (const uint8_t *)buf;
    uint32_t addr = (uintptr_t)buf;
    uint32_t total = count * SECTOR_BYTES;
    int cached  = addr_in_cached_sdram(addr, total);
    int uncached = addr_in_uncached_sdram(addr, total);

    /* The engine's AXI read master needs word alignment; cleaning the
     * containing cache lines is safe regardless of neighbors. */
    int direct = (cached || uncached) && (addr & 3u) == 0;

    uint32_t done = 0;
    while (done < total) {
        uint32_t chunk = total - done;
        if (chunk > OF_TARGET_CRAM0_DMA_CHUNK_SIZE)
            chunk = OF_TARGET_CRAM0_DMA_CHUNK_SIZE;

        if (direct) {
            uint32_t cur = addr + done;
            uint32_t dma = cached ? (cur - SDRAM_BASE)
                                  : (cur - SDRAM_UNCACHED_BASE);
            if (cached)
                of_cache_clean_range((void *)(uintptr_t)(cur & ~(CACHE_LINE - 1u)),
                                     ((chunk + (cur & (CACHE_LINE - 1u)) +
                                       CACHE_LINE - 1u) & ~(CACHE_LINE - 1u)));
            int rc = sector_cmd(DS_CMD_WRITE, lba + done / SECTOR_BYTES,
                                dma, chunk);
            if (rc < 0)
                return rc;
        } else {
            /* Stage through the cached view, clean so the engine's AXI
             * reads see the data (and no dirty scratch lines remain). */
            memcpy((void *)SCRATCH_CACHED, src + done, chunk);
            of_cache_clean_range((void *)SCRATCH_CACHED, chunk);
            fence();
            int rc = sector_cmd(DS_CMD_WRITE, lba + done / SECTOR_BYTES,
                                SCRATCH_DMA_ADDR, chunk);
            if (rc < 0)
                return rc;
        }

        done += chunk;
    }

    return 0;
}

/* ======================================================================
 * FatFs diskio glue (single volume, the OSD-mounted image)
 * ====================================================================== */

DSTATUS disk_status(BYTE pdrv) {
    (void)pdrv;
    if (!of_blockdev_present())
        return STA_NODISK | STA_NOINIT;
    return of_blockdev_readonly() ? STA_PROTECT : 0;
}

DSTATUS disk_initialize(BYTE pdrv) {
    return disk_status(pdrv);
}

DRESULT disk_read(BYTE pdrv, BYTE *buff, LBA_t sector, UINT count) {
    (void)pdrv;
    return of_blockdev_read(buff, (uint32_t)sector, count) == 0
        ? RES_OK : RES_ERROR;
}

DRESULT disk_write(BYTE pdrv, const BYTE *buff, LBA_t sector, UINT count) {
    (void)pdrv;
    int rc = of_blockdev_write(buff, (uint32_t)sector, count);
    if (rc == OF_ERR_NOT_SUPPORTED && of_blockdev_readonly())
        return RES_WRPRT;
    return rc == 0 ? RES_OK : RES_ERROR;
}

DRESULT disk_ioctl(BYTE pdrv, BYTE cmd, void *buff) {
    (void)pdrv;
    switch (cmd) {
    case CTRL_SYNC:
        /* sector_cmd is fully synchronous — nothing buffered below FatFs */
        return RES_OK;
    case GET_SECTOR_COUNT: {
        /* Clamp to the engine's 4 GiB addressing limit (see file header). */
        uint64_t sz = of_blockdev_size();
        if (sz > 0x100000000ull)
            sz = 0x100000000ull;
        *(LBA_t *)buff = (LBA_t)(sz / SECTOR_BYTES);
        return RES_OK;
    }
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
