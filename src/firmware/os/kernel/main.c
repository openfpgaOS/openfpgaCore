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
#include "../hal/memtest.h"
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

#ifdef OF_TARGET_SUPPORTS_RELAUNCH
    /* MiSTer F-load app model (Phase 2): when the OSD/MGL has F-loaded an
     * app.elf into SDRAM staging (HPS_STATUS_ELF_LOADED), the loader must read
     * those STAGED bytes, not the os.ini "ELF=..." name looked up in the vhd.
     * targets/mister/file.c serves the staged ELF only through the fixed app-elf
     * slot (APP_SLOT_ID), so force the resolved slot there; load_app(APP_SLOT_ID)
     * then reads through elf_slot_read and of_file_size64(APP_SLOT_ID) returns
     * HPS_ELF_LEN.  of_file_app_from_staging() is '0' on MiSTer when no ELF is
     * staged, so the by-name/vhd resolution above stands unchanged then —
     * backward compatible (that is what keeps a non-F-loaded core, and step 1
     * of this bring-up, reading app.elf from the mounted vhd).  Gated to
     * OF_TARGET_SUPPORTS_RELAUNCH so Pocket/sim are wholly unaffected (the hook
     * has a no-op '0' stub there, matching of_file_instance_ready). */
    if (of_file_app_from_staging())
        *slot_out = APP_SLOT_ID;
#endif

    return build_app_argv(argc_out);
}

static int prepare_app_launch(uint32_t *slot_out, int *argc_out) {
    return prepare_app_launch_ex(NULL, slot_out, argc_out);
}

#ifdef OF_TARGET_SUPPORTS_RELAUNCH
/* Two-root derivation for the MiSTer F-load instance model (see
 * targets/mister/file.c).  The F-loaded os.ini carries [os] GAME + INSTANCE;
 * from them the OS builds a READ-ONLY shared root ("/<GAME>/common", for
 * app.elf / sound bank / wads) and a WRITABLE per-instance root
 * ("/<GAME>/<INSTANCE>", for config + saves), both on the mounted boot.vhd
 * (volume 0).  When either key is absent (a legacy image), BOTH roots are
 * cleared so every path stays root-relative — exactly the pre-instance
 * behavior.  MiSTer only: the of_file_set_*_root setters are no-ops on
 * Pocket/sim, and this block is not compiled there. */
#define OS_ROOT_NAME_MAX 48
#define OS_ROOT_PATH_MAX 112
static char os_root_game[OS_ROOT_NAME_MAX];
static char os_root_instance[OS_ROOT_NAME_MAX];
static char os_common_root[OS_ROOT_PATH_MAX];
static char os_instance_root[OS_ROOT_PATH_MAX];

static char *os_root_append(char *dst, char *end, const char *src) {
    while (*src && dst < end - 1)
        *dst++ = *src++;
    return dst;
}

static void os_apply_instance_roots(void) {
    os_root_game[0] = '\0';
    os_root_instance[0] = '\0';

    int rg = of_config_get("os", "GAME", os_root_game, sizeof(os_root_game));
    int ri = of_config_get("os", "INSTANCE",
                           os_root_instance, sizeof(os_root_instance));

    if (rg == 0 && ri == 0 && os_root_game[0] && os_root_instance[0]) {
        char *p, *e;

        /* common_root = "/" + GAME + "/common" */
        p = os_common_root;
        e = os_common_root + sizeof(os_common_root);
        *p++ = '/';
        p = os_root_append(p, e, os_root_game);
        p = os_root_append(p, e, "/common");
        *p = '\0';

        /* instance_root = "/" + GAME + "/" + INSTANCE */
        p = os_instance_root;
        e = os_instance_root + sizeof(os_instance_root);
        *p++ = '/';
        p = os_root_append(p, e, os_root_game);
        p = os_root_append(p, e, "/");
        p = os_root_append(p, e, os_root_instance);
        *p = '\0';

        of_file_set_common_root(os_common_root);
        of_file_set_instance_root(os_instance_root);
    } else {
        /* Legacy image / no keys: root-relative paths (regression-safe). */
        of_file_set_common_root("");
        of_file_set_instance_root("");
    }
}
#endif /* OF_TARGET_SUPPORTS_RELAUNCH */

void os_main(void) {
    /* Clear IRQ callback state and hardware source masks before enabling
     * machine interrupts.  The bootloader deliberately keeps MIE disabled
     * while SDRAM .bss is being zeroed. */
    of_irq_init();

    /* Capture the known-good OS .text straight off the bootloader's verified
     * load, before any subsystem runs.  Every os_textguard_check() below then
     * brackets where the runtime .text corruption first appears. */
    os_textguard_baseline();

    /* Bring up ONLY the contract-stable HAL surfaces (clocks, cache,
     * timer, video, terminal) — enough to print.  The rest of the
     * register map is not touched until the handshake and memtest
     * below have vouched for the core/os pairing. */
    of_init_early();

    /* Boot stage: red logo = OS initializing */
    boot_logo("\033[91m");  /* red */

    /* Firmware/bitstream contract handshake.  A mismatched pair used to
     * boot into undefined behaviour -- blank screen or a trap cascade
     * with a garbled console that took a night to diagnose.  Halt with a
     * readable banner instead; only term/video and the timer have been
     * initialized at this point, both stable across every contract
     * revision -- a mismatched core cannot wedge us before the banner. */
    {
        uint32_t hw_rev = HW_CONTRACT_REV;
        if (hw_rev == 0) {
            /* Pre-versioning bitstream: can't verify, warn and proceed so
             * firmware can still be iterated against existing cores. */
            of_term_puts("\n  \033[93mWARN: pre-versioning bitstream -- core/os"
                         " contract unverified\033[0m\n\n");
            of_timer_delay_ms(3000);
        } else if (hw_rev != OS_EXPECTED_HW_CONTRACT) {
            for (;;) {
                of_term_printf("\n  \033[91mCORE/OS CONTRACT MISMATCH\033[0m\n"
                               "  core bitstream: rev %u\n"
                               "  this os.bin   : rev %u\n"
                               "  Rebuild and deploy BOTH from one tree:\n"
                               "  make full VARIANT=<v>, then ship rbf + os.bin together.\n",
                               (unsigned)hw_rev,
                               (unsigned)OS_EXPECTED_HW_CONTRACT);
                of_timer_delay_ms(3000);
            }
        }
    }

    /* Physical-layer check the contract handshake can't make: controller
     * interleave / DQ-capture mismatches and timing-marginal cores all
     * present as scrambled memory.  Runs before the FS/app half of HAL
     * bring-up (of_init_late below) -- of_memtest_boot() writes to
     * windows that carry no traffic yet. */
    of_term_puts("  Memory check...... ");
    if (of_memtest_boot() == 0) {
        status_ok();
    } else {
        status_fail();
        for (;;) {
            of_term_puts("  \033[91mMemory integrity FAILED\033[0m -- core/os pairing or hardware fault.\n"
                         "  Rebuild and deploy rbf + os.bin from ONE tree (make full VARIANT=<v>).\n");
            of_timer_delay_ms(3000);
        }
    }

    /* Core/os pairing verified: bring up the rest of the HAL (input,
     * disk, file, mixer, save, analogizer, link). */
    of_init_late();
    of_irq_enable_cpu();
    /* On a SW-audio-mixer build (of_mixer_use_sw, e.g. OS30) the OS owns a
     * permanent 1 kHz machine-timer tick that drives the CPU DAC pump
     * (sw_mixer_pump).  Without it audio is silent unless an app happens to
     * arm the timer via MIDI playback.  No-op on HW-mixer builds. */
    of_os_timer_boot_arm();
    os_textguard_check("after of_init");

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

    /* MiSTer F-load instance model: the OSD/MGL F-loads a menu-picked .ini
     * into SDRAM staging, and that becomes this boot's os.ini (slot 2 is
     * served from staging while HPS_STATUS_INI_LOADED is set — see
     * targets/mister/file.c).  The mounted S0 .vhd is the shared family
     * (wads under /assets, /app.elf, /bank.ofsf); the pushed ini selects the
     * game.  Wait for that selection before loading the config and launching;
     * if one is already staged we fall straight through.  Gated on
     * OF_TARGET_SUPPORTS_RELAUNCH so Pocket/sim (no HPS_STATUS) are wholly
     * unaffected — of_file_instance_ready() is a no-op '1' there and this
     * block is not compiled at all. */
#ifdef OF_TARGET_SUPPORTS_RELAUNCH
    if (!of_file_instance_ready()) {
        /* Recolor the logo blue (awaiting input) without disturbing the
         * status lines already printed below it — same save/restore idiom the
         * blue "launching" redraw uses further down. */
        int saved_col, saved_row;
        of_term_get_pos(&saved_col, &saved_row);
        boot_logo("\033[94m");
        of_term_set_pos(saved_col, saved_row);
        of_term_puts("  Select an instance from the menu...\n");
        /* Bounded wait so a plain core load (or a misconfigured MGL that never
         * F-loads a nonzero-index ini) can't hang here forever: after the
         * timeout, fall through to of_config_load, which serves the mounted
         * vhd's own /os.ini (the family default) — a graceful default boot
         * instead of a silent hang.  An MGL F-loads its ini ~1-2 s after boot,
         * far inside this window, so the selected game always wins. */
        unsigned waited_ms = 0u;
        while (!of_file_instance_ready() && waited_ms < 8000u) {
            of_timer_delay_ms(100);
            waited_ms += 100u;
        }
    }
#endif

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

#ifdef OF_TARGET_SUPPORTS_RELAUNCH
    /* Derive the two roots (common_root / instance_root) from the loaded
     * os.ini's [os] GAME/INSTANCE before resolving app.elf or any save/config
     * path.  Absent keys clear both roots (legacy root-relative layout). */
    os_apply_instance_roots();
#endif

    /* Brief pause to show banner */
    of_timer_delay_ms(800);
    os_textguard_check("after config_load");

    /* Load application ELF */
#ifdef OF_TARGET_SUPPORTS_RELAUNCH
    /* Diagnostic (MiSTer only): did the MGL/RTL index-2 F-load actually stage an
     * app.elf?  elf_fload=1 len=N confirms the staged engine reached the SoC and
     * this boot reads it via APP_SLOT_ID -> elf_slot_read; elf_fload=0 means the
     * F-load never fired — look upstream (CONF_STR / MGL / RTL), not at this
     * routing.  Compiled out on Pocket/sim so their boot output is unchanged. */
    of_term_printf("  [elf_fload=%d len=%u]\n",
                   of_file_app_from_staging(),
                   (unsigned)of_file_app_staging_len());
#endif
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

    /* Re-derive both roots from the freshly loaded os.ini's [os] GAME/INSTANCE
     * so every read (app.elf/bank) and write (config/save) resolves against
     * the relaunched instance.  Absent keys clear them (legacy layout). */
    os_apply_instance_roots();

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
