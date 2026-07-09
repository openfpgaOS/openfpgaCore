//------------------------------------------------------------------------------
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileType: SOURCE
// SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
//------------------------------------------------------------------------------

/*
 * openfpgaOS Bootloader
 * Runs from BRAM. Loads os.bin via deferload to SDRAM, jumps to os_main.
 *
 * PHDP (Pocket-Host Debug Protocol) support:
 *   On boot, broadcasts EVT_BOOT_ALIVE over UART for 1 second.
 *   If a host responds, slot loads can be intercepted and streamed
 *   over UART instead of from SD card.
 *
 * IMPORTANT: This code runs BEFORE os.bin is loaded into SDRAM.
 * It must NOT call any HAL functions (they live in SDRAM).
 * All hardware access is done via direct register writes.
 */

#define BUILDING_BOOTLOADER
#include "../hal/regs.h"
#include "phdp_proto.h"
#include "boot_disk.h"
#include "of_error.h"

/* Boot-side storage for the shared PHDP codec. Both must live in
 * BRAM (.bss.boot) — the codec references them by name through the
 * PHDP_BUF / PHDP_SEQ macros, so the compiler emits direct accesses
 * with no parameter setup overhead. */
static uint8_t phdp_buf[PHDP_BUF_SIZE] __attribute__((section(".bss.boot")));
static uint8_t phdp_seq                __attribute__((section(".bss.boot")));

/* Pull in shared codec helpers as static copies tagged with the
 * .text.boot section attribute so they live in BRAM alongside the
 * rest of the bootloader. SHARED_ATTR + PHDP_BUF/PHDP_SEQ are
 * consumed by each .inc.c. */
#define SHARED_ATTR __attribute__((section(".text.boot")))
#define PHDP_BUF    phdp_buf
#define PHDP_SEQ    phdp_seq
#include "uart_poll.inc.c"
#include "phdp_codec.inc.c"
#include "dcache_evict.inc.c"
#undef PHDP_SEQ
#undef PHDP_BUF
#undef SHARED_ATTR

/* Trap context breadcrumbs (read by misaligned handler), kept in BRAM. */
volatile unsigned int __attribute__((section(".bss.boot"))) pd_dbg_stage;
volatile unsigned int __attribute__((section(".bss.boot"))) pd_dbg_info;

/* Counts how many times the loaded OS image failed its CRC and had to be
 * reloaded this boot (see boot_load_os_sd).  Non-zero means the SD->CRAM0->SDRAM
 * load pipeline corrupted the image and the reinforcement caught it. */
volatile unsigned int __attribute__((section(".bss.boot"))) os_load_crc_retries;

/* Data slot IDs */
#define OS_SLOT_ID      1       /* OS binary */
#define OS_DT_SIZE_WORD 3       /* entry 1 size word: id word 2, size word 3 */

/* OS image integrity trailer (append_os_crc.py): [magic 'OFC1'][crc32 LE].
 * 'O','F','C','1' read back as a little-endian word from SDRAM = 0x3143464F. */
#define OS_CRC_MAGIC         0x3143464Fu
#define OS_CRC_TRAILER_BYTES 8u
#define OS_LOAD_MAX_ATTEMPTS 4

/* Image-carried metadata block (append_os_crc.py), 16 bytes immediately
 * before the CRC trailer: [magic 'OSE2'][entry][bss_start][bss_end], LE,
 * covered by the CRC.
 *
 * WHY: this bootloader is baked into the bitstream while os.bin swaps
 * freely.  Zeroing .bss and jumping to os_main via OUR linked symbols
 * uses bounds/entry frozen at bitstream build time — any kernel whose
 * layout shifted gets a stale .bss clear (tail statics keep power-on
 * DRAM junk → per-layout trap/hang: the 2026-07-02 boot lottery) and/or
 * a stale entry jump.  Prefer the loaded image's own values; unstamped
 * images fall back to the baked symbols. */
#define OS_META_MAGIC  0x3245534Fu
#define OS_META_BYTES  16u

/* Boot-ABI block (append_os_crc.py), 8 bytes immediately before the OSE2
 * block: [magic 'OABI'][boot_abi], LE, covered by the CRC.  The CRC is
 * content-, not layout-checked, so a wrong-target or stale mif+os.bin pair
 * still passes it and then black-boots (loads/runs at the wrong addresses).
 * The Makefile -D's OF_TARGET_BOOT_ABI (a hash of the target + memory-map
 * constants) into this bootloader AND stamps the same value into os.bin;
 * boot.c compares them and halts with a visible error on mismatch.  Unstamped
 * images (no OABI, or no id injected at build time) skip the check. */
#ifndef OF_TARGET_BOOT_ABI
#define OF_TARGET_BOOT_ABI 0u   /* build injected no id → check disabled */
#endif
#define OS_ABI_MAGIC   0x4942414Fu   /* 'OABI' read LE from SDRAM */
#define OS_ABI_BYTES   8u

/* DMA timeout (~2 seconds at 100MHz) */
#define BOOT_DMA_TIMEOUT 200000000
#define BOOT_DMA_CHUNK_SIZE 4096u
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
#define BOOT_SNAC_DIV_DEFAULT    499u
#define BOOT_SNAC_GPIO_IDLE      (0xFFu | (0x03u << 8))
#define BOOT_ANALOGIZER_VIDEO_SHIFT      10u
#define BOOT_ANALOGIZER_POCKET_OFF_MASK  (ANLG_VIDEO_POCKET_OFF << BOOT_ANALOGIZER_VIDEO_SHIFT)

/* External symbols from linker */
extern char _os_bss_start[], _os_bss_end[];
extern char _runtime_stack_top[];
extern char _os_load_addr[];
extern char _os_copy_size[];
extern char _osdata_init_vma_start[];

/* OS entry point */
extern void os_main(void);
extern void switch_to_runtime_stack_and_call(void (*entry)(void), void *stack_top);

/* noinline: expanded at ~10 call sites; one out-of-line copy keeps the
 * loader inside the BRAM budget. */
__attribute__((section(".text.boot"), noinline))
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

/* PHDP wire constants come from shared/phdp_proto.h. Boot-specific
 * timing constants stay here so the host has nothing to coordinate. */
#define PHDP_DISCOVERY_CYCLES   (25000000)      /* 250ms — fast fallback to SD */
#define PHDP_ALIVE_INTERVAL     (5000000)       /* 50ms between broadcasts */
#define PHDP_OVERRIDE_CYCLES    (20000000)      /* 200ms */
#define PHDP_CHUNK_CYCLES       (100000000)     /* 1 second */
#define PHDP_MAX_RETRIES        5

/* ---- All functions below are self-contained, no HAL calls ---- */

/* Font in BRAM (.fastrodata) — defined in terminal.c */
extern const uint8_t font8x8[2048];

/* Initialize palette[15] = white so the boot stub's "white-on-black"
 * pixel writes (palette index 15) are actually visible. The OS's
 * of_term_init() later overwrites this as part of the full VGA
 * palette setup; until then palette[15] is whatever the RTL reset
 * gives (typically zero = black), and loader text comes out as
 * black-on-black = invisible. palette[0] is left as the RTL default
 * (which is zero / black on this target) to save BRAM space. */
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


/* os_finalize_memory() lives in BRAM .fasttext.  It only zeroes .bss;
 * the .rodata and .data init image has already been copied to SDRAM. */
extern void os_finalize_memory(void *bss_start, void *bss_end);

__attribute__((section(".text.boot")))
static void flush_icache(void) {
    __asm__ volatile("fence");
    __asm__ volatile(".word 0x0000100f");  /* fence.i */
}

/* Shared tiny helpers (noinline): the CRAM0 mode flip + settle and the
 * 100 ms retry delay are repeated all over the loader; out-of-lining
 * them buys back the BRAM bytes the F1/F2ii forensics paths cost. */
__attribute__((section(".text.boot"), noinline))
static void boot_settle(void) {
    for (volatile int s = 0; s < 8; s++) {}  /* settle ~4 clk_74a */
}

__attribute__((section(".text.boot"), noinline))
static void boot_cram0_mode(uint32_t mode) {
    CRAM0_MODE = mode;
    boot_settle();
}

__attribute__((section(".text.boot"), noinline))
static void boot_delay_100ms(void) {
    uint32_t t0 = SYS_CYCLE_LO;          /* ~100 ms @ 100 MHz */
    while ((SYS_CYCLE_LO - t0) < 10000000u) {}
}

/* Forward decls — boot_warmup_note shares the on-screen retry pattern
 * between the CRAM warmup and the F1 bridge-leg warmup loops. */
static void boot_fb_puts(int col, int row, const char *s);
static void boot_fb_hex32(int col, int row, uint32_t v);
static int  boot_ds_wait(uint32_t mask);

/* Column-0 text is the overwhelmingly common case — shrink every call
 * site by a register setup. */
__attribute__((section(".text.boot"), noinline))
static void boot_fb_puts0(int row, const char *s) {
    boot_fb_puts(0, row, s);
}

__attribute__((section(".text.boot"), noinline))
static void boot_warmup_note(const char *label, int hexcol, unsigned n) {
    boot_fb_puts0(1, label);
    boot_fb_hex32(hexcol, 1, n);
    boot_delay_100ms();
}

/* Little-endian 32-bit store into a byte buffer (PHDP payload packing). */
__attribute__((section(".text.boot"), noinline))
static void boot_store_le32(uint8_t *p, uint32_t v) {
    for (int i = 0; i < 4; i++) {
        p[i] = (uint8_t)v;
        v >>= 8;
    }
}

__attribute__((section(".text.boot")))
static void boot_hw_stabilize(void) {
    __asm__ volatile("fence" ::: "memory");

    /* Stale interrupt enables/pending bits are the most dangerous warm-boot
     * leftovers.  Machine interrupts are still globally disabled by start.S;
     * this resets the peripheral side before the OS installs callbacks. */
    IRQ_MASK = 0;
    TIMER_CTRL = TIMER_CTRL_W1C_IRQ;
    VSYNC_IRQ_PENDING = 1;
    DS_STATUS = DS_STATUS_IRQ_PENDING;
    INPUT_IRQ_MASK = 0;
    INPUT_IRQ_CLEAR = INPUT_IRQ_CLEAR_FIFO | INPUT_IRQ_CLEAR_OVERFLOW;

    /* Return display scanout to the boot console's conservative mode.  Clear
     * only the persistent Analogizer "Pocket OFF" latch so a bad interact
     * state cannot make the LCD unrecoverable; keep video type, enable, SNAC,
     * and offsets intact for normal runtime/config handling. */
    TERM_FB_CTRL = 1u;
    SYS_COLOR_MODE = COLOR_MODE_8BIT;
    FB_MODE_SIZE = (FB_HEIGHT << 16) | FB_WIDTH;
    FB_MODE_STRIDE = FB_STRIDE;
    VIDEO_SCALER_MODE = VIDEO_SCALER_SLOT_DEFAULT_320X240;
    VIDEO_VTOTAL = VIDEO_VTOTAL_60HZ;
    uint32_t analogizer_boot_settings = ANALOGIZER_SETTINGS;
    if (analogizer_boot_settings & BOOT_ANALOGIZER_POCKET_OFF_MASK) {
        ANALOGIZER_SETTINGS =
            analogizer_boot_settings & ~BOOT_ANALOGIZER_POCKET_OFF_MASK;
        for (volatile int i = 0; i < 16; i++) {}
    }

    /* CRAM0 is bridge-owned by default.  The loader only flips to CPU mode
     * for the short scratch-copy window inside boot_load_os_sd(). */
    boot_cram0_mode(CRAM0_MODE_BRIDGE);
    /* Shared waiter (BOOT_DMA_TIMEOUT bound, ~2 s): a wedged bridge now
     * stalls stabilize longer than the old 10 ms bail, but boot cannot
     * proceed usefully on a wedged bridge anyway (the F1 bridge-leg
     * warmup below would spin on it forever regardless). */
    (void)boot_ds_wait(DS_STATUS_READY | DS_STATUS_WR_IDLE);

    /* DS_SLOT_ID..DS_RESP_ADDR are six consecutive words at 0x20..0x34. */
    for (uint32_t r = 0; r < 6u; r++)
        REG32(SYSREG_BASE + 0x20u + (r << 2)) = 0;

    /* Stop subsystems that can continue producing bus traffic or IRQs after
     * an app/OS warm restart.  Apps and the OS reprogram these explicitly. */
    MIX_CTRL = 0;
    for (uint32_t v = 0; v < 32u; v++)
        MIX_VOICE_CTRL(v) = 0;
    MIX_MASTER_VOL = 0xFFu;
    for (uint32_t g = 0; g < 4u; g++)
        MIX_GROUP_VOL(g) = 0xFFu;
    MIX_VOICE_GROUP_LO = 0;
    MIX_VOICE_GROUP_HI = 0;
    MIX_IRQ_CLEAR = 0xFFFFFFFFu;

    SNAC_HW_CTRL = 0;
    SNAC_HW_CLEAR = SNAC_HW_CLEAR_IRQ | SNAC_HW_CLEAR_EDGES;
    SNAC_CTRL = 0;
    SNAC_DIV = BOOT_SNAC_DIV_DEFAULT;
    SNAC_DATA = 0;
    SNAC_GPIO = BOOT_SNAC_GPIO_IDLE;

    LINK_CTRL = BOOT_LINK_CTRL_RESET;

    BOOT_GPU_CTRL = BOOT_GPU_CTRL_SOFT_RESET;
    boot_settle();
    uint32_t gpu_wait = BOOT_HW_IDLE_TIMEOUT;
    while (BOOT_GPU_STATUS & BOOT_GPU_STATUS_DMA_BUSY) {
        if (--gpu_wait == 0)
            break;
    }
    BOOT_GPU_DMA_SRC = 0;
    BOOT_GPU_DMA_LEN = 0;
    BOOT_GPU_TEX_FLUSH = 1;
    BOOT_GPU_CTRL = BOOT_GPU_CTRL_RING_RESET;
    boot_settle();

    __asm__ volatile("fence" ::: "memory");
}

/* uart_putc/uart_getc, phdp_crc16/phdp_send/phdp_recv, and
 * flush_dcache_evict are pulled in from shared include-C files at the top of
 * this file. The boot-only state they need (sequence counter, packet
 * buffer, UART probe latch) lives here so it can be tagged .bss.boot
 * and end up in BRAM. */

static int uart_available __attribute__((section(".bss.boot")));  /* 0 = untested */

__attribute__((section(".text.boot"), unused))
static int uart_probe(void) {
    if (uart_available) return uart_available;  /* already probed (1=yes, 2=no) */
    uint32_t status = UART_STATUS;
    uart_available = (status & 1) ? 1 : 2;
    return (uart_available == 1);
}

/* phdp_buf / phdp_seq live at the top of this file (above the
 * shared include-C files) so the codec sees them via PHDP_BUF /
 * PHDP_SEQ. */

/* ======================================================================
 * PHDP discovery - Phase I
 * Returns 1 if host connected, 0 if timeout (boot from SD).
 * ====================================================================== */

__attribute__((section(".text.boot"), unused))
static int phdp_discover(void) {
    /* EVT_BOOT_ALIVE payload: Core ID (4B) + Version (2B) + Max Chunk (2B) */
    uint8_t alive_payload[8];
    boot_store_le32(alive_payload, PHDP_CORE_ID);
    alive_payload[4] = (PHDP_VERSION >>  0) & 0xFF;
    alive_payload[5] = (PHDP_VERSION >>  8) & 0xFF;
    alive_payload[6] = (PHDP_MAX_CHUNK >> 0) & 0xFF;
    alive_payload[7] = (PHDP_MAX_CHUNK >> 8) & 0xFF;

    uint32_t disc_start = SYS_CYCLE_LO;
    uint32_t last_alive = 0;

    while ((SYS_CYCLE_LO - disc_start) < PHDP_DISCOVERY_CYCLES) {
        /* Broadcast EVT_BOOT_ALIVE every 100ms */
        uint32_t now = SYS_CYCLE_LO;
        if ((now - last_alive) >= PHDP_ALIVE_INTERVAL) {
            phdp_send(PHDP_EVT_BOOT_ALIVE, alive_payload, 8);
            last_alive = now;
        }

        /* Try to receive CMD_CLIENT_READY with short timeout
         * (10ms — enough for one packet at 2 Mbaud) */
        uint32_t dummy;
        int cmd = phdp_recv(1000000, &dummy);
        if (cmd == PHDP_CMD_CLIENT_READY)
            return 1;
    }

    return 0;
}

/* ======================================================================
 * PHDP slot override — Phase II
 * Returns 1 if host will stream (total_size and chunk_size filled).
 * Returns 0 if host says use SD or timeout.
 * ====================================================================== */

__attribute__((section(".text.boot")))
static int phdp_request_override(uint8_t slot_id,
                                  uint32_t *total_size,
                                  uint16_t *chunk_size) {
    /* Send REQ_OVERRIDE */
    phdp_send(PHDP_REQ_OVERRIDE, &slot_id, 1);

    /* Loop expecting RES_STREAM, dropping any other packet that may
     * already be in flight (e.g. a stale DATA_CHUNK left in the FIFO
     * from a prior size query). Bounded so a flapping link still
     * times out instead of spinning forever. */
    for (int attempts = 0; attempts < 16; attempts++) {
        uint32_t len = 0;
        int cmd = phdp_recv(PHDP_OVERRIDE_CYCLES, &len);

        if (cmd < 0) return 0;                /* timeout */
        if (cmd == PHDP_RES_USE_SD) return 0; /* host has nothing queued */

        if (cmd == PHDP_RES_STREAM && len >= 6) {
            uint8_t *p = phdp_buf + PHDP_HEADER_SIZE;
            *total_size = (uint32_t)p[0]
                        | ((uint32_t)p[1] << 8)
                        | ((uint32_t)p[2] << 16)
                        | ((uint32_t)p[3] << 24);
            *chunk_size = (uint16_t)p[4]
                        | ((uint16_t)p[5] << 8);

            if (*total_size > 16 * 1024 * 1024) return 0;
            if (*chunk_size > PHDP_MAX_CHUNK) *chunk_size = PHDP_MAX_CHUNK;
            return 1;
        }
        /* Other cmd (likely stale DATA_CHUNK) → ignore and re-recv */
    }
    return 0;
}

/* ======================================================================
 * PHDP chunk loop — shared between boot streaming and OS-callable read
 * ======================================================================
 *
 * Caller must have already issued REQ_OVERRIDE and parsed the
 * RES_STREAM total_size. This routine drives the DATA_CHUNK loop and
 * window-copies the intersection of each chunk with the requested
 * range [slot_offset, slot_offset+length) into dest. The host
 * streams the whole slot from offset 0; chunks outside the window
 * are ACKed but not copied. The loop drains to received==total_size
 * (even when the window is already satisfied) so the host cleanly
 * finishes the stream and returns to READY. Returns 0 on success,
 * OF_ERR_TIMEOUT on a NAK loop, OF_ERR_IO if the stream ends short. */
__attribute__((section(".text.boot")))
static int phdp_chunk_loop(uint32_t slot_offset, uint32_t length,
                           uint32_t total_size, void *dest) {
    uint8_t *d = (uint8_t *)dest;
    uint32_t received = 0;
    uint32_t copied = 0;
    int retries = 0;

    while (received < total_size) {
        uint32_t clen = 0;
        int c = phdp_recv(PHDP_CHUNK_CYCLES, &clen);

        if (c == PHDP_DATA_CHUNK && clen > 0) {
            uint8_t *src = phdp_buf + PHDP_HEADER_SIZE;

            uint32_t chunk_end = received + clen;
            uint32_t want_end  = slot_offset + length;
            if (slot_offset + copied < chunk_end && want_end > received) {
                uint32_t s = (slot_offset + copied > received) ? (slot_offset + copied) : received;
                uint32_t e = (want_end < chunk_end) ? want_end : chunk_end;
                uint32_t off_in_chunk = s - received;
                uint32_t off_in_dest  = s - slot_offset;
                for (uint32_t i = 0; i < e - s; i++)
                    d[off_in_dest + i] = src[off_in_chunk + i];
                copied += (e - s);
            }

            received += clen;
            retries = 0;

            uint8_t prog[4];
            boot_store_le32(prog, received);
            phdp_send(PHDP_REPORT_PROGRESS, prog, 4);
        } else if (c < 0) {
            retries++;
            if (retries >= PHDP_MAX_RETRIES) return OF_ERR_TIMEOUT;
            uint8_t nak_seq = phdp_seq - 1;
            phdp_send(PHDP_CMD_NAK_RETRY, &nak_seq, 1);
        }
        /* Ignore other commands during streaming */
    }

    return (copied == length) ? 0 : OF_ERR_IO;
}

/* ======================================================================
 * Boot-ROM disk service — exported for the OS
 * ======================================================================
 *
 * BRAM is preserved after the jump to os_main, so these symbols
 * stay reachable. The kernel calls boot_disk_read / boot_disk_size
 * via the of_disk_boot backend wrapper (targets/pocket/disk_boot.c)
 * to stream data slot reads without ever linking the wire-protocol
 * code into os.bin. boot_disk_available is set when the boot ROM
 * has successfully streamed os.bin via the UART channel; the OS
 * dispatcher checks it to decide whether the channel is usable.
 *
 * This is the only piece of BRAM code intended to outlive boot. */

__attribute__((section(".bss.boot")))
volatile int boot_disk_available;

/* Save/restore the M-mode external interrupt enable bit so the
 * kernel's irq.c can't drain UART_RX_DATA out from under us. */
__attribute__((section(".text.boot")))
static uint32_t boot_mie_disable(void) {
    uint32_t prev;
    __asm__ volatile("csrrci %0, mstatus, 0x8" : "=r"(prev));
    return prev & 0x8;
}
__attribute__((section(".text.boot")))
static void boot_mie_restore(uint32_t prev) {
    if (prev) __asm__ volatile("csrrsi zero, mstatus, 0x8");
}

/* Read [slot_offset, slot_offset+length) from data slot `slot_id`
 * into `dest`. Issues REQ_OVERRIDE, then runs the shared chunk loop.
 * Owns MIE masking so the OS irq handler can't drain UART_RX_DATA
 * from underneath the polled receiver. */
__attribute__((section(".text.boot")))
int boot_disk_read(uint32_t slot_id, uint32_t slot_offset,
                   void *dest, uint32_t length) {
    uint32_t mie = boot_mie_disable();

    uint32_t total = 0;
    uint16_t chunk = 0;
    if (!phdp_request_override((uint8_t)slot_id, &total, &chunk)) {
        boot_mie_restore(mie);
        return OF_ERR_IO;
    }
    if (slot_offset + length > total) {
        boot_mie_restore(mie);
        return OF_ERR_INVALID_PARAM;
    }

    int rc = phdp_chunk_loop(slot_offset, length, total, dest);
    boot_mie_restore(mie);
    return rc;
}

/* Query slot size without consuming the stream. Returns the size
 * from RES_STREAM and leaves the host mid-stream; the next
 * boot_disk_read for the same slot restarts via a fresh
 * REQ_OVERRIDE, and phdp_request_override skips any stale chunks
 * left in the FIFO. */
__attribute__((section(".text.boot")))
long boot_disk_size(uint32_t slot_id) {
    uint32_t mie = boot_mie_disable();

    uint32_t total = 0;
    uint16_t chunk = 0;
    int ok = phdp_request_override((uint8_t)slot_id, &total, &chunk);

    boot_mie_restore(mie);
    return ok ? (long)total : -1;
}

/* ======================================================================
 * Standard SD card boot (original path)
 * ====================================================================== */

/* Shared DS_STATUS wait: spin until (DS_STATUS & mask) == mask, capture
 * the failing status in pd_dbg_info on timeout.  Every wait point in
 * boot_dma_read wants exactly this shape; sharing it keeps the loader
 * inside the BRAM budget. */
__attribute__((section(".text.boot"), noinline))
static int boot_ds_wait(uint32_t mask) {
    uint32_t timeout = BOOT_DMA_TIMEOUT;
    while ((DS_STATUS & mask) != mask) {
        if (--timeout == 0) { pd_dbg_info = DS_STATUS; return -1; }
    }
    return 0;
}

/* Inline DMA read -- does not call any SDRAM functions */
__attribute__((section(".text.boot")))
static int boot_dma_read(uint32_t slot_id, uint32_t slot_offset,
                         uint32_t bridge_addr, uint32_t length) {
    __asm__ volatile("fence" ::: "memory");

    /* Wait for bridge to be idle before issuing a new command. READY
     * means the APF command FSM can accept work; WR_IDLE means any
     * prior bridge writes to CRAM0 have fully drained. */
    if (boot_ds_wait(DS_STATUS_READY | DS_STATUS_WR_IDLE))
        return -3;

    DS_SLOT_ID     = slot_id;
    DS_SLOT_OFFSET = slot_offset;
    DS_BRIDGE_ADDR = bridge_addr;
    DS_LENGTH      = length;

    /* Warm instance switches can leave the peripheral-side dispatch
     * guard briefly unable to consume DS_COMMAND even though the live
     * bridge status already reads READY|WR_IDLE.  If the write is
     * dropped, status remains idle forever (the on-screen E1 60 case).
     * Probe for either ACK or READY deassertion and retry the command
     * before entering the long ACK wait. */
    for (int attempt = 0; attempt < 4; attempt++) {
        DS_COMMAND = DS_CMD_READ;
        __asm__ volatile("fence" ::: "memory");

        for (int i = 0; i < 256; i++) {
            uint32_t st = DS_STATUS;
            if (st & DS_STATUS_ACK)
                goto accepted;
            if (!(st & DS_STATUS_READY))
                goto accepted;
        }
    }

accepted:

    /* Wait for ACK */
    if (boot_ds_wait(DS_STATUS_ACK))
        return -1;

    /* Wait for DONE */
    if (boot_ds_wait(DS_STATUS_DONE))
        return -2;

    /* Check bridge/APF error and capture the same status byte that the
     * failure screen prints. */
    uint32_t st = DS_STATUS;
    uint32_t err = (st & DS_STATUS_ERR_MASK) >> DS_STATUS_ERR_SHIFT;
    if (err) {
        pd_dbg_info = st;
        return -(int)err;
    }

    /* DONE reports APF command completion. Do not switch CRAM0 to CPU
     * ownership until the live drain bit confirms the last bridge write
     * has landed in memory. */
    if (boot_ds_wait(DS_STATUS_WR_IDLE))
        return -4;

    return 0;
}

__attribute__((section(".text.boot")))
static uint32_t boot_dt_read_word(uint32_t word) {
    DT_QUERY = word;
    for (int i = 0; i < 100000; i++) {
        uint32_t v = DT_QUERY;
        if (v & 0x80000000u)
            return v & 0x7FFFFFFFu;
    }
    return 0;
}

__attribute__((section(".text.boot")))
static uint32_t boot_os_slot_size(void) {
    /* Prefer the LIVE DataTable size so the os.bin size is DECOUPLED from the
     * baked boot ROM — a resized os.bin must not require a bitstream rebake.
     * The OS reads this same table reliably (of_file_size), so the value is
     * present; at boot the APF may not have populated it the instant the bridge
     * reports ALLCOMPLETE, so retry briefly until it reports a sane size.  The
     * baked _os_copy_size stays ONLY as a last-resort safety net (a table that
     * never reports still boots), so this can never regress vs the old code. */
    for (int attempt = 0; attempt < 64; attempt++) {
        uint32_t dt_size = boot_dt_read_word(OS_DT_SIZE_WORD);
        if (dt_size >= 4096u && dt_size <= 768u * 1024u)
            return dt_size;
        for (volatile int d = 0; d < 200000; d++) {}   /* ~ms-scale settle, then re-query */
    }
    return (uint32_t)(uintptr_t)_os_copy_size;
}

/* CRC-32/ISO-HDLC (zlib-compatible) over a loaded SDRAM range, read through the
 * uncached alias so it reflects what landed in DRAM, independent of cache state.
 * Word-based to minimise slow uncached reads; bytes are folded in LSB-first to
 * match the on-disk little-endian byte order that append_os_crc.py CRCs. */
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

/* Returns 1 if the just-loaded OS image is trusted, 0 otherwise.  The build
 * pipeline stamps every os.bin with an 8-byte CRC trailer (magic 'OFC1' +
 * crc32), so a missing/garbled magic is treated as CORRUPTION, not as an
 * unstamped dev image: on a cold boot a lost SDRAM write can hit the trailer
 * word itself, and the old accept-if-unstamped escape booted exactly such a
 * corrupted image (2026-06-09 cold-boot forensics).  'total' is the number of
 * bytes copied to SDRAM, including the 8-byte trailer. */
__attribute__((section(".text.boot")))
static int boot_verify_os_image_at(uint32_t total, uint32_t base) {
    if (total < OS_CRC_TRAILER_BYTES)
        return 0;   /* too small to carry the mandatory trailer */
    uint32_t image_len = total - OS_CRC_TRAILER_BYTES;
    volatile const uint32_t *trailer =
        (volatile const uint32_t *)boot_sdram_uncached_addr(
            (void *)(uintptr_t)(base + image_len));
    if (trailer[0] != OS_CRC_MAGIC)
        return 0;   /* trailer lost/garbled — reload, do not trust */
    return boot_crc32_uncached(base, image_len) == trailer[1];
}

/* Image-carried entry + .bss bounds (OSE2 block — see the #define block
 * at the top for why this exists).  Fills the os_meta_* globals and
 * returns 1 when a plausible block is present; on any doubt returns 0
 * and the caller keeps the legacy baked symbols. */
volatile uint32_t __attribute__((section(".bss.boot"))) os_meta_entry;
volatile uint32_t __attribute__((section(".bss.boot"))) os_meta_bss_lo;
volatile uint32_t __attribute__((section(".bss.boot"))) os_meta_bss_hi;

__attribute__((section(".text.boot")))
static int boot_read_os_meta(uint32_t total) {
    os_meta_entry = 0;
    if (total < OS_CRC_TRAILER_BYTES + OS_META_BYTES)
        return 0;
    uint32_t base = (uint32_t)(uintptr_t)_osdata_init_vma_start;
    volatile const uint32_t *m =
        (volatile const uint32_t *)boot_sdram_uncached_addr(
            (void *)(uintptr_t)(base + total - OS_CRC_TRAILER_BYTES - OS_META_BYTES));
    if (m[0] != OS_META_MAGIC)
        return 0;
    uint32_t entry = m[1], lo = m[2], hi = m[3];
    /* Plausibility: entry inside the loaded image; .bss at/above the
     * image start, ordered, within the SDRAM PMA window. */
    if (entry < base || entry >= base + total)
        return 0;
    if (lo < base || hi < lo || hi > (OF_TARGET_SDRAM_BASE + OF_TARGET_SDRAM_SIZE))
        return 0;
    os_meta_entry  = entry;
    os_meta_bss_lo = lo;
    os_meta_bss_hi = hi;
    return 1;
}

/* Boot-ABI guard.  Returns 1 to proceed (id matches, or the image is
 * unstamped / the id is build-disabled), 0 to stop (id present and differs —
 * a wrong-target or stale mif+os.bin pair).  Reads the OABI block from the
 * loaded blob's tail ([OABI][OSE2][CRC]); the blob is raw-copied to
 * _osdata_init_vma_start, so the tail is readable even when the map is wrong. */
__attribute__((section(".text.boot")))
static int boot_check_os_abi(uint32_t total) {
    if ((uint32_t)OF_TARGET_BOOT_ABI == 0u)
        return 1;   /* no id injected at build time — check disabled */
    if (total < OS_CRC_TRAILER_BYTES + OS_META_BYTES + OS_ABI_BYTES)
        return 1;   /* too small to carry an OABI block — legacy image */
    uint32_t base = (uint32_t)(uintptr_t)_osdata_init_vma_start;
    volatile const uint32_t *a =
        (volatile const uint32_t *)boot_sdram_uncached_addr(
            (void *)(uintptr_t)(base + total - OS_CRC_TRAILER_BYTES
                                - OS_META_BYTES - OS_ABI_BYTES));
    if (a[0] != OS_ABI_MAGIC)
        return 1;   /* unstamped (no OABI) — don't enforce */
    return a[1] == (uint32_t)OF_TARGET_BOOT_ABI;
}


/* OS image load: bridge DMAs os.bin into CRAM0 scratch, then the CPU copies the
 * whole blob contiguously into SDRAM starting at __osdata_vma (== __os_entry).
 * One .osdata section, so no text/data split.  (The direct SD->SDRAM bridge path
 * was removed: bridge_to_sdram corrupted the loaded image on silicon — a sparse
 * single-bit flip — so the CRAM0 bounce + CPU copy is the only SDRAM-dest load
 * path now.  The RTL module is gone; there is no direct alternative to select.) */
__attribute__((section(".text.boot")))
static int boot_load_os_sd_once_to(uint32_t total, uint32_t dst_cached) {
    uint32_t bounce_bridge = CRAM0_SCRATCH_BRIDGE;
    volatile uint8_t *bounce_src = (volatile uint8_t *)CRAM0_SCRATCH;
    volatile uint8_t *sdram_dst =
        (volatile uint8_t *)boot_sdram_uncached_addr(
            (void *)(uintptr_t)dst_cached);
    uint32_t done = 0;

    boot_dcache_inval_range((void *)(uintptr_t)dst_cached, total);

    while (done < total) {
        uint32_t chunk = total - done;
        if (chunk > BOOT_DMA_CHUNK_SIZE)
            chunk = BOOT_DMA_CHUNK_SIZE;

        /* Bridge DMA: SD card → CRAM0 scratch */
        boot_cram0_mode(CRAM0_MODE_BRIDGE);
        int rc = boot_dma_read(OS_SLOT_ID, done, bounce_bridge, chunk);
        if (rc < 0)
            return rc;

        /* CPU reads CRAM0 scratch, writes uncached SDRAM.  The OS executes
         * from SDRAM immediately after this loader, so avoid depending on a
         * best-effort D-cache eviction sweep to publish instruction bytes. */
        boot_cram0_mode(CRAM0_MODE_CPU);
        volatile uint32_t *src32 = (volatile uint32_t *)bounce_src;
        volatile uint32_t *dst32 = (volatile uint32_t *)(sdram_dst + done);
        for (uint32_t i = 0; i < chunk / 4; i++)
            dst32[i] = src32[i];
        for (uint32_t i = chunk & ~3u; i < chunk; i++)
            sdram_dst[done + i] = bounce_src[i];

        done += chunk;
    }

    __asm__ volatile("fence" ::: "memory");

    /* Leave the mux in bridge mode — later code issuing bridge DMAs
     * is the common case; one-off CPU reads flip it as needed. */
    boot_cram0_mode(CRAM0_MODE_BRIDGE);
    return 0;
}

/* Load the OS image, then verify its CRC and reload on mismatch.  The
 * SD->CRAM0->SDRAM copy occasionally corrupts a word (CRAM0 PSRAM async path,
 * cold-boot lost SDRAM writes); a corrupt image surfaces later as an
 * illegal-instruction trap or silent data corruption.  Re-running the whole
 * load almost always clears an intermittent fault, so retry FOREVER rather
 * than ever booting a corrupt image: a visible retry loop the user can see
 * (and power-cycle out of) is strictly better than the old proceed-anyway
 * escape, which produced undebuggable in-game corruption on cold boots.
 * os_load_crc_retries / pd_dbg_info keep the count visible to the OS. */
__attribute__((section(".text.boot")))
static void boot_fb_hex32(int col, int row, uint32_t v) {
    for (int i = 7; i >= 0; i--) {
        unsigned d = (v >> (i * 4)) & 0xF;
        boot_fb_putchar(col++, row, (d < 10) ? ('0' + d) : ('a' + d - 10));
    }
}

__attribute__((section(".text.boot")))
static int boot_load_os_sd(uint32_t total) {
    for (;;) {
        for (int attempt = 0; attempt < OS_LOAD_MAX_ATTEMPTS; attempt++) {
            int rc = boot_load_os_sd_once_to(
                total, (uint32_t)(uintptr_t)_osdata_init_vma_start);
            if (rc < 0)
                return rc;              /* DMA/IO failure — surface as-is */
            if (boot_verify_os_image_at(total,
                    (uint32_t)(uintptr_t)_osdata_init_vma_start))
                return 0;               /* image trusted */
            os_load_crc_retries++;      /* corruption detected — reload */
        }
        pd_dbg_info = 0xC0DE0000u | (os_load_crc_retries & 0xFFFFu);
        boot_fb_puts0(1, "OS CRC retry x");
        boot_fb_putchar(14, 1, '0' + (os_load_crc_retries & 7));
        if (os_load_crc_retries >= 2 * OS_LOAD_MAX_ATTEMPTS) {
            /* Bounded fail-open: booting a possibly-corrupt image with a
             * visible warning beats a boot jail — warm boots historically
             * load intact images, and the forensics rows carry the
             * evidence either way. */
            boot_fb_puts0(1, "BOOTING UNVERIFIED IMAGE ");
            return 0;
        }
    }
}

/* CRAM0 wait-until-healthy self-test (cold-boot forensics 2026-06-09).
 * Bench data: cold starts fail the OS load with DETERMINISTIC,
 * load-repeatable corruption (CRC forensic D:0 B:0) that heals after
 * ~30-40 s of sitting in the menu — a boot-time-latched PSRAM/rail
 * settling marginality, not a per-read race.  Every load path bounces
 * through CRAM0 scratch, so: pattern-test the scratch and simply WAIT
 * (re-testing every ~100 ms, counter on screen) until the chip answers
 * correctly.  Converts the manual wait into an automatic minimal one. */
__attribute__((section(".text.boot")))
static void boot_cram_warmup(void) {
    volatile uint32_t *scr = (volatile uint32_t *)CRAM0_SCRATCH;
    unsigned warm_tries = 0;
    for (;;) {
        boot_cram0_mode(CRAM0_MODE_CPU);
        /* FULL 4 KB scratch sweep: the original 64-word test passed on
         * boots where high scratch offsets read 0xFFFFFFFF (bench
         * 2026-06-09: ~0xB5C floats cold, low offsets fine) — an
         * address-dependent fault the short test was blind to. */
        for (uint32_t i = 0; i < 1024; i++)
            scr[i] = (i * 0x01010101u) ^ 0xA5C35A3Cu;
        __asm__ volatile("fence" ::: "memory");
        int ok = 1;
        for (uint32_t i = 0; i < 1024; i++)
            if (scr[i] != ((i * 0x01010101u) ^ 0xA5C35A3Cu))
                { ok = 0; break; }
        boot_cram0_mode(CRAM0_MODE_BRIDGE);
        if (ok)
            break;
        warm_tries++;
        boot_warmup_note("CRAM warmup x", 13, warm_tries);
    }
    if (warm_tries)
        boot_fb_clear_row(1);
}

/* ======================================================================
 * Main
 * ====================================================================== */

__attribute__((section(".text.boot")))
int main(void) {
    pd_dbg_stage = 1;

#if OF_TARGET_PLATFORM_ID == OF_PLATFORM_SIM
    /* Sim target: the Verilator harness preloads os.bin into CRAM0 via
     * a backdoor port before releasing reset, so there's no PHDP peer,
     * no SD card, and nothing to wait for.  Skip directly to start_os
     * (caches + BSS clear + jump) — no framebuffer, no UART probe, no
     * PHDP discovery.  Keeps the fast-iteration loop tight.
     *
     * Enable UART mirroring so the OS's of_terminal writes are also
     * sent to the UART — tb_system_main.cpp prints UART bytes to
     * stdout, giving us visibility into OS boot / testdemo progress.
     *
     * NOTE: flush_dcache_evict in start_os currently hangs under
     * sdram_fast_model around iteration ~800 of its 1024-line sweep,
     * for reasons not yet debugged.  Running the built-in selftest
     * (./Vtb_system [cycles]) bypasses this entirely and is currently
     * the best way to exercise cpu_system changes against this sim. */
    extern volatile int uart_mirror_on;
    uart_mirror_on = 1;
    /* Sim harness mailbox: the Verilator harness pokes the true os.bin
     * size (incl. trailer) at a fixed top-of-SDRAM address ('OSSZ' magic
     * + size) so the OSE2 meta block can be located — the sim path has
     * no slot/HPS size source.  Absent mailbox = legacy baked symbols. */
    {
        volatile const uint32_t *mb = (volatile const uint32_t *)
            boot_sdram_uncached_addr((void *)(uintptr_t)0x13FFFFF0u);
        if (mb[0] == 0x5A53534Fu)
            (void)boot_read_os_meta(mb[1]);
    }
    goto start_os;
#endif

    /* Wait for APF bridge */
    unsigned int start_wait = SYS_CYCLE_LO;
    while (!(SYS_STATUS & SYS_STATUS_ALLCOMPLETE)) {
        if ((SYS_CYCLE_LO - start_wait) > 500000000) {
            /* APF never raised ALLCOMPLETE (e.g. slow cold SD mount).
             * Do NOT charge into boot_hw_stabilize's DS-register
             * zeroing while the bridge may still be streaming
             * nonvolatile slots — wait (bounded) for the bridge to go
             * quiet first, then fall back to the old behaviour.
             *
             * F6 (bridge-corruption mitigation 2026-06): a single LIVE
             * sample of READY|WR_IDLE races mid-burst gaps — between
             * two nonvolatile-slot bursts the bridge transiently reads
             * idle, and proceeding then lets boot_hw_stabilize zero the
             * DS registers under an active stream.  Require a SUSTAINED
             * ~50 ms window of continuous READY|WR_IDLE before
             * proceeding: any busy sample restarts the window.  Still
             * bounded by the 3 s wedge escape. */
            unsigned int quiet_wait = SYS_CYCLE_LO;
            unsigned int quiet_t0 = SYS_CYCLE_LO;
            for (;;) {
                if ((DS_STATUS & (DS_STATUS_READY | DS_STATUS_WR_IDLE))
                        != (DS_STATUS_READY | DS_STATUS_WR_IDLE))
                    quiet_t0 = SYS_CYCLE_LO;   /* busy — restart window */
                else if ((SYS_CYCLE_LO - quiet_t0) >= 5000000u)
                    break;          /* ~50 ms continuously quiet */
                if ((SYS_CYCLE_LO - quiet_wait) > 300000000)
                    break;          /* bridge truly wedged */
            }
            break;
        }
    }

    pd_dbg_stage = 2;

    boot_hw_stabilize();

    /* CRAM0 wait-until-healthy self-test — see boot_cram_warmup(). */
    boot_cram_warmup();

    /* A soft reset can leave dirty D-cache lines from the previous run.
     * Drain them before any boot-critical SDRAM writes; later targeted
     * invalidates drop stale lines for the exact OS/BSS/stack ranges. */
    flush_dcache_evict();

    /* Brief delay for deferload to settle */
    for (volatile int i = 0; i < 100; i++) {}  // Shortened for fast sim

    /* Initialize the palette entries the boot stub uses (0 = black,
     * 15 = white). Without this the loader text is invisible because
     * the RTL palette is uninitialized at reset. */
    boot_palette_init();

    /* Clear terminal framebuffer (scanout reads it by default via term_fb_active=1) */
    {
        volatile uint32_t *p = (volatile uint32_t *)TERM_FB_BASE;
        for (int i = 0; i < (320 * 240) / 4; i++) p[i] = 0;
    }

    boot_fb_puts0(0, "Booting...");

    /* ── PHDP Discovery ─────────────────────────────────────────── */
    int debug_mode = 0;

    if (uart_probe()) {
        debug_mode = phdp_discover();
    }

    if (debug_mode) {
        boot_fb_clear_row(0);
        boot_fb_puts0(0, "Service host connected");

        uint32_t total_size = 0;
        uint16_t chunk_size = 0;

        if (phdp_request_override(OS_SLOT_ID, &total_size, &chunk_size)) {
            /* Stream OS binary over UART */
            boot_fb_clear_row(0);
            boot_fb_puts0(0, "Loading via UART...");

            /* v2 arch: OS is ONE contiguous section in SDRAM
             * (__os_load_addr == __osdata_init_vma_start, same
             * address).  No text/data split — stream the whole blob
             * directly into SDRAM with a single phdp_chunk_loop call.
             * Use the uncached alias for the same reason as the SD path:
             * the next instruction fetch must see the bytes immediately. */
            (void)chunk_size;
            uint8_t *os_dst = (uint8_t *)boot_sdram_uncached_addr(_os_load_addr);
            boot_dcache_inval_range(_os_load_addr, total_size);
            int rc = phdp_chunk_loop(0, total_size, total_size, os_dst);

            if (rc < 0 || !boot_verify_os_image_at(total_size,
                    (uint32_t)(uintptr_t)_osdata_init_vma_start)) {
                boot_fb_clear_row(0);
                boot_fb_puts0(0, "UART failed, trying SD...");
                goto load_from_sd;
            }

            /* phdpd is alive — let the OS know it can keep using the
             * boot ROM's disk channel for app-slot loads after this
             * point. of_disk_init() reads boot_disk_available. */
            boot_disk_available = 1;

            (void)boot_read_os_meta(total_size);

            /* Send EVT_EXEC_START */
            uint8_t exec_payload[4];
            boot_store_le32(exec_payload,
                            os_meta_entry ? os_meta_entry
                                          : (uint32_t)(uintptr_t)os_main);
            phdp_send(PHDP_EVT_EXEC_START, exec_payload, 4);

            /* Enable UART mirror before jumping to OS */
            extern volatile int uart_mirror_on;
            uart_mirror_on = 1;

            /* Wait for EXEC_START to finish FULLY transmitting before
             * jumping to the OS. With the TX FIFO, UART_TX_RDY just
             * means "FIFO has space", which is set the moment the
             * AXI write completes -- not what we want here. Use
             * UART_TX_IDLE which only asserts when the FIFO is empty
             * AND uart_tx is not currently shifting. */
            while (!(UART_STATUS & UART_TX_IDLE)) {}

            boot_fb_clear_row(0);
            goto start_os;
        }
        /* Host said USE_SD or timeout — fall through */
    }

load_from_sd:
    /* ── Standard SD card boot ──────────────────────────────────── */
    boot_fb_clear_row(0);
    boot_fb_puts0(0, "Loading...");

    pd_dbg_stage = 3;

    uint32_t os_size = boot_os_slot_size();



    int rc = boot_load_os_sd(os_size);

    if (rc < 0) {
        boot_fb_clear_row(0);
        boot_fb_puts0(0, "Load failed E");
        boot_fb_putchar(14, 0, '0' + (unsigned int)(-rc));
        /* pd_dbg_info holds DS_STATUS captured at timeout — show the
         * full word after the error digit so we can see which bridge
         * handshake bit was stuck (low byte: 0=ACK 1=DONE 2-4=ERR
         * 5=READY 6=WR_IDLE).  Reuses the shared hex routine. */
        boot_fb_hex32(16, 0, pd_dbg_info);
        while (1) {}
    }

    /* Refuse to run an os.bin whose memory map differs from this baked
     * bootloader (wrong target / stale mif): it would pass the content CRC
     * yet load+run at the wrong addresses (silent black).  Show why + stop. */
    if (!boot_check_os_abi(os_size)) {
        boot_fb_clear_row(0);
        boot_fb_clear_row(1);
        boot_fb_puts0(0, "BOOT ABI MISMATCH");
        boot_fb_puts0(1, "os.bin != this core's bootloader");
        boot_fb_puts0(2, "Rebuild firmware.mif");
        while (1) {}
    }

    (void)boot_read_os_meta(os_size);

    pd_dbg_stage = 4;
    boot_fb_clear_row(0);

start_os:
    /* uart_mirror_on is armed by the PHDP path above and by
     * of_term_enable_uart_mirror() in kernel/main.c for SD boot.
     *
     * v2 split-staging model: CRAM0 is only a bridge scratchpad. The
     * load path has copied os.bin into its SDRAM VMA; only .bss remains
     * to be zeroed before entering the kernel.
     *
     * The .bss bounds and the entry point come from the image's own
     * OSE2 metadata when present (os_meta_entry != 0) — the baked
     * _os_bss_* / os_main symbols only describe the kernel THIS
     * bootloader was built with, and a swapped os.bin with a shifted
     * layout would otherwise get a stale clear range (tail statics
     * keep DRAM junk) and/or a stale entry jump: the boot lottery. */
    {
    char *bss_lo = os_meta_entry ? (char *)(uintptr_t)os_meta_bss_lo
                                 : _os_bss_start;
    char *bss_hi = os_meta_entry ? (char *)(uintptr_t)os_meta_bss_hi
                                 : _os_bss_end;
    void (*os_entry_fn)(void) = os_meta_entry
                                 ? (void (*)(void))(uintptr_t)os_meta_entry
                                 : os_main;

    boot_dcache_inval_range(bss_lo, (uint32_t)(bss_hi - bss_lo));
    boot_dcache_inval_range((void *)(uintptr_t)(RUNTIME_STACK_TOP - RUNTIME_STACK_SIZE),
                            RUNTIME_STACK_SIZE);
    flush_icache();
    os_finalize_memory(bss_lo, bss_hi);
    boot_zero_uncached_sdram(RUNTIME_STACK_TOP - RUNTIME_STACK_SIZE,
                             RUNTIME_STACK_TOP);

    /* Write-landing probe (cold-boot lost-write forensics, 2026-06-09):
     * the loop above just streamed zeros through the uncached alias
     * across the whole stack window.  Sample it back; any non-zero word
     * means SDRAM is dropping writes RIGHT NOW.  Re-zero once, and if
     * it still fails halt with a visible code instead of letting
     * os_main run on memory that loses stores. */
    for (int probe_try = 0; ; probe_try++) {
        uint32_t bad = 0;
        for (uint32_t a = RUNTIME_STACK_TOP - RUNTIME_STACK_SIZE;
             a < RUNTIME_STACK_TOP && !bad; a += 8192) {
            volatile uint32_t *w = (volatile uint32_t *)
                boot_sdram_uncached_addr((void *)(uintptr_t)a);
            if (w[0] | w[1])
                bad = a;
        }
        /* Always check the very top words — os_main's first frames. */
        volatile uint32_t *top = (volatile uint32_t *)
            boot_sdram_uncached_addr(
                (void *)(uintptr_t)(RUNTIME_STACK_TOP - 64));
        for (int i = 0; i < 16 && !bad; i++)
            if (top[i])
                bad = RUNTIME_STACK_TOP - 64 + (uint32_t)i * 4u;
        if (!bad)
            break;
        pd_dbg_info = 0xBADB0000u | ((bad >> 8) & 0xFFFFu);
        if (probe_try >= 1) {
            boot_fb_puts0(1, "SDRAM WRITE FAIL");
            while (1) {}
        }
        boot_zero_uncached_sdram(RUNTIME_STACK_TOP - RUNTIME_STACK_SIZE,
                                 RUNTIME_STACK_TOP);
    }

    pd_dbg_stage = 5;

    /* If the OS image had to be reloaded to pass its CRC, surface it: the
     * count survives in BRAM (os_load_crc_retries) for the OS to report, and
     * we flash it here so it's visible during boot.  Skipped on the sim path,
     * where retries is always 0 and there is no framebuffer. */
    if (os_load_crc_retries) {
        boot_fb_puts0(1, "OS reloaded (CRC) x");
        boot_fb_putchar(19, 1, '0' + (os_load_crc_retries & 7));
    }

    /* Jump to OS */
    switch_to_runtime_stack_and_call(os_entry_fn, _runtime_stack_top);
    }

    while (1) {}
    return 0;
}
