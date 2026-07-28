//------------------------------------------------------------------------------
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileType: SOURCE
// SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
//------------------------------------------------------------------------------

/*
 * openfpgaOS Save File HAL
 * Nonvolatile CRAM0-window save system (persisted to SD card by bridge).
 * v2 arch: CRAM1 retired, save slots moved into CRAM0 via CRAM0_MODE mux.
 */

#ifndef OFOS_SAVE_H
#define OFOS_SAVE_H

#include <stdint.h>
#include "regs.h"

#define SAVE_MAX_SLOTS      OF_TARGET_SAVE_MAX_SLOTS
#define SAVE_SLOT_SIZE      OF_TARGET_SAVE_SLOT_SIZE
#define SAVE_REGION_ADDR    OF_TARGET_SAVE_REGION_ADDR

/* Initialize save subsystem */
void of_save_init(void);

/* Hold/release CPU ownership of the save CRAM0 window across a group
 * of save operations. Calls may nest; the bridge regains ownership
 * when the outermost hold is released. */
void of_save_begin_cpu(void);
void of_save_end_cpu(void);

/* Core-exit security wipe: zero the non-save CRAM0 regions (stale boot
 * staging, DMA scratch, app file-staging pool) so transient loaded data
 * cannot survive a warm reset.  PRESERVES the nonvolatile presave + save
 * window the bridge persists to SD.  Must be called while the CPU still owns
 * CRAM0 — i.e. before of_check_shutdown hands the mux to the bridge. */
void of_save_security_wipe(void);

/* Generic nonvolatile APF data slots backed by the CRAM0 window.
 * TWO independent config windows exist and may be used SIMULTANEOUSLY
 * (Diablo: id 8 = stash, id 9 = settings):
 *   id 9  -> the presave window   (bridge 0x200C0000, before the saves)
 *   id 8  -> the shared-config /
 *            save-meta window     (bridge 0x20380000, after the saves)
 * plus game save ids 10-19 (10 slots x 256 KB).  Historical note: ids
 * 8 and 9 used to alias one window and clobbered each other; the
 * nvslot_map in targets/<t>/save.c is the authority. */
int of_nvslot_is_supported(uint32_t data_slot_id);
uint32_t of_nvslot_capacity(uint32_t data_slot_id);
int of_nvslot_read(uint32_t data_slot_id, void *buf,
                   uint32_t offset, uint32_t len);
int of_nvslot_write(uint32_t data_slot_id, const void *buf,
                    uint32_t offset, uint32_t len);
int of_nvslot_set_size(uint32_t data_slot_id, uint32_t size);
int of_nvslot_flush_size(uint32_t data_slot_id, uint32_t size);

#endif /* OFOS_SAVE_H */
