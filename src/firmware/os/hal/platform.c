/*
 * openfpgaOS Target Platform Descriptor
 */

#include "platform.h"

static const of_target_platform_t g_target_platform = {
    .platform_id = OF_TARGET_PLATFORM_ID,
    .cpu_freq_hz = OF_TARGET_CPU_FREQ_HZ,

    .bram_base = OF_TARGET_BRAM_BASE,
    .bram_size = OF_TARGET_BRAM_SIZE,
    .app_bram_base = OF_TARGET_APP_BRAM_BASE,
    .app_bram_end = OF_TARGET_APP_BRAM_END,

    .sdram_base = OF_TARGET_SDRAM_BASE,
    .sdram_size = OF_TARGET_SDRAM_SIZE,
    .sdram_uncached_base = OF_TARGET_SDRAM_UNCACHED_BASE,

    .fb_bases = {
        OF_TARGET_FB0_BASE,
        OF_TARGET_FB1_BASE,
        OF_TARGET_FB2_BASE,
    },
    .fb_count = OF_TARGET_FB_COUNT,
    .fb_width = OF_TARGET_FB_WIDTH,
    .fb_height = OF_TARGET_FB_HEIGHT,
    .fb_stride = OF_TARGET_FB_STRIDE,
    .term_fb_base = OF_TARGET_TERM_FB_BASE,

    .dma_chunk_size = OF_TARGET_DMA_CHUNK_SIZE,

    .interact_base = OF_TARGET_INTERACT_BASE,
    .interact_uncached = OF_TARGET_INTERACT_UNCACHED,
    .interact_max_vars = OF_TARGET_INTERACT_MAX_VARS,

    .cram0_base = OF_TARGET_CRAM0_BASE,
    .cram1_base = OF_TARGET_CRAM1_BASE,
    .cram0_uncached = OF_TARGET_CRAM0_UNCACHED,
    .cram1_uncached = OF_TARGET_CRAM1_UNCACHED,
    .cram_size = OF_TARGET_CRAM_SIZE,
    .cram0_bridge = OF_TARGET_CRAM0_BRIDGE,
    .cram1_bridge = OF_TARGET_CRAM1_BRIDGE,
    .cram1_ftab_offset = OF_TARGET_CRAM1_FTAB_OFFSET,
    .cram1_scratch_offset = OF_TARGET_CRAM1_SCRATCH_OFFSET,

    .sram_base = OF_TARGET_SRAM_BASE,
    .sram_size = OF_TARGET_SRAM_SIZE,

    .runtime_stack_top = OF_TARGET_RUNTIME_STACK_TOP,
    .runtime_stack_size = OF_TARGET_RUNTIME_STACK_SIZE,

    .sample_base = OF_TARGET_SAMPLE_BASE,
    .sample_size = OF_TARGET_SAMPLE_SIZE,

    .save_region_addr = OF_TARGET_SAVE_REGION_ADDR,
    .save_slot_size = OF_TARGET_SAVE_SLOT_SIZE,
    .save_max_slots = OF_TARGET_SAVE_MAX_SLOTS,

    .gpu_base = OF_TARGET_GPU_BASE,
};

const of_target_platform_t *of_target_platform_get(void)
{
    return &g_target_platform;
}
