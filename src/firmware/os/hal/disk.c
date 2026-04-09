/*
 * openfpgaOS Disk HAL — dispatcher
 *
 * Probes the available read backends in priority order and
 * installs the first one that answers as the active driver.
 * See disk.h for the contract and rationale.
 */

#include "disk.h"
#include "of_error.h"

/* Active backend after of_disk_init. NULL means no backend
 * is usable — of_disk_read will return OF_ERR_NOT_SUPPORTED. */
static const of_disk_driver_t *active_disk = (void *)0;

void of_disk_init(void) {
    /* Probe order:
     *
     *   1. Boot — forwards to a function pointer the boot ROM
     *      published in BRAM. Bypasses the bridge entirely; the
     *      probe is a single load of boot_disk_available. The OS
     *      doesn't know what wire protocol the boot ROM uses to
     *      reach the host (opaque to us).
     *
     *   2. Bridge — APF data slot DMA → SD card. Default in
     *      production, only path that works headless. Probe
     *      checks the bridge READY status bit.
     *
     * If both fail, active_disk stays NULL and of_disk_read
     * returns OF_ERR_NOT_SUPPORTED; file I/O will fail cleanly without
     * deadlocking.
     */
    if (of_disk_boot.probe()) {
        active_disk = &of_disk_boot;
        return;
    }
    if (of_disk_bridge.probe()) {
        active_disk = &of_disk_bridge;
        return;
    }
    active_disk = (void *)0;
}

const of_disk_driver_t *of_disk_active(void) {
    return active_disk;
}

int of_disk_read(uint32_t slot_id, uint32_t slot_offset,
                 void *dest, uint32_t length) {
    if (!active_disk)
        return OF_ERR_NOT_SUPPORTED;
    return active_disk->read(slot_id, slot_offset, dest, length);
}

long of_disk_size(uint32_t slot_id) {
    if (!active_disk)
        return -1;
    return active_disk->size(slot_id);
}

const char *of_disk_active_name(void) {
    return active_disk ? active_disk->name : "none";
}
