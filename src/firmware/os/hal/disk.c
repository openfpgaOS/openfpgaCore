//------------------------------------------------------------------------------
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileType: SOURCE
// SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
//------------------------------------------------------------------------------

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

/* Lazy re-probe: of_disk_init runs once inside of_init(), BEFORE anything
 * guarantees the medium exists (on MiSTer the HPS mounts .vhd images with
 * no ordering against boot).  If no backend answered then, active_disk
 * stayed NULL — without this, every later read would fail forever even
 * after the image appears.  Probes are cheap and side-effect-free by
 * contract, so retrying on each access while unbound is safe; once a
 * backend answers, the normal selection sticks. */
static void reprobe_if_unbound(void) {
    if (!active_disk)
        of_disk_init();
}

int of_disk_read(uint32_t slot_id, uint32_t slot_offset,
                 void *dest, uint32_t length) {
    reprobe_if_unbound();
    if (!active_disk)
        return OF_ERR_NOT_SUPPORTED;
    int rc = active_disk->read(slot_id, slot_offset, dest, length);
    /* If the boot backend (PHDP) failed, fall back to the bridge (SD).
     * The host may not have every file the app needs — the SD card is
     * the ground truth and should always work headless. Once SD answers,
     * keep using it; mixing PHDP size queries with SD reads leaves stale
     * UART packets behind and can make app-load failures look random. */
    if (rc < 0 && active_disk == &of_disk_boot) {
        rc = of_disk_bridge.read(slot_id, slot_offset, dest, length);
        if (rc == 0)
            active_disk = &of_disk_bridge;
    }
    return rc;
}

long of_disk_size(uint32_t slot_id) {
    reprobe_if_unbound();
    if (!active_disk)
        return -1;
    long sz = active_disk->size(slot_id);
    if (sz < 0 && active_disk == &of_disk_boot) {
        sz = of_disk_bridge.size(slot_id);
        if (sz >= 0)
            active_disk = &of_disk_bridge;
    }
    return sz;
}
