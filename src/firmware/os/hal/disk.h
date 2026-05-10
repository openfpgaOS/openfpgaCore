/*
 * openfpgaOS Disk HAL — pluggable read-side backends
 *
 * Two backends provide file-slot READ:
 *   - disk_boot:   forwards to the boot ROM's BRAM-resident disk
 *                  read service (shared/boot_disk.h). The wire
 *                  protocol lives entirely in BRAM and is opaque
 *                  to the OS.
 *   - disk_bridge: APF data slot via bridge DMA → SD card.
 *
 * The kernel auto-probes both at boot and selects the first that
 * responds (disk_boot first, so the kernel can use the boot ROM's
 * transport when a service host is attached and bypass the bridge
 * entirely). The selection is read-only — file WRITES (saves)
 * always go through the bridge directly via the existing
 * of_file_slot_write API. See file.h.
 */

#ifndef OFOS_HAL_DISK_H
#define OFOS_HAL_DISK_H

#include <stdint.h>

/* Driver vtable. All fields required. */
typedef struct {
    const char *name;

    /* Returns 1 if the backend is usable in the current
     * environment, 0 otherwise. Called once at of_disk_init.
     * Must be cheap (~ms) and side-effect-free. */
    int (*probe)(void);

    /* Read `length` bytes from data slot `slot_id` starting at
     * `slot_offset` into `dest`. Returns 0 on success, negative
     * on error (matching of_file_read's existing convention). */
    int (*read)(uint32_t slot_id, uint32_t slot_offset,
                void *dest, uint32_t length);

    /* Returns the size in bytes of `slot_id`, or -1 if the slot
     * is empty / unavailable. Used by the kernel loader to detect
     * missing app slots before issuing a read (so the bridge can't
     * hang on a never-acked DMA, and so empty UART slots fail
     * fast instead of timing out mid-stream). */
    long (*size)(uint32_t slot_id);
} of_disk_driver_t;

/* Probe both backends in priority order, pick the first that
 * answers, install it as the active read driver. Must be called
 * before any of_file_read / of_disk_read.
 *
 * On entry the active driver is NULL — calls to of_disk_read
 * will return OF_ERR_NOT_SUPPORTED. After this returns the active driver is
 * either disk_boot, disk_bridge, or NULL (no backend usable). */
void of_disk_init(void);

/* Dispatch a read to the active backend. Same signature and
 * return convention as of_file_read. Used internally by
 * of_file_read; callers should keep using of_file_read. */
int of_disk_read(uint32_t slot_id, uint32_t slot_offset,
                 void *dest, uint32_t length);

/* Dispatch a slot-size query to the active backend. Returns the
 * slot size in bytes, or -1 if the slot is empty / the backend
 * can't answer. The kernel loader uses this to bail out cleanly
 * on missing app slots without triggering a backend-specific hang. */
long of_disk_size(uint32_t slot_id);

/* Returns the name of the active backend ("boot", "bridge", or
 * "none") for status display. Never NULL. */
const char *of_disk_active_name(void);

/* Returns a pointer to the active driver descriptor, or NULL if
 * no backend is installed. Lets callers compare against a known
 * backend by pointer (e.g. file.c's bridge-only warmup DMA) without
 * resorting to string compares. */
const of_disk_driver_t *of_disk_active(void);

/* Backend instances. Defined in their respective .c files. */
extern const of_disk_driver_t of_disk_bridge;
extern const of_disk_driver_t of_disk_boot;

#endif /* OFOS_HAL_DISK_H */
