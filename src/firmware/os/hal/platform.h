/*
 * openfpgaOS Target Platform Contract
 *
 * Each target provides a `target_platform.h` describing the platform's
 * memory map and fixed runtime characteristics. Shared HAL/kernel code
 * consumes this contract instead of hardcoding Pocket values directly.
 */

#ifndef OFOS_PLATFORM_H
#define OFOS_PLATFORM_H

#include <stdint.h>
#include "of_caps.h"
#include "target_platform.h"

typedef struct {
    uint32_t platform_id;
    uint32_t cpu_freq_hz;

    uint32_t bram_base;
    uint32_t bram_size;
    uint32_t app_bram_base;
    uint32_t app_bram_end;

    uint32_t sdram_base;
    uint32_t sdram_size;
    uint32_t sdram_uncached_base;

    uint32_t fb_bases[3];
    uint32_t fb_count;
    uint32_t fb_width;
    uint32_t fb_height;
    uint32_t fb_stride;
    uint32_t term_fb_base;

    uint32_t dma_chunk_size;

    uint32_t interact_base;
    uint32_t interact_uncached;
    uint32_t interact_max_vars;

    uint32_t cram0_base;
    uint32_t cram1_base;
    uint32_t cram0_uncached;
    uint32_t cram1_uncached;
    uint32_t cram_size;
    uint32_t cram0_bridge;
    uint32_t cram1_bridge;
    uint32_t cram1_ftab_offset;
    uint32_t cram1_scratch_offset;

    uint32_t sram_base;
    uint32_t sram_size;

    uint32_t runtime_stack_top;
    uint32_t runtime_stack_size;

    uint32_t sample_base;
    uint32_t sample_size;

    uint32_t save_region_addr;
    uint32_t save_slot_size;
    uint32_t save_max_slots;

    uint32_t gpu_base;          /* GPU MMIO window base, 0 if no GPU */
} of_target_platform_t;

const of_target_platform_t *of_target_platform_get(void);

#endif /* OFOS_PLATFORM_H */
