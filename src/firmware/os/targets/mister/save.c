//------------------------------------------------------------------------------
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileType: SOURCE
// SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
//------------------------------------------------------------------------------

/*
 * openfpgaOS Save HAL Implementation — MiSTer
 *
 * Nonvolatile slots are ordinary FAT files inside the mounted disk image:
 *
 *   slot 8      /config/shared.cfg
 *   slot 9      /config/duke3d.cfg
 *   slot 10-19  /saves/slot_N.sav
 *
 * Every file is pre-created at full capacity (256 KB, contiguous) by the
 * SDK image builder and is NEVER grown or shrunk at runtime: writes are
 * f_lseek + f_write + f_sync into existing data clusters, so FAT metadata
 * (directory entries, cluster chains) is never touched during normal
 * operation and a power cut can corrupt at most one save's payload, never
 * the filesystem.
 *
 * Unlike the Pocket — where of_nvslot_write lands in a volatile CRAM0
 * mirror and persistence is deferred to the APF launcher's exit writeback —
 * every write here is durable when it returns (write-through + f_sync).
 * MiSTer has no launcher save-on-exit and cores exit by hard reset, so
 * deferred persistence would lose saves.
 *
 * Logical save sizes live inside the app's own save format (the SDK save
 * helper length-prefixes + CRCs its payload); of_nvslot_set_size has no
 * datatable to update.  set_size(slot, 0) invalidates the slot by zeroing
 * its first sector.
 */

#include "save.h"
#include "file.h"
#include "regs.h"
#include "terminal.h"
#include "blockdev.h"
#include "mister_file.h"
#include <string.h>

#define NV_SLOT_ID_SHARED_CONFIG 8u
#define NV_SLOT_ID_DUKE_SETTINGS 9u
#define NV_SLOT_ID_SAVE_BASE     10u

/* Scratch + app staging wipe range, via the uncached alias (no cache lines
 * to manage).  The os.bin staging region is deliberately NOT wiped: the
 * boot CRC-mismatch reload path reads it, and MiSTer re-delivers boot.rom
 * on every core start anyway. */
#define WIPE_UNCACHED_BASE  (SDRAM_UNCACHED_BASE + OF_TARGET_CRAM0_BRIDGE + \
                             OF_TARGET_CRAM0_SCRATCH_OFFSET)
#define WIPE_BYTES          ((OF_TARGET_CRAM0_APP_DMA_OFFSET + \
                              OF_TARGET_CRAM0_APP_DMA_SIZE) - \
                             OF_TARGET_CRAM0_SCRATCH_OFFSET)

void of_save_init(void) {
    /* Nothing to arm — persistence is per-write. */
}

/* No CRAM0 ownership mux on MiSTer; the begin/end pairing is a no-op kept
 * for the shared syscall save path. */
void of_save_begin_cpu(void) {}
void of_save_end_cpu(void) {}

void of_save_security_wipe(void) {
    volatile uint32_t *p = (volatile uint32_t *)WIPE_UNCACHED_BASE;
    for (uint32_t i = 0; i < WIPE_BYTES / 4u; i++)
        p[i] = 0;
    fence();
}

static uint32_t nvslot_cap(uint32_t data_slot_id) {
    if (data_slot_id == NV_SLOT_ID_SHARED_CONFIG ||
        data_slot_id == NV_SLOT_ID_DUKE_SETTINGS)
        return OF_TARGET_PRESAVE_SLOT_SIZE;
    if (data_slot_id >= NV_SLOT_ID_SAVE_BASE &&
        data_slot_id < NV_SLOT_ID_SAVE_BASE + (uint32_t)SAVE_MAX_SLOTS)
        return SAVE_SLOT_SIZE;
    /* By-name config slots (file.c, hal/file.h of_file_config_slot) are
     * nonvolatile text configs with the same preallocated capacity as the
     * fixed config slots. */
    if (mister_file_cfg_slot_valid(data_slot_id))
        return OF_TARGET_PRESAVE_SLOT_SIZE;
    return 0;
}

int of_nvslot_is_supported(uint32_t data_slot_id) {
    return nvslot_cap(data_slot_id) != 0;
}

uint32_t of_nvslot_capacity(uint32_t data_slot_id) {
    return nvslot_cap(data_slot_id);
}

static int nvslot_read_body(uint32_t data_slot_id, void *buf,
                            uint32_t offset, uint32_t len) {
    FIL *fil = mister_file_open_slot(data_slot_id, 0);
    if (!fil)
        return -1;
    if (f_lseek(fil, offset) != FR_OK)
        return -1;

    UINT br = 0;
    if (f_read(fil, buf, len, &br) != FR_OK)
        return -1;

    /* A pre-created slot file is always full capacity; a short read means
     * the image was built without preallocation — treat the tail as zeros
     * so logically-empty slots still read back deterministically. */
    if (br < len)
        memset((uint8_t *)buf + br, 0, len - br);

    return (int)len;
}

int of_nvslot_read(uint32_t data_slot_id, void *buf,
                   uint32_t offset, uint32_t len) {
    uint32_t cap = nvslot_cap(data_slot_id);
    if (cap == 0 || offset > cap || len > cap - offset)
        return -1;

    mister_fs_enter();
    int rc = nvslot_read_body(data_slot_id, buf, offset, len);
    mister_fs_exit();
    return rc;
}

static int nvslot_write_body(uint32_t data_slot_id, const void *buf,
                             uint32_t offset, uint32_t len) {
    FIL *fil = mister_file_open_slot(data_slot_id, 1);
    if (!fil)
        return -1;

    /* Durability invariant: writes land in EXISTING data clusters only
     * (see the file header).  If the backing file is short — an image
     * built without preallocation — a write past its end would make
     * FatFs allocate clusters and rewrite directory entries, turning a
     * power cut into filesystem corruption instead of one stale save.
     * Refuse loudly; the read path's zero-fill keeps such slots readable. */
    if ((FSIZE_t)offset + len > f_size(fil)) {
        static int warned;
        if (!warned) {
            warned = 1;
            of_term_printf("[save] slot %u file is short — image built "
                           "without preallocation; write refused\n",
                           (unsigned)data_slot_id);
        }
        return -1;
    }

    if (f_lseek(fil, offset) != FR_OK)
        return -1;

    UINT bw = 0;
    if (f_write(fil, buf, len, &bw) != FR_OK || bw != len)
        return -1;
    if (f_sync(fil) != FR_OK)
        return -1;

    return (int)len;
}

int of_nvslot_write(uint32_t data_slot_id, const void *buf,
                    uint32_t offset, uint32_t len) {
    uint32_t cap = nvslot_cap(data_slot_id);
    if (cap == 0 || offset > cap || len > cap - offset)
        return -1;

    mister_fs_enter();
    int rc = nvslot_write_body(data_slot_id, buf, offset, len);
    mister_fs_exit();
    return rc;
}

/* A rewritten named config can be SHORTER than its previous contents; the
 * backing file stays at the full 256 KB preallocation, so bytes of the old
 * text would survive beyond the new logical end and be re-parsed on the
 * next load (the fd size comes from file.c's cfg_logical_size — bytes
 * before the first 0x00/0xFF).  Zero the old extent's remainder: scan
 * forward from `size` and zero-fill up to the first 0x00/0xFF byte (the
 * old terminator / untouched preallocation fill).  Bounded by capacity; a
 * write to a fresh zero-filled slot, or a longer rewrite, hits a zero byte
 * immediately and writes nothing. */
static int cfg_zero_stale_tail(uint32_t data_slot_id, uint32_t size,
                               uint32_t cap) {
    if (size >= cap)
        return 0;

    uint8_t buf[256];
    uint32_t end = size;
    int found = 0;
    while (end < cap && !found) {
        uint32_t n = cap - end;
        if (n > sizeof(buf))
            n = sizeof(buf);
        if (of_nvslot_read(data_slot_id, buf, end, n) != (int)n)
            return -1;
        uint32_t i = 0;
        while (i < n && buf[i] != 0x00u && buf[i] != 0xFFu)
            i++;
        end += i;
        if (i < n)
            found = 1;
    }

    if (end == size)
        return 0;

    uint8_t zeros[256];
    memset(zeros, 0, sizeof(zeros));
    uint32_t off = size;
    while (off < end) {
        uint32_t n = end - off;
        if (n > sizeof(zeros))
            n = sizeof(zeros);
        if (of_nvslot_write(data_slot_id, zeros, off, n) != (int)n)
            return -1;
        off += n;
    }
    return 0;
}

int of_nvslot_set_size(uint32_t data_slot_id, uint32_t size) {
    uint32_t cap = nvslot_cap(data_slot_id);
    if (cap == 0)
        return -1;

    if (size == 0) {
        /* Invalidate: zero the slot's first sector so any header/CRC the
         * app format keeps there reads as empty. */
        uint8_t zeros[OF_TARGET_BLOCKDEV_SECTOR_BYTES];
        memset(zeros, 0, sizeof(zeros));
        if (of_nvslot_write(data_slot_id, zeros, 0, sizeof(zeros)) < 0)
            return -1;
        return 0;
    }

    /* Named text configs need their stale tail cleared on close (this is
     * where sys_close's dirty-fd flush lands). */
    if (mister_file_cfg_slot_valid(data_slot_id))
        return cfg_zero_stale_tail(data_slot_id, size, cap);

    /* Logical size is carried by the app's save format; the backing file
     * stays at full capacity (see file header).  Writes are already
     * durable, so commit is a no-op. */
    return 0;
}

int of_nvslot_flush_size(uint32_t data_slot_id, uint32_t size) {
    int rc = of_nvslot_set_size(data_slot_id, size);
    if (rc < 0)
        return rc;

    /* Pocket flushes its CRAM mirror to SD here; MiSTer writes are already
     * on the medium.  Make sure the handle is synced and report success. */
    mister_fs_enter();
    FIL *fil = mister_file_open_slot(data_slot_id, 1);
    int sync_rc = (fil && f_sync(fil) != FR_OK) ? -1 : 0;
    mister_fs_exit();
    return sync_rc;
}
