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

/* App load address (after OS region) */
#define APP_LOAD_ADDR   0x10400000

/* Symbols from linker script */
extern char __os_bss_end[];

static void boot_banner(void) {
    of_term_clear();
    of_term_puts("\033[96m");  /* bright cyan */
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
    of_term_puts("\\____/___/  \033[93mv0.1\033[0m\n\n");
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

    /* Show boot banner on terminal */
    SYS_DISPLAY_MODE = DISPLAY_MODE_TERMINAL;
    boot_banner();

    of_term_enable_uart_mirror();

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

    /* Set heap to after the app's BSS */
    syscall_init(app.bss_end);

    /* Populate libc jump table for the app */
    libc_table_init();

    of_term_puts("  Libc init......... ");
    status_ok();

    of_timer_delay_ms(300);

    /* Execute the app */
    char *argv[] = {"app", NULL};
    elf_exec(&app, 1, argv);

    /* Should never reach here */
    while (1) {}
}
