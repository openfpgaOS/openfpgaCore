/*
 * openfpgaOS Capability Descriptor Table
 *
 * Populates a static of_capabilities struct in kernel BSS during boot
 * and exposes it via caps_table_get(). The pointer is handed to apps
 * through the AT_OF_CAPS auxv tag in elf_exec(), so an app never has
 * to know where the struct lives -- it just calls of_get_caps() (which
 * returns the auxv-supplied pointer) and reads it like any other
 * read-only blob.
 *
 * The struct is plain BSS in the OS .data region, so it ends up in
 * CRAM0 alongside the rest of the kernel. Apps read it through the
 * cached path; the kernel writes it once at boot and never touches
 * it again, so there's no coherency concern.
 */

#include "caps_table.h"
#include "services_table.h"
#include "syscall.h"
#include "of_caps.h"
#include "of_services.h"
#include "of_version.h"
#include "../hal/regs.h"
#include "../hal/mixer.h"

/* Single source of truth for the app-visible cap struct. Lives in BSS
 * (zero-initialized at boot), populated by caps_table_init(). */
static struct of_capabilities g_caps;

void caps_table_init(uintptr_t heap_base) {
    const of_target_platform_t *platform = of_target_platform_get();
    struct of_capabilities *caps = &g_caps;
    uint32_t features = HW_FEATURES;

    caps->magic   = OF_CAPS_MAGIC;
    caps->version = OF_CAPS_VERSION;

    /* Memory regions */
    caps->heap_base   = (uint32_t)heap_base;
    uintptr_t heap_limit = of_brk_limit();
    caps->heap_size   = heap_limit > heap_base
                      ? (uint32_t)(heap_limit - heap_base)
                      : 0;
    caps->fb_base     = platform->fb_bases[0];
    caps->fb_size     = FB_SIZE;
    caps->fb_width    = platform->fb_width;
    caps->fb_height   = platform->fb_height;
    caps->fb_stride   = platform->fb_stride;
    caps->sample_base = of_mixer_reserved_base();
    caps->sample_size = of_mixer_reserved_size();

    /* Hardware features — read from RTL register (single source of truth) */
    caps->hw_features   = features;
    caps->mixer_voices  = (features & HW_FEAT_MIXER) ? 32u : 0u;
    caps->mixer_rate    = (features & HW_FEAT_MIXER) ? 48000u : 0u;

    /* Platform identity */
    caps->platform_id   = platform->platform_id;
    caps->core_variant  = 0;    /* default variant */
    caps->sdram_size    = platform->sdram_size;
    caps->cram_size     = platform->cram_size;

    /* OS info */
    caps->os_version      = OF_API_VERSION;
    caps->cpu_freq_hz     = platform->cpu_freq_hz;
    /* Legacy field: still pointed at the services table for any old app
     * that reads it. New apps get the same pointer via AT_OF_SVC. */
    caps->services_table  = (uint32_t)(uintptr_t)services_table_get();

    /* v2 fields: memory bases for inline accessors that previously
     * baked the addresses into every app .elf. */
    caps->sdram_base          = platform->sdram_base;
    caps->sdram_uncached_base = platform->sdram_uncached_base;
    caps->gpu_base            = platform->gpu_base;
}

const struct of_capabilities *caps_table_get(void) {
    return &g_caps;
}
