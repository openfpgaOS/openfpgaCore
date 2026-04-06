/*
 * openfpgaOS Capability Descriptor Table
 *
 * Populates the of_capabilities struct at a fixed BRAM address before
 * launching the application. Apps read this struct to discover the
 * platform, memory layout, and available hardware features.
 *
 * The struct lives at 0x7800 in BRAM — fast access, no D-cache pollution.
 * Must not overlap app BRAM (0x2000-0x7800) or libc table (0x7C00-0x7E00).
 */

#include "caps_table.h"
#include "../../api/of_caps.h"
#include "../../api/of_libc.h"
#include "../../api/of_services.h"
#include "../../api/of_version.h"
#include "../hal/regs.h"

/* Sample pool constants — must match mixer.c */
#define SAMPLE_POOL_BASE  (CRAM1_UNCACHED + 0x00400000)
#define SAMPLE_POOL_SIZE  (11 * 1024 * 1024)  /* 11 MB */

void caps_table_init(uintptr_t heap_base) {
    struct of_capabilities *caps = (struct of_capabilities *)OF_CAPS_ADDR;

    caps->magic   = OF_CAPS_MAGIC;
    caps->version = OF_CAPS_VERSION;

    /* Memory regions */
    caps->heap_base   = (uint32_t)heap_base;
    caps->heap_size   = (SDRAM_BASE + SDRAM_SIZE) - (uint32_t)heap_base - RUNTIME_STACK_SIZE;
    caps->fb_base     = FB0_BASE;
    caps->fb_size     = FB_SIZE;
    caps->fb_width    = FB_WIDTH;
    caps->fb_height   = FB_HEIGHT;
    caps->fb_stride   = FB_STRIDE;
    caps->sample_base = SAMPLE_POOL_BASE;
    caps->sample_size = SAMPLE_POOL_SIZE;

    /* Hardware features — read from RTL register (single source of truth) */
    caps->hw_features   = HW_FEATURES;
    caps->mixer_voices  = 32;
    caps->mixer_rate    = 48000;

    /* Platform identity */
    caps->platform_id   = OF_PLATFORM_POCKET;
    caps->core_variant  = 0;    /* default variant */
    caps->sdram_size    = SDRAM_SIZE;
    caps->cram_size     = CRAM_SIZE;

    /* OS info */
    caps->os_version      = OF_API_VERSION;
    caps->cpu_freq_hz     = CPU_FREQ_HZ;
    caps->libc_table      = OF_LIBC_ADDR;
    caps->services_table  = OF_SVC_ADDR;
}
