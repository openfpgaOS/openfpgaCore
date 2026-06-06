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

/* Open (or fetch the cached handle for) a slot's backing file.
 * writable=1 reopens read-only handles in FA_READ|FA_WRITE.
 * Returns NULL if the image is unmounted or the file doesn't exist. */
FIL *mister_file_open_slot(uint32_t slot_id, int writable);

/* Drop a cached handle (closes the file). */
void mister_file_drop_slot(uint32_t slot_id);

#endif /* OFOS_MISTER_FILE_H */
