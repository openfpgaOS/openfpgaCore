/*
 * openfpgaOS Save HAL Implementation
 * Nonvolatile CRAM1 (PSRAM) region, persisted to SD card by APF bridge.
 * Bridge reads/writes CRAM1 directly via dedicated FIFO paths in the FPGA.
 *
 * CPU accesses CRAM1 via the uncached alias (0x39000000) so writes are
 * immediately visible to the bridge without D-cache flushing.
 *
 * Layout in CRAM1 (starting at 0x39000000, mapped to bridge 0x30000000):
 *   [0x00000 .. 0x27FFFF]  Save data (10 slots × 256KB)
 *   [0x280000 ..]           Available for OS data (FTAB, config, etc.)
 *
 * Each slot is 256KB (SAVE_SLOT_SIZE = 0x40000).
 */

#include "save.h"
#include "file.h"
#include "regs.h"

#define SAVE_CRC_MAGIC      0x4F465356  /* "OFSV" */
#define SAVE_META_ADDR      (SAVE_REGION_ADDR + SAVE_MAX_SLOTS * SAVE_SLOT_SIZE)

typedef struct {
    uint32_t crc;
    uint32_t magic;
} save_meta_t;

/* CRC32 (no table, small code) */
static uint32_t save_crc32(const volatile uint8_t *data, uint32_t len) {
    uint32_t crc = 0xFFFFFFFF;
    for (uint32_t i = 0; i < len; i++) {
        crc ^= data[i];
        for (int j = 0; j < 8; j++)
            crc = (crc >> 1) ^ (0xEDB88320 & -(crc & 1));
    }
    return ~crc;
}

static volatile uint8_t *slot_base(int slot) {
    return (volatile uint8_t *)(SAVE_REGION_ADDR +
                                 (uint32_t)slot * SAVE_SLOT_SIZE);
}

static volatile save_meta_t *slot_meta(int slot) {
    return (volatile save_meta_t *)(SAVE_META_ADDR +
                                     (uint32_t)slot * sizeof(save_meta_t));
}

void of_save_init(void) {
    /* Populate save_prefetch's per-slot base-address table.
     *
     * SAVE_SLOT_BASE[N] = N * (SAVE_SLOT_SIZE / 4)  (CRAM1 word
     * address — slots are byte-addressed in firmware but the FPGA
     * controller indexes 32-bit words).  256 KB / 4 B = 64 K words =
     * 0x10000 word stride per slot.
     *
     * The FPGA save_prefetch module reads these whenever APF asserts
     * dataslot_requestread, then issues a sync burst from CRAM1 to
     * its 32-word ring BRAM ahead of the bridge_rd strobes.  Without
     * this init, every entry stays 0 and slot N>0 reads would fetch
     * from slot 0 — overwriting save N's SD file with garbage. */
    for (uint32_t n = 0; n < SAVE_MAX_SLOTS; n++)
        SAVE_SLOT_BASE(n) = n * (SAVE_SLOT_SIZE / 4u);
}

int of_save_read(int slot, void *buf, uint32_t offset, uint32_t len) {
    if (slot < 0 || slot >= (int)SAVE_MAX_SLOTS)
        return -1;
    if (offset + len > SAVE_SLOT_SIZE)
        return -1;

    volatile uint8_t *src = slot_base(slot) + offset;
    uint8_t *dst = (uint8_t *)buf;
    for (uint32_t i = 0; i < len; i++)
        dst[i] = src[i];

    return (int)len;
}

int of_save_write(int slot, const void *buf, uint32_t offset, uint32_t len) {
    if (slot < 0 || slot >= (int)SAVE_MAX_SLOTS)
        return -1;
    if (offset + len > SAVE_SLOT_SIZE)
        return -1;

    volatile uint8_t *dst = slot_base(slot) + offset;
    const uint8_t *src = (const uint8_t *)buf;
    for (uint32_t i = 0; i < len; i++)
        dst[i] = src[i];

    return (int)len;
}

int of_save_flush_size(int slot, uint32_t size) {
    if (slot < 0 || slot >= (int)SAVE_MAX_SLOTS)
        return -1;
    if (size > SAVE_SLOT_SIZE)
        size = SAVE_SLOT_SIZE;

    /* Update datatable so bridge knows the size for shutdown saves */
    SAVE_DT_SLOT = (uint32_t)slot;
    SAVE_DT_SIZE = size;           /* writing SIZE triggers the commit */

    /* Tell the bridge to save the slot data to .sav file */
    uint32_t bridge_addr = CRAM1_BRIDGE + (uint32_t)slot * SAVE_SLOT_SIZE;
    return of_file_slot_write(10 + (uint32_t)slot, bridge_addr, size);
}

int of_save_flush(int slot) {
    return of_save_flush_size(slot, SAVE_SLOT_SIZE);
}

void of_save_update_crc(int slot) {
    if (slot < 0 || slot >= (int)SAVE_MAX_SLOTS)
        return;

    volatile uint8_t *base = slot_base(slot);
    uint32_t crc = save_crc32(base, SAVE_SLOT_SIZE);

    volatile save_meta_t *meta = slot_meta(slot);
    meta->crc = crc;
    meta->magic = SAVE_CRC_MAGIC;
}

int of_save_check(int slot) {
    if (slot < 0 || slot >= (int)SAVE_MAX_SLOTS)
        return -1;

    volatile save_meta_t *meta = slot_meta(slot);

    if (meta->magic != SAVE_CRC_MAGIC)
        return -2;

    volatile uint8_t *base = slot_base(slot);
    uint32_t computed = save_crc32(base, SAVE_SLOT_SIZE);
    if (computed != meta->crc)
        return -3;

    return 0;
}

uint32_t of_save_get_size(int slot) {
    (void)slot;
    return SAVE_SLOT_SIZE;
}

void of_save_erase(int slot) {
    if (slot < 0 || slot >= (int)SAVE_MAX_SLOTS)
        return;

    volatile uint32_t *dst = (volatile uint32_t *)slot_base(slot);
    for (uint32_t i = 0; i < SAVE_SLOT_SIZE / 4; i++)
        dst[i] = 0xFFFFFFFF;

    /* Clear metadata */
    volatile save_meta_t *meta = slot_meta(slot);
    meta->crc = 0;
    meta->magic = 0;
}
