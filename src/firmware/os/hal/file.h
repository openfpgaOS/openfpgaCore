/*
 * openfpgaOS File HAL
 * Low-level file I/O through the Analogue Pocket bridge
 */

#ifndef OFOS_FILE_H
#define OFOS_FILE_H

#include <stdint.h>
#include "../../api/of_error.h"

/* File commands (written to DS_COMMAND register) */
#define OF_FILE_CMD_READ       1
#define OF_FILE_CMD_WRITE      2
#define OF_FILE_CMD_OPENFILE   3

/* Initialize file subsystem */
void of_file_init(void);

/* Set idle hook — called during DMA/bridge polling waits.
 * Apps use this for background work (audio, input) during file I/O.
 * Set to NULL to disable. */
void of_file_set_idle_hook(void (*hook)(void));

/* Check and handle shutdown handshake from bridge.
 * Flushes D-cache and acknowledges if bridge requests shutdown. */
void of_check_shutdown(void);

/* Read data from a file slot into SDRAM.
 * slot_id: APF file slot ID
 * slot_offset: byte offset within slot
 * dest: destination address (must be in SDRAM)
 * length: bytes to read
 * Returns 0 on success, negative on error. */
int of_file_read(uint32_t slot_id, uint32_t slot_offset,
                  void *dest, uint32_t length);

/* Read data from a slot in chunks.
 * dest can be in SDRAM (direct DMA) or CRAM/SRAM (bounced through SDRAM).
 * For non-SDRAM destinations, data is DMA'd to a bounce buffer first,
 * then copied to the target address. */
int of_file_read_chunked(uint32_t slot_id, uint32_t slot_offset,
                          void *dest, uint32_t total);

/* Get the size of a file slot in bytes.
 * Uses the bridge open-file command to query the slot.
 * Returns size on success, negative on error. */
long of_file_size(uint32_t slot_id);

/* Flush a data slot to SD card (Data Slot Write command).
 * Tells the bridge to read from bridge_addr and write to the slot's .sav file.
 * slot_id: APF data slot ID (10-19 for saves)
 * bridge_addr: source address in bridge address space (0x30xxxxxx for CRAM1)
 * length: bytes to write
 * Returns 0 on success, negative on error. */
int of_file_slot_write(uint32_t slot_id, uint32_t bridge_addr, uint32_t length);

/* Write a portion of a data slot to SD card at a specific file offset.
 * Like of_file_slot_write but writes to slot_offset within the .sav file. */
int of_file_slot_write_at(uint32_t slot_id, uint32_t slot_offset,
                           uint32_t bridge_addr, uint32_t length);

/* Raw bridge DMA: issue a read command and wait for completion.
 * No cache flush — caller is responsible for cache coherency.
 * Used by I/O cache for CRAM1 targets where no SDRAM flush is needed. */
int of_file_read_raw(uint32_t slot_id, uint32_t slot_offset,
                      uint32_t bridge_addr, uint32_t length);

/* Invalidate D-cache for CRAM cached aliases after bridge writes.
 * Call after any bridge operation that writes to CRAM (dataslot load,
 * save restore) so CPU reads via 0x30/0x31 see fresh data. */
void of_file_inval_cram(uint32_t bridge_addr, uint32_t length);

#endif /* OFOS_FILE_H */
