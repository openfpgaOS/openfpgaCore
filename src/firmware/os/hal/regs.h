/*
 * openfpgaOS Hardware Register Definitions
 * Complete register map for Analogue Pocket FPGA peripherals
 */

#ifndef OFOS_REGS_H
#define OFOS_REGS_H

#include <stdint.h>

/* ======================================================================
 * Register access macros
 * ====================================================================== */

#define REG32(addr)     (*(volatile uint32_t *)(addr))
#define REG16(addr)     (*(volatile uint16_t *)(addr))
#define REG8(addr)      (*(volatile uint8_t *)(addr))

/* ======================================================================
 * Memory Map
 * ====================================================================== */

#define BRAM_BASE           0x00000000
#define BRAM_SIZE           (32 * 1024)

/* App BRAM region — available for app hot code after OS sections.
 * Top 512 bytes reserved for trap handler stack frame. */
#define APP_BRAM_BASE       0x00002000
#define APP_BRAM_END        (BRAM_BASE + BRAM_SIZE - 512)  /* 0x00007E00 */
#define APP_BRAM_SIZE       (APP_BRAM_END - APP_BRAM_BASE)

#define SDRAM_BASE          0x10000000
#define SDRAM_SIZE          (64 * 1024 * 1024)
#define SDRAM_UNCACHED_BASE 0x50000000      /* D-cache bypass alias */

#define FB0_BASE            0x10000000      /* Framebuffer 0 */
#define FB1_BASE            0x10100000      /* Framebuffer 1 */
#define FB_WIDTH            320
#define FB_HEIGHT           240
#define FB_STRIDE           320             /* 8-bit indexed, 1 byte/pixel */
#define FB_SIZE             (FB_WIDTH * FB_HEIGHT)

#define DMA_BUFFER          0x10180000      /* 512KB DMA bounce buffer (cached) */
#define DMA_BUFFER_UNCACHED 0x50180000      /* Same region, D-cache bypass */
#define DMA_CHUNK_SIZE      (512 * 1024)

#define INTERACT_BASE       0x103FE000      /* APF interact settings (4KB) */
#define INTERACT_UNCACHED   0x503FE000      /* Uncached alias */
#define INTERACT_MAX_VARS   64              /* Max interact variables */

#define TERM_VRAM_BASE      0x20000000      /* Terminal character VRAM */
#define TERM_COLOR_BASE     0x20000800      /* Terminal color attribute VRAM */
#define TERM_COLS           40
#define TERM_ROWS           30

#define CRAM0_BASE          0x30000000      /* CRAM0 cached */
#define CRAM1_BASE          0x31000000      /* CRAM1 cached */
#define CRAM0_UNCACHED      0x38000000      /* CRAM0 uncached (D-cache bypass) */
#define CRAM1_UNCACHED      0x39000000      /* CRAM1 uncached (D-cache bypass) */
#define CRAM_SIZE           (16 * 1024 * 1024)

#define AUDIO_RING_ADDR     0x3A000000      /* Audio ring buffer in SRAM (32KB, separate bus) */

#define SRAM_BASE           0x3A000000      /* SRAM uncached */
#define SRAM_SIZE           (256 * 1024)

/* Legacy alias */
#define PSRAM_BASE          CRAM0_BASE
#define PSRAM_SIZE          CRAM_SIZE

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
#define SYS_DISPLAY_MODE    REG32(SYSREG_BASE + 0x0C)
#define   DISPLAY_MODE_TERMINAL     0
#define   DISPLAY_MODE_FRAMEBUFFER  1
#define   DISPLAY_MODE_OVERLAY      2  /* White terminal text over framebuffer */

#define SYS_COLOR_MODE      REG32(SYSREG_BASE + 0x70)
#define   COLOR_MODE_8BIT       0   /* 8-bit indexed (256 colors, 1 byte/pixel) */
#define   COLOR_MODE_4BIT       1   /* 4-bit indexed (16 colors, 0.5 byte/pixel) */
#define   COLOR_MODE_2BIT       2   /* 2-bit indexed (4 colors, 0.25 byte/pixel) */
#define   COLOR_MODE_RGB565     3   /* 16-bit direct (R5G6B5, 2 bytes/pixel) */
#define   COLOR_MODE_RGB555     4   /* 15-bit direct (X1R5G5B5, 2 bytes/pixel) */
#define   COLOR_MODE_RGBA5551   5   /* 15-bit + alpha (R5G5B5A1, 2 bytes/pixel) */

/* Framebuffer control (double-buffered) */
#define FB_DISPLAY_ADDR     REG32(SYSREG_BASE + 0x10)
#define FB_DRAW_ADDR        REG32(SYSREG_BASE + 0x14)
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

/* Tile Engine (0x40000080) */
#define TILE_CTRL           REG32(SYSREG_BASE + 0x80)
#define   TILE_CTRL_ENABLE    (1 << 0)
#define   TILE_CTRL_PRIORITY  (1 << 1)  /* 0=behind FB, 1=over FB */
#define TILE_SCROLL         REG32(SYSREG_BASE + 0x84)
#define TILE_MAP_ADDR       REG32(SYSREG_BASE + 0x88)
#define TILE_MAP_DATA       REG32(SYSREG_BASE + 0x8C)
#define TILE_CHR_ADDR       REG32(SYSREG_BASE + 0x90)
#define TILE_CHR_DATA       REG32(SYSREG_BASE + 0x94)

/* Sprite Engine (0x40000098) */
#define SPR_CTRL            REG32(SYSREG_BASE + 0x98)
#define   SPR_CTRL_ENABLE     (1 << 0)
#define SPR_SAT_IDX         REG32(SYSREG_BASE + 0x9C)
#define SPR_SAT_XY          REG32(SYSREG_BASE + 0xA0)
#define SPR_SAT_ATTR        REG32(SYSREG_BASE + 0xA4)
#define SPR_CHR_ADDR        REG32(SYSREG_BASE + 0xA8)
#define SPR_CHR_DATA        REG32(SYSREG_BASE + 0xAC)

/* Shutdown handshake (0xB0) */
#define SYS_SHUTDOWN        REG32(SYSREG_BASE + 0xB0)
#define   SHUTDOWN_PENDING  (1 << 0)    /* Read: bridge requests shutdown */
#define   SHUTDOWN_ACK      (1 << 0)    /* Write: CPU acknowledges shutdown */

/* DMA engine (0xC0) — memory-to-memory copy/fill, bypasses D-cache */
#define DMA_SRC             REG32(SYSREG_BASE + 0xC0)
#define DMA_DST             REG32(SYSREG_BASE + 0xC4)
#define DMA_LEN             REG32(SYSREG_BASE + 0xC8)
#define DMA_CTRL            REG32(SYSREG_BASE + 0xCC)
#define   DMA_CTRL_START    (1 << 0)
#define   DMA_CTRL_FILL     (1 << 1)
#define DMA_STATUS          REG32(SYSREG_BASE + 0xD0)
#define   DMA_STATUS_BUSY   (1 << 0)

/* Audio DMA engine (0xE0) — reads from SDRAM ring buffer, feeds audio FIFO */
#define ADMA_RING_BASE      REG32(SYSREG_BASE + 0xE0)  /* SDRAM byte address of ring */
#define ADMA_RING_CFG       REG32(SYSREG_BASE + 0xE4)  /* [12:0]=size_log2, [16]=enable */
#define ADMA_RING_WPTR      REG32(SYSREG_BASE + 0xE8)  /* Write pointer (entry index) */
#define ADMA_RING_RPTR      REG32(SYSREG_BASE + 0xEC)  /* Read pointer (read-only) */
#define   ADMA_ENABLE       (1 << 16)

/* Low-level DMA start (no cache management — caller must handle) */
static inline void dma_start_copy(uint32_t dst, uint32_t src, uint32_t len) {
    DMA_SRC = src;
    DMA_DST = dst;
    DMA_LEN = len;
    DMA_CTRL = DMA_CTRL_START;
}

static inline void dma_start_fill(uint32_t dst, uint32_t value, uint32_t len) {
    DMA_SRC = value;
    DMA_DST = dst;
    DMA_LEN = len;
    DMA_CTRL = DMA_CTRL_START | DMA_CTRL_FILL;
}

static inline void dma_wait(void) {
    while (DMA_STATUS & DMA_STATUS_BUSY) {}
}

/* Tile/Sprite constants */
#define TILE_MAP_COLS       64
#define TILE_MAP_ROWS       32
#define TILE_SIZE           8       /* 8x8 pixels */
#define TILE_MAX_TILES      256
#define SPRITE_MAX          64

/* ======================================================================
 * Audio FIFO (0x4C000000)
 * ====================================================================== */

#define AUDIO_BASE          0x4C000000
#define AUDIO_SAMPLE        REG32(AUDIO_BASE + 0x00)    /* Write: stereo sample */
#define AUDIO_STATUS        REG32(AUDIO_BASE + 0x00)    /* Read: FIFO status */
#define   AUDIO_FIFO_LEVEL_MASK  0xFFF                  /* bits [11:0] */
#define   AUDIO_FIFO_FULL        (1 << 12)
#define AUDIO_FIFO_DEPTH    4096

/* ======================================================================
 * Link Cable (0x4D000000)
 * ====================================================================== */

#define LINK_BASE           0x4D000000
#define LINK_REG(n)         REG32(LINK_BASE + ((n) << 2))

/* ======================================================================
 * OPL3 (YMF262) Sound Synthesis (0x4E000000)
 * ====================================================================== */

#define OPL_BASE            0x4E000000
#define OPL_ADDR            REG32(OPL_BASE + 0x00)      /* Bank 0 address register */
#define OPL_DATA            REG32(OPL_BASE + 0x04)      /* Bank 0 data register */
#define OPL_ADDR2           REG32(OPL_BASE + 0x08)      /* Bank 1 address register */
#define OPL_DATA2           REG32(OPL_BASE + 0x0C)      /* Bank 1 data register */

/* UART (0x4F000000) — DevKey debug serial, 2 Mbaud 8N1 */
#define UART_BASE           0x4F000000
#define UART_STATUS         REG32(UART_BASE + 0x00)     /* Read: bit0=1, bit1=TX ready, bit2=RX valid */
#define UART_TX_DATA        REG32(UART_BASE + 0x04)     /* Write: send byte [7:0] */
#define UART_RX_DATA        REG32(UART_BASE + 0x08)     /* Read: received byte (clears valid) */
#define   UART_TX_RDY       (1 << 1)
#define   UART_RX_AVAIL     (1 << 2)

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
 * Pocket Controller Button Bits (cont1_key / cont2_key)
 * Matches Analogue Pocket APF controller format
 * ====================================================================== */

#define BTN_DPAD_UP         (1 << 0)
#define BTN_DPAD_DOWN       (1 << 1)
#define BTN_DPAD_LEFT       (1 << 2)
#define BTN_DPAD_RIGHT      (1 << 3)
#define BTN_A               (1 << 4)
#define BTN_B               (1 << 5)
#define BTN_X               (1 << 6)
#define BTN_Y               (1 << 7)
#define BTN_L1              (1 << 8)
#define BTN_R1              (1 << 9)
#define BTN_L2              (1 << 10)
#define BTN_R2              (1 << 11)
#define BTN_L3              (1 << 12)
#define BTN_R3              (1 << 13)
#define BTN_SELECT          (1 << 14)
#define BTN_START           (1 << 15)

/* ======================================================================
 * CPU Constants
 * ====================================================================== */

#define CPU_FREQ_HZ         100000000   /* 100 MHz */

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
    __asm__ volatile(".word 0x0000100f");  /* fence.i */
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
    if (a >= 0x39000000 && a < 0x3A000000) return (a - 0x39000000) + 0x30000000; /* CRAM1 uncached */
    if (a >= 0x38000000 && a < 0x39000000) return (a - 0x38000000) + 0x20000000; /* CRAM0 uncached */
    if (a >= 0x31000000 && a < 0x32000000) return (a - 0x31000000) + 0x30000000; /* CRAM1 cached */
    if (a >= 0x30000000 && a < 0x31000000) return (a - 0x30000000) + 0x20000000; /* CRAM0 cached */
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
#define OF_MEM_DMA_BUFFER           DMA_BUFFER
#define OF_MEM_DMA_CHUNK_SIZE       DMA_CHUNK_SIZE
#define OF_MEM_CRAM0_BASE           CRAM0_BASE
#define OF_MEM_CRAM1_BASE           CRAM1_BASE
#define OF_MEM_CRAM0_UNCACHED       CRAM0_UNCACHED
#define OF_MEM_CRAM1_UNCACHED       CRAM1_UNCACHED
#define OF_MEM_CRAM_SIZE            CRAM_SIZE
#define OF_MEM_SRAM_BASE            SRAM_BASE
#define OF_MEM_SRAM_SIZE            SRAM_SIZE
#define OF_MEM_TERM_VRAM_BASE       TERM_VRAM_BASE

/* System registers */
#define OF_REG_SYSREG_BASE          SYSREG_BASE
#define OF_REG_SYS_STATUS           SYS_STATUS
#define OF_REG_SYS_CYCLE_LO         SYS_CYCLE_LO
#define OF_REG_SYS_CYCLE_HI         SYS_CYCLE_HI
#define OF_REG_SYS_DISPLAY_MODE     SYS_DISPLAY_MODE
#define OF_REG_FB_DISPLAY_ADDR      FB_DISPLAY_ADDR
#define OF_REG_FB_DRAW_ADDR         FB_DRAW_ADDR
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

/* Tile engine registers */
#define OF_REG_TILE_CTRL            TILE_CTRL
#define OF_REG_TILE_SCROLL          TILE_SCROLL
#define OF_REG_TILE_MAP_ADDR        TILE_MAP_ADDR
#define OF_REG_TILE_MAP_DATA        TILE_MAP_DATA
#define OF_REG_TILE_CHR_ADDR        TILE_CHR_ADDR
#define OF_REG_TILE_CHR_DATA        TILE_CHR_DATA

/* Sprite engine registers */
#define OF_REG_SPR_CTRL             SPR_CTRL
#define OF_REG_SPR_SAT_IDX          SPR_SAT_IDX
#define OF_REG_SPR_SAT_XY           SPR_SAT_XY
#define OF_REG_SPR_SAT_ATTR         SPR_SAT_ATTR
#define OF_REG_SPR_CHR_ADDR         SPR_CHR_ADDR
#define OF_REG_SPR_CHR_DATA         SPR_CHR_DATA

/* Audio registers */
#define OF_REG_AUDIO_SAMPLE         AUDIO_SAMPLE
#define OF_REG_AUDIO_STATUS         AUDIO_STATUS
#define OF_REG_OPL_ADDR             OPL_ADDR
#define OF_REG_OPL_DATA             OPL_DATA

/* Link registers */
#define OF_REG_LINK_BASE            LINK_BASE

#endif /* OFOS_REGS_H */

