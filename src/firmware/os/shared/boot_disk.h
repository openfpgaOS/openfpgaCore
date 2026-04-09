/*
 * shared/boot_disk.h — boot-ROM resident disk read service
 *
 * The boot ROM may, during early boot, establish a working channel
 * for streaming data slot reads from a development host. If it
 * succeeds, it publishes three symbols in BRAM that the OS can
 * call to use the same transport for app-slot loads:
 *
 *   boot_disk_available    — non-zero iff the channel is usable.
 *   boot_disk_read(...)    — read `length` bytes from `slot_id`
 *                            at `slot_offset` into `dest`.
 *                            Returns 0 / OF_ERR_*.
 *   boot_disk_size(slot)   — returns the slot size in bytes, or
 *                            -1 if the host has nothing for it.
 *
 * All three live in the .bss.boot / .text.boot regions (BRAM) and
 * remain valid for the lifetime of the kernel — the BRAM region is
 * never reused after boot. The boot-side implementation masks the
 * machine-mode external interrupt for the duration of each call so
 * the kernel's irq.c cannot drain UART_RX_DATA out from under the
 * polled receiver; OS callers must NOT mask MIE themselves.
 */

#ifndef OFOS_SHARED_BOOT_DISK_H
#define OFOS_SHARED_BOOT_DISK_H

#include <stdint.h>

extern volatile int boot_disk_available;

int boot_disk_read(uint32_t slot_id, uint32_t slot_offset,
                   void *dest, uint32_t length);

/* Query the size in bytes of `slot_id` without reading any data.
 * Returns the size on success or -1 if the host has no override
 * queued for the slot. Leaves the host mid-stream; the next
 * boot_disk_read for the same slot restarts the stream from
 * offset 0 and tolerates any stale chunks left in the UART FIFO. */
long boot_disk_size(uint32_t slot_id);

#endif /* OFOS_SHARED_BOOT_DISK_H */
