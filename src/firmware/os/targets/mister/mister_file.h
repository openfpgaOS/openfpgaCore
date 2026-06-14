//------------------------------------------------------------------------------
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileType: SOURCE
// SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
//------------------------------------------------------------------------------

/*
 * MiSTer target-internal file helpers shared between file.c and save.c.
 * The FIL handle cache is owned by file.c; save.c reuses it so FatFs file
 * locking (FF_FS_LOCK) never sees conflicting opens of the same slot.
 */

#ifndef OFOS_MISTER_FILE_H
#define OFOS_MISTER_FILE_H

#include <stdint.h>
#include "fatfs/ff.h"

/* Per-instance root (of_file_set/get_instance_root) is declared in hal/file.h
 * and consumed by slot_path() in file.c. */

/* Open (or fetch the cached handle for) a slot's backing file.
 * writable=1 reopens read-only handles in FA_READ|FA_WRITE.
 * Returns NULL if the image is unmounted or the file doesn't exist. */
FIL *mister_file_open_slot(uint32_t slot_id, int writable);

/* Drop a cached handle (closes the file). */
void mister_file_drop_slot(uint32_t slot_id);

/*
 * FatFs reentrancy guard (FF_FS_REENTRANT=0).
 *
 * Async completion callbacks are delivered from the periodic timer IRQ
 * (kernel/irq.c → of_check_shutdown → of_file_async_irq_service), and a
 * callback may legally issue new file I/O.  On the Pocket that is safe —
 * the backend is the APF bridge — but here every file op runs through
 * non-reentrant FatFs, so the IRQ drain must never fire while the main
 * thread is inside the filesystem.
 *
 * Every public file.c/save.c entry that can reach an f_* call brackets
 * itself with enter/exit (a depth counter, so nesting is fine); the IRQ
 * drain skips delivery while the depth is nonzero and the completion is
 * picked up by the next drain tick or of_file_async_poll().
 */
extern volatile int mister_fs_depth;
static inline void mister_fs_enter(void) { mister_fs_depth++; }
static inline void mister_fs_exit(void)  { mister_fs_depth--; }

#endif /* OFOS_MISTER_FILE_H */
