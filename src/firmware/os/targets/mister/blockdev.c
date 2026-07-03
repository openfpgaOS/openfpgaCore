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
 *
 * Multi-disk: DS_SLOT_ID carries the disk index (0=S0, 1=S1, 2=S2) in its
 * low 4 bits.  Pre-multidisk RTL rejects nonzero ids with ERR_BADREQ and
 * hardwires HPS_STATUS bits 5..9 to 0; of_blockdev_present() gates on the
 * MULTIDISK_CAP bit, so nonzero ids are never issued to old bitstreams.
 * The handshake (READY|WR_IDLE pre-dispatch gate, ACK-rise-before-DONE,
 * post-op drain, W1C) is identical for every disk index.
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

/* DS_STATUS err-field code raised by the hps_bridge SD_TIMEOUT watchdog
 * (~1.34 s at 100 MHz): the HPS didn't service sd_rd/sd_wr in time.  Main
 * stops pumping disk ops while the user has the OSD open, so this error is
 * TRANSIENT — the op itself is fine, the host is just busy elsewhere.
 * sector_wait_complete returns DS errors as -err, so this surfaces as -5
 * (NOT OF_ERR_TIMEOUT, which is the CPU-side spin-wait giving up). */
#define DS_ERR_TIMEOUT      5u

/* ERR_TIMEOUT retry budget.  Each failed attempt already burned one full
 * RTL watchdog period, so re-dispatching the same op SECTOR_RETRY_MAX
 * times bounds the total wait at ~600 x 1.34 s ~= 13 minutes of
 * continuously-open OSD before the error is surfaced for real.  All other
 * DS errors (NOMOUNT/BADREQ/READONLY/AXI/NOSUPPORT) stay immediate.
 *
 * BOOT is different: at core load the HPS legitimately doesn't service DS
 * ops for a while (Main is still starting; remembered secondary mounts are
 * being restored), so an ERR_TIMEOUT during init/probe means "host not
 * there yet", not "host busy in the OSD".  Retrying then turns the
 * fail-fast mount miss boot used to shrug off into a minutes-long silent
 * stall before any terminal output — a blank screen at core load.  The
 * retry loop is therefore ARMED ONLY POST-BOOT (kernel/main.c calls
 * of_file_boot_complete() -> of_blockdev_enable_retries() right before
 * handing control to the app); every boot-time op fails fast exactly as
 * it always did. */
#define SECTOR_RETRY_MAX    600u

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

/* Debug observability: total ERR_TIMEOUT re-dispatches since boot (kept
 * non-static so it can be inspected from a debugger / terminal dump). */
int of_blockdev_timeout_retries;

/* 0 during boot: every sector op fails fast (see the SECTOR_RETRY_MAX
 * comment).  Set once by of_blockdev_enable_retries() when the kernel
 * declares boot complete; deliberately NOT reset by of_blockdev_init()
 * re-runs after that point (relaunch re-inits file state, but the host
 * link is already known-alive). */
static int retries_armed;

void of_blockdev_enable_retries(void) {
    retries_armed = 1;
}

void of_blockdev_set_idle_hook(void (*hook)(void)) { idle_hook = hook; }
void (*of_blockdev_get_idle_hook(void))(void) { return idle_hook; }

void of_blockdev_init(void) {
    idle_hook = (void *)0;
    op_count = 0;
}

int of_blockdev_multidisk_cap(void) {
    return (HPS_STATUS & HPS_STATUS_MULTIDISK_CAP) ? 1 : 0;
}

int of_blockdev_present(uint32_t disk) {
    uint32_t st = HPS_STATUS;
    switch (disk) {
    case 0:
        return (st & HPS_STATUS_IMG_MOUNTED) ? 1 : 0;
    case 1:
        /* Old RTL hardwires the cap AND the mount bit to 0; the explicit
         * cap gate documents that disks 1/2 are a multidisk-only feature. */
        return ((st & HPS_STATUS_MULTIDISK_CAP) &&
                (st & HPS_STATUS_IMG1_MOUNTED)) ? 1 : 0;
    case 2:
        return ((st & HPS_STATUS_MULTIDISK_CAP) &&
                (st & HPS_STATUS_IMG2_MOUNTED)) ? 1 : 0;
    default:
        return 0;
    }
}

int of_blockdev_readonly(uint32_t disk) {
    uint32_t st = HPS_STATUS;
    switch (disk) {
    case 0:  return (st & HPS_STATUS_IMG_READONLY) ? 1 : 0;
    case 1:  return (st & HPS_STATUS_IMG1_READONLY) ? 1 : 0;
    case 2:  return (st & HPS_STATUS_IMG2_READONLY) ? 1 : 0;
    default: return 0;
    }
}

uint64_t of_blockdev_size(uint32_t disk) {
    if (!of_blockdev_present(disk))
        return 0;
    uint32_t hi1, lo, hi2;
    /* hi/lo/hi torn-read guard, same discipline for every size pair. */
    switch (disk) {
    case 0:
        do {
            hi1 = HPS_IMG_SIZE_HI;
            lo  = HPS_IMG_SIZE_LO;
            hi2 = HPS_IMG_SIZE_HI;
        } while (hi1 != hi2);
        break;
    case 1:
        do {
            hi1 = HPS_IMG1_SIZE_HI;
            lo  = HPS_IMG1_SIZE_LO;
            hi2 = HPS_IMG1_SIZE_HI;
        } while (hi1 != hi2);
        break;
    case 2:
        do {
            hi1 = HPS_IMG2_SIZE_HI;
            lo  = HPS_IMG2_SIZE_LO;
            hi2 = HPS_IMG2_SIZE_HI;
        } while (hi1 != hi2);
        break;
    default:
        return 0;
    }
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

/* Issue one sector-engine command.  dma_addr is an SDRAM byte offset.
 *
 * Once retries are armed (post-boot only — see SECTOR_RETRY_MAX above),
 * the RTL watchdog's ERR_TIMEOUT is treated as transient: the SAME op is
 * re-dispatched with the full handshake discipline — READY|WR_IDLE
 * pre-dispatch gate, ACK-rise-before-DONE, post-op drain, W1C — until it
 * completes or the retry budget runs out, at which point the real error
 * propagates.  During boot, and for every other failure, the error
 * returns immediately (fail fast). */
static int sector_cmd(uint32_t disk, uint32_t cmd, uint32_t lba,
                      uint32_t dma_addr, uint32_t bytes) {
    for (uint32_t attempt = 0; ; attempt++) {
        uint32_t wait = SECTOR_TIMEOUT;
        while ((DS_STATUS & (DS_STATUS_READY | DS_STATUS_WR_IDLE))
               != (DS_STATUS_READY | DS_STATUS_WR_IDLE)) {
            if (--wait == 0) return OF_ERR_TIMEOUT;
        }

        DS_SLOT_ID     = disk & 0xFu;   /* disk index (0=S0, 1=S1, 2=S2) */
        DS_SLOT_OFFSET = lba * SECTOR_BYTES;
        DS_BRIDGE_ADDR = dma_addr;
        DS_LENGTH      = bytes;
        fence();
        DS_COMMAND     = cmd;
        fence();

        int rc = sector_wait_complete();
        if (rc != -(int)DS_ERR_TIMEOUT || !retries_armed ||
            attempt >= SECTOR_RETRY_MAX)
            return rc;

        of_blockdev_timeout_retries++;
        if (attempt == 0)
            of_term_printf("[blk] DS ERR_TIMEOUT #%d — host busy (OSD?), "
                           "retrying\n", op_count);
    }
}

static int addr_in_cached_sdram(uint32_t addr, uint32_t length) {
    return addr >= SDRAM_BASE && length <= SDRAM_SIZE &&
           addr <= SDRAM_BASE + SDRAM_SIZE - length;
}

static int addr_in_uncached_sdram(uint32_t addr, uint32_t length) {
    return addr >= SDRAM_UNCACHED_BASE && length <= SDRAM_SIZE &&
           addr <= SDRAM_UNCACHED_BASE + SDRAM_SIZE - length;
}

int of_blockdev_read(uint32_t disk, void *buf, uint32_t lba, uint32_t count) {
    if (disk >= OF_BLOCKDEV_DISK_COUNT || !of_blockdev_present(disk))
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
            int rc = sector_cmd(disk, DS_CMD_READ, lba + done / SECTOR_BYTES,
                                dma, chunk);
            if (rc < 0)
                return rc;
            if (cached)
                of_cache_inval_range(dst + done, chunk);
        } else {
            int rc = sector_cmd(disk, DS_CMD_READ, lba + done / SECTOR_BYTES,
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

int of_blockdev_write(uint32_t disk, const void *buf, uint32_t lba,
                      uint32_t count) {
    if (disk >= OF_BLOCKDEV_DISK_COUNT || !of_blockdev_present(disk))
        return OF_ERR_NOT_SUPPORTED;
    if (of_blockdev_readonly(disk))
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
            int rc = sector_cmd(disk, DS_CMD_WRITE, lba + done / SECTOR_BYTES,
                                dma, chunk);
            if (rc < 0)
                return rc;
        } else {
            /* Stage through the cached view, clean so the engine's AXI
             * reads see the data (and no dirty scratch lines remain). */
            memcpy((void *)SCRATCH_CACHED, src + done, chunk);
            of_cache_clean_range((void *)SCRATCH_CACHED, chunk);
            fence();
            int rc = sector_cmd(disk, DS_CMD_WRITE, lba + done / SECTOR_BYTES,
                                SCRATCH_DMA_ADDR, chunk);
            if (rc < 0)
                return rc;
        }

        done += chunk;
    }

    return 0;
}

/* ======================================================================
 * FatFs diskio glue.  Logical drive N binds 1:1 to disk index N
 * (FF_MULTI_PARTITION=0): pdrv 0 = S0 family/legacy, 1 = S1 instance,
 * 2 = S2 borrow.  An unmounted disk reports STA_NODISK so f_mount on an
 * absent volume fails cleanly instead of issuing sector commands.
 * ====================================================================== */

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
    int rc = of_blockdev_write(pdrv, buff, (uint32_t)sector, count);
    if (rc == OF_ERR_NOT_SUPPORTED && of_blockdev_readonly(pdrv))
        return RES_WRPRT;
    return rc == 0 ? RES_OK : RES_ERROR;
}

DRESULT disk_ioctl(BYTE pdrv, BYTE cmd, void *buff) {
    switch (cmd) {
    case CTRL_SYNC:
        /* sector_cmd is fully synchronous — nothing buffered below FatFs */
        return RES_OK;
    case GET_SECTOR_COUNT: {
        /* Clamp to the engine's 4 GiB addressing limit (see file header). */
        uint64_t sz = of_blockdev_size(pdrv);
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
