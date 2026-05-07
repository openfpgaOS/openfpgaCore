/*
 * openfpgaOS Kernel Main
 * Initializes HAL, loads application ELF, and executes it
 */

#include "../hal/hal.h"
#include "syscall.h"
#include "loader.h"
#include "caps_table.h"
#include "services_table.h"
#include "bank_preload.h"
#include <stddef.h>
#include <string.h>

/* Data slot IDs (match data.json) */
#define OS_SLOT_ID      1       /* OS binary (loaded by bootloader) */
#define APP_SLOT_ID     2       /* Application ELF binary */

/* Fallback load address for PIE (ET_DYN) apps that have vaddrs relative to 0.
 * ET_EXEC apps linked at a nonzero base (e.g. 0x10400000 SDRAM) ignore this
 * and use their own absolute vaddrs.  Matches __app_load_base in os.ld and
 * must stay in SDRAM, not the CRAM0 nonvolatile window. */
#define APP_LOAD_ADDR   0x10400000u

/* Symbols from linker script */
extern char __os_bss_end[];

/* Zero OS .bss in SDRAM. Lives in OS .text (CRAM0) so it adds zero
 * BRAM cost. Called by the BRAM bootloader AFTER .rodata/.data have
 * been streamed directly to their SDRAM VMA by the load path, so
 * only .bss remains to be cleared. Uses only stack locals and the
 * pointer arguments, so it is safe to invoke before .bss is zeroed. */
__attribute__((noinline, section(".text.os_finalize_memory")))
void os_finalize_memory(void *bss_start, void *bss_end) {
    /* DEBUG: direct UART writes to track BSS-zero progress in
     * tb_system.  Stack-only, no globals; safe even though .bss
     * is being zeroed concurrently. */
    volatile uint32_t * const UART_ST  = (volatile uint32_t *)0x4F000000u;
    volatile uint32_t * const UART_TX  = (volatile uint32_t *)0x4F000004u;
    #define DBG_PUTC(ch) do { \
        while (!(*UART_ST & (1u << 1))) ; \
        *UART_TX = (uint32_t)(uint8_t)(ch); \
    } while (0)
    #define DBG_HEX(v) do { \
        for (int _s = 28; _s >= 0; _s -= 4) { \
            uint32_t _n = ((v) >> _s) & 0xF; \
            DBG_PUTC(_n < 10 ? '0' + _n : 'a' + _n - 10); \
        } \
    } while (0)
    DBG_PUTC('b'); DBG_PUTC('s'); DBG_PUTC('s'); DBG_PUTC(':');
    DBG_HEX((uint32_t)(uintptr_t)bss_start);
    DBG_PUTC('-');
    DBG_HEX((uint32_t)(uintptr_t)bss_end);
    DBG_PUTC('\n');

    /* Bypass cache via uncached SDRAM alias (0x10... → 0x50...).
     * If cache writeback is what's wedging the LSU FIFO, this
     * sidesteps it. */
    uint32_t bss_uc_start = ((uint32_t)(uintptr_t)bss_start) - 0x10000000u + 0x50000000u;
    uint32_t bss_uc_end   = ((uint32_t)(uintptr_t)bss_end)   - 0x10000000u + 0x50000000u;
    uint32_t *bss = (uint32_t *)(uintptr_t)bss_uc_start;
    uint32_t *bend = (uint32_t *)(uintptr_t)bss_uc_end;
    uint32_t step = 0;
    while (bss < bend) {
        *bss++ = 0;
        if ((++step & 0xFFF) == 0) {  /* every 4096 words = 16KB */
            DBG_PUTC('.');
        }
    }
    DBG_PUTC('!'); DBG_PUTC('\n');
}

/* Draw boot logo. First call clears screen, subsequent calls just recolor. */
static int logo_drawn = 0;

static void boot_logo(const char *color) {
    if (!logo_drawn) {
        of_term_clear();
        logo_drawn = 1;
    }
    of_term_puts("\033[H");  /* cursor home */
    of_term_puts(color);
    of_term_putchar('\n');
    of_term_puts("         ___  ___  ___ ___\n");
    of_term_puts("        / _ \\/ _ \\/ -_) _ \\\n");
    of_term_puts("        \\___/ .__/\\__/_//_/\n");
    of_term_puts("       ____/_/  ________\n");
    of_term_puts("      / __/ _ \\/ ___/ _ |\n");
    of_term_puts("     / _// ___/ (_ / __ |\n");
    of_term_puts("    /_/_/_/___\\___/_/ |_|\n");
    of_term_puts("   / __ \\/ __/\n");
    of_term_puts("  / /_/ /\\ \\\n");
    of_term_puts("  \\____/___/  \033[93mv0.3\033[0m\n\n");
}

static void status_ok(void) {
    of_term_puts(" \033[92mOK\033[0m\n");
}

static void status_fail(void) {
    of_term_puts(" \033[91mFAIL\033[0m\n");
}

/* DEBUG ONLY: synthetic Duke3D-shape GPU stress test, sim only.
 * Issues N frames of (CMD_SET_FB → CMD_CLEAR_RECT → CMD_DRAW_SPAN ×K
 * → CMD_FLIP) into the GPU ring, watching for the wedge that
 * Duke3D triggers on hardware.  Direct MMIO writes — no SDK
 * dependencies. */
#if OF_TARGET_PLATFORM_ID == OF_PLATFORM_SIM
#include "../hal/regs.h"
#define UART_PUTC(c) do { \
    while (!(UART_STATUS & UART_TX_RDY)) ; \
    UART_TX_DATA = (uint32_t)(uint8_t)(c); \
} while (0)
static inline void sim_putc(char c) { UART_PUTC(c); }
static inline void sim_puts(const char *s) { while (*s) UART_PUTC(*s++); }
static inline void sim_puthex(uint32_t v) {
    for (int s = 28; s >= 0; s -= 4) {
        uint32_t n = (v >> s) & 0xF;
        UART_PUTC(n < 10 ? '0' + n : 'a' + n - 10);
    }
}
static inline void sim_putdec(uint32_t v) {
    if (v == 0) { UART_PUTC('0'); return; }
    char buf[12]; int i = 0;
    while (v) { buf[i++] = '0' + (v % 10); v /= 10; }
    while (i--) UART_PUTC(buf[i]);
}

/* Use periph_slave's decoded GPU base, not target_platform's
 * (sim target intentionally uses 0x4B to validate caps-based
 * runtime lookup, but the slave decodes only 0x4A). */
#define GPU_BASE        0x4A000000u
#define GPU_REG(o)      (*(volatile uint32_t *)(GPU_BASE + (o)))
#define GPU_CTRL_R      GPU_REG(0x00)
#define GPU_RING_DATA   GPU_REG(0x08)
#define GPU_RING_RDPTR_R GPU_REG(0x10)
#define GPU_RING_WRPTR_R GPU_REG(0x0C)
#define GPU_STATUS_R    GPU_REG(0x14)
#define GPU_FENCE_R     GPU_REG(0x18)

static uint32_t sim_wrptr = 0;
static const uint32_t SIM_RING_MASK = 0x3FFC;  /* 16KB ring, word-aligned */

static inline void sim_ring_write(uint32_t w) {
    GPU_RING_DATA = w;
    sim_wrptr = (sim_wrptr + 4) & SIM_RING_MASK;
}
static inline void sim_ring_kick(void) { GPU_RING_WRPTR_R = sim_wrptr; }
static inline uint32_t sim_ring_free(void) {
    return (GPU_RING_RDPTR_R - sim_wrptr - 4) & SIM_RING_MASK;
}
static inline void sim_ring_ensure(uint32_t bytes, int frame) {
    if (sim_ring_free() >= bytes) return;
    sim_puts("[sim] ring stall f=");
    sim_putdec(frame);
    sim_puts(" need=");
    sim_putdec(bytes);
    sim_puts(" rdptr=");
    sim_puthex(GPU_RING_RDPTR_R);
    sim_puts(" wrptr=");
    sim_puthex(sim_wrptr);
    sim_puts(" status=");
    sim_puthex(GPU_STATUS_R);
    sim_puts("\n");
    GPU_RING_WRPTR_R = sim_wrptr;
    /* Bounded spin so the test errors out instead of locking sim. */
    uint32_t spin = 1000000u;
    while (sim_ring_free() < bytes) {
        if (--spin == 0) {
            sim_puts("[sim] RING WEDGED — abort\n");
            while (1) ;
        }
    }
}

__attribute__((noinline))
static void sim_gpu_stress_test(void) {
    sim_puts("\n[sim] gpu stress start\n");
    sim_puts("[sim] reset GPU..\n");
    /* Soft-reset GPU + ring (mirrors of_gpu_init's preamble). */
    GPU_CTRL_R = 6;          /* soft_reset | ring_reset */
    sim_puts("[sim] CTRL=6 done\n");
    for (volatile int i = 0; i < 100; i++) ;
    GPU_CTRL_R = 1;          /* gpu_enable */
    sim_puts("[sim] CTRL=1 done\n");
    sim_wrptr = 0;
    GPU_RING_WRPTR_R = 0;
    sim_puts("[sim] WRPTR=0 done\n");
    sim_puts("[sim] read RDPTR..\n");
    uint32_t rd = GPU_RING_RDPTR_R;
    sim_puts("[sim] RDPTR=");
    sim_puthex(rd);
    sim_puts("\n");

    const int N_FRAMES = 64;
    const int SPANS_PER_FRAME = 8;
    uint32_t fence_token = 1;
    uint32_t fb_addrs[3] = { 0x10000000u, 0x10100000u, 0x10200000u };
    int draw_idx = 0;

    for (int f = 0; f < N_FRAMES; f++) {
        uint32_t fb = fb_addrs[draw_idx];

        if (f == 0) sim_puts("[sim] f=0 enter\n");
        /* CMD_SET_FB (opcode 0x23, 2-word payload) */
        sim_ring_ensure(3 * 4, f);
        if (f == 0) sim_puts("[sim] f=0 ensure ok\n");
        sim_ring_write(((uint32_t)0x23 << 24) | 2);
        if (f == 0) sim_puts("[sim] f=0 write hdr ok\n");
        sim_ring_write(fb);
        sim_ring_write(320);
        if (f == 0) sim_puts("[sim] f=0 set_fb done\n");

        /* CMD_CLEAR_RECT — small rect (matches Duke3D bars) */
        sim_ring_ensure(4 * 4, f);
        sim_ring_write(((uint32_t)0x11 << 24) | 3);
        sim_ring_write(fb);
        sim_ring_write(((uint32_t)320 << 16) | 4);
        sim_ring_write(((uint32_t)320 << 16) | (uint32_t)(f & 0xFF));

        /* SPANS_PER_FRAME of CMD_DRAW_SPAN (opcode 0x30, fewer fields
         * than full BATCH but same write traffic via m_wr_*) */
        for (int s = 0; s < SPANS_PER_FRAME; s++) {
            sim_ring_ensure(16 * 4, f);
            sim_ring_write(((uint32_t)0x30 << 24) | 15);
            sim_ring_write(fb + (uint32_t)(s * 320));  /* fb_addr */
            sim_ring_write(0);   /* tex_addr (none) */
            sim_ring_write(0);   /* s, t */
            sim_ring_write(0);
            sim_ring_write(0x10000); /* sstep */
            sim_ring_write(0);   /* tstep */
            sim_ring_write(((uint32_t)10 << 16) | 320);  /* light | count */
            sim_ring_write(0);   /* flags */
            sim_ring_write(1);   /* fb_stride */
            sim_ring_write(((uint32_t)64 << 16) | 0);
            sim_ring_write(0);
            sim_ring_write(0);
            sim_ring_write(0);
            sim_ring_write(0);
            sim_ring_write(0);
        }

        /* CMD_FLIP (opcode 0x42, 2-word payload) */
        sim_ring_ensure(3 * 4, f);
        sim_ring_write(((uint32_t)0x42 << 24) | 2);
        sim_ring_write((uint32_t)draw_idx);
        sim_ring_write(fence_token);

        sim_ring_kick();

        /* Wait for fence to advance — with a bounded timeout. */
        uint32_t spin = 5000000u;
        while ((int32_t)(GPU_FENCE_R - fence_token) < 0) {
            if (--spin == 0) {
                sim_puts("[sim] fence WEDGED f=");
                sim_putdec(f);
                sim_puts(" tok=");
                sim_puthex(fence_token);
                sim_puts(" reached=");
                sim_puthex(GPU_FENCE_R);
                sim_puts(" status=");
                sim_puthex(GPU_STATUS_R);
                sim_puts(" rdptr=");
                sim_puthex(GPU_RING_RDPTR_R);
                sim_puts("\n");
                return;
            }
        }
        if ((f & 0xF) == 0xF) {
            sim_puts("[sim] f=");
            sim_putdec(f);
            sim_puts(" ok\n");
        }

        fence_token++;
        draw_idx = (draw_idx + 1) % 3;
    }
    sim_puts("[sim] gpu stress PASS\n");
}
#endif /* SIM */

void os_main(void) {
    /* Initialize all hardware */
    of_init();

#if OF_TARGET_PLATFORM_ID == OF_PLATFORM_SIM
    sim_puts("[sim] of_init returned\n");
    /* Run synthetic Duke3D-shape GPU stress before regular boot
     * continues, so the harness sees the result before any
     * potential wedge in later init paths. */
    sim_gpu_stress_test();
#endif

    /* Boot stage: red logo = OS initializing */
    boot_logo("\033[91m");  /* red */

    of_term_enable_uart_mirror();

    /* Boot stage: green logo = HAL ready */
    boot_logo("\033[92m");  /* green */

    of_term_puts("  HAL init.......... ");
    status_ok();

    /* Disk backend banner: SD = production (green), UART = debug
     * host attached (yellow), none = file I/O will fail (red).
     * Labels are left-aligned with the OK / FAIL column above and
     * pointer-compared so renames don't break the colour. */
    {
        const of_disk_driver_t *active = of_disk_active();
        const char *colour = "\033[91m";
        const char *label  = "none";
        if (active == &of_disk_bridge) {
            colour = "\033[92m";
            label  = "SD";
        } else if (active == &of_disk_boot) {
            colour = "\033[93m";
            label  = "UART";
        }
        of_term_printf("  Disk backend......  %s%s\033[0m\n", colour, label);
    }

    /* Initialize syscall subsystem */
    uintptr_t heap_start = ((uintptr_t)__os_bss_end + 15) & ~15;
    syscall_init(heap_start);

    of_term_puts("  Syscall init...... ");
    status_ok();

    /* Brief pause to show banner */
    of_timer_delay_ms(800);

    /* Load application ELF */
    of_term_puts("  Loading app....... ");

    elf_load_result_t app;
    int rc = elf_load(APP_SLOT_ID, APP_LOAD_ADDR, &app);

    if (rc < 0) {
        status_fail();
        of_term_printf("  rc=%d\n", rc);
        of_term_puts("  \033[93mNo application found.\033[0m\n\n");
        of_term_puts("  Place .elf in data slot 2\n");
        of_term_puts("  and press START to retry.\n");

        while (1) {
            of_input_poll();
            if (of_input_is_pressed(0, OF_BTN_START)) {
                of_term_puts("\n  Retrying...");
                rc = elf_load(APP_SLOT_ID, APP_LOAD_ADDR, &app);
                if (rc == 0)
                    break;
                of_term_printf(" rc=%d\n", rc);
                status_fail();
            }
        }
    }

    status_ok();

    /* Heap in SDRAM after app's BSS (app is loaded at 0x10400000+) */
    syscall_init(app.bss_end);

    /* Apps statically link musl and emit Linux syscalls via ecall.
     * The kernel's linux_dispatch() in syscall.c handles them; no
     * libc jump table is needed any more. */

    /* Populate OS services table first -- caps_table.c reads its
     * address to fill the legacy caps->services_table field. */
    services_table_init();

    of_term_puts("  Services init..... ");
    status_ok();

    /* Populate capability descriptor for the app */
    caps_table_init(app.bss_end);

    of_term_puts("  Caps init......... ");
    status_ok();

    /* Filesystem init — last step before handing control to the app.
     * Opens every data slot (required for deferload:true) and populates
     * the filename→slot map so apps can fopen() by name. */
    of_term_puts("  Filesystem init... ");
    int fs_slots = filesystem_init();
    of_term_printf(" \033[92m%d slot%s\033[0m\n",
                   fs_slots, fs_slots == 1 ? "" : "s");

    /* Auto-load a .ofsf SoundFont if one is present in a data slot.
     * Silent when no bank is staged; emits its own boot line otherwise. */
    bank_preload();

    of_timer_delay_ms(300);

    /* Boot stage: blue logo = launching app. Save/restore cursor so
     * the redraw (which jumps to 0,0) doesn't leave the cursor inside
     * the logo -- otherwise the app's first writes overwrite the
     * status messages below it. */
    {
        int saved_col, saved_row;
        of_term_get_pos(&saved_col, &saved_row);
        boot_logo("\033[94m");  /* blue */
        of_term_set_pos(saved_col, saved_row);
    }
    of_timer_delay_ms(200);

    /* Execute the app */
    char *argv[] = {"app", NULL};
    elf_exec(&app, 1, argv);

    /* Should never reach here */
    while (1) {}
}
