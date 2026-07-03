//------------------------------------------------------------------------------
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileType: SOURCE
// SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
//------------------------------------------------------------------------------

/*
 * openfpgaOS File HAL
 * Low-level file I/O through the Analogue Pocket bridge
 */

#ifndef OFOS_FILE_H
#define OFOS_FILE_H

#include <stdint.h>
#include "of_error.h"

/* Initialize file subsystem */
void of_file_init(void);

/* Per-instance root for the nonvolatile game files (e.g. "/games/Quake").
 * Empty selects the legacy image-root layout.  Set by the kernel during an
 * in-OS relaunch before re-reading the instance's os.ini.  Targets without a
 * writable instance tree (Pocket: the host supplies per-instance files) carry
 * a no-op implementation, so callers stay target-agnostic.  Pass NULL or ""
 * to clear back to the root layout. */
void of_file_set_instance_root(const char *root);
const char *of_file_get_instance_root(void);

/* Tear down per-app file state ahead of an in-OS relaunch: close any cached
 * backing-file handles (so no FatFs lock or unsynced write leaks), cancel a
 * pending async read, and force the dynamic asset registry to re-enumerate for
 * the next app.  No-op on targets that don't support relaunch. */
void of_file_relaunch_reset(void);

/* Enumerate game instances: subdirectories of /games that contain an os.ini.
 * Writes up to `max` directory names into the flat buffer `names`, one every
 * `stride` bytes (NUL-terminated).  Returns the number written.  0 on targets
 * without an instance tree. */
int of_file_list_instances(char *names, uint32_t stride, uint32_t max);

/* Set idle hook — called during DMA/bridge polling waits.
 * Apps use this for background work (audio, input) during file I/O.
 * Set to NULL to disable. */
void of_file_set_idle_hook(void (*hook)(void));

/* Check and handle shutdown handshake from bridge.
 * Flushes D-cache and acknowledges if bridge requests shutdown. */
void of_check_shutdown(void);

/* Boot has finished (kernel/main.c calls this once, right before handing
 * control to the app).  Targets may relax fail-fast transport policies
 * that must hold during init/probe: MiSTer arms the DS ERR_TIMEOUT retry
 * loop for operational reads here — at core load the HPS legitimately
 * doesn't service sector ops for a while, so boot-time ops must fail
 * fast (retrying there blank-screens the core), while the same error
 * during gameplay means "host busy in the OSD" and is retried.  No-op on
 * Pocket/sim. */
void of_file_boot_complete(void);

/* Read data from a file slot into memory.
 * slot_id: APF file slot ID
 * slot_offset: byte offset within slot
 * dest: destination address
 * length: bytes to read
 * Returns 0 on success, negative on error. */
int of_file_read(uint32_t slot_id, uint32_t slot_offset,
                  void *dest, uint32_t length);

/* Read data from a slot in chunks.
 * SDRAM destinations use direct bridge DMA for aligned cache-line ranges.
 * Unaligned edges and non-SDRAM destinations bounce through CRAM0. */
int of_file_read_chunked(uint32_t slot_id, uint32_t slot_offset,
                          void *dest, uint32_t total);

/* Flush a data slot to SD card (Data Slot Write command).
 * Tells the bridge to read from bridge_addr and write to the slot's .sav file.
 * slot_id: APF nonvolatile data slot ID (config/settings or save)
 * bridge_addr: source address in bridge address space (0x20xxxxxx for CRAM0)
 * length: bytes to write
 * Returns 0 on success, negative on error. */
int of_file_slot_write(uint32_t slot_id, uint32_t bridge_addr, uint32_t length);

/* Write a portion of a data slot to SD card at a specific file offset.
 * Like of_file_slot_write but writes to slot_offset within the .sav file. */
int of_file_slot_write_at(uint32_t slot_id, uint32_t slot_offset,
                           uint32_t bridge_addr, uint32_t length);

/* Write a larger source range as multiple Data Slot Write commands.
 * chunk_size == 0 selects one command for the whole range. */
int of_file_slot_write_chunked(uint32_t slot_id, uint32_t slot_offset,
                                uint32_t bridge_addr, uint32_t total,
                                uint32_t chunk_size);

/* Raw bridge DMA: issue a read command and wait for completion.
 * No cache flush — caller is responsible for cache coherency.
 * CRAM0 targets are limited by OF_TARGET_CRAM0_DMA_CHUNK_SIZE; SDRAM
 * targets are limited by DMA_CHUNK_SIZE. */
int of_file_read_raw(uint32_t slot_id, uint32_t slot_offset,
                      uint32_t bridge_addr, uint32_t length);

/* Get the filename for a data slot from the bridge.
 * Returns 0 on success, <0 on error or empty slot. */
int of_file_get_name(uint32_t slot_id, char *name_out, uint32_t name_max);

/* Registry-miss fall-through: resolve a BASENAME directly against the
 * target's backing store and return a read-only slot id usable with
 * of_file_read/of_file_size64, or -1.  The id must stay stable until the
 * next relaunch/init (fds and the kernel io cache key on it).  MiSTer
 * searches its mounted FAT volumes (S1 instance -> S2 borrow -> S0 family)
 * and hands out overflow ids beyond the enumerated slot registry, so
 * libraries larger than the registry stay reachable by name; targets whose
 * host enumerates every file up front (Pocket/sim) return -1. */
int of_file_resolve_name(const char *name);

/* By-NAME nonvolatile config slots.  Per-game settings are saved through
 * fopen("<name>.cfg", "wb") (e.g. chocolate-doom M_SaveDefaults with a
 * -config filename); the backing store is a PREALLOCATED /config/<name>
 * file on the target's pinned nonvolatile volume.  of_file_config_slot
 * resolves such a basename to a slot id in
 * [OF_FILE_CFG_SLOT_BASE, OF_FILE_CFG_SLOT_BASE + OF_FILE_CFG_SLOT_COUNT)
 * that works with the full of_nvslot_* API exactly like slots 8/9, or -1
 * when no preallocated backing file exists for that name.  The durability
 * contract holds: files are never created/extended/truncated at runtime —
 * O_TRUNC maps to a logical rewrite from 0 (of_nvslot_set_size(id, 0)),
 * and a missing backing file is a clean open failure, never a create.
 * Ids are stable until relaunch/init.  Targets without named-config
 * support (Pocket/sim: the host owns settings persistence) return -1. */
#define OF_FILE_CFG_SLOT_BASE  96u
#define OF_FILE_CFG_SLOT_COUNT 8u
int of_file_config_slot(const char *name);

/* Query APF datatable metadata for a data slot ID.
 * APF exposes two words per datatable entry: concrete file id, then
 * current size. Returns the value on success, or <0 if the slot has no
 * datatable entry matching that id. */
/* Raw datatable word readout (word = entry*2 for id, entry*2+1 for size).
 * Boot diagnostic for the position-indexed entry layout contract. */
int of_file_datatable_word(uint32_t word, uint32_t *value_out);

/* Resolve a data slot id to its datatable ENTRY index by scanning the
 * entries' id words (never the positional fast path — see file.c).  Used
 * by save.c's entry-resolved size commits (OF_HW_SAVE_DT_WORD).  Returns
 * <0 when no entry holds that id (file never loaded/created). */
int of_file_datatable_entry_for_slot(uint32_t slot_id, uint32_t *entry_out);

long of_file_flags(uint32_t slot_id);
long of_file_size(uint32_t slot_id);      /* legacy/saturating on rv32 */
int64_t of_file_size64(uint32_t slot_id);

/* ======================================================================
 * Async file read — non-blocking DMA with callback on completion
 * ====================================================================== */

/* Start a non-blocking file read. Returns a token (>= 0) on success,
 * or negative error if a read is already in flight.
 * The callback is called from the data-slot completion IRQ with
 * (token, result), where result is 0 on success or <0 on bridge error.
 * of_file_async_poll() is only a compatibility/fallback drain.
 * Only one async read can be in flight at a time (bridge limitation).
 * CRAM0 destinations use direct DMA; other destinations are filled through
 * an internal CRAM0 bounce buffer before callback dispatch. */
int of_file_read_async(uint32_t slot_id, uint32_t slot_offset,
                       void *dest, uint32_t length,
                       void (*callback)(int token, int result));

/* Poll async read progress. Optional: completion is IRQ-driven on Pocket.
 * Returns 1 once per completed read, 0 otherwise. */
int of_file_async_poll(void);

/* Check if an async read is currently in flight. */
int of_file_async_busy(void);

/* App-visible CRAM0 staging memory for async file reads.  This prevents apps
 * from hardcoding CRAM0 addresses while keeping the bridge DMA target in the
 * region the hardware supports. */
void *of_file_dma_stage_alloc(uint32_t size, uint32_t align);
int of_file_dma_stage_reset(void);
uint32_t of_file_async_max_read(void);
uint32_t of_file_dma_stage_size(void);

/* Service a data-slot completion IRQ. Called by the central IRQ dispatcher;
 * exported so the async path can share the same completion logic with the
 * polling fallback. */
void of_file_async_irq_service(void);

#endif /* OFOS_FILE_H */
