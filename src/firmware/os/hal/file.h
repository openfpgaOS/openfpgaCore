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

/* Set idle hook — called during DMA/bridge polling waits.
 * Apps use this for background work (audio, input) during file I/O.
 * Set to NULL to disable. */
void of_file_set_idle_hook(void (*hook)(void));

/* Check and handle shutdown handshake from bridge.
 * Flushes D-cache and acknowledges if bridge requests shutdown. */
void of_check_shutdown(void);

/* Read data from a file slot into memory.
 * slot_id: APF file slot ID
 * slot_offset: byte offset within slot
 * dest: destination address
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
 * Used by I/O cache for CRAM0 targets where no SDRAM flush is needed
 * (CRAM0 is uncached per PMA in v2 arch).  Length must not exceed
 * DMA_CHUNK_SIZE; use of_file_read_chunked/of_file_read for larger reads. */
int of_file_read_raw(uint32_t slot_id, uint32_t slot_offset,
                      uint32_t bridge_addr, uint32_t length);

/* Invalidate D-cache for CRAM cached aliases after bridge writes.
 * Call after any bridge operation that writes to CRAM (dataslot load,
 * save restore) so CPU reads via 0x30/0x31 see fresh data. */
void of_file_inval_cram(uint32_t bridge_addr, uint32_t length);

/* Get the filename for a data slot from the bridge.
 * Returns 0 on success, <0 on error or empty slot. */
int of_file_get_name(uint32_t slot_id, char *name_out, uint32_t name_max);

/* Query APF datatable metadata for a data slot ID.
 * APF exposes two words per datatable entry: concrete file id, then
 * current size. Returns the value on success, or <0 if the slot has no
 * datatable entry matching that id. */
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
 * dest must be in CRAM0 (direct DMA target). */
int of_file_read_async(uint32_t slot_id, uint32_t slot_offset,
                       void *dest, uint32_t length,
                       void (*callback)(int token, int result));

/* Poll async read progress. Optional: completion is IRQ-driven on Pocket.
 * Returns 1 once per completed read, 0 otherwise. */
int of_file_async_poll(void);

/* Check if an async read is currently in flight. */
int of_file_async_busy(void);

/* Service a data-slot completion IRQ. Called by the central IRQ dispatcher;
 * exported so the async path can share the same completion logic with the
 * polling fallback. */
void of_file_async_irq_service(void);

#endif /* OFOS_FILE_H */
