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

#define OF_TARGET_DMA_CHUNK_SIZE       (512u * 1024u)

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
#define OF_TARGET_CRAM0_SAVE_OFFSET    0x00100000u   /* 1 MB in */
#define OF_TARGET_CRAM0_SCRATCH_OFFSET 0x00200000u   /* 2 MB in — general-purpose bridge scratch */

/* SRAM is GPU-private in v2 — no AXI alias, not CPU-addressable. */
/* (OF_TARGET_SRAM_BASE / OF_TARGET_SRAM_SIZE removed) */

/* Placed 16 MB below the SDRAM PMA end (0x14000000), matching
 * PocketQuake's "conservative known-good SDRAM region" approach.
 * VexiiRiscv's aggressive speculation (branch prediction, rpt
 * prefetch when on, cache line ahead-fill) can issue loads several
 * lines past the last committed one; with the stack top at the PMA
 * edge those speculated addresses cross into unmapped memory and
 * surface as architectural load access faults.  16 MB of headroom
 * is more than any speculation window reaches and costs us nothing —
 * the sample pool, mmap, and app load still fit. */
#define OF_TARGET_RUNTIME_STACK_TOP    0x13000000u
#define OF_TARGET_RUNTIME_STACK_SIZE   (512u * 1024u)

/* Sample pool: 8 MB in SDRAM carved out of the top of the app's
 * heap/mmap window so app load base (0x10400000) stays unchanged —
 * existing app ELFs remain compatible.  The OS shifts mmap_bottom
 * down by 8 MB so mmap never allocates into the sample region.
 * Size is 2 MB of headroom over the SC-55 bank (~6 MB). */
#define OF_TARGET_SAMPLE_BASE          0x13700000u
#define OF_TARGET_SAMPLE_SIZE          (8u * 1024u * 1024u)

/* Save region is now the CRAM0 save slot window — the OS copies an
 * SDRAM save buffer into CRAM0 before asking the bridge to DMA it out
 * to the APF data slot.  10 slots × 256 KB = 2.5 MB, starting at the
 * CRAM0 save offset. */
#define OF_TARGET_SAVE_REGION_ADDR     (OF_TARGET_CRAM0_BASE + OF_TARGET_CRAM0_SAVE_OFFSET)
#define OF_TARGET_SAVE_SLOT_SIZE       0x00040000u
#define OF_TARGET_SAVE_MAX_SLOTS       10u

#define OF_TARGET_GPU_BASE             0x4A000000u

#endif /* OFOS_TARGET_PLATFORM_H */
