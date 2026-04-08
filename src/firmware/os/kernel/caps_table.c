/*
 * openfpgaOS Capability Descriptor Table
 *
 * Populates the of_capabilities struct at a fixed BRAM address before
 * launching the application. Apps read this struct to discover the
 * platform, memory layout, and available hardware features.
 *
 * The struct lives at 0x7800 in BRAM — fast access, no D-cache pollution.
 * Must not overlap app BRAM (0x2000-0x7800) or trap stack region.
 */

#include "caps_table.h"
#include "../../api/of_caps.h"
#include "../../api/of_services.h"
#include "../../api/of_version.h"
#include "../hal/regs.h"

void caps_table_init(uintptr_t heap_base) {
    const of_target_platform_t *platform = of_target_platform_get();
    struct of_capabilities *caps = (struct of_capabilities *)OF_CAPS_ADDR;
    uint32_t features = HW_FEATURES;

    caps->magic   = OF_CAPS_MAGIC;
    caps->version = OF_CAPS_VERSION;

    /* Memory regions */
    caps->heap_base   = (uint32_t)heap_base;
    caps->heap_size   = (platform->sdram_base + platform->sdram_size)
                      - (uint32_t)heap_base - platform->runtime_stack_size;
    caps->fb_base     = platform->fb_bases[0];
    caps->fb_size     = FB_SIZE;
    caps->fb_width    = platform->fb_width;
    caps->fb_height   = platform->fb_height;
    caps->fb_stride   = platform->fb_stride;
    caps->sample_base = platform->sample_base;
    caps->sample_size = platform->sample_size;

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
    caps->services_table  = OF_SVC_ADDR;
}
