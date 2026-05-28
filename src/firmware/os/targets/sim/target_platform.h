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

/* v2 memory arch: CRAM1 retired, CRAM0 on bridge clock. */
#define OF_TARGET_CRAM0_BASE           0x30000000u
#define OF_TARGET_CRAM_SIZE            (16u * 1024u * 1024u)
#define OF_TARGET_CRAM0_BRIDGE         0x20000000u
#define OF_TARGET_CRAM0_OS_OFFSET      0x00000000u
#define OF_TARGET_CRAM0_PRESAVE_OFFSET 0x000C0000u
#define OF_TARGET_CRAM0_SAVE_OFFSET    0x00100000u
#define OF_TARGET_CRAM0_SCRATCH_OFFSET 0x00400000u
#define OF_TARGET_CRAM0_ASYNC_BOUNCE_OFFSET (OF_TARGET_CRAM0_SCRATCH_OFFSET + OF_TARGET_DMA_CHUNK_SIZE)
#define OF_TARGET_CRAM0_APP_DMA_OFFSET 0x00500000u
#define OF_TARGET_CRAM0_APP_DMA_SIZE   0x00100000u

#define OF_TARGET_GPU_PALOOKUP_BASE    0x13FC0000u
#define OF_TARGET_GPU_PALOOKUP_SIZE    0x00040000u

#define OF_TARGET_RUNTIME_STACK_TOP    0x13F80000u
#define OF_TARGET_RUNTIME_STACK_SIZE   (512u * 1024u)

#define OF_TARGET_AUDIO_RESERVE_TOP    (OF_TARGET_RUNTIME_STACK_TOP - OF_TARGET_RUNTIME_STACK_SIZE)
#define OF_TARGET_AUDIO_STREAM_SIZE    0x00004000u
#define OF_TARGET_AUDIO_RESERVE_ALIGN  0x00001000u
#define OF_TARGET_APP_STATIC_END       (OF_TARGET_AUDIO_RESERVE_TOP - OF_TARGET_AUDIO_STREAM_SIZE - OF_TARGET_RUNTIME_STACK_SIZE)

#define OF_TARGET_PRESAVE_REGION_ADDR  (OF_TARGET_CRAM0_BASE + OF_TARGET_CRAM0_PRESAVE_OFFSET)
#define OF_TARGET_PRESAVE_SLOT_SIZE    0x00040000u

#define OF_TARGET_SAVE_REGION_ADDR     (OF_TARGET_CRAM0_BASE + OF_TARGET_CRAM0_SAVE_OFFSET)
#define OF_TARGET_SAVE_SLOT_SIZE       0x00040000u
#define OF_TARGET_SAVE_MAX_SLOTS       10u
#define OF_TARGET_SAVE_WRITEBACK_CHUNK_SIZE 0x00002000u

#if (OF_TARGET_CRAM0_PRESAVE_OFFSET + OF_TARGET_PRESAVE_SLOT_SIZE) > OF_TARGET_CRAM0_SAVE_OFFSET
#error "Pre-save nonvolatile slot overlaps the save-slot window"
#endif

#if OF_TARGET_CRAM0_SCRATCH_OFFSET < (OF_TARGET_CRAM0_SAVE_OFFSET + OF_TARGET_SAVE_SLOT_SIZE * OF_TARGET_SAVE_MAX_SLOTS)
#error "CRAM0 scratch overlaps the nonvolatile save-slot window"
#endif

#if (OF_TARGET_CRAM0_ASYNC_BOUNCE_OFFSET + OF_TARGET_DMA_CHUNK_SIZE) > OF_TARGET_CRAM0_APP_DMA_OFFSET
#error "CRAM0 app DMA pool overlaps the async bounce DMA window"
#endif

#if (OF_TARGET_CRAM0_APP_DMA_OFFSET + OF_TARGET_CRAM0_APP_DMA_SIZE) > OF_TARGET_CRAM_SIZE
#error "CRAM0 app DMA pool exceeds CRAM0"
#endif

/* Deliberate divergence from Pocket: GPU base shifted by 0x01000000
 * to validate that of_gpu.h reads it at runtime via of_get_caps()
 * rather than baking in the Pocket value. */
#define OF_TARGET_GPU_BASE             0x4B000000u

#endif /* OFOS_TARGET_PLATFORM_H */
