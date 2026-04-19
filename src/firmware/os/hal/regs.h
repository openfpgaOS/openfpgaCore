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
#define REG16(addr)     (*(volatile uint16_t *)(addr))
#define REG8(addr)      (*(volatile uint8_t *)(addr))

/* ======================================================================
 * Memory Map
 * ====================================================================== */

#define BRAM_BASE           OF_TARGET_BRAM_BASE
#define BRAM_SIZE           OF_TARGET_BRAM_SIZE

/* App BRAM region — available for app hot code after OS sections.
 * Top 512 bytes reserved for trap handler stack frame. */
#define APP_BRAM_BASE       OF_TARGET_APP_BRAM_BASE
#define APP_BRAM_END        OF_TARGET_APP_BRAM_END  /* Caps at 0x7800, libc at 0x7C00, trap stack at 0x7E00 */
#define APP_BRAM_SIZE       (APP_BRAM_END - APP_BRAM_BASE)

#define SDRAM_BASE          OF_TARGET_SDRAM_BASE
#define SDRAM_SIZE          OF_TARGET_SDRAM_SIZE
#define SDRAM_UNCACHED_BASE OF_TARGET_SDRAM_UNCACHED_BASE      /* D-cache bypass alias */

#define FB0_BASE            OF_TARGET_FB0_BASE      /* Framebuffer 0 */
#define FB1_BASE            OF_TARGET_FB1_BASE      /* Framebuffer 1 */
#define FB2_BASE            OF_TARGET_FB2_BASE      /* Framebuffer 2 */
#define TERM_FB_BASE        OF_TARGET_TERM_FB_BASE  /* Dedicated terminal framebuffer */
#define FB_COUNT            OF_TARGET_FB_COUNT
#define FB_WIDTH            OF_TARGET_FB_WIDTH
#define FB_HEIGHT           OF_TARGET_FB_HEIGHT
#define FB_STRIDE           OF_TARGET_FB_STRIDE
#define FB_SIZE             (FB_WIDTH * FB_HEIGHT)

#define DMA_CHUNK_SIZE      OF_TARGET_DMA_CHUNK_SIZE    /* Max transfer per target file op */

#define INTERACT_BASE       OF_TARGET_INTERACT_BASE
#define INTERACT_UNCACHED   OF_TARGET_INTERACT_UNCACHED
#define INTERACT_MAX_VARS   OF_TARGET_INTERACT_MAX_VARS

/* Terminal VRAM removed — terminal renders to TERM_FB_BASE in SDRAM */
#define TERM_COLS           40
#define TERM_ROWS           30

#define CRAM0_BASE          OF_TARGET_CRAM0_BASE      /* CRAM0 cached */
#define CRAM1_BASE          OF_TARGET_CRAM1_BASE      /* CRAM1 cached */
#define CRAM0_UNCACHED      OF_TARGET_CRAM0_UNCACHED  /* CRAM0 uncached (D-cache bypass) */
#define CRAM1_UNCACHED      OF_TARGET_CRAM1_UNCACHED  /* CRAM1 uncached (D-cache bypass) */
#define CRAM_SIZE           OF_TARGET_CRAM_SIZE

#define CRAM0_BRIDGE        OF_TARGET_CRAM0_BRIDGE      /* CRAM0 in bridge address space */
#define CRAM1_BRIDGE        OF_TARGET_CRAM1_BRIDGE      /* CRAM1 in bridge address space */

/* FTAB (file table) — written by Chip32 loader at boot (256 bytes) */
#define CRAM1_FTAB            (CRAM1_BASE + OF_TARGET_CRAM1_FTAB_OFFSET)
#define CRAM1_FTAB_UNCACHED   (CRAM1_UNCACHED + OF_TARGET_CRAM1_FTAB_OFFSET)
#define CRAM1_FTAB_BRIDGE     (CRAM1_BRIDGE + OF_TARGET_CRAM1_FTAB_OFFSET)

/* DMA scratch area in CRAM1 (after FTAB, before I/O cache) */
#define CRAM1_SCRATCH         (CRAM1_BASE + OF_TARGET_CRAM1_SCRATCH_OFFSET)
#define CRAM1_SCRATCH_UNCACHED (CRAM1_UNCACHED + OF_TARGET_CRAM1_SCRATCH_OFFSET)
#define CRAM1_SCRATCH_BRIDGE  (CRAM1_BRIDGE + OF_TARGET_CRAM1_SCRATCH_OFFSET)

#define SRAM_BASE           OF_TARGET_SRAM_BASE      /* SRAM uncached */
#define SRAM_SIZE           OF_TARGET_SRAM_SIZE

/* Runtime stack layout (must match os.ld) */
#define RUNTIME_STACK_TOP   OF_TARGET_RUNTIME_STACK_TOP
#define RUNTIME_STACK_SIZE  OF_TARGET_RUNTIME_STACK_SIZE
#define APP_STACK_TOP       (RUNTIME_STACK_TOP - RUNTIME_STACK_SIZE)  /* 0x13F80000 */

/* Sample memory pool for the shared mixer allocator. */
#define SAMPLE_POOL_BASE    OF_TARGET_SAMPLE_BASE
#define SAMPLE_POOL_SIZE    OF_TARGET_SAMPLE_SIZE
#define SAMPLE_POOL_END     (SAMPLE_POOL_BASE + SAMPLE_POOL_SIZE)


/* ======================================================================
 * System Registers (0x40000000)
 * ====================================================================== */

#define SYSREG_BASE         0x40000000

/* Core status & control */
#define SYS_STATUS          REG32(SYSREG_BASE + 0x00)
#define   SYS_STATUS_SDRAM_READY    (1 << 0)
#define   SYS_STATUS_ALLCOMPLETE    (1 << 1)
#define SYS_CYCLE_LO        REG32(SYSREG_BASE + 0x04)
#define SYS_CYCLE_HI        REG32(SYSREG_BASE + 0x08)
/* Terminal FB control: bit[0] = 1 scanout reads terminal FB, 0 = app FB */
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
#define FB_DISPLAY_ADDR     REG32(SYSREG_BASE + 0x10)
#define FB_DISPLAY_IDX      REG32(SYSREG_BASE + 0x14)
#define FB_SWAP_CTRL        REG32(SYSREG_BASE + 0x18)

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
#define DS_STATUS           REG32(SYSREG_BASE + 0x3C)
#define   DS_STATUS_ACK     (1 << 0)
#define   DS_STATUS_DONE    (1 << 1)
#define   DS_STATUS_ERR_MASK 0x1C   /* bits [4:2] */
#define   DS_STATUS_ERR_SHIFT 2
#define   DS_STATUS_READY   (1 << 5)  /* live: bridge idle, ready for new command */
#define   DS_STATUS_WR_IDLE (1 << 6)  /* live: all bridge writes drained to memory */

/* Palette (256-color indexed) */
#define PAL_INDEX           REG32(SYSREG_BASE + 0x40)
#define PAL_WRITE           REG32(SYSREG_BASE + 0x44)

/* Save datatable control (per-slot size for bridge shutdown saves) */
#define SAVE_DT_SLOT        REG32(SYSREG_BASE + 0x48)
#define SAVE_DT_SIZE        REG32(SYSREG_BASE + 0x4C)

/* Controller input */
#define CONT1_KEY           REG32(SYSREG_BASE + 0x50)
#define CONT1_JOY           REG32(SYSREG_BASE + 0x54)
#define CONT1_TRIG          REG32(SYSREG_BASE + 0x58)
#define CONT2_KEY           REG32(SYSREG_BASE + 0x5C)
#define CONT2_JOY           REG32(SYSREG_BASE + 0x60)
#define CONT2_TRIG          REG32(SYSREG_BASE + 0x64)

/* Misc */
#define SYS_GAME_ID         REG32(SYSREG_BASE + 0x68)

/* SNAC Shifter + GPIO (0xA0-0xAC) — software-driven SNAC controller interface
 * Replaces hardware protocol FSMs with a generic SPI/shift register master.
 * CPU bit-bangs controller protocols (NES/SNES/PSX/PCE) via this interface. */
#define SNAC_CTRL           REG32(SYSREG_BASE + 0xA0)
#define SNAC_DIV            REG32(SYSREG_BASE + 0xA4)
#define SNAC_DATA           REG32(SYSREG_BASE + 0xA8)
#define SNAC_GPIO           REG32(SYSREG_BASE + 0xAC)

/* SNAC_CTRL write bits */
#define   SNAC_CTRL_START       (1 << 0)    /* Start shift (self-clearing) */
#define   SNAC_CTRL_BITCNT_SHIFT 1          /* bits [5:1] = bit_count - 1 */
#define   SNAC_CTRL_LATCH       (1 << 6)    /* Pulse LATCH before shifting */
#define   SNAC_CTRL_ENABLE      (1 << 7)    /* 1=SNAC mode, 0=UART mode */
#define   SNAC_CTRL_MODE_SHIFT  8           /* bits [9:8]: 00=CfgA, 01=CfgB */
#define   SNAC_CTRL_MODE_A      (0 << 8)    /* Config A: NES/SNES/DB15 */
#define   SNAC_CTRL_MODE_B      (1 << 8)    /* Config B: PSX */
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
 *   [2] = IO3   (bank0[4]) — CfgA: DATA_IN, CfgB: CLK_OUT
 *   [3] = IN7   (bank0[5]) — CfgA: DATA4,   CfgB: IRQ10
 *   [4] =        bank0[6]  — CfgB: ACK_IN
 *   [5] = IN4   (bank0[7]) — CfgA: DATA2,   CfgB: DAT_IN
 *   [6] = IO5   (pin30)    — CfgA: CLK2,    CfgB: ACK_IN
 *   [7] = IO6   (pin31)    — CfgA: DATA3,   CfgB: CMD_OUT
 */
#define   SNAC_PIN_OUT1     (1 << 0)
#define   SNAC_PIN_OUT2     (1 << 1)
#define   SNAC_PIN_IO3      (1 << 2)
#define   SNAC_PIN_IN7      (1 << 3)
#define   SNAC_PIN_BK06     (1 << 4)
#define   SNAC_PIN_IN4      (1 << 5)
#define   SNAC_PIN_IO5      (1 << 6)
#define   SNAC_PIN_IO6      (1 << 7)
/* Direction bits in [15:8] — same mapping, shifted left 8 */
#define   SNAC_DIR_IO3      (1 << 10)
#define   SNAC_DIR_IN7      (1 << 11)
#define   SNAC_DIR_BK06     (1 << 12)
#define   SNAC_DIR_IN4      (1 << 13)
#define   SNAC_DIR_IO5      (1 << 14)
#define   SNAC_DIR_IO6      (1 << 15)

/* Shutdown handshake (0xB0) */
#define SYS_SHUTDOWN        REG32(SYSREG_BASE + 0xB0)
#define   SHUTDOWN_PENDING  (1 << 0)    /* Read: bridge requests shutdown */
#define   SHUTDOWN_ACK      (1 << 0)    /* Write: CPU acknowledges shutdown */

/* Hardware timer (0xB4-0xBC) — countdown with auto-reload, drives int_m_timer */
#define TIMER_PERIOD        REG32(SYSREG_BASE + 0xB4)  /* Reload value (CPU cycles) */
#define TIMER_CTRL          REG32(SYSREG_BASE + 0xB8)  /* [0]=enable, [1]=irq_pending (W1C) */
#define TIMER_COUNTER       REG32(SYSREG_BASE + 0xBC)  /* Current countdown (read-only) */
#define   TIMER_CTRL_ENABLE   (1 << 0)
#define   TIMER_CTRL_W1C_IRQ  (1 << 1)

/* Hardware PCM mixer (0x80-0x88, 0xC0-0xF8) — 48-voice CRAM1-backed */
#define MIX_VOICE_SEL        REG32(SYSREG_BASE + 0xC0)  /* Write: voice index 0-47 */
#define MIX_VOICE_ADDR       REG32(SYSREG_BASE + 0xC4)  /* Write: CRAM1 word address */
#define MIX_VOICE_LEN        REG32(SYSREG_BASE + 0xC8)  /* Write: length (also sets LOOP_END/LOOP_START defaults) */
#define MIX_VOICE_RATE       REG32(SYSREG_BASE + 0xCC)  /* Write: rate (16.16 fixed-point) */
#define MIX_VOICE_CTRL       REG32(SYSREG_BASE + 0xD0)  /* Write: [0]=active [1]=loop [2]=fmt16 [3]=bidi [4]=dir */
#define MIX_VOICE_POS        REG32(SYSREG_BASE + 0xD0)  /* Read: position[21:0] for selected voice */
#define MIX_CTRL             REG32(SYSREG_BASE + 0xD4)  /* RW: [0]=enable */
#define MIX_VOICE_VOL_LR     REG32(SYSREG_BASE + 0xD8)  /* Write: {vol_r[15:8], vol_l[7:0]} (current, ramped by HW) */
#define MIX_STATUS           REG32(SYSREG_BASE + 0xD8)  /* Read: [5:0]=active voices */
#define MIX_VOICE_LOOP_END   REG32(SYSREG_BASE + 0xE4)  /* Write: loop end point[21:0] */
#define MIX_VOICE_POS_WR     REG32(SYSREG_BASE + 0xE8)  /* Write: set position[21:0] */
#define MIX_VOICE_LOOP_START REG32(SYSREG_BASE + 0xEC)  /* Write: loop start point[21:0] */
#define MIX_VOICE_VOL_TARGET REG32(SYSREG_BASE + 0xF0)  /* Write: {target_r[7:0], target_l[7:0]} */
#define MIX_VOICE_VOL_RATE   REG32(SYSREG_BASE + 0xF4)  /* Write: ramp step size (0=instant) */
#define MIX_IRQ_PENDING      REG32(SYSREG_BASE + 0xF8)  /* Read: voice-end bitmask [31:0] */
#define MIX_IRQ_CLEAR        REG32(SYSREG_BASE + 0xF8)  /* Write: W1C */

/* Mixer extension registers (0x100+) */
#define MIX_VOICE_FILTER_FC  REG32(SYSREG_BASE + 0x100) /* Write: Q0.16 SVF cutoff coefficient */
#define MIX_VOICE_FILTER_Q   REG32(SYSREG_BASE + 0x104) /* Write: {enable[8], Q[7:0]} */
#define MIX_CRAM1_INHIBIT    REG32(SYSREG_BASE + 0x10C) /* W: bit 0 = pause mixer CRAM1 reads (Phase 6a) */
/* MIX_IRQ_PENDING_HI / MIX_IRQ_CLEAR_HI removed in Phase 8 — voice
 * count is 32 so the upper half is always zero. */

/* AWE coprocessor registers (0x110+).  See of_awe.h for the awe_voice_t struct. */
#define AWE_NOTE_ON          REG32(SYSREG_BASE + 0x114) /* W: data[5:0] = voice id strobe */
#define AWE_NOTE_OFF         REG32(SYSREG_BASE + 0x118) /* W: data[5:0] = voice id strobe */
#define AWE_VOICE_STOP       REG32(SYSREG_BASE + 0x11C) /* W: data[5:0] = voice id strobe */
#define AWE_VOICE_LOAD_ADDR  REG32(SYSREG_BASE + 0x120) /* W: {voice[5:0], word[4:0]} cursor */
#define AWE_VOICE_LOAD_DATA  REG32(SYSREG_BASE + 0x124) /* W: data; auto-incs word cursor */
#define AWE_CHAN_LOAD_ADDR   REG32(SYSREG_BASE + 0x128) /* W: {ch[3:0], word[1:0]} cursor */
#define AWE_CHAN_LOAD_DATA   REG32(SYSREG_BASE + 0x12C) /* W: data; auto-incs word cursor */
#define AWE_MASTER_VOL       REG32(SYSREG_BASE + 0x130) /* W: 0..255 */
#define AWE_BEND_RANGE       REG32(SYSREG_BASE + 0x134) /* W: cents */
/* 0x110 AWE_CTRL / 0x138 AWE_REVERB_PRESET / 0x13C AWE_CHORUS_PRESET /
 * 0x140 AWE_INTERP_DEFAULT removed in Phase 8 — fabric never read them. */
#define AWE_ACTIVE_MASK_LO   REG32(SYSREG_BASE + 0x144) /* R: voices [31:0] active */
#define AWE_ACTIVE_MASK_HI   REG32(SYSREG_BASE + 0x148) /* R: voices [47:32] active (always 0 at 32 voices) */
#define AWE_TICK_COUNT       REG32(SYSREG_BASE + 0x14C) /* R: 1 kHz tick counter (Phase 2) */
#define AWE_HW_ENVELOPE      REG32(SYSREG_BASE + 0x150) /* W: bit 0 = HW envelope enable (Phase 3) */
#define AWE_MM_WRITE         REG32(SYSREG_BASE + 0x154) /* W: Phase 4 mod-matrix scale
                                                         *    [31:16] = s16 scale
                                                         *    [10:8]  = field (0..4)
                                                         *    [5:0]   = voice (0..47) */
#define AWE_REVERB_LEVEL     REG32(SYSREG_BASE + 0x158) /* W: Phase 6a reverb wet mix 0..255 */
#define AWE_REVERB_FEEDBACK  REG32(SYSREG_BASE + 0x15C) /* W: Phase 6a reverb feedback  0..255 */
#define AWE_CHORUS_LEVEL     REG32(SYSREG_BASE + 0x160) /* W: Phase 6b chorus wet mix 0..255 */
#define AWE_CHORUS_RATE      REG32(SYSREG_BASE + 0x164) /* W: Phase 6b chorus LFO incr per sample, Q16 */
#define AWE_CHORUS_DEPTH     REG32(SYSREG_BASE + 0x168) /* W: Phase 6b chorus LFO swing in samples 0..255 */
/* Phase 5c — Ramp1 (mod env) trigger.  Two-write protocol:
 *   1. Write rate (Q16.16 incr per ms tick) to AWE_RAMP1_RATE.
 *   2. Write {stage[11:8], voice[5:0]} to AWE_RAMP1_TRIGGER.
 *      stage = ENV_ATTACK (2)  → restart from level=0 with given rate
 *      stage = ENV_RELEASE (6) → fade from current level
 *      stage = ENV_DONE (7)    → snap to 0 and stop  */
#define AWE_RAMP1_RATE       REG32(SYSREG_BASE + 0x16C)
#define AWE_RAMP1_TRIGGER    REG32(SYSREG_BASE + 0x170)

/* Link-lite peripheral (0x4D000000) — IRQ-driven, 1-word TX/RX */
#define LINK_BASE            0x4D000000
#define LINK_CTRL            REG32(LINK_BASE + 0x00)  /* RW: [0]=enable [1]=reset [2]=master */
#define LINK_STATUS          REG32(LINK_BASE + 0x00)  /* R: [0]=enable [2]=master [4]=tx_empty [5]=rx_ready */
#define LINK_TX_DATA         REG32(LINK_BASE + 0x04)  /* W: word to transmit */
#define LINK_RX_DATA         REG32(LINK_BASE + 0x08)  /* R: received word (clears rx_ready) */
#define LINK_DIVISOR         REG32(LINK_BASE + 0x0C)  /* W: half-period clock divider */
#define   LINK_STATUS_TX_EMPTY  (1 << 4)
#define   LINK_STATUS_RX_READY  (1 << 5)

/* Vsync IRQ pending (0x9C) — read: bit 0 = pending, write: W1C clears */
#define VSYNC_IRQ_PENDING    REG32(SYSREG_BASE + 0x9C)

/* External IRQ mask (0xFC) — bits[3:0] = {vsync, mix_voice_end, link, uart_rx} enable */
#define IRQ_MASK             REG32(SYSREG_BASE + 0xFC)
#define   IRQ_MASK_UART_RX   (1 << 0)
#define   IRQ_MASK_LINK      (1 << 1)
#define   IRQ_MASK_MIX_VOICE (1 << 2)
#define   IRQ_MASK_VSYNC     (1 << 3)

/* VRR (Variable Refresh Rate) — dynamic V_TOTAL for video timing (0xDC)
 * Write: bits[9:0] = V_TOTAL line count (262–375, default 262)
 * Read:  bits[9:0] = current V_TOTAL
 * Pocket scaler accepts 42-60 Hz. RTL hard-clamps to [262, 375]. */
#define VRR_V_TOTAL         REG32(SYSREG_BASE + 0xDC)

/* VRR swap hold — skip N vsyncs before presenting a queued frame (0xE0)
 * Write: bits[3:0] = number of vsyncs to skip (0=immediate, 1=skip one, etc.)
 * Used for even frame pacing in the 30-40 FPS gap. */
#define VRR_SWAP_HOLD       REG32(SYSREG_BASE + 0xE0)

/* Datatable slot size query (0x90) — write slot entry address, read result */
#define DT_QUERY            REG32(SYSREG_BASE + 0x90)

/* Bridge debug (0x94) — internal latch state for DMA diagnostics */
#define DS_DEBUG            REG32(SYSREG_BASE + 0x94)

/* Hardware features (0x98) — read-only, set at synthesis time in RTL */
#define HW_FEATURES         REG32(SYSREG_BASE + 0x98)
#define   HW_FEAT_MIXER         (1 << 0)
#define   HW_FEAT_LINK          (1 << 2)
#define   HW_FEAT_ANALOGIZER    (1 << 3)
#define   HW_FEAT_GPU_SPAN      (1 << 4)   /* GPU span renderer (always set) */
#define   HW_FEAT_GPU_TRIANGLE  (1 << 5)   /* GPU triangle rasterizer (Full) */
#define   HW_FEAT_MIDI          (1 << 6)   /* MIDI playback (any backend) */
#define   HW_FEAT_WIFI          (1 << 7)
#define   HW_FEAT_FPU           (1 << 8)
#define   HW_FEAT_SAVE_SLOTS    (1 << 9)
#define   HW_FEAT_GPU_VCOLOR    (1 << 10)  /* GPU vertex color (Full) */
#define   HW_FEAT_GPU_BILINEAR  (1 << 11)  /* GPU bilinear filtering (Full) */
#define   HW_FEAT_GPU_ALPHA     (1 << 12)  /* GPU alpha blending (Full) */
#define   HW_FEAT_GPU_PERSP     (1 << 13)  /* GPU perspective spans (Lite/Full) */
#define   HW_FEAT_GPU_FRAGPIPE  (1 << 14)  /* GPU 1-px/cycle frag pipeline (Lite/Full) */
#define   HW_FEAT_MIDI_SMP      (1 << 15)  /* Sample-based MIDI synthesis */


/* ======================================================================
 * Audio FIFO (0x4C000000)
 * ====================================================================== */

#define AUDIO_BASE          0x4C000000
#define AUDIO_SAMPLE        REG32(AUDIO_BASE + 0x00)    /* Write: stereo sample */
#define AUDIO_STATUS        REG32(AUDIO_BASE + 0x00)    /* Read: FIFO status */
#define   AUDIO_FIFO_LEVEL_MASK  0x1FF                  /* bits [8:0] */
#define   AUDIO_FIFO_FULL        (1 << 9)
#define AUDIO_FIFO_DEPTH    512

/* ======================================================================
 * Link Cable (0x4D000000)
 * ====================================================================== */

#define LINK_BASE           0x4D000000
#define LINK_REG(n)         REG32(LINK_BASE + ((n) << 2))

/* UART (0x4F000000) — DevKey debug serial, 2 Mbaud 8N1 */
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
 * CPU reads current state from bridge-synced registers.
 * Write access requires FPGA register additions.
 * ====================================================================== */

#define ANALOGIZER_BRIDGE_BASE  0xF7000000
/* Bits [4:0]: SNAC controller type, [9:6]: assignment, [13:10]: video type, [15]: enable */

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
#define SNAC_PSX_ANALOG     0x12
#define SNAC_PSX_ANALOG_FAST 0x13

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
 * Bridge address space:
 *   0x00000000  SDRAM   (CPU 0x10000000)
 *   0x20000000  CRAM0   (CPU 0x30000000 cached / 0x38000000 uncached)
 *   0x30000000  CRAM1   (CPU 0x31000000 cached / 0x39000000 uncached)
 *   0x3A000000  SRAM    (CPU 0x3A000000 uncached)
 */
static inline uint32_t sdram_to_bridge(void *addr) {
    return (uint32_t)addr - SDRAM_BASE;
}

static inline uint32_t cpu_to_bridge(void *addr) {
    uint32_t a = (uint32_t)addr;
    if (a >= CRAM1_UNCACHED && a < CRAM1_UNCACHED + CRAM_SIZE) return (a - CRAM1_UNCACHED) + CRAM1_BRIDGE; /* CRAM1 uncached */
    if (a >= CRAM0_UNCACHED && a < CRAM0_UNCACHED + CRAM_SIZE) return (a - CRAM0_UNCACHED) + CRAM0_BRIDGE; /* CRAM0 uncached */
    if (a >= CRAM1_BASE && a < CRAM1_BASE + CRAM_SIZE) return (a - CRAM1_BASE) + CRAM1_BRIDGE; /* CRAM1 cached */
    if (a >= CRAM0_BASE && a < CRAM0_BASE + CRAM_SIZE) return (a - CRAM0_BASE) + CRAM0_BRIDGE; /* CRAM0 cached */
    return a - SDRAM_BASE; /* SDRAM */
}

/* ======================================================================
 * Standardized aliases (OF_* prefix)
 * ====================================================================== */

/* Memory map */
#define OF_MEM_BRAM_BASE            BRAM_BASE
#define OF_MEM_BRAM_SIZE            BRAM_SIZE
#define OF_MEM_SDRAM_BASE           SDRAM_BASE
#define OF_MEM_SDRAM_SIZE           SDRAM_SIZE
#define OF_MEM_SDRAM_UNCACHED_BASE  SDRAM_UNCACHED_BASE
#define OF_MEM_FB0_BASE             FB0_BASE
#define OF_MEM_FB1_BASE             FB1_BASE
#define OF_MEM_FB2_BASE             FB2_BASE
#define OF_MEM_DMA_CHUNK_SIZE       DMA_CHUNK_SIZE
#define OF_MEM_CRAM0_BASE           CRAM0_BASE
#define OF_MEM_CRAM1_BASE           CRAM1_BASE
#define OF_MEM_CRAM0_UNCACHED       CRAM0_UNCACHED
#define OF_MEM_CRAM1_UNCACHED       CRAM1_UNCACHED
#define OF_MEM_CRAM_SIZE            CRAM_SIZE
#define OF_MEM_SRAM_BASE            SRAM_BASE
#define OF_MEM_SRAM_SIZE            SRAM_SIZE
#define OF_MEM_TERM_FB_BASE         TERM_FB_BASE

/* System registers */
#define OF_REG_SYSREG_BASE          SYSREG_BASE
#define OF_REG_SYS_STATUS           SYS_STATUS
#define OF_REG_SYS_CYCLE_LO         SYS_CYCLE_LO
#define OF_REG_SYS_CYCLE_HI         SYS_CYCLE_HI
#define OF_REG_TERM_FB_CTRL         TERM_FB_CTRL
#define OF_REG_FB_DISPLAY_ADDR      FB_DISPLAY_ADDR
#define OF_REG_FB_DISPLAY_IDX       FB_DISPLAY_IDX
#define OF_REG_FB_SWAP_CTRL         FB_SWAP_CTRL
#define OF_REG_DS_SLOT_ID           DS_SLOT_ID
#define OF_REG_DS_SLOT_OFFSET       DS_SLOT_OFFSET
#define OF_REG_DS_BRIDGE_ADDR       DS_BRIDGE_ADDR
#define OF_REG_DS_LENGTH            DS_LENGTH
#define OF_REG_DS_COMMAND           DS_COMMAND
#define OF_REG_DS_STATUS            DS_STATUS
#define OF_REG_PAL_INDEX            PAL_INDEX
#define OF_REG_PAL_WRITE            PAL_WRITE
#define OF_REG_CONT1_KEY            CONT1_KEY
#define OF_REG_CONT1_JOY            CONT1_JOY
#define OF_REG_CONT1_TRIG           CONT1_TRIG
#define OF_REG_CONT2_KEY            CONT2_KEY
#define OF_REG_CONT2_JOY            CONT2_JOY
#define OF_REG_CONT2_TRIG           CONT2_TRIG
#define OF_REG_SYS_GAME_ID          SYS_GAME_ID
#define OF_REG_HW_FEATURES          HW_FEATURES

/* Audio registers */
#define OF_REG_AUDIO_SAMPLE         AUDIO_SAMPLE
#define OF_REG_AUDIO_STATUS         AUDIO_STATUS
/* Link registers */
#define OF_REG_LINK_BASE            LINK_BASE

#endif /* OFOS_REGS_H */
