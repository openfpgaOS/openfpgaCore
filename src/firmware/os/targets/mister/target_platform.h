//------------------------------------------------------------------------------
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileType: SOURCE
// SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
//------------------------------------------------------------------------------

/*
 * openfpgaOS target platform contract: MiSTer (DE10-Nano / SuperStation One)
 *
 * Memory architecture: a single 16-bit SDR SDRAM module backs everything.
 * The hot 64 MB map (framebuffers, OS, apps, file cache, stack, GPU LUTs)
 * is byte-identical to the Pocket so os.ld and app ELFs need no changes.
 *
 * The Pocket's CRAM0 (bridge staging PSRAM) has no MiSTer equivalent —
 * MiSTer's hps_io lets the core DMA incoming data anywhere — so the CRAM0
 * macros are repointed at an 8 MB staging arena carved from the top of the
 * 64 MB map, directly below the OS file cache:
 *
 *   0x13300000  os.bin staging   (boot.rom ioctl DMA target, 768 KB)
 *   0x133C0000  presave mirror   (256 KB, assembly buffer only — real
 *   0x13400000  save mirror      (2.5 MB)   persistence is FAT files)
 *   0x13700000  DMA scratch      (sector-engine bounce, 1 MB)
 *   0x13800000  app async pool   (of_file_dma_stage_alloc, 1 MB)
 *   0x13900000  reserved         (2 MB spare)
 *   0x13B00000  (file cache — unchanged from Pocket)
 *
 * IMPORTANT vs Pocket: this arena is CACHED SDRAM (the Pocket CRAM0 was
 * uncached per PMA). The MiSTer file/save HAL does explicit cache
 * maintenance around sector-engine DMA, and reads DMA scratch exclusively
 * through the 0x53xxxxxx uncached alias so no cache lines ever exist for it.
 *
 * "Bridge" addresses on MiSTer are plain SDRAM byte offsets (the hps_bridge
 * sector/ioctl DMA master addresses SDRAM directly), so CRAM0_BRIDGE is the
 * arena's SDRAM offset and cpu_to_bridge()/sdram_to_bridge() keep working.
 */

#ifndef OFOS_TARGET_PLATFORM_H
#define OFOS_TARGET_PLATFORM_H

#define OF_TARGET_PLATFORM_ID          OF_PLATFORM_MISTER
#define OF_TARGET_CPU_FREQ_HZ          100000000u

/* In-OS application relaunch (menu.elf hand-off without an FPGA reset).
 * Supported on MiSTer: saves are write-through FAT files (durable per write)
 * and the single full-feature bitstream runs every instance.  NOT defined on
 * Pocket, where instance switching goes through the Analogue host (a core
 * reload) and nonvolatile persistence only happens at core exit. */
#define OF_TARGET_SUPPORTS_RELAUNCH    1

/* The video sink is ALWAYS fixed-rate on MiSTer: the core raster feeds the
 * framework scaler (HDMI/analog IO board), and emu.sv does not wire
 * vrr_v_total into the raster at all.  Defining this pins the adaptive-VRR
 * policy in video.c to the 525-line 60.02 Hz raster unconditionally instead
 * of relying on the joystick type-nibble accident (hps_bridge reports
 * cont type 0, which merely happens to read as "not handheld"). */
#define OF_TARGET_VIDEO_FIXED_RATE_SINK 1

#define OF_TARGET_BRAM_BASE            0x00000000u
#define OF_TARGET_BRAM_SIZE            (32u * 1024u)
#define OF_TARGET_APP_BRAM_BASE        0x00004000u
#define OF_TARGET_APP_BRAM_END         0x00007800u

#define OF_TARGET_SDRAM_BASE           0x10000000u
#define OF_TARGET_SDRAM_SIZE           (64u * 1024u * 1024u)
#define OF_TARGET_SDRAM_UNCACHED_BASE  0x50000000u

/* Framebuffers — identical to Pocket (see pocket/target_platform.h for the
 * cached-alias + cbo.clean flip rationale). */
#define OF_TARGET_FB0_BASE             0x10000000u
#define OF_TARGET_FB1_BASE             0x10100000u
#define OF_TARGET_FB2_BASE             0x10200000u
#define OF_TARGET_FB_COUNT             3u
#define OF_TARGET_FB_WIDTH             320u
#define OF_TARGET_FB_HEIGHT            240u
#define OF_TARGET_FB_STRIDE            320u
#define OF_TARGET_TERM_FB_BASE         0x50300000u

/* The sector engine DMAs straight into SDRAM; there is no shallow ingress
 * FIFO like the Pocket CRAM0 path, so both chunk limits are the full DMA
 * chunk.  OF_TARGET_CRAM0_DMA_CHUNK_SIZE also sizes of_file_async_max_read. */
#define OF_TARGET_DMA_CHUNK_SIZE       (32u * 1024u)
#define OF_TARGET_CRAM0_DMA_CHUNK_SIZE (32u * 1024u)

#define OF_TARGET_INTERACT_BASE        0x103FE000u
#define OF_TARGET_INTERACT_UNCACHED    0x503FE000u
#define OF_TARGET_INTERACT_MAX_VARS    64u

/* Staging arena ("CRAM0" role) — cached SDRAM + uncached alias. */
#define OF_TARGET_CRAM0_BASE           0x13300000u
#define OF_TARGET_CRAM_SIZE            (8u * 1024u * 1024u)
/* No dedicated fast texture memory — MiSTer textures live in SDRAM. */
#define OF_TARGET_TEX_FAST_SIZE        0u
/* SDRAM byte offset of the arena = DMA-engine address of the arena. */
#define OF_TARGET_CRAM0_BRIDGE         (OF_TARGET_CRAM0_BASE - OF_TARGET_SDRAM_BASE)
#define OF_TARGET_CRAM0_OS_OFFSET      0x00000000u
#define OF_TARGET_CRAM0_PRESAVE_OFFSET 0x000C0000u   /* mirror/assembly only */
/* Arena repack 2026-07-02: the 3 MB save-mirror window (0x100000-0x400000)
 * had NO consumer on MiSTer (FAT write-through persistence; only the presave
 * assembly buffer is used).  Scratch and the app DMA pool slide down over it
 * and the freed arena top becomes the audio reserve — the old below-arena
 * reserve left ~2.5 MB above Doom2's bss, too small for the 2.7 MB
 * SoundFont (bank_preload NONE(reserve) → no music). */
#define OF_TARGET_CRAM0_SCRATCH_OFFSET 0x00100000u   /* sector DMA bounce */
#define OF_TARGET_CRAM0_ASYNC_BOUNCE_OFFSET (OF_TARGET_CRAM0_SCRATCH_OFFSET + OF_TARGET_CRAM0_DMA_CHUNK_SIZE)
#define OF_TARGET_CRAM0_APP_DMA_OFFSET 0x00120000u   /* app async staging pool */
#define OF_TARGET_CRAM0_APP_DMA_SIZE   0x00100000u   /* 1 MB */
#define OF_TARGET_CRAM0_AUDIO_OFFSET   (OF_TARGET_CRAM0_APP_DMA_OFFSET + OF_TARGET_CRAM0_APP_DMA_SIZE)

/* High SDRAM reservations — identical to Pocket above the staging arena:
 *   0x13FC0000-0x14000000  GPU palookup tables (fixed RTL address)
 *   0x13F80000-0x13FC0000  speculation headroom / guard
 *   0x13F00000-0x13F80000  OS runtime stack
 *   0x13B00000-0x13F00000  OS file read cache (4 MB)
 *   0x13300000-0x13B00000  staging arena (this target only)
 *   below the arena        dynamic audio/SoundFont reservations
 * The app heap/stack ceiling therefore sits at the staging arena base. */
#define OF_TARGET_RUNTIME_STACK_TOP    0x13F80000u
#define OF_TARGET_RUNTIME_STACK_SIZE   (512u * 1024u)

#define OF_TARGET_FILE_CACHE_SIZE       (4u * 1024u * 1024u)
#define OF_TARGET_FILE_CACHE_BLOCK_SIZE OF_TARGET_DMA_CHUNK_SIZE
#define OF_TARGET_FILE_CACHE_TOP        (OF_TARGET_RUNTIME_STACK_TOP - OF_TARGET_RUNTIME_STACK_SIZE)
#define OF_TARGET_FILE_CACHE_BASE       (OF_TARGET_FILE_CACHE_TOP - OF_TARGET_FILE_CACHE_SIZE)

/* Dynamic audio memory — carved below the staging arena (the Pocket carves
 * below the file cache; the arena slots in between on MiSTer). */
#define OF_TARGET_AUDIO_RESERVE_TOP    (OF_TARGET_CRAM0_BASE + OF_TARGET_CRAM_SIZE)
#define OF_TARGET_AUDIO_RESERVE_FLOOR  (OF_TARGET_CRAM0_BASE + OF_TARGET_CRAM0_AUDIO_OFFSET)
#define OF_TARGET_AUDIO_STREAM_SIZE    0x00010000u
#define OF_TARGET_AUDIO_RESERVE_ALIGN  0x00001000u
#define OF_TARGET_APP_STACK_TOP        OF_TARGET_CRAM0_BASE
#define OF_TARGET_APP_STATIC_END       (OF_TARGET_APP_STACK_TOP - OF_TARGET_AUDIO_STREAM_SIZE - OF_TARGET_RUNTIME_STACK_SIZE)

/* Nonvolatile slots: persistence is FAT files inside the mounted disk image
 * (config CFGs and saves/slot_N.sav — see targets/mister/save.c).  The
 * region macros below point at the in-arena mirror window so shared code
 * and caps keep a valid address; nothing persists from the mirror itself. */
#define OF_TARGET_PRESAVE_REGION_ADDR  (OF_TARGET_CRAM0_BASE + OF_TARGET_CRAM0_PRESAVE_OFFSET)
#define OF_TARGET_PRESAVE_SLOT_SIZE    0x00040000u

#define OF_TARGET_SAVE_REGION_ADDR     OF_TARGET_PRESAVE_REGION_ADDR  /* mirror role collapsed; nothing persists from it */
#define OF_TARGET_SAVE_SLOT_SIZE       0x00040000u
#define OF_TARGET_SAVE_MAX_SLOTS       10u
#define OF_TARGET_SAVE_WRITEBACK_CHUNK_SIZE 0x00008000u

/* Disk-image block device (hps_io sd_rd/sd_wr sector engine). */
#define OF_TARGET_BLOCKDEV_SECTOR_BYTES 512u

#if (OF_TARGET_CRAM0_PRESAVE_OFFSET + OF_TARGET_PRESAVE_SLOT_SIZE) > OF_TARGET_CRAM0_SCRATCH_OFFSET
#error "Pre-save mirror overlaps the DMA scratch window"
#endif

#if (OF_TARGET_CRAM0_APP_DMA_OFFSET + OF_TARGET_CRAM0_APP_DMA_SIZE) > OF_TARGET_CRAM0_AUDIO_OFFSET
#error "App DMA pool overlaps the audio reserve"
#endif

#if (OF_TARGET_CRAM0_ASYNC_BOUNCE_OFFSET + OF_TARGET_CRAM0_DMA_CHUNK_SIZE) > OF_TARGET_CRAM0_APP_DMA_OFFSET
#error "App DMA pool overlaps the async bounce window"
#endif

#if (OF_TARGET_CRAM0_APP_DMA_OFFSET + OF_TARGET_CRAM0_APP_DMA_SIZE) > OF_TARGET_CRAM_SIZE
#error "App DMA pool exceeds the staging arena"
#endif

#if (OF_TARGET_CRAM0_BASE + OF_TARGET_CRAM_SIZE) != OF_TARGET_FILE_CACHE_BASE
#error "Staging arena must abut the OS file cache"
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
#error "Staging/audio/app-stack reservations leave no app SDRAM"
#endif

#define OF_TARGET_GPU_BASE             0x4A000000u

#endif /* OFOS_TARGET_PLATFORM_H */
