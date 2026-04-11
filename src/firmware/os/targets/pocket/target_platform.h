/*
 * openfpgaOS target platform contract: Analogue Pocket
 */

#ifndef OFOS_TARGET_PLATFORM_H
#define OFOS_TARGET_PLATFORM_H

#define OF_TARGET_PLATFORM_ID          OF_PLATFORM_POCKET
#define OF_TARGET_CPU_FREQ_HZ          100000000u

#define OF_TARGET_BRAM_BASE            0x00000000u
#define OF_TARGET_BRAM_SIZE            (32u * 1024u)
#define OF_TARGET_APP_BRAM_BASE        0x00002000u
#define OF_TARGET_APP_BRAM_END         0x00007800u

#define OF_TARGET_SDRAM_BASE           0x10000000u
#define OF_TARGET_SDRAM_SIZE           (64u * 1024u * 1024u)
#define OF_TARGET_SDRAM_UNCACHED_BASE  0x50000000u

/* App framebuffers live at the uncached SDRAM alias (0x50xxxxxx) so CPU
 * pixel writes go straight through p_axi to SDRAM, bypassing the L1 D$.
 * This avoids cache pollution: without the alias, a 76 KB triple buffer
 * thrashes the 64 KB L1 and evicts real app working-set data.  The
 * scanout engine and GPU access the same physical SDRAM via their own
 * paths (scanout has hardcoded FB_ADDR_0/1/2 constants in
 * axi_periph_slave.v; GPU writes pass through axi_sdram_slave's [25:2]
 * address mask which ignores the upper alias bits), so the CPU-side
 * alias is transparent to hardware.
 *
 * Trade-off: full-frame memset/memcpy into the FB is slower (~20 MB/s
 * uncached vs ~100+ MB/s cached writeback-bound), but dirty-rect apps
 * keep L1 available for real working-set data instead of wasting it
 * on pixels the scanout just reads back anyway. */
#define OF_TARGET_FB0_BASE             0x50000000u
#define OF_TARGET_FB1_BASE             0x50100000u
#define OF_TARGET_FB2_BASE             0x50200000u
#define OF_TARGET_FB_COUNT             3u
#define OF_TARGET_FB_WIDTH             320u
#define OF_TARGET_FB_HEIGHT            240u
#define OF_TARGET_FB_STRIDE            320u
#define OF_TARGET_TERM_FB_BASE         0x50300000u

#define OF_TARGET_DMA_CHUNK_SIZE       (512u * 1024u)

#define OF_TARGET_INTERACT_BASE        0x103FE000u
#define OF_TARGET_INTERACT_UNCACHED    0x503FE000u
#define OF_TARGET_INTERACT_MAX_VARS    64u

#define OF_TARGET_CRAM0_BASE           0x30000000u
#define OF_TARGET_CRAM1_BASE           0x31000000u
#define OF_TARGET_CRAM0_UNCACHED       0x38000000u
#define OF_TARGET_CRAM1_UNCACHED       0x39000000u
#define OF_TARGET_CRAM_SIZE            (16u * 1024u * 1024u)
#define OF_TARGET_CRAM0_BRIDGE         0x20000000u
#define OF_TARGET_CRAM1_BRIDGE         0x30000000u
#define OF_TARGET_CRAM1_FTAB_OFFSET    0x00280000u
#define OF_TARGET_CRAM1_SCRATCH_OFFSET 0x00290000u

#define OF_TARGET_SRAM_BASE            0x3A000000u
#define OF_TARGET_SRAM_SIZE            (256u * 1024u)

#define OF_TARGET_RUNTIME_STACK_TOP    0x14000000u
#define OF_TARGET_RUNTIME_STACK_SIZE   (512u * 1024u)

#define OF_TARGET_SAMPLE_BASE          (OF_TARGET_CRAM1_UNCACHED + 0x00400000u)
#define OF_TARGET_SAMPLE_SIZE          (11u * 1024u * 1024u)

#define OF_TARGET_SAVE_REGION_ADDR     OF_TARGET_CRAM1_UNCACHED
#define OF_TARGET_SAVE_SLOT_SIZE       0x00040000u
#define OF_TARGET_SAVE_MAX_SLOTS       10u

#define OF_TARGET_GPU_BASE             0x4A000000u

#endif /* OFOS_TARGET_PLATFORM_H */
