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
    /* Bypass cache via uncached SDRAM alias (0x10... → 0x50...).
     * If cache writeback is what's wedging the LSU FIFO, this
     * sidesteps it. */
    uint32_t bss_uc_start = ((uint32_t)(uintptr_t)bss_start) - 0x10000000u + 0x50000000u;
    uint32_t bss_uc_end   = ((uint32_t)(uintptr_t)bss_end)   - 0x10000000u + 0x50000000u;
    uint32_t *bss = (uint32_t *)(uintptr_t)bss_uc_start;
    uint32_t *bend = (uint32_t *)(uintptr_t)bss_uc_end;
    while (bss < bend)
        *bss++ = 0;
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

static int app_load_transient(int rc) {
    return rc == -1 ||  /* missing size / first header read failed */
           rc == -2 ||  /* bad magic: often stale/partial first bytes */
           rc == -5 ||  /* program header read failed */
           rc == -6;    /* segment payload read failed */
}

static int load_app_with_retries(elf_load_result_t *app) {
    int rc = -1;

    for (int attempt = 0; attempt < 32; attempt++) {
        rc = elf_load(APP_SLOT_ID, APP_LOAD_ADDR, app);
        if (rc == 0)
            return 0;
        if (!app_load_transient(rc))
            return rc;

        if ((attempt & 3) == 3)
            of_term_putchar('.');
        of_timer_delay_ms(250);
    }

    return rc;
}

void os_main(void) {
    /* Initialize all hardware */
    of_init();

    /* Boot stage: red logo = OS initializing */
    boot_logo("\033[91m");  /* red */

    /* Boot stage: green logo = HAL ready */
    boot_logo("\033[92m");  /* green */

    of_term_puts("  HAL init.......... ");
    status_ok();

    /* Disk backend banner: SD = production (green), UART = service
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
    int rc = load_app_with_retries(&app);

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
                rc = load_app_with_retries(&app);
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

    /* analogizer.cfg is a shared, filename-addressed config file. It is
     * loaded after filesystem_init() so the manifest filename mapping is
     * available, then applied before the app starts. */
    of_analogizer_load_config("analogizer.cfg");

    /* UART shares cart pins with Analogizer/SNAC. Only mirror the boot console
     * when neither feature owns the adapter pins after all config is applied. */
    {
        const of_analogizer_state_t *analogizer_state = of_analogizer_get_state();
        if (!analogizer_state->enabled &&
            analogizer_state->snac_type == SNAC_NONE)
            of_term_enable_uart_mirror();
    }

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
