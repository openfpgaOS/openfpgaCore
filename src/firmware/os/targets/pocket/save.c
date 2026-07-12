//------------------------------------------------------------------------------
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileType: SOURCE
// SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
//------------------------------------------------------------------------------

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
#include "terminal.h"
#include "timer.h"
#include "of_caps.h"   /* OF_HW_SAVE_DT_WORD (HW_FEATURES bit 24) */

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
static volatile uint8_t *slot_base(int slot) {
    return (volatile uint8_t *)(SAVE_REGION_ADDR +
                                 (uint32_t)slot * SAVE_SLOT_SIZE);
}

static uint32_t cram0_cpu_hold_count;

static int nvslot_map(uint32_t data_slot_id, nvslot_map_t *map) {
    if (data_slot_id == NV_SLOT_ID_SHARED_CONFIG) {
        /* Id 8 gets its OWN 256 KB window at the save_meta region
         * (bridge 0x20380000) — the address the data.json contract
         * declares for it (SDK template, Quake, SM64, Diablo stash).
         * It was previously aliased onto the presave window, so ids 8
         * and 9 clobbered each other in CRAM and the host loaded /
         * persisted the slot-8 file at 0x20380000, bytes this kernel
         * never wrote.  dt_slot keeps the presave sentinel: the legacy
         * fixed-index commit has no slot for this window (unchanged
         * behavior on old bitstreams); entry-resolved bitstreams
         * (OF_HW_SAVE_DT_WORD) ignore dt_slot and scan by id. */
        if (map) {
            map->base = (volatile uint8_t *)SAVE_META_ADDR;
            map->bridge_addr = CRAM0_BRIDGE + OF_TARGET_CRAM0_SAVE_OFFSET +
                               (uint32_t)SAVE_MAX_SLOTS * SAVE_SLOT_SIZE;
            map->capacity = SAVE_SLOT_SIZE;
            map->dt_slot = NV_DT_SLOT_PRESAVE;
        }
        return 0;
    }

    if (data_slot_id == NV_SLOT_ID_DUKE_SETTINGS) {
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

/* The wiped ranges must NEVER overlap the nonvolatile window
 * [PRESAVE_OFFSET, SCRATCH_OFFSET) that the bridge persists on exit. */
_Static_assert(OF_TARGET_CRAM0_PRESAVE_OFFSET < OF_TARGET_CRAM0_SCRATCH_OFFSET,
               "presave/save window must precede the scratch region");
_Static_assert(OF_TARGET_CRAM0_APP_DMA_OFFSET + OF_TARGET_CRAM0_APP_DMA_SIZE
               <= OF_TARGET_CRAM_SIZE, "app-DMA pool exceeds CRAM0");

/* Zero a word-aligned CRAM0 byte range [off, off+len).  CRAM0 is uncached per
 * PMA, so these stores go straight to PSRAM; len/off are region constants that
 * are 4-byte multiples, so whole-word stores are always safe (no sub-word RMW
 * needed — see copy_to_cram). Caller must already hold CPU ownership. */
static void cram0_wipe_words(uint32_t off, uint32_t len) {
    volatile uint32_t *p =
        (volatile uint32_t *)(uintptr_t)(OF_TARGET_CRAM0_BASE + off);
    uint32_t words = len >> 2;
    for (uint32_t i = 0; i < words; i++)
        p[i] = 0;
}

void of_save_security_wipe(void) {
    of_save_begin_cpu();
    fence();
    /* Below the nonvolatile window: the stale boot-time copy of os.bin. */
    cram0_wipe_words(0, OF_TARGET_CRAM0_PRESAVE_OFFSET);
    /* Above it: the DMA scratch and the app file-staging pool. */
    cram0_wipe_words(OF_TARGET_CRAM0_SCRATCH_OFFSET,
                     (OF_TARGET_CRAM0_APP_DMA_OFFSET + OF_TARGET_CRAM0_APP_DMA_SIZE)
                         - OF_TARGET_CRAM0_SCRATCH_OFFSET);
    fence();
    of_save_end_cpu();
}

int of_nvslot_is_supported(uint32_t data_slot_id) {
    return nvslot_map(data_slot_id, 0) == 0;
}

uint32_t of_nvslot_capacity(uint32_t data_slot_id) {
    nvslot_map_t map;
    if (!of_nvslot_is_supported(data_slot_id) ||
        nvslot_map(data_slot_id, &map) < 0)
        return 0;
    return map.capacity;
}

int of_nvslot_read(uint32_t data_slot_id, void *buf,
                   uint32_t offset, uint32_t len) {
    nvslot_map_t map;
    if (!of_nvslot_is_supported(data_slot_id) ||
        nvslot_map(data_slot_id, &map) < 0)
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
    if (!of_nvslot_is_supported(data_slot_id) ||
        nvslot_map(data_slot_id, &map) < 0)
        return -1;
    if (offset > map.capacity || len > map.capacity - offset)
        return -1;

    of_save_begin_cpu();
    copy_to_cram(map.base + offset, buf, len);
    fence();
    of_save_end_cpu();

    return (int)len;
}

/* Commit `size` into the APF datatable for data_slot_id, returning the
 * datatable entry index actually targeted via entry_out (for readback
 * diagnostics).
 *
 * New bitstreams (OF_HW_SAVE_DT_WORD, HW_FEATURES bit 24) take the
 * entry-RESOLVED path: scan the datatable for the entry whose id word
 * matches the slot id and commit through SAVE_DT_WORD at entry*2+1.  The
 * legacy SAVE_DT_SLOT fixed mapping ("entry 8 = pre-save, 9-18 = saves
 * 0-9") only holds when every declared data slot loaded a file — the
 * Pocket compacts the table to LOADED slots, so layouts with optional
 * slots (Diablo) shift every save entry and legacy commits land on the
 * wrong entry: the ini gets flushed at the save archive's size and the
 * save file is never created.  On layouts where the fixed mapping is
 * valid (Duke3D/Quake) the resolved address equals the legacy constant,
 * so behavior there is unchanged by construction.
 *
 * Old bitstreams (bit 24 absent) fall back to the legacy mapping —
 * unchanged behavior, no worse than today, for every {old,new} pairing. */
static int nvslot_commit_size(uint32_t data_slot_id, const nvslot_map_t *map,
                              uint32_t size, uint32_t *entry_out)
{
    if (hw_feature_present(OF_HW_SAVE_DT_WORD)) {
        uint32_t entry;
        if (of_file_datatable_entry_for_slot(data_slot_id, &entry) < 0) {
            /* No datatable entry: the host never loaded/created a file
             * for this slot (data.json missing a filename / create
             * flags), so there is nothing to size — and a positional
             * write would land on some OTHER file's entry.  Refuse. */
            static int warned_missing;
            if (!warned_missing) {
                warned_missing = 1;
                of_term_printf("[save] slot %u has NO datatable entry; "
                               "size commit refused (data.json filename/"
                               "create flags missing?)\n",
                               (unsigned)data_slot_id);
            }
            return -1;
        }
        /* SAVE_DT_WORD arms word-addressed routing; the SAVE_DT_SIZE
         * write commits the pair across the FPGA-side CDC into exactly
         * that datatable word. */
        SAVE_DT_WORD = entry * 2u + 1u;
        SAVE_DT_SIZE = size;
        if (entry_out)
            *entry_out = entry;
    } else {
        /* Legacy: SAVE_DT_SLOT selects save slot 0-9 or the pre-save
         * sentinel; SAVE_DT_SIZE commits the pair. */
        SAVE_DT_SLOT = map->dt_slot;
        SAVE_DT_SIZE = size;
        if (entry_out)
            *entry_out = (map->dt_slot == NV_DT_SLOT_PRESAVE)
                       ? 8u : 9u + (uint32_t)map->dt_slot;
    }
    fence();
    return 0;
}

int of_nvslot_set_size(uint32_t data_slot_id, uint32_t size) {
    nvslot_map_t map;
    if (!of_nvslot_is_supported(data_slot_id) ||
        nvslot_map(data_slot_id, &map) < 0)
        return -1;
    if (size > map.capacity)
        size = map.capacity;

    return nvslot_commit_size(data_slot_id, &map, size, 0);
}

int of_nvslot_flush_size(uint32_t data_slot_id, uint32_t size) {
    nvslot_map_t map;
    if (!of_nvslot_is_supported(data_slot_id) ||
        nvslot_map(data_slot_id, &map) < 0)
        return -1;
    if (size > map.capacity)
        size = map.capacity;

    uint32_t entry = 0;
    int rc = nvslot_commit_size(data_slot_id, &map, size, &entry);
    if (rc < 0)
        return rc;

    /* Diagnostic: read the datatable size word back and compare with what
     * the SAVE_DT commit was meant to write.  The host's exit-time
     * nonvolatile writeback truncates each save file to this value, so a
     * mismatch here is exactly "saves vanish on exit".  With the
     * entry-resolved path this readback targets the entry the commit
     * ACTUALLY wrote (id-scanned), so it now genuinely catches mismatches
     * instead of agreeing with the same wrong positional guess. */
    {
        uint32_t got = 0xFFFFFFFFu;
        of_timer_delay_us(100);  /* let the commit cross the CDC + arbiter */
        if (of_file_datatable_word(entry * 2u + 1u, &got) == 0)
            of_term_printf("[save] slot %u dt[%u] size: want=%u got=%u%s\n",
                           (unsigned)data_slot_id, (unsigned)entry,
                           (unsigned)size, (unsigned)got,
                           got == size ? "" : "  <-- MISMATCH");
        else
            of_term_printf("[save] slot %u dt readback FAILED\n",
                           (unsigned)data_slot_id);
    }

    cram0_cpu_hold_count = 0;
    cram0_release_to_bridge();

    uint32_t chunk = OF_TARGET_SAVE_WRITEBACK_CHUNK_SIZE;
    if (chunk == 0 || chunk > map.capacity)
        chunk = map.capacity;

    return of_file_slot_write_chunked(data_slot_id, 0,
                                      map.bridge_addr, size, chunk);
}
