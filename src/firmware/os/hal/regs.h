//------------------------------------------------------------------------------
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileType: SOURCE
// SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
//------------------------------------------------------------------------------

/*
 * openfpgaOS Hardware Register Definitions
 * Generic SoC register map plus target-selected platform constants.
 */

#ifndef OFOS_REGS_H
#define OFOS_REGS_H

#include <stdint.h>
#include "platform.h"

/* ======================================================================
 * Register access macros
 * ====================================================================== */

#define REG32(addr)     (*(volatile uint32_t *)(addr))

/* ======================================================================
 * Memory Map
 * ====================================================================== */

#define BRAM_BASE           OF_TARGET_BRAM_BASE
#define BRAM_SIZE           OF_TARGET_BRAM_SIZE

/* App BRAM region — available for app hot code after OS sections.
 * Top BRAM remains reserved for the trap handler register frame. */
#define APP_BRAM_BASE       OF_TARGET_APP_BRAM_BASE
#define APP_BRAM_END        OF_TARGET_APP_BRAM_END  /* Caps at 0x7800; top BRAM holds trap/boot state */

#define SDRAM_BASE          OF_TARGET_SDRAM_BASE
#define SDRAM_SIZE          OF_TARGET_SDRAM_SIZE
#define SDRAM_UNCACHED_BASE OF_TARGET_SDRAM_UNCACHED_BASE      /* D-cache bypass alias */

#define FB0_BASE            OF_TARGET_FB0_BASE      /* Framebuffer 0 */
#define FB1_BASE            OF_TARGET_FB1_BASE      /* Framebuffer 1 */
#define FB2_BASE            OF_TARGET_FB2_BASE      /* Framebuffer 2 */
#define TERM_FB_BASE        OF_TARGET_TERM_FB_BASE  /* Dedicated terminal framebuffer */
#define FB_WIDTH            OF_TARGET_FB_WIDTH
#define FB_HEIGHT           OF_TARGET_FB_HEIGHT
#define FB_STRIDE           OF_TARGET_FB_STRIDE
#define FB_SIZE             (FB_WIDTH * FB_HEIGHT)

#define DMA_CHUNK_SIZE      OF_TARGET_DMA_CHUNK_SIZE    /* Max transfer per target file op */
#define FILE_CACHE_BASE     OF_TARGET_FILE_CACHE_BASE
#define FILE_CACHE_SIZE     OF_TARGET_FILE_CACHE_SIZE
#define FILE_CACHE_BLOCK_SIZE OF_TARGET_FILE_CACHE_BLOCK_SIZE

#define INTERACT_UNCACHED   OF_TARGET_INTERACT_UNCACHED

/* Terminal VRAM removed — terminal renders to TERM_FB_BASE in SDRAM */
#define TERM_COLS           40
#define TERM_ROWS           30

/* v2 memory arch: CRAM1 retired.  CRAM0 is the only PSRAM kept, running
 * on the APF bridge clock (clk_74a); CPU access is through a fabric-side
 * CDC.  PMA marks 0x30000000 as uncached so there's no cache-coherency
 * concern around the bridge writing behind the CPU. */
#define CRAM0_BASE          OF_TARGET_CRAM0_BASE      /* CRAM0 (uncached) */
#define CRAM_SIZE           OF_TARGET_CRAM_SIZE

#define CRAM0_BRIDGE        OF_TARGET_CRAM0_BRIDGE    /* CRAM0 in bridge address space */

/* FTAB (file table) — written by Chip32 loader at boot into CRAM0 (not CRAM1). */

/* DMA scratch area in CRAM0 (shared with bridge, managed via CRAM0_MODE). */
#define CRAM0_SCRATCH         (CRAM0_BASE + OF_TARGET_CRAM0_SCRATCH_OFFSET)
#define CRAM0_SCRATCH_BRIDGE  (CRAM0_BRIDGE + OF_TARGET_CRAM0_SCRATCH_OFFSET)

/* SRAM retired from AXI: GPU-private only.  No CPU-visible alias. */

/* Runtime stack layout (must match os.ld) */
#define RUNTIME_STACK_TOP   OF_TARGET_RUNTIME_STACK_TOP
#define RUNTIME_STACK_SIZE  OF_TARGET_RUNTIME_STACK_SIZE
#define APP_STACK_SIZE     RUNTIME_STACK_SIZE
#define APP_STACK_TOP      OF_TARGET_APP_STACK_TOP


/* ======================================================================
 * System Registers (0x40000000)
 * ====================================================================== */

#define SYSREG_BASE         0x40000000

/* Core status & control */
#define SYS_STATUS          REG32(SYSREG_BASE + 0x00)
#define   SYS_STATUS_ALLCOMPLETE    (1 << 1)
#define SYS_CYCLE_LO        REG32(SYSREG_BASE + 0x04)
#define SYS_CYCLE_HI        REG32(SYSREG_BASE + 0x08)
/* Unix epoch seconds from the APF host RTC, latched once shortly after boot
 * by core_bridge_cmd and quasi-static afterwards.  Reads 0 until the host
 * has delivered the RTC command (treat 0 as "no RTC").  The Pocket host
 * reports wall-clock local time as epoch seconds. */
#define RTC_EPOCH           REG32(SYSREG_BASE + 0xC4)
/* Terminal FB control: bit[0] = 1 scanout reads terminal FB, 0 = app FB.
 * bit[1] = 1 the analog (Analogizer) output keeps the app FB + ANALOG_FB_*
 * geometry while bit[0] shows the terminal on the LCD (Pocket LCD=Terminal);
 * 0 = the analog mirrors the LCD selection, terminal included. */
#define TERM_FB_CTRL        REG32(SYSREG_BASE + 0x0C)
#define   DISPLAY_MODE_TERMINAL     0
#define   DISPLAY_MODE_FRAMEBUFFER  1
#define   DISPLAY_MODE_OVERLAY      2

#define SYS_COLOR_MODE      REG32(SYSREG_BASE + 0x70)
#define   COLOR_MODE_8BIT       0   /* 8-bit indexed (256 colors, 1 byte/pixel) */
#define   COLOR_MODE_4BIT       1   /* 4-bit indexed (16 colors, 0.5 byte/pixel) */
#define   COLOR_MODE_2BIT       2   /* 2-bit indexed (4 colors, 0.25 byte/pixel) */
#define   COLOR_MODE_RGB565     3   /* 16-bit direct (R5G6B5, 2 bytes/pixel) */
#define   COLOR_MODE_RGB555     4   /* 15-bit direct (X1R5G5B5, 2 bytes/pixel) */
#define   COLOR_MODE_RGBA5551   5   /* 15-bit + alpha (R5G5B5A1, 2 bytes/pixel) */

/* Framebuffer control (triple-buffered)
 * FB_SWAP_CTRL write: bit[0]=trigger, bits[2:1]=buffer index to display at next vsync
 * FB_SWAP_CTRL read:  bit[0]=pending, bits[2:1]=current display buffer index */
#define FB_SWAP_CTRL        REG32(SYSREG_BASE + 0x18)

/* Runtime framebuffer geometry.  Size packs width in bits [15:0] and
 * height in bits [31:16].  Stride is bytes per source framebuffer row.
 * Hardware currently clamps to 800x600 and a 2048-byte stride so every
 * supported mode fits the scanout line cache and 1 MB FB slot spacing. */
#define FB_MODE_SIZE        REG32(SYSREG_BASE + 0xE4)
#define FB_MODE_STRIDE      REG32(SYSREG_BASE + 0xE8)
#define VIDEO_SCALER_MODE   REG32(SYSREG_BASE + 0xEC)
#define VIDEO_SCALER_SLOT_DEFAULT_320X240 0u

/* Independent analog-output framebuffer geometry (Pocket LCD = Terminal: the
 * LCD shows the OS console while the Analogizer keeps showing the app).  Keep
 * these = the app's geometry; the analog base tracks the app display buffer in
 * hardware.  Same packing as FB_MODE_SIZE/STRIDE/SYS_COLOR_MODE. */
#define ANALOG_FB_SIZE      REG32(SYSREG_BASE + 0xF0)
#define ANALOG_FB_STRIDE    REG32(SYSREG_BASE + 0xF4)
#define ANALOG_COLOR_MODE   REG32(SYSREG_BASE + 0xF8)
#define FB_MODE_MAX_WIDTH   800u
#define FB_MODE_MAX_HEIGHT  600u
#define FB_MODE_MAX_STRIDE  2048u

/* Current physical output timing advertised by dist/core/video.json.
 * Dynamic app framebuffers are scaled into this output unless a later
 * physical-output mode switch changes the RTL timing. */

/* GPU fence-reached register, used by of_video_acquire_next() to
 * confirm a CMD_FLIP retired before waiting on fb_swap_pending.  GPU
 * MMIO base is target-specific; on Pocket it sits at 0x4a000000 with
 * fence_reached at offset 0x18 (per src/fpga/common/gpu_core.v's
 * MMIO map).  Apps reach it via OF_GPU_REG / GPU_FENCE_REACHED in
 * the SDK; the kernel has no abstraction yet, so hardcode here. */
#define GPU_FENCE_REACHED_REG  REG32(0x4a000018u)

/* CMD_FLIP diagnostic: 0x34 exposes the low 4 bits of the current GPU
 * framebuffer write inflight count (m_wr_inflight). */

/* Display timing control.
 * VIDEO_VTOTAL is the firmware-owned scanout V_TOTAL request. Hardware
 * clamps again in the video clock domain. In normal LCD mode, shorter
 * requests may be consumed during the current blanking interval; Analogizer
 * and SNAC fixed-rate modes override this register. */
#define VIDEO_VTOTAL       REG32(SYSREG_BASE + 0xDC)
#define VIDEO_VTOTAL_MIN   514u  /* 24.576 MHz / (780 * 514) = 61.30 Hz */
#define VIDEO_VTOTAL_MAX   750u  /* 24.576 MHz / (780 * 750) = 42.01 Hz */
#define VIDEO_VTOTAL_60HZ  525u  /* 24.576 MHz / (780 * 525) = 60.02 Hz; conservative normal LCD timing */

/* Data slot / DMA interface */
#define DS_SLOT_ID          REG32(SYSREG_BASE + 0x20)
#define DS_SLOT_OFFSET      REG32(SYSREG_BASE + 0x24)
#define DS_BRIDGE_ADDR      REG32(SYSREG_BASE + 0x28)
#define DS_LENGTH           REG32(SYSREG_BASE + 0x2C)
#define DS_PARAM_ADDR       REG32(SYSREG_BASE + 0x30)
#define DS_RESP_ADDR        REG32(SYSREG_BASE + 0x34)
#define DS_COMMAND          REG32(SYSREG_BASE + 0x38)
#define   DS_CMD_READ       1
#define   DS_CMD_WRITE      2
#define   DS_CMD_OPENFILE   3
#define   DS_CMD_GETFILE    4
/* F2i bridge word counters (read-only diagnostic, cleared in hardware on
 * each DS_COMMAND issue).  Counts every bridge word the host pushed during
 * the current/last data-slot command on both write legs, BEFORE any
 * FIFO/mux drop, so firmware can split "words never arrived" (count <
 * length/4 => RX-side drop) from "words arrived corrupt" (exact count).
 * Quasi-static contract: only meaningful after DS_STATUS reports
 * DONE|WR_IDLE — the CDC is a plain per-bit 2FF bank.
 * Pocket-only tap; reads 0 on targets without the APF bridge. */
#define DS_BRIDGE_WCNT      REG32(SYSREG_BASE + 0x1C)
#define   DS_BWC_CRAM0_MASK     0x00007FFFu /* CRAM0-leg words (sat 0x7FFF) */
#define   DS_BWC_CRAM0_DROPPED  (1u << 15)  /* sticky: CRAM0 write arrived while CPU owned mux */
#define   DS_BWC_SDRAM_MASK     0x7FFF0000u /* SDRAM-leg words (sat 0x7FFF) */
#define   DS_BWC_SDRAM_SHIFT    16
#define   DS_BWC_CRAM0_OVERRUN  (1u << 31)  /* sticky: CRAM0 write FIFO overrun drop */
#define DS_STATUS           REG32(SYSREG_BASE + 0x3C)
#define   DS_STATUS_ACK     (1 << 0)
#define   DS_STATUS_DONE    (1 << 1)
#define   DS_STATUS_ERR_MASK 0x1C   /* bits [4:2] */
#define   DS_STATUS_ERR_SHIFT 2
#define   DS_STATUS_READY   (1 << 5)  /* live: bridge idle, ready for new command */
#define   DS_STATUS_WR_IDLE (1 << 6)  /* live: all bridge writes drained to memory */
#define   DS_STATUS_IRQ_PENDING (1 << 7)  /* read: data-slot done IRQ, write 1 to clear */

/* Palette (256-color indexed) */
#define PAL_INDEX           REG32(SYSREG_BASE + 0x40)
#define PAL_WRITE           REG32(SYSREG_BASE + 0x44)
#define PAL_INDEX_COMMIT    (1u << 31)  /* write to PAL_INDEX: swap staged palette at frame start */
#define PAL_INDEX_BUSY      (1u << 31)  /* read from PAL_INDEX: staged swap not visible yet */

/* Save datatable control (per-slot size for bridge shutdown saves) */
#define SAVE_DT_SLOT        REG32(SYSREG_BASE + 0x48)
#define SAVE_DT_SIZE        REG32(SYSREG_BASE + 0x4C)
/* Entry-resolved nonvolatile size commits (HW_FEATURES bit 24,
 * OF_HW_SAVE_DT_WORD).  The legacy SAVE_DT_SLOT mapping assumes the fixed
 * "entry 8 = pre-save, entries 9-18 = saves 0-9" layout, which only holds
 * when every declared data slot loaded a file — the Pocket compacts the
 * datatable to loaded slots, so optional slots (Diablo) shift every save
 * entry and legacy commits land on the WRONG entry.  Write the raw
 * datatable WORD address here (entry*2+1, entry resolved by scanning the
 * id words) to ARM word-addressed routing; the next SAVE_DT_SIZE write
 * commits to exactly that word.  A SAVE_DT_SLOT write re-arms legacy mode.
 * Probe the feature bit and fall back to SAVE_DT_SLOT on old bitstreams. */
#define SAVE_DT_WORD        REG32(SYSREG_BASE + 0xC8)

/* Controller input */
#define CONT1_KEY           REG32(SYSREG_BASE + 0x50)
#define CONT1_JOY           REG32(SYSREG_BASE + 0x54)
#define CONT1_TRIG          REG32(SYSREG_BASE + 0x58)
#define CONT2_KEY           REG32(SYSREG_BASE + 0x5C)
#define CONT2_JOY           REG32(SYSREG_BASE + 0x60)
#define CONT2_TRIG          REG32(SYSREG_BASE + 0x64)

/* Input hub (0x100-0x158)
 * IRQ-on-change raw APF slots plus a compact hardware event FIFO.
 * FIFO event DATA1 read pops the event; read DATA0 first. */
#define INPUT_STATUS        REG32(SYSREG_BASE + 0x100)
#define   INPUT_STATUS_PENDING      (1 << 0)
#define   INPUT_STATUS_FIFO_EMPTY   (1 << 1)
#define   INPUT_STATUS_OVERFLOW     (1 << 3)
#define INPUT_IRQ_MASK      REG32(SYSREG_BASE + 0x104)  /* bits [3:0] enable APF slot change records */
#define INPUT_IRQ_CLEAR     REG32(SYSREG_BASE + 0x108)
#define   INPUT_IRQ_CLEAR_FIFO      (1 << 0)
#define   INPUT_IRQ_CLEAR_OVERFLOW  (1 << 3)
#define INPUT_SEQ           REG32(SYSREG_BASE + 0x10C)
#define INPUT_SLOT_INFO(n)  REG32(SYSREG_BASE + 0x110 + ((n) * 0x10))
#define INPUT_SLOT_KEY(n)   REG32(SYSREG_BASE + 0x114 + ((n) * 0x10))
#define INPUT_SLOT_JOY(n)   REG32(SYSREG_BASE + 0x118 + ((n) * 0x10))
#define INPUT_SLOT_TRIG(n)  REG32(SYSREG_BASE + 0x11C + ((n) * 0x10))
#define INPUT_FIFO_DATA0    REG32(SYSREG_BASE + 0x150)
#define INPUT_FIFO_DATA1    REG32(SYSREG_BASE + 0x154)  /* read pops */
#define INPUT_FIFO_COUNT    REG32(SYSREG_BASE + 0x158)
/* Optional decoded PSX SNAC poller. This is layered above the same physical
 * pinout as SNAC_GPIO; enabling it takes ownership of the SNAC pins until
 * disabled or until the raw SNAC_CTRL path is enabled again. */
#define SNAC_HW_CTRL        REG32(SYSREG_BASE + 0x160)
#define   SNAC_HW_CTRL_ENABLE      (1 << 0)
/* bit 1 reserved (was ANALOG; analog reads are auto-detected from the pad's
 * reported mode ID, so there is no separate analog enable any more) */
#define   SNAC_HW_CTRL_FAST        (1 << 2)
#define   SNAC_HW_STATUS_VALID     (1 << 8)
#define   SNAC_HW_STATUS_IRQ       (1 << 9)
#define SNAC_HW_BUTTONS     REG32(SYSREG_BASE + 0x164)
#define SNAC_HW_PRESSED     REG32(SYSREG_BASE + 0x168)
#define SNAC_HW_RELEASED    REG32(SYSREG_BASE + 0x16C)
#define SNAC_HW_JOY_L       REG32(SYSREG_BASE + 0x170)  /* [15:0]=LX, [31:16]=LY */
#define SNAC_HW_JOY_R       REG32(SYSREG_BASE + 0x174)  /* [15:0]=RX, [31:16]=RY */
#define SNAC_HW_DEBUG       REG32(SYSREG_BASE + 0x178)
#define SNAC_HW_CLEAR       REG32(SYSREG_BASE + 0x17C)
#define SNAC_HW_RAW_BUTTONS REG32(SYSREG_BASE + 0x180)
#define   SNAC_HW_CLEAR_IRQ        (1 << 0)
#define   SNAC_HW_CLEAR_EDGES      (1 << 1)

#define   INPUT_SLOT_INFO_PRESENT   (1 << 0)
#define   INPUT_SLOT_INFO_IRQ_EN    (1 << 8)


/* Analogizer CPU mirror. Pocket interact.json writes the same settings
 * through bridge addresses 0xF7000000/04/08; firmware uses these sysregs
 * to read the bridge defaults. */
#define ANALOGIZER_SETTINGS REG32(SYSREG_BASE + 0x80)
#define ANALOGIZER_H_OFFSET REG32(SYSREG_BASE + 0x84)
#define ANALOGIZER_V_OFFSET REG32(SYSREG_BASE + 0x88)

/* Misc */
#define SYS_GAME_ID         REG32(SYSREG_BASE + 0x68)

/* SNAC Shifter + GPIO (0xA0-0xAC) — raw access to the RndMnkIII
 * Analogizer/SNAC physical pinout. Firmware uses this for non-PSX protocols,
 * diagnostics, and manual transfers. */
#define SNAC_CTRL           REG32(SYSREG_BASE + 0xA0)
#define SNAC_DIV            REG32(SYSREG_BASE + 0xA4)
#define SNAC_DATA           REG32(SYSREG_BASE + 0xA8)
#define SNAC_GPIO           REG32(SYSREG_BASE + 0xAC)

/* SNAC_CTRL write bits */
#define   SNAC_CTRL_START       (1 << 0)    /* Start shift (self-clearing) */
#define   SNAC_CTRL_BITCNT_SHIFT 1          /* bits [5:1] = bit_count - 1 */
#define   SNAC_CTRL_LATCH       (1 << 6)    /* Pulse LATCH before shifting */
#define   SNAC_CTRL_ENABLE      (1 << 7)    /* 1=SNAC mode, 0=UART mode */
#define   SNAC_CTRL_MODE_A      (0 << 8)    /* Config A: NES/SNES/DB15 */
/* SNAC_CTRL read bits */
#define   SNAC_CTRL_BUSY        (1 << 0)    /* Shift in progress */

/* SNAC_DIV: [15:0] = half-period in CPU clocks.
 * Shift clock freq = CPU_FREQ / (2 * (div + 1))
 * e.g., div=499 → 100 KHz, div=199 → 250 KHz, div=49 → 1 MHz */

/* SNAC_DATA: write = TX shift-out data, read = RX shifted-in data */

/* SNAC_GPIO write: [7:0]=pin output values, [15:8]=pin directions (1=out)
 * SNAC_GPIO read:  [7:0]=pin input values,  [15:8]=pin directions
 * Pin mapping:
 *   [0] = OUT1  (bank1[6]) — always output
 *   [1] = OUT2  (bank1[7]) — always output
 *   [2] = IO3   (bank0[4]) — CfgA: DATA_IN, CfgB: unused/input
 *   [3] = IN7   (bank0[5]) — CfgA: DATA4,   CfgB: IRQ10
 *   [4] =        bank0[6]  — CfgB: ACK_IN
 *   [5] = IN4   (bank0[7]) — CfgA: DATA2,   CfgB: DAT_IN
 *   [6] = IO5   (pin30)    — CfgA: CLK2,    CfgB: CLK_OUT
 *   [7] = IO6   (pin31)    — CfgA: DATA3,   CfgB: CMD_OUT
 */
#define   SNAC_PIN_OUT1     (1 << 0)
#define   SNAC_PIN_OUT2     (1 << 1)
#define   SNAC_PIN_BK06     (1 << 4)
#define   SNAC_PIN_IO5      (1 << 6)
#define   SNAC_PIN_IO6      (1 << 7)
/* Direction bits in [15:8] — same mapping, shifted left 8 */
#define   SNAC_DIR_IO5      (1 << 14)
#define   SNAC_DIR_IO6      (1 << 15)

/* Shutdown handshake (0xB0) */
#define SYS_SHUTDOWN        REG32(SYSREG_BASE + 0xB0)
#define   SHUTDOWN_PENDING  (1 << 0)    /* Read: bridge requests shutdown */
#define   SHUTDOWN_ACK      (1 << 0)    /* Write: CPU acknowledges shutdown */

/* Hardware timer (0xB4-0xBC) — countdown with auto-reload, drives int_m_timer */
#define TIMER_PERIOD        REG32(SYSREG_BASE + 0xB4)  /* Reload value (CPU cycles) */
#define TIMER_CTRL          REG32(SYSREG_BASE + 0xB8)  /* [0]=enable, [1]=irq_pending (W1C) */
#define   TIMER_CTRL_ENABLE   (1 << 0)
#define   TIMER_CTRL_W1C_IRQ  (1 << 1)

/* Hardware PCM mixer (0x48000000) — 32 voices, SDRAM sample fetch,
 * linear interp, HW volume ramp, voice-end IRQ, group/master volume
 * composition.  See audio_mixer.v v3.
 *
 * Flat addressing — the address itself encodes voice + field, no SEL
 * latch, no race window.  Each per-voice field gets a unique address
 * so concurrent writes from main + ISR can't cross.
 *
 * Layout:
 *   0x000–0x7FF  per-voice fields:  base + voice*64 + field*4
 *   0x800        master_vol         (low byte used)
 *   0x804–0x810  group_vol[0..3]    (low byte used)
 *   0x814–0x818  voice_group map    (32 voices × 2 bits packed in 2 words)
 *   0x820        MIX_CTRL           [0]=enable
 *   0x824        MIX_IRQ            R: pending bitmap   W: W1C bits
 *   0x828        MIX_LAST_SAMPLE    R: retired diagnostic, reads 0
 *   0x82C        MIX_SAMPLE_COUNT   R: retired diagnostic, reads 0
 *   0x830        MIX_ACTIVE_MASK    R: 32-bit voice bitmap
 *   0x834        MIX_STATUS         R: retired diagnostic, reads 0
 *   0x880–0x8FF  per-voice POS_RD:  base + 0x880 + voice*4 (read-only)
 *
 * Address decode (axi_periph_slave):
 *   addr[11]=0           per-voice field write (voice=addr[10:6], field=addr[5:2])
 *   addr[11]=1, [7]=0    control regs            (addr[6:2] picks register)
 *   addr[11]=1, [7]=1    per-voice POS read     (voice=addr[6:2])
 */
#define MIX_BASE             0x48000000u   /* 0x4A=GPU(pocket), 0x4B=GPU(sim) — mixer lives below */
#define MIX_VOICE_FIELD(v, f)  REG32(MIX_BASE + ((v) << 6) + ((f) << 2))
#define MIX_VOICE_ADDR(v)        MIX_VOICE_FIELD((v), 0)   /* W: absolute SDRAM byte address */
#define MIX_VOICE_LEN(v)         MIX_VOICE_FIELD((v), 1)   /* W: sample count */
#define MIX_VOICE_RATE(v)        MIX_VOICE_FIELD((v), 2)   /* W: Q16.16 playback rate */
#define MIX_VOICE_CTRL(v)        MIX_VOICE_FIELD((v), 3)   /* W: [0]=active [1]=stereo [2]=loop [3]=stream */
#define MIX_VOICE_POS_WR(v)      MIX_VOICE_FIELD((v), 4)   /* W: set pos_int (0 or jump) */
#define MIX_VOICE_VOL_LR(v)      MIX_VOICE_FIELD((v), 6)   /* W: {vol_r[15:8], vol_l[7:0]} current */
#define MIX_VOICE_LOOP_END(v)    MIX_VOICE_FIELD((v), 7)   /* W: loop_end sample index */
#define MIX_VOICE_LOOP_START(v)  MIX_VOICE_FIELD((v), 8)   /* W: loop_start sample index */
#define MIX_VOICE_VOL_TARGET(v)  MIX_VOICE_FIELD((v), 9)   /* W: {tgt_r[15:8], tgt_l[7:0]} */
#define MIX_VOICE_VOL_RATE(v)    MIX_VOICE_FIELD((v), 10)  /* W: ramp step (0=snap) */
/* Stream mode (ctrl[3]): producer write pointer, sample/pair index.  A
 * stream voice plays only up to WPTR — when it catches up it fades out
 * (via the voice's vol ramp), holds position in silence, and resumes by
 * itself once WPTR advances.  Bitstreams older than the stream-mode RTL
 * ignore both ctrl[3] and this field (legacy loop/replay behavior).
 * CONTRACT: a stream voice must use loop_start=0, loop_end=length=ring
 * size, wptr<length (the HW backlog math wraps at LENGTH, the advance
 * at LOOP_END→LOOP_START — other loop windows break the hold). */
#define MIX_VOICE_WPTR(v)        MIX_VOICE_FIELD((v), 11)  /* W: stream write pointer */
#define MIX_MASTER_VOL       REG32(MIX_BASE + 0x800)
#define MIX_GROUP_VOL(g)     REG32(MIX_BASE + 0x804 + ((g) << 2))
#define MIX_VOICE_GROUP_LO   REG32(MIX_BASE + 0x814)  /* voices 0..15  packed 2 bits each */
#define MIX_VOICE_GROUP_HI   REG32(MIX_BASE + 0x818)  /* voices 16..31 packed 2 bits each */
#define MIX_CTRL             REG32(MIX_BASE + 0x820)  /* [0]=enable */
#define MIX_IRQ_PENDING      REG32(MIX_BASE + 0x824)  /* R: voice-end bitmap */
#define MIX_IRQ_CLEAR        REG32(MIX_BASE + 0x824)  /* W: W1C bits */
#define MIX_ACTIVE_MASK      REG32(MIX_BASE + 0x830)  /* R: 32-bit active-voice bitmap */
#define MIX_VOICE_POS(v)     REG32(MIX_BASE + 0x880 + ((v) << 2))  /* R: pos_int per voice */
#define   MIX_CTRL_ENABLE       (1 << 0)

/* Link-lite peripheral (0x4D000000) — IRQ-driven, 1-word TX/RX */
#define LINK_BASE            0x4D000000
#define LINK_CTRL            REG32(LINK_BASE + 0x00)  /* RW: [0]=enable [1]=reset [2]=master */
#define LINK_STATUS          REG32(LINK_BASE + 0x00)  /* R: [0]=enable [2]=master [4]=tx_empty [5]=rx_ready */
#define LINK_TX_DATA         REG32(LINK_BASE + 0x04)  /* W: word to transmit */
#define LINK_RX_DATA         REG32(LINK_BASE + 0x08)  /* R: received word (clears rx_ready) */
#define   LINK_STATUS_RX_READY  (1 << 5)

/* Vsync IRQ pending (0x9C) — read: bit 0 = pending, write: W1C clears */
#define VSYNC_IRQ_PENDING    REG32(SYSREG_BASE + 0x9C)

/* External IRQ mask (0xFC) — bit 5=data-slot completion, bit 4=input,
 * bit 3=vsync, bit 2=reserved, bit 1=link, bit 0=uart_rx.
 * Bit 2 was the HW mixer voice-end IRQ; mixer retired, bit reserved. */
#define IRQ_MASK             REG32(SYSREG_BASE + 0xFC)
#define   IRQ_MASK_LINK      (1 << 1)
#define   IRQ_MASK_VSYNC     (1 << 3)
#define   IRQ_MASK_INPUT     (1 << 4)
#define   IRQ_MASK_DATASLOT  (1 << 5)

/* Datatable slot size query:
 *   0x90 write: datatable word address
 *   0x90 read : bit31=result valid for this query, bits30:0=legacy data
 *   0x94 read : full 32-bit result, including size bit31 */
#define DT_QUERY            REG32(SYSREG_BASE + 0x90)
#define DT_QUERY_DATA       REG32(SYSREG_BASE + 0x94)

/* Hardware features (0x98) — read-only, set at synthesis time in RTL */
#define HW_FEATURES         REG32(SYSREG_BASE + 0x98)
#define   HW_FEAT_MIXER         (1 << 0)   /* a 32-voice mixer is available (always set) */
#define   HW_FEAT_MIXER_HW      (1 << 1)   /* mixer is HW audio_mixer.v (clear → SW mixer) */
#define   HW_FEAT_LINK          (1 << 2)
#define   HW_FEAT_ANALOGIZER    (1 << 3)
#define   HW_FEAT_GPU_SPAN      (1 << 4)   /* GPU span renderer (always set) */
#define   HW_FEAT_SAVE_SLOTS    (1 << 9)


/* ======================================================================
 * Audio FIFO status (0x4C000000) — HW mixer drives the FIFO directly in
 * v2.  Firmware reads AUDIO_STATUS only for diagnostics; AUDIO_SAMPLE
 * and AUDIO_DMA_* have been retired (writes no-op, reads return 0).
 * ====================================================================== */

#define AUDIO_BASE          0x4C000000
#define AUDIO_STATUS        REG32(AUDIO_BASE + 0x00)    /* Read: FIFO status */
/* DAC FIFO status read (+0x00): {21'b0, fifo_full, fifo_level[9:0]}.
 * fifo_full is the only reliable "full" condition — a completely full
 * 1024-deep dcfifo reads back fifo_level == 0 (wrusedw wraps).  Used by
 * the CPU software mixer pump (sw_mixer.c, active when of_mixer_use_sw) to
 * throttle sample pushes. */
#define   AUDIO_FIFO_LEVEL_MASK 0x3FFu
#define   AUDIO_FIFO_FULL       (1u << 10)
/* AUDIO_PCM_SAMPLE write (+0x04): {left[15:0], right[15:0]} pushes ONE
 * stereo sample into the DAC FIFO.  On a HW-mixer core (of_mixer_use_sw==0)
 * the mixer FSM drives the FIFO directly and firmware never touches this; on
 * an OS30 core (of_mixer_use_sw==1) the CPU software mixer renders samples and
 * pushes them here.  Same physical contract the RTL half implements. */
#define AUDIO_PCM_SAMPLE    REG32(AUDIO_BASE + 0x04)    /* Write: push {L,R} stereo sample */

/* CRAM0 ownership mux (v2 memory arch).  CRAM0 runs on the bridge
 * clock (clk_74a) and is time-sliced between the APF bridge and the
 * CPU's CDC by the single bit exposed here.  Firmware must drain the
 * currently-selected master before flipping the bit. */
#define CRAM0_MODE          REG32(0x4E000000u)
#define   CRAM0_MODE_BRIDGE (0u << 0)   /* bridge drives CRAM0 controller (default) */
#define   CRAM0_MODE_CPU    (1u << 0)   /* CPU's CDC drives CRAM0 controller */

/* ======================================================================
 * Link Cable (0x4D000000)
 * ====================================================================== */

#define LINK_BASE           0x4D000000

/* UART (0x4F000000): DevKey service serial, 2 Mbaud 8N1 */
#define UART_BASE           0x4F000000
#define UART_STATUS         REG32(UART_BASE + 0x00)     /* Read status bits */
#define UART_TX_DATA        REG32(UART_BASE + 0x04)     /* Write: push byte to TX FIFO */
#define UART_RX_DATA        REG32(UART_BASE + 0x08)     /* Read: received byte (pops RX FIFO) */
#define   UART_TX_RDY       (1 << 1)  /* TX FIFO has space (poll before write) */
#define   UART_RX_AVAIL     (1 << 2)  /* RX FIFO has at least one byte */
#define   UART_TX_IDLE      (1 << 3)  /* TX FIFO empty AND uart_tx not shifting
                                       * (use this to wait for all output to
                                       * fully drain before, e.g., entering a
                                       * low-power state or jumping to OS) */

/* ======================================================================
 * Analogizer (Bridge registers, accessible via interact.json settings)
 * CPU reads/writes through ANALOGIZER_* sysregs above. The bridge-side
 * addresses below are used by interact.json and are not CPU MMIO.
 * ====================================================================== */

/* Bits [4:0]: SNAC controller type, [9:6]: assignment, [13:10]: video type,
 * [15]: enable, [17:16]: Pocket LCD mode (owned by the video layer),
 * [19:18]: 15 kHz timing (ANLG_TIMING_*, ignored by RGBHV/scandoubler modes) */

/* SNAC controller type IDs */
#define SNAC_NONE           0x00
#define SNAC_DB15           0x01
#define SNAC_NES            0x02
#define SNAC_SNES           0x03
#define SNAC_PCE_2BTN       0x04
#define SNAC_PCE_6BTN       0x05
#define SNAC_PCE_MULTITAP   0x06
#define SNAC_DB15_FAST      0x09
#define SNAC_SNES_SWAP      0x0B
#define SNAC_PSX            0x10
#define SNAC_PSX_FAST       0x11
/* 0x12/0x13 (PSX Analog / PSX Analog Fast) retired: analog is auto-detected
 * from the controller's mode ID; digital pads center the sticks.  Persisted
 * menu values 0x12/0x13 degrade to SNAC_NONE via snac_type_supported(). */

/* Analogizer video output modes */
#define ANLG_VIDEO_RGBS             0x0
#define ANLG_VIDEO_RGSB             0x1
#define ANLG_VIDEO_YPBPR            0x2
#define ANLG_VIDEO_YC_NTSC          0x3
#define ANLG_VIDEO_YC_PAL           0x4
#define ANLG_VIDEO_SC_0PCT          0x5
#define ANLG_VIDEO_SC_50PCT         0x6
#define ANLG_VIDEO_SC_HQ2X          0x7
/* Modes 0x8-0xF: same as above but with Pocket screen OFF */
#define ANLG_VIDEO_POCKET_OFF       0x8

/* Analogizer 15 kHz timing (settings bits [19:18]).  Selects the dedicated
 * analog raster for the 15 kHz video modes (RGBS/RGsB/YPbPr/Y-C): 240p
 * progressive (default, pre-existing), 480i NTSC interlace (60 Hz fields,
 * full 480-line vertical resolution from a 640x480 FB), or 576i PAL
 * interlace (50 Hz fields, 480 lines letterboxed; makes Y/C PAL true
 * PAL-50 instead of PAL-60).  RGBHV/scandoubler modes ignore it. */
#define ANLG_TIMING_240P            0x0
#define ANLG_TIMING_480I            0x1
#define ANLG_TIMING_576I            0x2

/* ======================================================================
 * Controller Button Bits
 *
 * The canonical button bitmasks live in api/of_input.h as OF_BTN_*.
 * The Analogue Pocket APF native register layout in CONT1_KEY /
 * CONT2_KEY happens to match OF_BTN_* bit-for-bit, so no translation
 * is needed in the Pocket input HAL -- the raw register can be passed
 * straight through. A future target with a different native layout
 * must translate to OF_BTN_* in its own targets/<name>/input.c.
 * ====================================================================== */

/* ======================================================================
 * CPU Constants
 * ====================================================================== */

#define CPU_FREQ_HZ         OF_TARGET_CPU_FREQ_HZ

/* ======================================================================
 * Inline helpers
 * ====================================================================== */

static inline uint64_t read_cycles(void) {
    uint32_t hi1, lo, hi2;
    do {
        hi1 = SYS_CYCLE_HI;
        lo  = SYS_CYCLE_LO;
        hi2 = SYS_CYCLE_HI;
    } while (hi1 != hi2);
    return ((uint64_t)hi1 << 32) | lo;
}

static inline void fence(void) {
    __asm__ volatile("fence" ::: "memory");
}

static inline void fence_i(void) {
    __asm__ volatile("fence");
    /* fence.i (Zifencei) — encoded as .word because -march does not
     * include Zifencei so the assembler won't accept the mnemonic. */
    __asm__ volatile(".word 0x0000100f");
}

static inline int hw_feature_present(uint32_t feature) {
    return (HW_FEATURES & feature) != 0;
}

/* Convert CPU SDRAM address to uncached alias */
static inline volatile void *sdram_uncached(void *addr) {
    return (volatile void *)((uint32_t)addr - SDRAM_BASE + SDRAM_UNCACHED_BASE);
}

/* Convert CPU address to bridge address (for DMA).
 * Bridge address space (v2 memory arch):
 *   0x00000000  SDRAM   (CPU 0x10000000, cached)
 *   0x20000000  CRAM0   (CPU 0x30000000, uncached) — the only PSRAM left
 *
 * CRAM1 and SRAM are no longer CPU-addressable. */
static inline uint32_t sdram_to_bridge(void *addr) {
    uint32_t a = (uint32_t)addr;
    if (a >= SDRAM_UNCACHED_BASE && a < SDRAM_UNCACHED_BASE + SDRAM_SIZE)
        return a - SDRAM_UNCACHED_BASE;
    return a - SDRAM_BASE;
}

static inline uint32_t cpu_to_bridge(void *addr) {
    uint32_t a = (uint32_t)addr;
    if (a >= CRAM0_BASE && a < CRAM0_BASE + CRAM_SIZE) return (a - CRAM0_BASE) + CRAM0_BRIDGE;
    return sdram_to_bridge(addr);
}

/* ======================================================================
 * Runtime mixer-backend selection: HW MMIO vs CPU software mixer.
 *
 * ONE os.bin runs on every bitstream.  At boot of_init() sets the global
 * `of_mixer_use_sw` from the HW_FEATURES OF_HW_MIXER_HW bit (bit 1): the
 * default Pocket / OS25 / MiSTer cores have the hardware audio_mixer.v at
 * 0x48000000 (bit 1 set → of_mixer_use_sw=0, MMIO path); the OS30 Pocket
 * variant cuts audio_mixer.v to free FPGA budget (bit 1 clear →
 * of_mixer_use_sw=1, CPU software mixer in hal/sw_mixer.c).
 *
 * The MIX_VOICE_x / MIX_MASTER_x / MIX_CTRL / MIX_IRQ macros above are
 * REDEFINED here to branch on of_mixer_use_sw at every access, so
 * hal/mixer.c and targets/pocket/audio.c stay byte-for-byte identical
 * source.  When of_mixer_use_sw==0 the macro yields the SAME MMIO
 * address/value as before (compile to the identical volatile load/store):
 * apps + OS25 + MiSTer are byte-equivalent on the HW path.  When
 * of_mixer_use_sw==1 every access is retargeted onto sw_mix_regs[], the
 * flat voice-register backing store owned by hal/sw_mixer.c, whose CPU
 * render loop mixes the voices per-sample (byte-exact port of
 * audio_mixer.v) and pushes finished stereo pairs to the DAC FIFO via
 * AUDIO_PCM_SAMPLE.  sw_mixer.c is ALWAYS compiled/linked now; its
 * render/pump self-no-op when of_mixer_use_sw==0.
 * ====================================================================== */

/* Set once at boot by of_init(): 1 = CPU software mixer, 0 = HW MMIO. */
extern int of_mixer_use_sw;

/* The whole 0x000..0x8FF mixer MMIO aperture (the HW slave's decode
 * window) is mirrored by a flat 32-bit word array owned by hal/sw_mixer.c.
 * Indexing by (byte_offset >> 2).  A few HW registers have read != write
 * semantics (MIX_IRQ_PENDING reads the pending bitmap; MIX_IRQ_CLEAR W1Cs
 * it; MIX_ACTIVE_MASK / MIX_VOICE_POS are read-only snapshots); sw_mixer.c
 * special-cases those addresses via the helper accessors below. */
#define SW_MIX_APERTURE_WORDS  0x240u   /* 0x900 bytes >> 2 */
extern volatile uint32_t sw_mix_regs[SW_MIX_APERTURE_WORDS];

/* Read-divergent registers (IRQ-pending bitmap, active-mask, per-voice pos
 * snapshot) route through this accessor so they behave like the HW. */
uint32_t sw_mixer_reg_read(uint32_t mix_byte_addr);
volatile uint32_t *sw_mix_irq_doorbell(void);

/* Runtime-select an lvalue: SW array slot when of_mixer_use_sw, else MMIO.
 * Both ternary arms are (volatile uint32_t *), so the dereference is a
 * plain volatile load/store usable as lvalue OR rvalue — every MIX_*
 * call site compiles unchanged.  With of_mixer_use_sw==0 the optimizer
 * folds this to the exact `*(volatile uint32_t *)(MIX_BASE + off)` the
 * old MMIO macro emitted (HW path byte-equivalent). */
#define SW_MIX_SEL(byteoff)                                                  \
    (*(of_mixer_use_sw ? &sw_mix_regs[(byteoff) >> 2]                        \
                       : (volatile uint32_t *)(MIX_BASE + (byteoff))))

#undef MIX_VOICE_FIELD
#undef MIX_VOICE_ADDR
#undef MIX_VOICE_LEN
#undef MIX_VOICE_RATE
#undef MIX_VOICE_CTRL
#undef MIX_VOICE_POS_WR
#undef MIX_VOICE_VOL_LR
#undef MIX_VOICE_LOOP_END
#undef MIX_VOICE_LOOP_START
#undef MIX_VOICE_VOL_TARGET
#undef MIX_VOICE_VOL_RATE
#undef MIX_VOICE_WPTR
#undef MIX_MASTER_VOL
#undef MIX_GROUP_VOL
#undef MIX_VOICE_GROUP_LO
#undef MIX_VOICE_GROUP_HI
#undef MIX_CTRL
#undef MIX_IRQ_PENDING
#undef MIX_IRQ_CLEAR
#undef MIX_ACTIVE_MASK
#undef MIX_VOICE_POS

/* Plain RW fields: runtime-selected lvalue/rvalue, identical call-site source. */
#define MIX_VOICE_FIELD(v, f)    SW_MIX_SEL(((v) << 6) + ((f) << 2))
#define MIX_VOICE_ADDR(v)        MIX_VOICE_FIELD((v), 0)
#define MIX_VOICE_LEN(v)         MIX_VOICE_FIELD((v), 1)
#define MIX_VOICE_RATE(v)        MIX_VOICE_FIELD((v), 2)
#define MIX_VOICE_CTRL(v)        MIX_VOICE_FIELD((v), 3)
#define MIX_VOICE_POS_WR(v)      MIX_VOICE_FIELD((v), 4)
#define MIX_VOICE_VOL_LR(v)      MIX_VOICE_FIELD((v), 6)
#define MIX_VOICE_LOOP_END(v)    MIX_VOICE_FIELD((v), 7)
#define MIX_VOICE_LOOP_START(v)  MIX_VOICE_FIELD((v), 8)
#define MIX_VOICE_VOL_TARGET(v)  MIX_VOICE_FIELD((v), 9)
#define MIX_VOICE_VOL_RATE(v)    MIX_VOICE_FIELD((v), 10)
#define MIX_VOICE_WPTR(v)        MIX_VOICE_FIELD((v), 11)
#define MIX_MASTER_VOL           SW_MIX_SEL(0x800)
#define MIX_GROUP_VOL(g)         SW_MIX_SEL(0x804 + ((g) << 2))
#define MIX_VOICE_GROUP_LO       SW_MIX_SEL(0x814)
#define MIX_VOICE_GROUP_HI       SW_MIX_SEL(0x818)
#define MIX_CTRL                 SW_MIX_SEL(0x820)

/* Read/write-divergent registers.  Each call site uses only ONE form of a
 * given name (read OR assign), so a read-macro and an assign-target-macro
 * are sufficient and unambiguous:
 *
 *   MIX_IRQ_PENDING            rvalue  — voice-end pending bitmap
 *   MIX_IRQ_CLEAR  = bits      assign  — W1C clear of pending bits
 *   MIX_ACTIVE_MASK            rvalue  — active-voice bitmap
 *   MIX_VOICE_POS(v)           rvalue  — per-voice pos_int snapshot
 *
 * MMIO path (of_mixer_use_sw==0): straight REG32 read / W1C write at the
 * physical address — byte-equivalent to the old HW macros.
 *
 * SW path (of_mixer_use_sw==1): pending uses a W1C doorbell.  Writes to
 * MIX_IRQ_CLEAR OR the bits to clear into a doorbell word; every reader of
 * pending (and the render loop) drains the doorbell first (pending &=
 * ~doorbell), so W1C is correct regardless of who observes it when.
 * sw_mixer_reg_read() implements that drain for the read side. */
#define MIX_IRQ_PENDING   (of_mixer_use_sw ? sw_mixer_reg_read(0x824)        \
                                           : REG32(MIX_BASE + 0x824))
#define MIX_IRQ_CLEAR     (*(of_mixer_use_sw ? sw_mix_irq_doorbell()         \
                                             : (volatile uint32_t *)(MIX_BASE + 0x824)))
#define MIX_ACTIVE_MASK   (of_mixer_use_sw ? sw_mixer_reg_read(0x830)        \
                                           : REG32(MIX_BASE + 0x830))
#define MIX_VOICE_POS(v)  (of_mixer_use_sw ? sw_mixer_reg_read(0x880 + ((v) << 2)) \
                                           : REG32(MIX_BASE + 0x880 + ((v) << 2)))

#ifdef BUILDING_BOOTLOADER
/* Clean compile-time bypass of software mixer for bootloader (BRAM) context.
 * Direct MMIO writes only. */
#undef SW_MIX_SEL
#define SW_MIX_SEL(byteoff)   (*(volatile uint32_t *)(MIX_BASE + (byteoff)))

#undef MIX_IRQ_PENDING
#define MIX_IRQ_PENDING       REG32(MIX_BASE + 0x824)

#undef MIX_IRQ_CLEAR
#define MIX_IRQ_CLEAR         (*(volatile uint32_t *)(MIX_BASE + 0x824))

#undef MIX_ACTIVE_MASK
#define MIX_ACTIVE_MASK       REG32(MIX_BASE + 0x830)

#undef MIX_VOICE_POS
#define MIX_VOICE_POS(v)      REG32(MIX_BASE + 0x880 + ((v) << 2))
#endif

#endif /* OFOS_REGS_H */
