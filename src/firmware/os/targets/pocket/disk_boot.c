/*
 * openfpgaOS Disk HAL — boot-ROM backend wrapper
 *
 * Thin shim that exposes the boot ROM's BRAM-resident disk service
 * (shared/boot_disk.h) as an of_disk_driver_t. The OS knows nothing
 * about the wire protocol used to talk to the host — that lives
 * entirely in boot.c, in BRAM.
 *
 * Probe is a single load: if the boot ROM set boot_disk_available,
 * the channel is usable; otherwise the dispatcher falls through to
 * the bridge backend.
 *
 * The boot.c implementation handles its own MIE masking, so this
 * wrapper just forwards the call.
 */

#include "disk.h"
#include "boot_disk.h"

static int boot_probe(void) {
    return boot_disk_available ? 1 : 0;
}

static int boot_read(uint32_t slot_id, uint32_t slot_offset,
                     void *dest, uint32_t length) {
    return boot_disk_read(slot_id, slot_offset, dest, length);
}

static long boot_size(uint32_t slot_id) {
    return boot_disk_size(slot_id);
}

const of_disk_driver_t of_disk_boot = {
    .name  = "UART",
    .probe = boot_probe,
    .read  = boot_read,
    .size  = boot_size,
};
