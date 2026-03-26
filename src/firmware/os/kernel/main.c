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

/* Heap cannot grow past the save region */
#define HEAP_LIMIT      0x13C00000

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
    of_term_puts("\\____/___/  \033[93mv0.1\n");  /* bright yellow */
    of_term_puts("\033[0m\n");  /* reset */
}

static void term_ok(void) {
    of_term_puts("\033[92mOK\033[0m\n");  /* bright green + reset */
}

static void term_fail(const char *msg) {
    of_term_puts("\033[91m");  /* bright red */
    of_term_puts(msg);
    of_term_puts("\033[0m");   /* reset */
}

void os_main(void) {
    /* Initialize all hardware */
    of_init();

    /* Show boot banner on terminal */
    SYS_DISPLAY_MODE = DISPLAY_MODE_TERMINAL;
    boot_banner();

    of_term_enable_uart_mirror();

    of_term_puts("HAL init............ ");
    term_ok();

    /* Clear heap region so stale allocations from a previous instance
     * don't corrupt dlmalloc's internal state on reload. */
    of_term_puts("Clearing memory..... ");
    uintptr_t heap_start = ((uintptr_t)__os_bss_end + 15) & ~15;
    memset((void *)heap_start, 0, HEAP_LIMIT - heap_start);
    term_ok();

    /* Initialize syscall subsystem with heap starting after OS BSS */
    syscall_init(heap_start);
    of_term_puts("Syscall init........ ");
    term_ok();

    /* Stay on boot screen for 1 second */
    of_timer_delay_ms(1000);

    /* Attempt to load application ELF from data slot */
    of_term_printf("Loading app slot %d.. ", APP_SLOT_ID);

    elf_load_result_t app;
    int rc = elf_load(APP_SLOT_ID, APP_LOAD_ADDR, &app);

    if (rc < 0) {
        term_fail("FAIL\n");
        of_term_printf("  Error code: %d\n", rc);
        of_term_putchar('\n');
        of_term_puts("No application found.\n");
        of_term_puts("Place .elf in data slot.\n");
        of_term_puts("Press START to retry.\n");

        while (1) {
            of_input_poll();
            if (of_input_is_pressed(0, BTN_START)) {
                of_term_puts("Retrying...\n");
                rc = elf_load(APP_SLOT_ID, APP_LOAD_ADDR, &app);
                if (rc == 0)
                    break;
                term_fail("FAIL");
                of_term_printf(" (rc=%d)\n", rc);
            }
        }
    }

    term_ok();

    /* Set heap to after the app's BSS BEFORE libc init.
     * musl's setvbuf/malloc may call brk() during libc_table_init,
     * so brk must already point to the app's free memory. */
    syscall_init(app.bss_end);

    /* Populate libc jump table for the app */
    libc_table_init();
    of_term_puts("Libc table.......... ");
    term_ok();

    /* Brief delay to show boot messages */
    of_timer_delay_ms(500);

    /* Execute the app (app controls display mode) */
    char *argv[] = {"app", NULL};
    elf_exec(&app, 1, argv);

    /* Should never reach here */
    while (1) {}
}
