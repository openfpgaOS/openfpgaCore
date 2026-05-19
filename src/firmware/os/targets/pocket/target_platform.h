/*
 * openfpgaOS target platform contract: Analogue Pocket
 */

#ifndef OFOS_TARGET_PLATFORM_H
#define OFOS_TARGET_PLATFORM_H

#define OF_TARGET_PLATFORM_ID          OF_PLATFORM_POCKET
#define OF_TARGET_CPU_FREQ_HZ          100000000u

#define OF_TARGET_BRAM_BASE            0x00000000u
#define OF_TARGET_BRAM_SIZE            (32u * 1024u)
#define OF_TARGET_APP_BRAM_BASE        0x00004000u
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
 * With Zicbom enabled, FBs live at the CACHED SDRAM alias (0x10xxxxxx)
 * so pixel writes hit the L1 D-cache at ~1 cycle/word. of_video_flip()
 * issues a cbo.clean range flush (~84 µs for 76 KB) before handing the
 * buffer to the scanout DMA. */
#define OF_TARGET_FB0_BASE             0x10000000u
#define OF_TARGET_FB1_BASE             0x10100000u
#define OF_TARGET_FB2_BASE             0x10200000u
#define OF_TARGET_FB_COUNT             3u
#define OF_TARGET_FB_WIDTH             320u
#define OF_TARGET_FB_HEIGHT            240u
#define OF_TARGET_FB_STRIDE            320u
#define OF_TARGET_TERM_FB_BASE         0x50300000u

/* APF data-slot reads into CRAM0 are absorbed by a 1024-word bridge FIFO
 * before the async PSRAM controller drains them.  Keep one file operation
 * within that hardware buffer so a bursty SD/APF transfer cannot overrun it
 * and leave app/OS loads with missing words. */
#define OF_TARGET_DMA_CHUNK_SIZE       (4u * 1024u)

#define OF_TARGET_INTERACT_BASE        0x103FE000u
#define OF_TARGET_INTERACT_UNCACHED    0x503FE000u
#define OF_TARGET_INTERACT_MAX_VARS    64u

/* CRAM0 — bridge staging only (v2 memory arch).  Runs on the APF
 * bridge clock (clk_74a); CPU accesses go through a CDC in the FPGA
 * fabric.  CPU-side alias is uncached (PMA main=0) at 0x30000000.
 * CRAM1 was retired along with its two aliases and the CRAM0 uncached
 * alias — there is now only one CRAM0 address. */
#define OF_TARGET_CRAM0_BASE           0x30000000u
#define OF_TARGET_CRAM_SIZE            (16u * 1024u * 1024u)
#define OF_TARGET_CRAM0_BRIDGE         0x20000000u
/* APF data slot offset inside CRAM0 for OS boot payload (slot 1).
 * The bootloader in BRAM reads from CRAM0_BASE + this and copies to
 * SDRAM before jumping.  Save/load scratch lives at a separate
 * offset so the two don't collide with an in-flight transfer. */
#define OF_TARGET_CRAM0_OS_OFFSET      0x00000000u
#define OF_TARGET_CRAM0_PRESAVE_OFFSET 0x000C0000u   /* 768 KB in: shared config / per-game settings */
#define OF_TARGET_CRAM0_SAVE_OFFSET    0x00100000u   /* 1 MB in */
#define OF_TARGET_CRAM0_SCRATCH_OFFSET 0x00400000u   /* 4 MB in — above config + the 2.5 MB save window */
#define OF_TARGET_CRAM0_ASYNC_BOUNCE_OFFSET (OF_TARGET_CRAM0_SCRATCH_OFFSET + OF_TARGET_DMA_CHUNK_SIZE)
#define OF_TARGET_CRAM0_APP_DMA_OFFSET 0x00500000u   /* App-visible async file staging pool */
#define OF_TARGET_CRAM0_APP_DMA_SIZE   0x00100000u   /* 1 MB */

/* SRAM is GPU-private in v2 — no AXI alias, not CPU-addressable. */
/* (OF_TARGET_SRAM_BASE / OF_TARGET_SRAM_SIZE removed) */

/* Placed 16 MB below the SDRAM PMA end (0x14000000) as a conservative
 * known-good SDRAM region.  VexiiRiscv's aggressive speculation
 * (branch prediction, rpt prefetch when on, cache line ahead-fill)
 * can issue loads several lines past the last committed one; with
 * the stack top at the PMA edge those speculated addresses cross
 * into unmapped memory and
 * surface as architectural load access faults.  16 MB of headroom
 * is more than any speculation window reaches and costs us nothing —
 * the sample pool, mmap, and app load still fit. */
#define OF_TARGET_RUNTIME_STACK_TOP    0x13000000u
#define OF_TARGET_RUNTIME_STACK_SIZE   (512u * 1024u)

/* Sample pool: 8 MB in SDRAM carved out of the top of the app's
 * heap/mmap window so app load base (0x10400000) stays unchanged —
 * existing app ELFs remain compatible.  The OS shifts mmap_bottom
 * down by 8 MB so mmap never allocates into the sample region.
 * Size is 2 MB of headroom over the SC-55 bank (~6 MB).
 *
 * SAMPLE_BASE is the cached alias — used for SFX uploads that pair
 * with cbo.flush.  SAMPLE_BASE_UNCACHED is the same physical region
 * via the uncached SDRAM alias; required for the audio_ring (voice
 * 31) where each store must stall on its AXI B-response so the HW
 * mixer's sub-millisecond reads see the latest sample.  See
 * hal/cache.c's flush-vs-uncached commentary. */
#define OF_TARGET_SAMPLE_BASE          0x13700000u
#define OF_TARGET_SAMPLE_BASE_UNCACHED 0x53700000u
#define OF_TARGET_SAMPLE_SIZE          (8u * 1024u * 1024u)

/* One pre-save nonvolatile slot covers either SDK Shared Config (id 8)
 * or Duke's per-game settings file (id 9), depending on the active
 * data.json. Game saves follow immediately after it. */
#define OF_TARGET_PRESAVE_REGION_ADDR  (OF_TARGET_CRAM0_BASE + OF_TARGET_CRAM0_PRESAVE_OFFSET)
#define OF_TARGET_PRESAVE_SLOT_SIZE    0x00040000u

/* Save region is the CRAM0 save slot window. 10 slots x 256 KB = 2.5 MB,
 * starting after the pre-save config/settings slot. */
#define OF_TARGET_SAVE_REGION_ADDR     (OF_TARGET_CRAM0_BASE + OF_TARGET_CRAM0_SAVE_OFFSET)
#define OF_TARGET_SAVE_SLOT_SIZE       0x00040000u
#define OF_TARGET_SAVE_MAX_SLOTS       10u
/* CRAM0 bridge writeback commands must fit inside the FPGA prefetch
 * buffer. Larger logical saves are persisted as multiple APF writes. */
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

#define OF_TARGET_GPU_BASE             0x4A000000u

#endif /* OFOS_TARGET_PLATFORM_H */
