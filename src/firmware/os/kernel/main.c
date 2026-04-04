/*
 * openfpgaOS Kernel Main
 * Initializes HAL, loads application ELF, and executes it
 */

#include "../hal/hal.h"
#include "syscall.h"
#include "loader.h"
#include "libc_table.h"
#include <stddef.h>
#include <string.h>

/* Data slot IDs (match data.json) */
#define OS_SLOT_ID      1       /* OS binary (loaded by bootloader) */
#define APP_SLOT_ID     2       /* Application ELF binary */

/* Fallback load address for PIE (ET_DYN) apps that have vaddrs relative to 0.
 * ET_EXEC apps linked at a nonzero base (e.g. 0x10400000 SDRAM) ignore this
 * and use their own absolute vaddrs.  Matches __app_load_base in os.ld. */
#define APP_LOAD_ADDR   0x30100000

/* Symbols from linker script */
extern char __os_bss_end[];

/* Draw boot logo. First call clears screen, subsequent calls just recolor. */
static int logo_drawn = 0;

static void boot_logo(const char *color) {
    if (!logo_drawn) {
        of_term_clear();
        logo_drawn = 1;
    }
    of_term_set_pos(0, 0);
    of_term_puts(color);
    of_term_putchar('\n');
    of_term_puts("       ___  ___  ___ ___\n");
    of_term_puts("      / _ \\/ _ \\/ -_) _ \\\n");
    of_term_puts("      \\___/ .__/\\__/_//_/\n");
    of_term_puts("     ____/_/  ________\n");
    of_term_puts("    / __/ _ \\/ ___/ _ |\n");
    of_term_puts("   / _// ___/ (_ / __ |\n");
    of_term_puts("  /_/_/_/___\\___/_/ |_|\n");
    of_term_puts(" / __ \\/ __/\n");
    of_term_puts("/ /_/ /\\ \\\n");
    of_term_puts("\\____/___/  \033[93mv0.2\033[0m\n\n");
}

static void status_ok(void) {
    of_term_puts(" \033[92mOK\033[0m\n");
}

static void status_fail(void) {
    of_term_puts(" \033[91mFAIL\033[0m\n");
}

void os_main(void) {
    /* Initialize all hardware */
    of_init();

    /* Boot stage: red logo = OS initializing */
    SYS_DISPLAY_MODE = DISPLAY_MODE_TERMINAL;
    boot_logo("\033[91m");  /* red */

    of_term_enable_uart_mirror();

    /* Boot stage: green logo = HAL ready */
    boot_logo("\033[92m");  /* green */

    of_term_puts("  HAL init.......... ");
    status_ok();

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
        of_term_putchar('\n');
        of_term_puts("  \033[93mNo application found.\033[0m\n\n");
        of_term_puts("  Place .elf in data slot 2\n");
        of_term_puts("  and press START to retry.\n");

        while (1) {
            of_input_poll();
            if (of_input_is_pressed(0, BTN_START)) {
                of_term_puts("\n  Retrying...");
                rc = elf_load(APP_SLOT_ID, APP_LOAD_ADDR, &app);
                if (rc == 0)
                    break;
                status_fail();
            }
        }
    }

    status_ok();

    /* Heap in SDRAM after app's BSS (app is loaded at 0x10400000+) */
    syscall_init(app.bss_end);

    /* Populate libc jump table for the app */
    libc_table_init();

    of_term_puts("  Libc init......... ");
    status_ok();

    of_timer_delay_ms(300);

    /* Boot stage: blue logo = launching app */
    boot_logo("\033[94m");  /* blue */
    of_timer_delay_ms(200);

    /* Execute the app */
    char *argv[] = {"app", NULL};
    elf_exec(&app, 1, argv);

    /* Should never reach here */
    while (1) {}
}
