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

/* Data slot IDs */
#define OS_SLOT_ID      1       /* OS binary */
#define OS_DT_SIZE_WORD 3       /* entry 1 size word: id word 2, size word 3 */

/* DMA timeout (~2 seconds at 100MHz) */
#define BOOT_DMA_TIMEOUT 200000000

/* External symbols from linker */
extern char _os_bss_start[], _os_bss_end[];
extern char _runtime_stack_top[];
extern char _os_load_addr[];
extern char _os_text_size[];
extern char _os_copy_size[];
extern char _osdata_init_vma_start[];
extern char _osdata_init_size[];

/* OS entry point */
extern void os_main(void);
extern void switch_to_runtime_stack_and_call(void (*entry)(void), void *stack_top);

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


/* os_finalize_memory() lives in OS .text, which the v2 boot path copies
 * into SDRAM before jumping. Defined in kernel/main.c. It only zeroes
 * .bss — the .rodata and .data init image has already been streamed to
 * its SDRAM VMA by the load path, so no copy is needed here. */
extern void os_finalize_memory(void *bss_start, void *bss_end);

__attribute__((section(".text.boot")))
static void flush_icache(void) {
    __asm__ volatile("fence");
    __asm__ volatile(".word 0x0000100f");  /* fence.i */
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
    alive_payload[0] = (PHDP_CORE_ID >>  0) & 0xFF;
    alive_payload[1] = (PHDP_CORE_ID >>  8) & 0xFF;
    alive_payload[2] = (PHDP_CORE_ID >> 16) & 0xFF;
    alive_payload[3] = (PHDP_CORE_ID >> 24) & 0xFF;
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
            prog[0] = (received >>  0) & 0xFF;
            prog[1] = (received >>  8) & 0xFF;
            prog[2] = (received >> 16) & 0xFF;
            prog[3] = (received >> 24) & 0xFF;
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

/* Inline DMA read -- does not call any SDRAM functions */
__attribute__((section(".text.boot")))
static int boot_dma_read(uint32_t slot_id, uint32_t slot_offset,
                         uint32_t bridge_addr, uint32_t length) {
    uint32_t timeout;

    __asm__ volatile("fence" ::: "memory");

    /* Wait for bridge to be idle before issuing a new command. READY
     * means the APF command FSM can accept work; WR_IDLE means any
     * prior bridge writes to CRAM0 have fully drained. */
    timeout = BOOT_DMA_TIMEOUT;
    while ((DS_STATUS & (DS_STATUS_READY | DS_STATUS_WR_IDLE)) !=
           (DS_STATUS_READY | DS_STATUS_WR_IDLE)) {
        if (--timeout == 0) { pd_dbg_info = DS_STATUS; return -3; }
    }

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
    timeout = BOOT_DMA_TIMEOUT;
    while (!(DS_STATUS & DS_STATUS_ACK)) {
        if (--timeout == 0) { pd_dbg_info = DS_STATUS; return -1; }
    }

    /* Wait for DONE */
    timeout = BOOT_DMA_TIMEOUT;
    while (!(DS_STATUS & DS_STATUS_DONE)) {
        if (--timeout == 0) { pd_dbg_info = DS_STATUS; return -2; }
    }

    /* Check error */
    uint32_t err = (DS_STATUS & DS_STATUS_ERR_MASK) >> DS_STATUS_ERR_SHIFT;
    if (err) return -(int)err;

    /* DONE reports APF command completion. Do not switch CRAM0 to CPU
     * ownership until the live drain bit confirms the last bridge write
     * has landed in memory. */
    timeout = BOOT_DMA_TIMEOUT;
    while (!(DS_STATUS & DS_STATUS_WR_IDLE)) {
        if (--timeout == 0) { pd_dbg_info = DS_STATUS; return -4; }
    }

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
    uint32_t linked_size = (uint32_t)(uintptr_t)_os_copy_size;
    uint32_t dt_size = boot_dt_read_word(OS_DT_SIZE_WORD);

    if (dt_size >= 4096u && dt_size <= 768u * 1024u)
        return dt_size;
    return linked_size;
}

__attribute__((section(".text.boot")))
static int boot_load_os_sd(uint32_t total) {
    /* v2 arch: CRAM1 retired, OS .text in SDRAM (CRAM0 is non-exec).
     * Single-destination layout: bridge DMAs os.bin into CRAM0
     * scratch, then the CPU copies the whole blob contiguously into
     * SDRAM starting at __osdata_vma (== __os_entry).  No
     * text-vs-data split needed since the linker put everything in
     * one .osdata section. */
    uint32_t bounce_bridge = CRAM0_SCRATCH_BRIDGE;
    volatile uint8_t *bounce_src = (volatile uint8_t *)CRAM0_SCRATCH;
    volatile uint8_t *sdram_dst =
        (volatile uint8_t *)(uintptr_t)_osdata_init_vma_start;
    uint32_t done = 0;

    while (done < total) {
        uint32_t chunk = total - done;
        if (chunk > DMA_CHUNK_SIZE)
            chunk = DMA_CHUNK_SIZE;

        /* Bridge DMA: SD card → CRAM0 scratch */
        CRAM0_MODE = CRAM0_MODE_BRIDGE;
        for (volatile int s = 0; s < 8; s++) {}  /* settle ~4 clk_74a */
        int rc = boot_dma_read(OS_SLOT_ID, done, bounce_bridge, chunk);
        if (rc < 0)
            return rc;

        /* CPU reads CRAM0 scratch, writes to SDRAM. */
        CRAM0_MODE = CRAM0_MODE_CPU;
        for (volatile int s = 0; s < 8; s++) {}  /* settle ~4 clk_74a */
        volatile uint32_t *src32 = (volatile uint32_t *)bounce_src;
        volatile uint32_t *dst32 = (volatile uint32_t *)(sdram_dst + done);
        for (uint32_t i = 0; i < chunk / 4; i++)
            dst32[i] = src32[i];

        done += chunk;
    }

    /* Leave the mux in bridge mode — later code issuing bridge DMAs
     * is the common case; one-off CPU reads flip it as needed. */
    CRAM0_MODE = CRAM0_MODE_BRIDGE;
    return 0;
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
    goto start_os;
#endif

    /* Wait for APF bridge */
    unsigned int start_wait = SYS_CYCLE_LO;
    while (!(SYS_STATUS & SYS_STATUS_ALLCOMPLETE)) {
        if ((SYS_CYCLE_LO - start_wait) > 500000000)
            break;
    }

    pd_dbg_stage = 2;

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

    boot_fb_puts(0, 0, "Booting...");

    /* ── PHDP Discovery ─────────────────────────────────────────── */
    int debug_mode = 0;

    if (uart_probe()) {
        debug_mode = phdp_discover();
    }

    if (debug_mode) {
        boot_fb_clear_row(0);
        boot_fb_puts(0, 0, "Service host connected");

        uint32_t total_size = 0;
        uint16_t chunk_size = 0;

        if (phdp_request_override(OS_SLOT_ID, &total_size, &chunk_size)) {
            /* Stream OS binary over UART */
            boot_fb_clear_row(0);
            boot_fb_puts(0, 0, "Loading via UART...");

            /* v2 arch: OS is ONE contiguous section in SDRAM
             * (__os_load_addr == __osdata_init_vma_start, same
             * address).  No text/data split — stream the whole blob
             * directly into SDRAM with a single phdp_chunk_loop call.
             * PHDP writes the CPU directly to SDRAM; the CRAM0 mux
             * is irrelevant here. */
            (void)chunk_size;
            uint8_t *os_dst = (uint8_t *)(uintptr_t)_os_load_addr;
            int rc = phdp_chunk_loop(0, total_size, total_size, os_dst);

            if (rc < 0) {
                boot_fb_clear_row(0);
                boot_fb_puts(0, 0, "UART failed, trying SD...");
                goto load_from_sd;
            }

            /* phdpd is alive — let the OS know it can keep using the
             * boot ROM's disk channel for app-slot loads after this
             * point. of_disk_init() reads boot_disk_available. */
            boot_disk_available = 1;

            /* Send EVT_EXEC_START */
            uint8_t exec_payload[4];
            uint32_t entry = (uint32_t)(uintptr_t)os_main;
            exec_payload[0] = (entry >>  0) & 0xFF;
            exec_payload[1] = (entry >>  8) & 0xFF;
            exec_payload[2] = (entry >> 16) & 0xFF;
            exec_payload[3] = (entry >> 24) & 0xFF;
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
    boot_fb_puts(0, 0, "Loading...");

    pd_dbg_stage = 3;

    uint32_t os_size = boot_os_slot_size();

    int rc = boot_load_os_sd(os_size);

    if (rc < 0) {
        boot_fb_clear_row(0);
        boot_fb_puts(0, 0, "Load failed E");
        boot_fb_putchar(14, 0, '0' + (unsigned int)(-rc));
        /* pd_dbg_info now holds DS_STATUS captured at timeout — show
         * the bottom byte as 2 hex chars after the error digit so we
         * can see which bridge handshake bit was stuck.
         * Bits: 0=ACK 1=DONE 2-4=ERR 5=READY 6=WR_IDLE. */
        unsigned st = pd_dbg_info & 0xFF;
        unsigned h = (st >> 4) & 0xF;
        unsigned l = st & 0xF;
        boot_fb_putchar(16, 0, (h < 10) ? ('0' + h) : ('a' + h - 10));
        boot_fb_putchar(17, 0, (l < 10) ? ('0' + l) : ('a' + l - 10));
        while (1) {}
    }

    pd_dbg_stage = 4;
    boot_fb_clear_row(0);

start_os:
    /* uart_mirror_on is armed by the PHDP path above and by
     * of_term_enable_uart_mirror() in kernel/main.c for SD boot.
     *
     * v2 split-staging model: CRAM0 is only a bridge scratchpad. The
     * load path has copied os.bin into its SDRAM VMA; only .bss remains
     * to be zeroed before entering os_main. */
#if OF_TARGET_PLATFORM_ID != OF_PLATFORM_SIM
    /* flush_dcache_evict reads one full D-cache worth of SDRAM to evict
     * deferload writes; sim's harness preloads SDRAM via backdoor so
     * there's nothing to evict, AND sdram_fast_model wedges around
     * iteration ~800 of the sweep (likely a burst-read state-machine
     * edge case).  Skip on sim — the kernel will run cbo.flush as
     * needed once it's up. */
    flush_dcache_evict();
#endif
    flush_icache();
    os_finalize_memory((void *)_os_bss_start, (void *)_os_bss_end);

    pd_dbg_stage = 5;

    /* Jump to OS */
    switch_to_runtime_stack_and_call(os_main, _runtime_stack_top);

    while (1) {}
    return 0;
}
