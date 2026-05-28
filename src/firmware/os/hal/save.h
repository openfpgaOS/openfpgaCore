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

/* Generic nonvolatile APF data slots backed by the CRAM0 window.
 * Supports the one pre-save config/settings slot (id 8 or 9, depending
 * on data.json) plus game save ids 10-19. */
int of_nvslot_is_supported(uint32_t data_slot_id);
uint32_t of_nvslot_capacity(uint32_t data_slot_id);
int of_nvslot_read(uint32_t data_slot_id, void *buf,
                   uint32_t offset, uint32_t len);
int of_nvslot_write(uint32_t data_slot_id, const void *buf,
                    uint32_t offset, uint32_t len);
int of_nvslot_set_size(uint32_t data_slot_id, uint32_t size);
int of_nvslot_flush_size(uint32_t data_slot_id, uint32_t size);

/* Read data from save slot. Returns bytes read, or -1 on error. */
int of_save_read(int slot, void *buf, uint32_t offset, uint32_t len);

/* Write data to save slot. Returns bytes written, or -1 on error. */
int of_save_write(int slot, const void *buf, uint32_t offset, uint32_t len);

/* Flush save slot to SD card via bridge (full SAVE_SLOT_SIZE).
 * Returns 0 on success, <0 on error. */
int of_save_flush(int slot);

/* Flush save slot with a specific size (for apps with smaller saves).
 * Size must not exceed SAVE_SLOT_SIZE. */
int of_save_flush_size(int slot, uint32_t size);

/* Update the APF datatable size for a save slot without issuing an
 * immediate Data Slot Write. */
int of_save_set_size(int slot, uint32_t size);

/* Compute and store CRC32 for a save slot (in metadata region).
 * Call after writing all data, before or after flush. */
void of_save_update_crc(int slot);

/* Verify save slot CRC integrity.
 * Returns 0 if CRC matches, -1 on bad params, -2 if no valid CRC,
 * -3 if CRC mismatch. */
int of_save_check(int slot);

/* Get usable data size of a save slot */
uint32_t of_save_get_size(int slot);

/* Erase a save slot (fill with 0xFF, clear footer) */
void of_save_erase(int slot);

#endif /* OFOS_SAVE_H */
