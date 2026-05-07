/*
 * openfpgaOS Save HAL Implementation (v2 memory arch)
 *
 * Nonvolatile CRAM0 save-slot window, persisted to SD card by the APF
 * bridge.  CRAM0 is uncached per PMA, so CPU writes/reads are
 * immediately visible to the bridge without a D-cache dance.
 *
 * The bridge and the CPU's CDC time-share the CRAM0 controller via the
 * CRAM0_MODE mux: CPU owns CRAM0 while reading/writing save-slot data,
 * bridge owns it during the DMA to/from the SD card nonvolatile file.
 *
 * Layout in CRAM0:
 *   [0x0C0000 .. 0x0FFFFF ]  One pre-save config/settings slot
 *   [0x100000 .. 0x37FFFF ]  Save data (10 slots x 256 KB)
 *   [0x380000 .. ..       ]  Per-slot save_meta_t (CRC, magic)
 *
 * Each slot is 256 KB (SAVE_SLOT_SIZE = 0x40000).
 */

#include "save.h"
#include "file.h"
#include "regs.h"

#define SAVE_CRC_MAGIC      0x4F465356  /* "OFSV" */
#define SAVE_META_ADDR      (SAVE_REGION_ADDR + SAVE_MAX_SLOTS * SAVE_SLOT_SIZE)
#define NV_SLOT_ID_SHARED_CONFIG 8u
#define NV_SLOT_ID_DUKE_SETTINGS 9u
#define NV_SLOT_ID_SAVE_BASE     10u
#define NV_DT_SLOT_PRESAVE       0x0Fu
#ifndef OF_TARGET_SAVE_WRITEBACK_CHUNK_SIZE
#define OF_TARGET_SAVE_WRITEBACK_CHUNK_SIZE SAVE_SLOT_SIZE
#endif

#ifndef OF_TARGET_CRAM0_PRESAVE_OFFSET
#define OF_TARGET_CRAM0_PRESAVE_OFFSET 0x000C0000u
#endif
#ifndef OF_TARGET_PRESAVE_REGION_ADDR
#define OF_TARGET_PRESAVE_REGION_ADDR  (OF_TARGET_CRAM0_BASE + OF_TARGET_CRAM0_PRESAVE_OFFSET)
#endif
#ifndef OF_TARGET_PRESAVE_SLOT_SIZE
#define OF_TARGET_PRESAVE_SLOT_SIZE    0x00040000u
#endif

typedef struct {
    uint32_t crc;
    uint32_t magic;
} save_meta_t;

typedef struct {
    volatile uint8_t *base;
    uint32_t bridge_addr;
    uint32_t capacity;
    uint8_t dt_slot;
} nvslot_map_t;

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

static uint32_t cram0_cpu_hold_count;

static int nvslot_map(uint32_t data_slot_id, nvslot_map_t *map) {
    if (data_slot_id == NV_SLOT_ID_SHARED_CONFIG ||
        data_slot_id == NV_SLOT_ID_DUKE_SETTINGS) {
        if (map) {
            map->base = (volatile uint8_t *)OF_TARGET_PRESAVE_REGION_ADDR;
            map->bridge_addr = CRAM0_BRIDGE + OF_TARGET_CRAM0_PRESAVE_OFFSET;
            map->capacity = OF_TARGET_PRESAVE_SLOT_SIZE;
            map->dt_slot = NV_DT_SLOT_PRESAVE;
        }
        return 0;
    }

    if (data_slot_id >= NV_SLOT_ID_SAVE_BASE &&
        data_slot_id < NV_SLOT_ID_SAVE_BASE + (uint32_t)SAVE_MAX_SLOTS) {
        uint32_t save_slot = data_slot_id - NV_SLOT_ID_SAVE_BASE;
        if (map) {
            map->base = slot_base((int)save_slot);
            map->bridge_addr = CRAM0_BRIDGE + OF_TARGET_CRAM0_SAVE_OFFSET +
                               save_slot * SAVE_SLOT_SIZE;
            map->capacity = SAVE_SLOT_SIZE;
            map->dt_slot = (uint8_t)save_slot;
        }
        return 0;
    }

    return -1;
}

static void copy_from_cram(volatile uint8_t *src, void *buf, uint32_t len) {
    uint8_t *dst = (uint8_t *)buf;
    for (uint32_t i = 0; i < len; i++)
        dst[i] = src[i];
}

static void copy_to_cram(volatile uint8_t *dst, const void *buf, uint32_t len) {
    const uint8_t *src = (const uint8_t *)buf;
    uintptr_t addr = (uintptr_t)dst;
    uint32_t done = 0;

    /* CRAM0's CPU path has shown bad behavior on sub-word stores:
     * a short header patch can leak the final halfword into the next
     * halfword.  Always write complete 32-bit words and preserve
     * untouched lanes with a local read/modify/write. */
    while (done < len) {
        uintptr_t word_addr = (addr + done) & ~(uintptr_t)3;
        uint32_t lane = (uint32_t)((addr + done) & 3u);
        uint32_t chunk = 4u - lane;
        if (chunk > len - done)
            chunk = len - done;

        volatile uint32_t *wordp = (volatile uint32_t *)word_addr;
        uint32_t word = *wordp;

        for (uint32_t i = 0; i < chunk; i++) {
            uint32_t shift = (lane + i) * 8u;
            word = (word & ~(0xffu << shift)) |
                   ((uint32_t)src[done + i] << shift);
        }

        *wordp = word;
        done += chunk;
    }
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
    cram0_cpu_hold_count = 0;
    cram0_release_to_bridge();
}

void of_save_begin_cpu(void) {
    if (cram0_cpu_hold_count++ == 0)
        cram0_acquire_cpu();
}

void of_save_end_cpu(void) {
    if (cram0_cpu_hold_count == 0) {
        cram0_release_to_bridge();
        return;
    }
    cram0_cpu_hold_count--;
    if (cram0_cpu_hold_count == 0)
        cram0_release_to_bridge();
}

int of_nvslot_is_supported(uint32_t data_slot_id) {
    return nvslot_map(data_slot_id, 0) == 0;
}

uint32_t of_nvslot_capacity(uint32_t data_slot_id) {
    nvslot_map_t map;
    if (nvslot_map(data_slot_id, &map) < 0)
        return 0;
    return map.capacity;
}

int of_nvslot_read(uint32_t data_slot_id, void *buf,
                   uint32_t offset, uint32_t len) {
    nvslot_map_t map;
    if (nvslot_map(data_slot_id, &map) < 0)
        return -1;
    if (offset > map.capacity || len > map.capacity - offset)
        return -1;

    of_save_begin_cpu();
    fence();
    copy_from_cram(map.base + offset, buf, len);
    fence();
    of_save_end_cpu();

    return (int)len;
}

int of_nvslot_write(uint32_t data_slot_id, const void *buf,
                    uint32_t offset, uint32_t len) {
    nvslot_map_t map;
    if (nvslot_map(data_slot_id, &map) < 0)
        return -1;
    if (offset > map.capacity || len > map.capacity - offset)
        return -1;

    of_save_begin_cpu();
    copy_to_cram(map.base + offset, buf, len);
    fence();
    of_save_end_cpu();

    return (int)len;
}

int of_nvslot_set_size(uint32_t data_slot_id, uint32_t size) {
    nvslot_map_t map;
    if (nvslot_map(data_slot_id, &map) < 0)
        return -1;
    if (size > map.capacity)
        size = map.capacity;

    /* SAVE_DT_SLOT selects either save slot 0-9 or the pre-save
     * config/settings sentinel. Writing SAVE_DT_SIZE commits the pair
     * across the FPGA-side CDC into the APF datatable size word. */
    SAVE_DT_SLOT = map.dt_slot;
    SAVE_DT_SIZE = size;
    fence();

    return 0;
}

int of_nvslot_flush_size(uint32_t data_slot_id, uint32_t size) {
    nvslot_map_t map;
    if (nvslot_map(data_slot_id, &map) < 0)
        return -1;
    if (size > map.capacity)
        size = map.capacity;

    int rc = of_nvslot_set_size(data_slot_id, size);
    if (rc < 0)
        return rc;

    cram0_cpu_hold_count = 0;
    cram0_release_to_bridge();

    uint32_t chunk = OF_TARGET_SAVE_WRITEBACK_CHUNK_SIZE;
    if (chunk == 0 || chunk > map.capacity)
        chunk = map.capacity;

    return of_file_slot_write_chunked(data_slot_id, 0,
                                      map.bridge_addr, size, chunk);
}

int of_save_read(int slot, void *buf, uint32_t offset, uint32_t len) {
    if (slot < 0 || slot >= (int)SAVE_MAX_SLOTS)
        return -1;
    return of_nvslot_read(NV_SLOT_ID_SAVE_BASE + (uint32_t)slot,
                          buf, offset, len);
}

int of_save_write(int slot, const void *buf, uint32_t offset, uint32_t len) {
    if (slot < 0 || slot >= (int)SAVE_MAX_SLOTS)
        return -1;
    return of_nvslot_write(NV_SLOT_ID_SAVE_BASE + (uint32_t)slot,
                           buf, offset, len);
}

int of_save_set_size(int slot, uint32_t size) {
    if (slot < 0 || slot >= (int)SAVE_MAX_SLOTS)
        return -1;
    return of_nvslot_set_size(NV_SLOT_ID_SAVE_BASE + (uint32_t)slot, size);
}

int of_save_flush_size(int slot, uint32_t size) {
    if (slot < 0 || slot >= (int)SAVE_MAX_SLOTS)
        return -1;
    return of_nvslot_flush_size(NV_SLOT_ID_SAVE_BASE + (uint32_t)slot, size);
}

int of_save_flush(int slot) {
    return of_save_flush_size(slot, SAVE_SLOT_SIZE);
}

void of_save_update_crc(int slot) {
    if (slot < 0 || slot >= (int)SAVE_MAX_SLOTS)
        return;

    /* v2 arch: CPU needs CRAM0 for the CRC sweep + metadata store. */
    of_save_begin_cpu();
    volatile uint8_t *base = slot_base(slot);
    uint32_t crc = save_crc32(base, SAVE_SLOT_SIZE);

    volatile save_meta_t *meta = slot_meta(slot);
    meta->crc = crc;
    meta->magic = SAVE_CRC_MAGIC;
    of_save_end_cpu();
}

int of_save_check(int slot) {
    if (slot < 0 || slot >= (int)SAVE_MAX_SLOTS)
        return -1;

    of_save_begin_cpu();
    volatile save_meta_t *meta = slot_meta(slot);

    if (meta->magic != SAVE_CRC_MAGIC) {
        of_save_end_cpu();
        return -2;
    }

    volatile uint8_t *base = slot_base(slot);
    uint32_t computed = save_crc32(base, SAVE_SLOT_SIZE);
    if (computed != meta->crc) {
        of_save_end_cpu();
        return -3;
    }

    of_save_end_cpu();
    return 0;
}

uint32_t of_save_get_size(int slot) {
    (void)slot;
    return SAVE_SLOT_SIZE;
}

void of_save_erase(int slot) {
    if (slot < 0 || slot >= (int)SAVE_MAX_SLOTS)
        return;

    of_save_begin_cpu();
    volatile uint32_t *dst = (volatile uint32_t *)slot_base(slot);
    for (uint32_t i = 0; i < SAVE_SLOT_SIZE / 4; i++)
        dst[i] = 0xFFFFFFFF;

    /* Clear metadata */
    volatile save_meta_t *meta = slot_meta(slot);
    meta->crc = 0;
    meta->magic = 0;
    of_save_end_cpu();
}
