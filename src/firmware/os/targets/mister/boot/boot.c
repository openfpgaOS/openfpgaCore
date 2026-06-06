//------------------------------------------------------------------------------
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileType: SOURCE
// SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
//------------------------------------------------------------------------------

/*
 * openfpgaOS Bootloader — MiSTer
 *
 * Runs from BRAM.  The MiSTer framework auto-delivers boot.rom (= os.bin)
 * over ioctl at core start; hps_bridge.v DMAs the byte stream into the
 * SDRAM staging arena and raises HPS_STATUS_BOOT_LOADED.  This loader
 * waits on that flag, copies the image from staging to its runtime VMA,
 * verifies the CRC trailer (append_os_crc.py), zeroes BSS/stack, and
 * jumps to os_main.
 *
 * Differences from the Pocket loader:
 *   - no APF ALLCOMPLETE wait, no CRAM0 mux, no datatable size query
 *     (HPS_BOOT_LEN carries the image size)
 *   - no PHDP/UART host discovery (the HPS is the delivery channel)
 *   - CRC-mismatch retries re-copy from staging (the staging copy itself
 *     is stable SDRAM, so retries only guard the SDRAM→SDRAM copy)
 *
 * IMPORTANT: This code runs BEFORE os.bin is loaded into SDRAM.
 * It must NOT call any HAL functions (they live in SDRAM).
 * All hardware access is done via direct register writes.
 */

#include "../hal/regs.h"
#include "../targets/mister/hps_regs.h"

#define SHARED_ATTR __attribute__((section(".text.boot")))
#include "dcache_evict.inc.c"
#undef SHARED_ATTR

/* Trap context breadcrumbs (read by misaligned handler), kept in BRAM. */
volatile unsigned int __attribute__((section(".bss.boot"))) pd_dbg_stage;
volatile unsigned int __attribute__((section(".bss.boot"))) pd_dbg_info;

/* Counts staging→SDRAM copies that failed the CRC and were retried. */
volatile unsigned int __attribute__((section(".bss.boot"))) os_load_crc_retries;

/* OS image integrity trailer (append_os_crc.py): [magic 'OFC1'][crc32 LE]. */
#define OS_CRC_MAGIC         0x3143464Fu
#define OS_CRC_TRAILER_BYTES 8u
#define OS_LOAD_MAX_ATTEMPTS 4

#define BOOT_ROM_WAIT_CYCLES 1000000000u   /* ~10 s for HPS file delivery */
#define BOOT_OS_SIZE_MAX     (768u * 1024u)
#define BOOT_CACHE_LINE_SIZE 64u
#define BOOT_HW_IDLE_TIMEOUT 1000000u

#define BOOT_GPU_REG(offset)     REG32(OF_TARGET_GPU_BASE + (offset))
#define BOOT_GPU_CTRL            BOOT_GPU_REG(0x00u)
#define BOOT_GPU_STATUS          BOOT_GPU_REG(0x14u)
#define BOOT_GPU_DMA_SRC         BOOT_GPU_REG(0x0Cu)
#define BOOT_GPU_DMA_LEN         BOOT_GPU_REG(0x1Cu)
#define BOOT_GPU_TEX_FLUSH       BOOT_GPU_REG(0x28u)
#define BOOT_GPU_CTRL_SOFT_RESET (1u << 1)
#define BOOT_GPU_CTRL_RING_RESET (1u << 2)
#define BOOT_GPU_STATUS_DMA_BUSY (1u << 2)

#define BOOT_LINK_CTRL_RESET     (1u << 1)

/* os.bin staging copy in the arena, via the uncached alias. */
#define BOOT_STAGE_UNCACHED  (SDRAM_UNCACHED_BASE + OF_TARGET_CRAM0_BRIDGE + \
                              OF_TARGET_CRAM0_OS_OFFSET)

/* External symbols from linker */
extern char _os_bss_start[], _os_bss_end[];
extern char _runtime_stack_top[];
extern char _os_copy_size[];
extern char _osdata_init_vma_start[];

/* OS entry point */
extern void os_main(void);
extern void switch_to_runtime_stack_and_call(void (*entry)(void), void *stack_top);

__attribute__((section(".text.boot")))
static uintptr_t boot_sdram_uncached_addr(const void *addr) {
    uint32_t a = (uint32_t)(uintptr_t)addr;
    if (a >= SDRAM_BASE && a < SDRAM_BASE + SDRAM_SIZE)
        return (uintptr_t)(a - SDRAM_BASE + SDRAM_UNCACHED_BASE);
    return (uintptr_t)a;
}

__attribute__((section(".text.boot")))
static void boot_dcache_inval_range(void *addr, uint32_t size) {
    if (size == 0)
        return;

    uintptr_t a = (uintptr_t)addr & ~(uintptr_t)(BOOT_CACHE_LINE_SIZE - 1u);
    uintptr_t end = (uintptr_t)addr + size;
    __asm__ volatile("fence" ::: "memory");
    for (; a < end; a += BOOT_CACHE_LINE_SIZE)
        __asm__ volatile(".insn i 0x0F, 2, x0, %0, 0" :: "r"(a) : "memory");
    __asm__ volatile("fence" ::: "memory");
}

__attribute__((section(".text.boot")))
static void boot_zero_uncached_sdram(uint32_t start, uint32_t end) {
    if (end <= start)
        return;

    volatile uint32_t *p =
        (volatile uint32_t *)boot_sdram_uncached_addr((void *)(uintptr_t)start);
    volatile uint32_t *e =
        (volatile uint32_t *)boot_sdram_uncached_addr((void *)(uintptr_t)end);
    while (p < e)
        *p++ = 0;
    __asm__ volatile("fence" ::: "memory");
}

/* Font in BRAM (.fastrodata) — defined in terminal.c */
extern const uint8_t font8x8[2048];

__attribute__((section(".text.boot")))
static void boot_palette_init(void) {
    PAL_INDEX = 15;
    PAL_WRITE = 0xFFFFFF;
    PAL_INDEX = PAL_INDEX_COMMIT;
}

__attribute__((section(".text.boot")))
static void boot_fb_putchar(int col, int row, char c) {
    if ((unsigned)col >= TERM_COLS || (unsigned)row >= TERM_ROWS) return;
    volatile uint8_t *fb = (volatile uint8_t *)TERM_FB_BASE;
    const uint8_t *glyph = &font8x8[(unsigned)(uint8_t)c * 8];
    int px = col * 8;
    int py = row * 8;
    for (int y = 0; y < 8; y++) {
        uint8_t bits = glyph[y];
        volatile uint8_t *dst = &fb[(py + y) * 320 + px];
        for (int x = 0; x < 8; x++) {
            dst[x] = (bits & 0x80) ? 15 : 0;  /* white on black */
            bits <<= 1;
        }
    }
}

__attribute__((section(".text.boot")))
static void boot_fb_puts(int col, int row, const char *s) {
    while (*s && col < TERM_COLS) {
        boot_fb_putchar(col, row, *s);
        col++;
        s++;
    }
}

__attribute__((section(".text.boot")))
static void boot_fb_clear_row(int row) {
    if ((unsigned)row >= TERM_ROWS) return;
    volatile uint8_t *fb = (volatile uint8_t *)TERM_FB_BASE;
    for (int i = 0; i < 320 * 8; i++)
        fb[row * 8 * 320 + i] = 0;
}

/* os_finalize_memory() lives in BRAM .fasttext.  It only zeroes .bss. */
extern void os_finalize_memory(void *bss_start, void *bss_end);

__attribute__((section(".text.boot")))
static void flush_icache(void) {
    __asm__ volatile("fence");
    __asm__ volatile(".word 0x0000100f");  /* fence.i */
}

__attribute__((section(".text.boot")))
static void boot_hw_stabilize(void) {
    __asm__ volatile("fence" ::: "memory");

    /* Stale interrupt enables/pending bits are the most dangerous warm-boot
     * leftovers. */
    IRQ_MASK = 0;
    TIMER_CTRL = TIMER_CTRL_W1C_IRQ;
    VSYNC_IRQ_PENDING = 1;
    DS_STATUS = DS_STATUS_IRQ_PENDING;

    /* Boot console scanout mode. */
    TERM_FB_CTRL = 1u;
    SYS_COLOR_MODE = COLOR_MODE_8BIT;
    FB_MODE_SIZE = (FB_HEIGHT << 16) | FB_WIDTH;
    FB_MODE_STRIDE = FB_STRIDE;
    VIDEO_SCALER_MODE = VIDEO_SCALER_SLOT_DEFAULT_320X240;
    VIDEO_VTOTAL = VIDEO_VTOTAL_60HZ;

    /* Quiesce the sector engine register block. */
    DS_SLOT_ID = 0;
    DS_SLOT_OFFSET = 0;
    DS_BRIDGE_ADDR = 0;
    DS_LENGTH = 0;
    DS_PARAM_ADDR = 0;
    DS_RESP_ADDR = 0;

    /* Stop subsystems that can keep producing bus traffic or IRQs after a
     * warm restart. */
    MIX_CTRL = 0;
    for (uint32_t v = 0; v < 32u; v++)
        MIX_VOICE_CTRL(v) = 0;
    MIX_MASTER_VOL = 0xFFu;
    MIX_GROUP_VOL(0) = 0xFFu;
    MIX_GROUP_VOL(1) = 0xFFu;
    MIX_GROUP_VOL(2) = 0xFFu;
    MIX_GROUP_VOL(3) = 0xFFu;
    MIX_VOICE_GROUP_LO = 0;
    MIX_VOICE_GROUP_HI = 0;
    MIX_IRQ_CLEAR = 0xFFFFFFFFu;

    LINK_CTRL = BOOT_LINK_CTRL_RESET;

    BOOT_GPU_CTRL = BOOT_GPU_CTRL_SOFT_RESET;
    for (volatile int i = 0; i < 8; i++) {}
    uint32_t gpu_wait = BOOT_HW_IDLE_TIMEOUT;
    while (BOOT_GPU_STATUS & BOOT_GPU_STATUS_DMA_BUSY) {
        if (--gpu_wait == 0)
            break;
    }
    BOOT_GPU_DMA_SRC = 0;
    BOOT_GPU_DMA_LEN = 0;
    BOOT_GPU_TEX_FLUSH = 1;
    BOOT_GPU_CTRL = BOOT_GPU_CTRL_RING_RESET;
    for (volatile int i = 0; i < 8; i++) {}

    __asm__ volatile("fence" ::: "memory");
}

/* Image size: HPS_BOOT_LEN when sane, else the linked size. */
__attribute__((section(".text.boot")))
static uint32_t boot_os_image_size(void) {
    uint32_t linked_size = (uint32_t)(uintptr_t)_os_copy_size;
    uint32_t hps_size = HPS_BOOT_LEN;

    if (hps_size >= 4096u && hps_size <= BOOT_OS_SIZE_MAX)
        return hps_size;
    return linked_size;
}

/* CRC-32/ISO-HDLC over a loaded SDRAM range via the uncached alias. */
__attribute__((section(".text.boot")))
static uint32_t boot_crc32_uncached(uint32_t cached_base, uint32_t len) {
    volatile const uint32_t *p =
        (volatile const uint32_t *)boot_sdram_uncached_addr((void *)(uintptr_t)cached_base);
    uint32_t crc = 0xFFFFFFFFu;
    uint32_t words = len >> 2;
    for (uint32_t w = 0; w < words; w++) {
        uint32_t v = p[w];
        for (int b = 0; b < 4; b++) {
            crc ^= (v & 0xFFu);
            v >>= 8;
            for (int k = 0; k < 8; k++)
                crc = (crc >> 1) ^ (0xEDB88320u & (uint32_t)(-(int32_t)(crc & 1u)));
        }
    }
    for (uint32_t b = 0; b < (len & 3u); b++) {
        uint32_t v = p[words] >> (b * 8);
        crc ^= (v & 0xFFu);
        for (int k = 0; k < 8; k++)
            crc = (crc >> 1) ^ (0xEDB88320u & (uint32_t)(-(int32_t)(crc & 1u)));
    }
    return crc ^ 0xFFFFFFFFu;
}

__attribute__((section(".text.boot")))
static int boot_verify_os_image(uint32_t total) {
    if (total < OS_CRC_TRAILER_BYTES)
        return 1;
    uint32_t image_len = total - OS_CRC_TRAILER_BYTES;
    uint32_t base = (uint32_t)(uintptr_t)_osdata_init_vma_start;
    volatile const uint32_t *trailer =
        (volatile const uint32_t *)boot_sdram_uncached_addr(
            (void *)(uintptr_t)(base + image_len));
    if (trailer[0] != OS_CRC_MAGIC)
        return 1;   /* unstamped image — cannot verify */
    return boot_crc32_uncached(base, image_len) == trailer[1];
}

/* Copy os.bin from the ioctl staging region to its runtime VMA.  Both
 * sides go through the uncached alias: the staging bytes were DMA'd by
 * hps_bridge (never cached), and the destination must be visible to the
 * imminent instruction fetch without trusting a cache sweep. */
__attribute__((section(".text.boot")))
static void boot_copy_os_once(uint32_t total) {
    volatile const uint32_t *src = (volatile const uint32_t *)BOOT_STAGE_UNCACHED;
    volatile uint32_t *dst =
        (volatile uint32_t *)boot_sdram_uncached_addr(_osdata_init_vma_start);

    boot_dcache_inval_range(_osdata_init_vma_start, total);

    uint32_t words = (total + 3u) / 4u;
    for (uint32_t i = 0; i < words; i++)
        dst[i] = src[i];

    __asm__ volatile("fence" ::: "memory");
}

__attribute__((section(".text.boot")))
static int boot_load_os(uint32_t total) {
    for (int attempt = 0; attempt < OS_LOAD_MAX_ATTEMPTS; attempt++) {
        boot_copy_os_once(total);
        if (boot_verify_os_image(total))
            return 0;
        os_load_crc_retries++;
    }
    pd_dbg_info = 0xC0DE0000u | (os_load_crc_retries & 0xFFFFu);
    return 0;   /* proceed with the last copy rather than bricking boot */
}

/* ======================================================================
 * Main
 * ====================================================================== */

__attribute__((section(".text.boot")))
int main(void) {
    pd_dbg_stage = 1;

    boot_hw_stabilize();

    /* A soft reset can leave dirty D-cache lines from the previous run. */
    flush_dcache_evict();

    boot_palette_init();

    /* Clear terminal framebuffer (scanout reads it via term_fb_active=1). */
    {
        volatile uint32_t *p = (volatile uint32_t *)TERM_FB_BASE;
        for (int i = 0; i < (320 * 240) / 4; i++) p[i] = 0;
    }

    boot_fb_puts(0, 0, "Waiting for boot.rom...");

    /* Wait for the HPS to deliver boot.rom.  The MiSTer main process
     * streams it right after loading the core, so this normally takes
     * tens of milliseconds; the long timeout covers slow SD cards. */
    pd_dbg_stage = 2;
    {
        unsigned int start_wait = SYS_CYCLE_LO;
        while (!(HPS_STATUS & HPS_STATUS_BOOT_LOADED)) {
            if ((SYS_CYCLE_LO - start_wait) > BOOT_ROM_WAIT_CYCLES) {
                boot_fb_clear_row(0);
                boot_fb_puts(0, 0, "No boot.rom from HPS");
                boot_fb_puts(0, 1, "Place boot.rom next to the core");
                while (!(HPS_STATUS & HPS_STATUS_BOOT_LOADED)) {}
                break;
            }
        }
    }

    boot_fb_clear_row(0);
    boot_fb_clear_row(1);
    boot_fb_puts(0, 0, "Loading...");

    pd_dbg_stage = 3;

    uint32_t os_size = boot_os_image_size();
    (void)boot_load_os(os_size);

    pd_dbg_stage = 4;
    boot_fb_clear_row(0);

    boot_dcache_inval_range(_os_bss_start,
                            (uint32_t)(_os_bss_end - _os_bss_start));
    boot_dcache_inval_range((void *)(uintptr_t)(RUNTIME_STACK_TOP - RUNTIME_STACK_SIZE),
                            RUNTIME_STACK_SIZE);
    flush_icache();
    os_finalize_memory((void *)_os_bss_start, (void *)_os_bss_end);
    boot_zero_uncached_sdram(RUNTIME_STACK_TOP - RUNTIME_STACK_SIZE,
                             RUNTIME_STACK_TOP);

    pd_dbg_stage = 5;

    if (os_load_crc_retries) {
        boot_fb_puts(0, 1, "OS reloaded (CRC) x");
        boot_fb_putchar(19, 1, '0' + (os_load_crc_retries & 7));
    }

    /* Jump to OS */
    switch_to_runtime_stack_and_call(os_main, _runtime_stack_top);

    while (1) {}
    return 0;
}
