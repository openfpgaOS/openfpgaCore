/*
 * openfpgaOS target platform contract: Verilator sim
 *
 * This is a *portability sanity-check* target: it claims a memory map
 * that deliberately differs from Pocket in places where the SDK API
 * could leak compile-time addresses. Building TARGET=sim and inspecting
 * the produced os.bin verifies that:
 *
 *   1. The kernel sources every per-target address from this header
 *      (no compile-time bake-in elsewhere).
 *   2. The runtime caps descriptor is populated from these values.
 *   3. SDK apps that read of_capabilities at runtime will pick up the
 *      sim-specific layout without needing a rebuild.
 *
 * The HAL .c files are #include shims pointing at the Pocket copies,
 * because the actual peripheral hardware behind the addresses is the
 * same in this sanity-check setup. A real second target (e.g. MiSTer)
 * would ship its own HAL .c files implementing different bus protocols.
 *
 * Differences from Pocket:
 *   - platform_id = OF_PLATFORM_SIM
 *   - gpu_base    = 0x4B000000 (Pocket: 0x4A000000)
 *
 * The shifted gpu_base specifically validates that of_gpu.h reads
 * gpu_base from caps at runtime instead of compiling in 0x4A000000.
 * On a real sim of this target, the GPU MMIO would have to live at
 * 0x4B000000; here we're only doing a build-time check, so the
 * physical RTL is irrelevant.
 */

#ifndef OFOS_TARGET_PLATFORM_H
#define OFOS_TARGET_PLATFORM_H

#define OF_TARGET_PLATFORM_ID          OF_PLATFORM_SIM
#define OF_TARGET_CPU_FREQ_HZ          100000000u

#define OF_TARGET_BRAM_BASE            0x00000000u
#define OF_TARGET_BRAM_SIZE            (32u * 1024u)
#define OF_TARGET_APP_BRAM_BASE        0x00004000u
#define OF_TARGET_APP_BRAM_END         0x00007800u

#define OF_TARGET_SDRAM_BASE           0x10000000u
#define OF_TARGET_SDRAM_SIZE           (64u * 1024u * 1024u)
#define OF_TARGET_SDRAM_UNCACHED_BASE  0x50000000u

/* Match pocket target: FBs at uncached SDRAM alias to bypass L1 D$ */
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

/* Deliberate divergence from Pocket: GPU base shifted by 0x01000000
 * to validate that of_gpu.h reads it at runtime via of_get_caps()
 * rather than baking in the Pocket value. */
#define OF_TARGET_GPU_BASE             0x4B000000u

#endif /* OFOS_TARGET_PLATFORM_H */
