//------------------------------------------------------------------------------
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileType: SOURCE
// SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
//------------------------------------------------------------------------------

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
#include "irq.h"
#include "config.h"
#include "launch.h"
#include <stddef.h>
#include <string.h>

/* Layout-shift pad for boot-lottery bisection (sim + HW pad sweeps).
 * Build with EXTRA_CFLAGS=-DOS_LAYOUT_PAD=<bytes> to insert N bytes of
 * never-referenced data into .text, shifting every symbol downstream of
 * this translation unit's placement — the same class of byte-layout
 * change that separates booting from black kernels on MiSTer.  Zero
 * runtime effect; absent from normal builds. */
#if defined(OS_LAYOUT_PAD) && OS_LAYOUT_PAD > 0
__attribute__((used, section(".text.zzz_os_layout_pad"), aligned(4)))
static const unsigned char os_layout_pad[OS_LAYOUT_PAD] = { 0x13 };
#endif

/* Data slot IDs (match data.json) */
#define APP_SLOT_ID     3       /* Default application ELF binary */
#define APP_DEFAULT_ELF "app.elf"
#define APP_ELF_NAME_MAX 64
#define APP_ARGS_MAX     512
#define APP_ARGC_MAX     32

/* Fallback load address for PIE (ET_DYN) apps that have vaddrs relative to 0.
 * ET_EXEC apps linked at a nonzero base (e.g. 0x10400000 SDRAM) ignore this
 * and use their own absolute vaddrs.  Matches __app_load_base in os.ld and
 * must stay in SDRAM, not the CRAM0 nonvolatile window. */
#define APP_LOAD_ADDR   0x10400000u

/* Symbols from linker script */
extern char __os_bss_end[];

/* OS .text integrity guard (kernel/misaligned.c).  Baseline the loaded code
 * once it is known-good, then check at each boot checkpoint; the first failing
 * checkpoint brackets the operation that overwrites the OS .text at runtime. */
void os_textguard_baseline(void);
void os_textguard_check(const char *where);

/* SW-mixer DAC pump driver: OS-owned 1 kHz timer arm (syscall.c). */
void of_os_timer_boot_arm(void);

/* Zero OS .bss in SDRAM from BRAM.  This runs before os_main(), while
 * SDRAM has just been populated by the bootloader and before the OS
 * can rely on its own .bss state.  Keep it in .fasttext so the first
 * uncached SDRAM cleanup does not execute from SDRAM at the same time. */
__attribute__((noinline, section(".fasttext.os_finalize_memory"), aligned(4)))
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
    __asm__ volatile("fence" ::: "memory");
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
    of_term_puts("  \\____/___/  \033[93mv0.7\033[0m\n\n");
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

static int load_app_with_retries(uint32_t slot_id, elf_load_result_t *app) {
    int rc = -1;

    for (int attempt = 0; attempt < 32; attempt++) {
        rc = elf_load(slot_id, APP_LOAD_ADDR, app);
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

static char app_elf_name[APP_ELF_NAME_MAX];
static char app_args_buf[APP_ARGS_MAX];
static char *app_argv[APP_ARGC_MAX + 1];

static int os_cfg_isspace(char c) {
    return c == ' ' || c == '\t' || c == '\r' || c == '\n';
}

static char os_cfg_tolower(char c) {
    if (c >= 'A' && c <= 'Z')
        return (char)(c + ('a' - 'A'));
    return c;
}

static int os_cfg_stricmp(const char *a, const char *b) {
    while (*a && *b) {
        char ca = os_cfg_tolower(*a++);
        char cb = os_cfg_tolower(*b++);
        if (ca != cb)
            return (int)(unsigned char)ca - (int)(unsigned char)cb;
    }
    return (int)(unsigned char)os_cfg_tolower(*a) -
           (int)(unsigned char)os_cfg_tolower(*b);
}

static int os_cfg_parse_slot_ref(const char *s, uint32_t *slot_out) {
    if (!s || !(s[0] == 's' || s[0] == 'S') ||
        !(s[1] == 'l' || s[1] == 'L') ||
        !(s[2] == 'o' || s[2] == 'O') ||
        !(s[3] == 't' || s[3] == 'T') ||
        s[4] != ':')
        return -1;

    const char *p = s + 5;
    uint32_t slot = 0;
    int digits = 0;
    while (*p >= '0' && *p <= '9') {
        slot = slot * 10u + (uint32_t)(*p - '0');
        digits++;
        p++;
    }

    if (!digits || *p)
        return -1;

    *slot_out = slot;
    return 0;
}

static int resolve_app_slot(const char *elf_name, uint32_t *slot_out) {
    if (os_cfg_parse_slot_ref(elf_name, slot_out) == 0)
        return 0;

    if (file_slot_find(elf_name, slot_out) == 0)
        return 0;

    if (os_cfg_stricmp(elf_name, APP_DEFAULT_ELF) == 0) {
        *slot_out = APP_SLOT_ID;
        return 0;
    }

    return -1;
}

static int build_app_argv(int *argc_out) {
    int argc = 1;
    char *read = app_args_buf;
    char *write = app_args_buf;

    app_argv[0] = app_elf_name;

    while (*read) {
        while (os_cfg_isspace(*read))
            read++;
        if (!*read)
            break;

        if (argc >= APP_ARGC_MAX)
            return OF_CONFIG_ERR_E2BIG;

        app_argv[argc++] = write;
        char quote = 0;

        while (*read) {
            char c = *read++;

            if (quote) {
                if (c == quote) {
                    quote = 0;
                    continue;
                }
                if (c == '\\' && *read) {
                    *write++ = *read++;
                    continue;
                }
                *write++ = c;
                continue;
            }

            if (c == '\'' || c == '"') {
                quote = c;
                continue;
            }
            if (c == '\\' && *read) {
                *write++ = *read++;
                continue;
            }
            if (os_cfg_isspace(c))
                break;

            *write++ = c;
        }

        if (quote)
            return OF_CONFIG_ERR_INVAL;

        *write++ = '\0';
    }

    app_argv[argc] = NULL;
    *argc_out = argc;
    return 0;
}

/* Resolve the app to launch and build its argv.  When elf_override is non-empty
 * it takes precedence over [os] ELF (used by the in-OS relaunch path, where the
 * launcher names the target directly); otherwise the os.ini [os] ELF is used,
 * defaulting to APP_DEFAULT_ELF. */
static int prepare_app_launch_ex(const char *elf_override,
                                 uint32_t *slot_out, int *argc_out) {
    if (elf_override && elf_override[0]) {
        strncpy(app_elf_name, elf_override, sizeof(app_elf_name));
        app_elf_name[sizeof(app_elf_name) - 1] = '\0';
    } else {
        strncpy(app_elf_name, APP_DEFAULT_ELF, sizeof(app_elf_name));
        app_elf_name[sizeof(app_elf_name) - 1] = '\0';

        int rc = of_config_get("os", "ELF", app_elf_name, sizeof(app_elf_name));
        if (rc == OF_CONFIG_ERR_NOENT)
            rc = 0;
        if (rc < 0)
            return rc;
    }

    app_args_buf[0] = '\0';
    int rc = of_config_get("os", "ARGS", app_args_buf, sizeof(app_args_buf));
    if (rc == OF_CONFIG_ERR_NOENT)
        rc = 0;
    if (rc < 0)
        return rc;

    filesystem_init();

    rc = resolve_app_slot(app_elf_name, slot_out);
    if (rc < 0)
        return OF_CONFIG_ERR_NOENT;

    return build_app_argv(argc_out);
}

static int prepare_app_launch(uint32_t *slot_out, int *argc_out) {
    return prepare_app_launch_ex(NULL, slot_out, argc_out);
}

void os_main(void) {
    /* Clear IRQ callback state and hardware source masks before enabling
     * machine interrupts.  The bootloader deliberately keeps MIE disabled
     * while SDRAM .bss is being zeroed. */
    of_irq_init();

    /* Capture the known-good OS .text straight off the bootloader's verified
     * load, before any subsystem runs.  Every os_textguard_check() below then
     * brackets where the runtime .text corruption first appears. */
    os_textguard_baseline();

    /* Initialize all hardware */
    of_init();
    of_irq_enable_cpu();
    /* On a SW-audio-mixer build (of_mixer_use_sw, e.g. OS30) the OS owns a
     * permanent 1 kHz machine-timer tick that drives the CPU DAC pump
     * (sw_mixer_pump).  Without it audio is silent unless an app happens to
     * arm the timer via MIDI playback.  No-op on HW-mixer builds. */
    of_os_timer_boot_arm();
    os_textguard_check("after of_init");

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
    os_textguard_check("after syscall_init");

    of_term_puts("  Syscall init...... ");
    status_ok();

    of_term_puts("  Config init....... ");
    int config_rc = of_config_load(OS_CONFIG_SLOT_ID);
    if (config_rc < 0) {
        status_fail();
        of_term_printf("  os.ini rc=%d", config_rc);
        if (of_config_error_line() > 0)
            of_term_printf(" line=%d", of_config_error_line());
        of_term_putchar('\n');
        while (1) of_timer_delay_ms(1000);
    }
    status_ok();

    /* Brief pause to show banner */
    of_timer_delay_ms(800);
    os_textguard_check("after config_load");

    /* Load application ELF */
    of_term_puts("  Loading app....... ");

    elf_load_result_t app;
    uint32_t app_slot = APP_SLOT_ID;
    int app_argc = 1;
    os_textguard_check("before app load");
    int rc = prepare_app_launch(&app_slot, &app_argc);
    if (rc == 0)
        rc = load_app_with_retries(app_slot, &app);
    os_textguard_check("after app load");

    if (rc < 0) {
        status_fail();
        of_term_printf("  rc=%d\n", rc);
        of_term_printf("  \033[93mNo application found: %s\033[0m\n\n",
                       app_elf_name);
        of_term_puts("  Place .elf in data slot 3\n");
        of_term_puts("  or set [os] ELF in os.ini\n");
        of_term_puts("  and press START to retry.\n");

        while (1) {
            of_input_poll();
            if (of_input_is_pressed(0, OF_BTN_START)) {
                of_term_puts("\n  Retrying...");
                rc = prepare_app_launch(&app_slot, &app_argc);
                if (rc == 0)
                    rc = load_app_with_retries(app_slot, &app);
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

    uintptr_t audio_floor = (app.bss_end + APP_STACK_SIZE
                           + OF_TARGET_AUDIO_RESERVE_ALIGN - 1u)
                          & ~(uintptr_t)(OF_TARGET_AUDIO_RESERVE_ALIGN - 1u);
    if (of_mixer_memory_set_floor((uint32_t)audio_floor) < 0) {
        of_term_puts("  App memory........ ");
        status_fail();
        of_term_puts("  Application is too large for the audio reserve layout.\n");
        while (1) of_timer_delay_ms(1000);
    }

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

    app.stack_top = of_mixer_app_memory_top();
    if (syscall_set_app_stack_top(app.stack_top) < 0) {
        of_term_puts("  App memory........ ");
        status_fail();
        of_term_puts("  Not enough SDRAM for app stack after audio reservations.\n");
        while (1) of_timer_delay_ms(1000);
    }

    /* Populate capability descriptor after audio/SoundFont reservations
     * so heap_size and sample_base/sample_size describe the real layout. */
    caps_table_init(app.bss_end);

    of_term_puts("  Caps init......... ");
    status_ok();

    /* UART shares cart pins with Analogizer/SNAC. Only mirror the boot console
     * when neither feature owns the adapter pins, and only after the SoundFont
     * preload has finished probing/loading staged files. */
    {
        const of_analogizer_state_t *analogizer_state = of_analogizer_get_state();
        if (!analogizer_state->enabled &&
            analogizer_state->snac_type == SNAC_NONE)
            of_term_enable_uart_mirror();
    }

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

    /* Boot is done — every init/probe sector op above ran fail-fast.
     * From here on the target may treat transport timeouts as transient
     * (MiSTer: DS ERR_TIMEOUT while the user sits in the OSD is retried
     * instead of failing the app's read).  See hal/file.h. */
    of_file_boot_complete();

    /* Execute the app */
    os_textguard_check("before elf_exec");
    elf_exec(&app, app_argc, app_argv);

    /* Should never reach here */
    while (1) {}
}

#ifdef OF_TARGET_SUPPORTS_RELAUNCH
/* ======================================================================
 * In-OS application relaunch (Architecture A) — see kernel/launch.h.
 *
 * A launcher app (menu.elf) selects an instance and ecalls OF_BASE_FID_
 * RELAUNCH; the kernel tears down the outgoing app, re-points the file
 * system at the instance, re-reads its os.ini, and execs the chosen ELF —
 * all without resetting the FPGA.  MiSTer only (saves are write-through FAT
 * files; one bitstream runs every instance).
 * ====================================================================== */

#define RELAUNCH_INSTANCE_MAX 64

/* Captured out of the caller's memory before any teardown (the app's strings
 * disappear once the next ELF is loaded over it). */
static char relaunch_instance[RELAUNCH_INSTANCE_MAX];
static char relaunch_elf[APP_ELF_NAME_MAX];
static int  relaunch_elf_set;

/* Launcher to re-enter when the running app exit()s (os_set_menu). */
static char menu_instance[RELAUNCH_INSTANCE_MAX];
static char menu_elf[APP_ELF_NAME_MAX];
static int  menu_set;

static void launch_str_copy(char *dst, uint32_t dst_sz, const char *src) {
    uint32_t i = 0;
    if (src)
        for (; src[i] && i < dst_sz - 1u; i++)
            dst[i] = src[i];
    dst[i] = '\0';
}

void os_request_relaunch(const char *instance, const char *elf) {
    launch_str_copy(relaunch_instance, sizeof(relaunch_instance), instance);
    relaunch_elf_set = (elf && elf[0]) ? 1 : 0;
    launch_str_copy(relaunch_elf, sizeof(relaunch_elf),
                    relaunch_elf_set ? elf : "");
}

void os_set_menu(const char *instance, const char *elf) {
    launch_str_copy(menu_instance, sizeof(menu_instance), instance);
    launch_str_copy(menu_elf, sizeof(menu_elf), elf);
    menu_set = (menu_instance[0] || menu_elf[0]) ? 1 : 0;
}

int os_menu_pending(void) { return menu_set; }

void os_request_menu_relaunch(void) {
    os_request_relaunch(menu_instance, menu_elf);
}

/* menu_set intentionally PERSISTS across relaunches: once a launcher registers,
 * every subsequent game exit must return to it, so os_relaunch() must NOT clear
 * it.  The launcher re-arms it on each entry; it never exit()s itself. */

static void relaunch_fail(const char *what, int rc) {
    /* Reached only on an unrecoverable relaunch error -> halt forever.  Keep
     * interrupts disabled so a timer/vsync IRQ can't fire into the now-stale
     * trap frame while we spin (os_relaunch may have already re-enabled them). */
    __asm__ volatile("csrci mstatus, 0x8" ::: "memory");
    of_term_printf("  \033[91mrelaunch: %s (rc=%d)\033[0m\n", what, rc);
    while (1)
        of_timer_delay_ms(1000);
}

void os_relaunch(void) {
    /* We run in the M-mode ecall trap handler, on the OS runtime stack (never
     * the outgoing app's stack), with interrupts globally disabled — so the
     * teardown below races nothing.  We never mret: exactly like the cold-boot
     * elf_exec(), we jr straight into the next app once it is loaded. */

    /* Drop the outgoing app's IRQ callbacks and source masks. */
    of_irq_init();

    /* Quiesce the dynamic HW the app may have dirtied.  Per the of_init()
     * idempotency audit, re-running of_init() is safe ONLY after these — it
     * does not stop voices, release sample allocations, or close file handles
     * itself, so doing so here prevents leaked voices/allocs/FILs. */
    of_mixer_stop_all();
    of_mixer_free_samples();
    of_file_relaunch_reset();

    /* Re-point the file system at the selected instance (no-op when empty). */
    of_file_set_instance_root(relaunch_instance);

    /* Re-init the HAL to a clean state for the next app.  Interrupts stay
     * globally disabled through all of the fallible setup below: of_irq_enable_
     * cpu() is deferred to just before the hand-off so a relaunch_fail() halt
     * can never spin with IRQs live against the stale ecall trap frame. */
    of_init();

    logo_drawn = 0;
    boot_logo("\033[94m");           /* blue: launching */
    of_term_puts("  Relaunching...\n");

    /* Load the selected instance's os.ini (slot 2 resolves under the instance
     * root on MiSTer).  Missing/empty falls back to defaults. */
    (void)of_config_load(OS_CONFIG_SLOT_ID);

    /* Capture and clear the one-shot ELF override up front, so it never leaks
     * into a later relaunch regardless of which path we take from here. */
    const char *elf_override = relaunch_elf_set ? relaunch_elf : NULL;
    relaunch_elf_set = 0;

    elf_load_result_t app;
    uint32_t app_slot = APP_SLOT_ID;
    int app_argc = 1;
    int rc = prepare_app_launch_ex(elf_override, &app_slot, &app_argc);
    if (rc == 0)
        rc = load_app_with_retries(app_slot, &app);
    if (rc < 0)
        relaunch_fail(app_elf_name, rc);

    /* Per-app kernel state — mirrors the os_main() launch tail; keep in sync. */
    syscall_init(app.bss_end);
    services_table_init();

    uintptr_t audio_floor = (app.bss_end + APP_STACK_SIZE
                           + OF_TARGET_AUDIO_RESERVE_ALIGN - 1u)
                          & ~(uintptr_t)(OF_TARGET_AUDIO_RESERVE_ALIGN - 1u);
    if (of_mixer_memory_set_floor((uint32_t)audio_floor) < 0)
        relaunch_fail("app too large for audio reserve", 0);

    filesystem_init();
    bank_preload();

    app.stack_top = of_mixer_app_memory_top();
    if (syscall_set_app_stack_top(app.stack_top) < 0)
        relaunch_fail("not enough SDRAM for app stack", 0);

    caps_table_init(app.bss_end);

    /* Mirror the boot console to UART only when neither Analogizer nor SNAC
     * owns the shared cart pins — same gate os_main() applies at cold boot. */
    {
        const of_analogizer_state_t *az = of_analogizer_get_state();
        if (!az->enabled && az->snac_type == SNAC_NONE)
            of_term_enable_uart_mirror();
    }

    /* of_input_init() (run inside of_init) zeroed prev_buttons; prime it so the
     * new app's first poll sees correct press edges, not every held button. */
    of_input_poll();

    /* All fallible setup done — safe to take interrupts now, then hand off. */
    of_irq_enable_cpu();
    elf_exec(&app, app_argc, app_argv);
    while (1) {}  /* unreachable */
}
#endif /* OF_TARGET_SUPPORTS_RELAUNCH */
