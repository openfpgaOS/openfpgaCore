//------------------------------------------------------------------------------
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileType: SOURCE
// SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
//------------------------------------------------------------------------------

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
 * thrashes the L1 D-cache and evicts real app working-set data.  The
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

/* Normal APF data-slot reads target SDRAM directly.  The CRAM0 ingress FIFO
 * is shallow (1024 words = 4 KB), but that bounds JITTER, not transfer size:
 * the host delivers SD-paced (slower than CRAM0 drains), so the FIFO never
 * fills on a sustained transfer.  Proof: the 256 KB nonvolatile save slots
 * (data.json id 10/11, addr 0x2010_0000 = CRAM0 range) auto-load through this
 * exact bridge->FIFO->CRAM0 path without overrun.  The old 4 KB cap was
 * therefore over-conservative; raised to 64 KB.  A large commanded read is
 * verified safe by reading DS_BRIDGE_WCNT bit 31 (DS_BWC_CRAM0_OVERRUN) after
 * it completes — it must read 0 (the bit is sticky, cleared on command issue).
 * Layout asserts cap this at 512 KB (scratch + async-bounce both scale with
 * it: SCRATCH_OFFSET + 2*CHUNK must stay <= APP_DMA_OFFSET). */
#define OF_TARGET_DMA_CHUNK_SIZE       (32u * 1024u)
#define OF_TARGET_CRAM0_DMA_CHUNK_SIZE (64u * 1024u)

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

/* Fast texture memory (Pocket: CRAM1) — dedicated GPU sync-burst texture
 * memory (16 MB), a separate chip from CRAM0 (saves).  CPU-invisible: textures
 * are uploaded via the GPU's fast-texture upload regs and addressed by GPU
 * byte offset.  Exposed to apps as caps->tex_fast_size via the of_texture.h
 * API. */
#define OF_TARGET_TEX_FAST_SIZE        (16u * 1024u * 1024u)
#define OF_TARGET_CRAM0_BRIDGE         0x20000000u
/* APF data slot offset inside CRAM0 for OS boot payload (slot 1).
 * The bootloader in BRAM reads from CRAM0_BASE + this and copies to
 * SDRAM before jumping.  Save/load scratch lives at a separate
 * offset so the two don't collide with an in-flight transfer. */
#define OF_TARGET_CRAM0_OS_OFFSET      0x00000000u
#define OF_TARGET_CRAM0_PRESAVE_OFFSET 0x000C0000u   /* 768 KB in: shared config / per-game settings */
#define OF_TARGET_CRAM0_SAVE_OFFSET    0x00100000u   /* 1 MB in */
#define OF_TARGET_CRAM0_SCRATCH_OFFSET 0x00400000u   /* 4 MB in — above config + the 2.5 MB save window */
#define OF_TARGET_CRAM0_ASYNC_BOUNCE_OFFSET (OF_TARGET_CRAM0_SCRATCH_OFFSET + OF_TARGET_CRAM0_DMA_CHUNK_SIZE)
#define OF_TARGET_CRAM0_APP_DMA_OFFSET 0x00500000u   /* App-visible async file staging pool */
#define OF_TARGET_CRAM0_APP_DMA_SIZE   0x00100000u   /* 1 MB */

/* SRAM is GPU-private in v2 — no AXI alias, not CPU-addressable. */

/* High SDRAM reservations, from top down:
 *   0x13FC0000-0x14000000  GPU palookup tables (fixed RTL address)
 *   0x13F80000-0x13FC0000  speculation headroom / guard
 *   0x13F00000-0x13F80000  OS runtime stack for syscall/trap C code
 *   0x13B00000-0x13F00000  OS file read cache (4 MB by default)
 *   below file cache       dynamic audio/SoundFont reservations
 *
 * The app heap/mmap/stack top is set at boot to the lowest audio
 * reservation, so apps get the unused SDRAM tail without hidden holes. */
#define OF_TARGET_RUNTIME_STACK_TOP    0x13F80000u
#define OF_TARGET_RUNTIME_STACK_SIZE   (512u * 1024u)

#define OF_TARGET_FILE_CACHE_SIZE       (4u * 1024u * 1024u)
#define OF_TARGET_FILE_CACHE_BLOCK_SIZE OF_TARGET_DMA_CHUNK_SIZE
#define OF_TARGET_FILE_CACHE_TOP        (OF_TARGET_RUNTIME_STACK_TOP - OF_TARGET_RUNTIME_STACK_SIZE)
#define OF_TARGET_FILE_CACHE_BASE       (OF_TARGET_FILE_CACHE_TOP - OF_TARGET_FILE_CACHE_SIZE)

/* Dynamic audio memory.  The stream ring and any boot-time .ofsf
 * bank are reserved top-down from below the OS file cache.
 *
 * OF_TARGET_AUDIO_STREAM_SIZE is the SINGLE KNOB for the SW music ring:
 * audio.c derives AUDIO_RING_PAIRS = OF_TARGET_AUDIO_STREAM_SIZE / 4 (4 bytes
 * per stereo pair), and the HW voice length follows it at runtime. 512 KB =
 * 131072 pairs = ~2.7 s of coast through a blocking load; with the mixer's
 * stream mode the ring depth is purely a continuity knob (overrun/stale
 * replay is impossible -- the voice holds at the write pointer), so size it
 * to span typical level loads. Must stay a power of two (audio.c
 * static-asserts it) and fit the 22-bit HW voice length; note the pump
 * fills the ring to full, so app-side decode-time music VOLUME changes lag
 * by the ring depth (~2.7 s on the rare settings change -- accepted).
 * Changing this moves OF_TARGET_APP_STATIC_END: keep BOTH app.ld copies'
 * SDRAM LENGTH equal to it or apps link into the loader-rejected window. */
#define OF_TARGET_AUDIO_RESERVE_TOP    OF_TARGET_FILE_CACHE_BASE
#define OF_TARGET_AUDIO_STREAM_SIZE    0x00080000u
#define OF_TARGET_AUDIO_RESERVE_ALIGN  0x00001000u
#define OF_TARGET_APP_STACK_TOP        OF_TARGET_AUDIO_RESERVE_TOP
#define OF_TARGET_APP_STATIC_END       (OF_TARGET_APP_STACK_TOP - OF_TARGET_AUDIO_STREAM_SIZE - OF_TARGET_RUNTIME_STACK_SIZE)

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

#if (OF_TARGET_CRAM0_ASYNC_BOUNCE_OFFSET + OF_TARGET_CRAM0_DMA_CHUNK_SIZE) > OF_TARGET_CRAM0_APP_DMA_OFFSET
#error "CRAM0 app DMA pool overlaps the async bounce DMA window"
#endif

#if (OF_TARGET_CRAM0_APP_DMA_OFFSET + OF_TARGET_CRAM0_APP_DMA_SIZE) > OF_TARGET_CRAM_SIZE
#error "CRAM0 app DMA pool exceeds CRAM0"
#endif

#if OF_TARGET_FILE_CACHE_SIZE == 0
#error "The file read cache is always on; OF_TARGET_FILE_CACHE_SIZE must be nonzero"
#endif

#if (OF_TARGET_FILE_CACHE_SIZE % OF_TARGET_FILE_CACHE_BLOCK_SIZE) != 0
#error "File cache size must be a multiple of the file cache block size"
#endif

#if OF_TARGET_FILE_CACHE_TOP > (OF_TARGET_RUNTIME_STACK_TOP - OF_TARGET_RUNTIME_STACK_SIZE)
#error "File cache overlaps the OS runtime stack"
#endif

#if OF_TARGET_APP_STATIC_END <= 0x10400000u
#error "File cache/audio/app-stack reservations leave no app SDRAM"
#endif

#define OF_TARGET_GPU_BASE             0x4A000000u

#endif /* OFOS_TARGET_PLATFORM_H */
