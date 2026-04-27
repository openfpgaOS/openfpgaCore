/*
 * openfpgaOS Save HAL Implementation (v2 memory arch)
 *
 * Nonvolatile CRAM0 save-slot window, persisted to SD card by the APF
 * bridge.  CRAM0 is uncached per PMA, so CPU writes/reads are
 * immediately visible to the bridge without a D-cache dance.
 *
 * The bridge and the CPU's CDC time-share the CRAM0 controller via the
 * CRAM0_MODE mux: CPU owns CRAM0 while reading/writing save-slot data,
 * bridge owns it during the DMA to/from the SD card (.sav file).
 *
 * Layout in CRAM0 (window starts at OF_TARGET_CRAM0_SAVE_OFFSET from
 * CRAM0_BASE — see target_platform.h):
 *   [0x0       .. 0x27FFFF ]  Save data (10 slots × 256 KB)
 *   [0x280000  .. ..        ]  Per-slot save_meta_t (CRC, magic)
 *
 * Each slot is 256 KB (SAVE_SLOT_SIZE = 0x40000).
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

/* Settle delay after a CRAM0_MODE flip — ~4 clk_74a cycles is enough
 * for the mux select to propagate before the next access lands. */
static inline void cram0_mode_settle(void) {
    for (volatile int s = 0; s < 8; s++) {}
}

/* Take CPU ownership of CRAM0 for direct load/store access. */
static inline void cram0_acquire_cpu(void) {
    fence();
    CRAM0_MODE = CRAM0_MODE_CPU;
    cram0_mode_settle();
    fence();
}

/* Hand CRAM0 back to the bridge so it can DMA save slots to/from SD. */
static inline void cram0_release_to_bridge(void) {
    fence();
    CRAM0_MODE = CRAM0_MODE_BRIDGE;
    cram0_mode_settle();
    fence();
}

void of_save_init(void) {
    /* Save slots are ordinary non-deferload APF nonvolatile data slots.
     * The Pocket bridge loads them into the CRAM0 save window before
     * dataslot_allcomplete; the BRAM boot stub waits for that before the
     * OS starts. Keep CRAM0 in bridge mode until a read/write needs CPU
     * ownership. */
    cram0_release_to_bridge();
}

int of_save_read(int slot, void *buf, uint32_t offset, uint32_t len) {
    if (slot < 0 || slot >= (int)SAVE_MAX_SLOTS)
        return -1;
    if (offset + len > SAVE_SLOT_SIZE)
        return -1;

    /* v2 arch: CPU must own CRAM0 during the copy-out. */
    cram0_acquire_cpu();
    volatile uint8_t *src = slot_base(slot) + offset;
    uint8_t *dst = (uint8_t *)buf;
    for (uint32_t i = 0; i < len; i++)
        dst[i] = src[i];

    cram0_release_to_bridge();
    return (int)len;
}

int of_save_write(int slot, const void *buf, uint32_t offset, uint32_t len) {
    if (slot < 0 || slot >= (int)SAVE_MAX_SLOTS)
        return -1;
    if (offset + len > SAVE_SLOT_SIZE)
        return -1;

    /* v2 arch: CPU must own CRAM0 during the copy-in. */
    cram0_acquire_cpu();
    volatile uint8_t *dst = slot_base(slot) + offset;
    const uint8_t *src = (const uint8_t *)buf;
    for (uint32_t i = 0; i < len; i++)
        dst[i] = src[i];

    cram0_release_to_bridge();
    return (int)len;
}

int of_save_set_size(int slot, uint32_t size) {
    if (slot < 0 || slot >= (int)SAVE_MAX_SLOTS)
        return -1;
    if (size > SAVE_SLOT_SIZE)
        size = SAVE_SLOT_SIZE;

    /* Update datatable so the APF bridge knows the logical save size.
     * Writing SAVE_DT_SIZE commits the slot+size pair. */
    SAVE_DT_SLOT = (uint32_t)slot;
    SAVE_DT_SIZE = size;

    return 0;
}

int of_save_flush_size(int slot, uint32_t size) {
    if (size > SAVE_SLOT_SIZE)
        size = SAVE_SLOT_SIZE;

    int rc = of_save_set_size(slot, size);
    if (rc < 0)
        return rc;

    /* v2 arch: hand CRAM0 to the bridge for the SD-write DMA.  The slot
     * base in bridge-space is CRAM0_BRIDGE + save offset + slot*slot_size. */
    cram0_release_to_bridge();
    uint32_t save_bridge_base = CRAM0_BRIDGE + OF_TARGET_CRAM0_SAVE_OFFSET;
    uint32_t bridge_addr = save_bridge_base + (uint32_t)slot * SAVE_SLOT_SIZE;
    return of_file_slot_write(10 + (uint32_t)slot, bridge_addr, size);
}

int of_save_flush(int slot) {
    return of_save_flush_size(slot, SAVE_SLOT_SIZE);
}

void of_save_update_crc(int slot) {
    if (slot < 0 || slot >= (int)SAVE_MAX_SLOTS)
        return;

    /* v2 arch: CPU needs CRAM0 for the CRC sweep + metadata store. */
    cram0_acquire_cpu();
    volatile uint8_t *base = slot_base(slot);
    uint32_t crc = save_crc32(base, SAVE_SLOT_SIZE);

    volatile save_meta_t *meta = slot_meta(slot);
    meta->crc = crc;
    meta->magic = SAVE_CRC_MAGIC;
    cram0_release_to_bridge();
}

int of_save_check(int slot) {
    if (slot < 0 || slot >= (int)SAVE_MAX_SLOTS)
        return -1;

    cram0_acquire_cpu();
    volatile save_meta_t *meta = slot_meta(slot);

    if (meta->magic != SAVE_CRC_MAGIC) {
        cram0_release_to_bridge();
        return -2;
    }

    volatile uint8_t *base = slot_base(slot);
    uint32_t computed = save_crc32(base, SAVE_SLOT_SIZE);
    if (computed != meta->crc) {
        cram0_release_to_bridge();
        return -3;
    }

    cram0_release_to_bridge();
    return 0;
}

uint32_t of_save_get_size(int slot) {
    (void)slot;
    return SAVE_SLOT_SIZE;
}

void of_save_erase(int slot) {
    if (slot < 0 || slot >= (int)SAVE_MAX_SLOTS)
        return;

    cram0_acquire_cpu();
    volatile uint32_t *dst = (volatile uint32_t *)slot_base(slot);
    for (uint32_t i = 0; i < SAVE_SLOT_SIZE / 4; i++)
        dst[i] = 0xFFFFFFFF;

    /* Clear metadata */
    volatile save_meta_t *meta = slot_meta(slot);
    meta->crc = 0;
    meta->magic = 0;
    cram0_release_to_bridge();
}
