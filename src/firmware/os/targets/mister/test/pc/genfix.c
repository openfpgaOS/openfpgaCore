//------------------------------------------------------------------------------
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileType: SOURCE
// SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
//------------------------------------------------------------------------------

/*
 * Fixture generator for the MiSTer FAT-stack host tests.
 *
 * Writes the deterministic payload files that get baked into the test .vhd:
 *
 *   <dir>/os.ini          small text config            (slot 2)
 *   <dir>/app.elf         137 KB pseudo-random pattern (slot 3, multi-cluster)
 *   <dir>/data1.bin       6053-byte pattern            (dynamic asset slot)
 *   <dir>/level2.dat      1031-byte pattern            (dynamic asset slot)
 *
 * plus the multi-vhd (S0 family / S1 instance / S2 borrow) fixture set,
 * prefixed s0_/s1_/s2_ — the harness Makefile bakes those into three
 * separate images (s0.vhd/s1.vhd/s2.vhd):
 *
 *   s0_os.ini, s0_app.elf          family fallbacks (searched last)
 *   s0_shared_lib.dat              name ALSO on S1 (must be shadowed)
 *   s0_family.dat, s0_family01..14 library files (overflow the dyn table)
 *   s1_os.ini                      instance config (must shadow S0's)
 *   s1_inst_only.dat, s1_shared_lib.dat
 *   s2_borrow.dat                  borrow-library unique file
 *
 * The patterns come from fixture_pattern.h, so test_main.c regenerates the
 * exact expected bytes at verify time.  Sizes are deliberately non-round
 * (prime-ish) so unaligned / partial-cluster reads have something to trip on.
 */

#include "fixture_pattern.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* Keep these in sync with test_main.c. */
#define APP_ELF_SIZE   140000u   /* > 100 KB, spans many FAT clusters */
#define DATA1_SIZE     6053u
#define LEVEL2_SIZE    1031u

static const char OS_INI_TEXT[] =
    "[os]\n"
    "ELF=app.elf\n"
    "name=fat-stack-host-test\n"
    "; deterministic fixture for targets/mister/test/pc\n";

static void write_pattern(const char *dir, const char *name,
                          uint32_t seed, uint32_t size) {
    char path[1024];
    snprintf(path, sizeof(path), "%s/%s", dir, name);
    FILE *f = fopen(path, "wb");
    if (!f) { perror(path); exit(1); }

    uint8_t buf[8192];
    uint32_t done = 0;
    while (done < size) {
        uint32_t n = size - done;
        if (n > sizeof(buf)) n = sizeof(buf);
        for (uint32_t i = 0; i < n; i++)
            buf[i] = of_fix_byte(seed, done + i);
        if (fwrite(buf, 1, n, f) != n) { perror("fwrite"); exit(1); }
        done += n;
    }
    fclose(f);
}

static void write_text(const char *dir, const char *name,
                       const char *text, size_t len) {
    char path[1024];
    snprintf(path, sizeof(path), "%s/%s", dir, name);
    FILE *f = fopen(path, "wb");
    if (!f) { perror(path); exit(1); }
    if (fwrite(text, 1, len, f) != len) { perror("fwrite"); exit(1); }
    fclose(f);
}

int main(int argc, char **argv) {
    if (argc != 2) {
        fprintf(stderr, "usage: %s <output_dir>\n", argv[0]);
        return 1;
    }
    const char *dir = argv[1];

    /* Legacy single-image fixture set. */
    write_text(dir, "os.ini", OS_INI_TEXT, sizeof(OS_INI_TEXT) - 1); /* no NUL */
    write_pattern(dir, "app.elf",    OF_FIX_SEED_APP,    APP_ELF_SIZE);
    write_pattern(dir, "data1.bin",  OF_FIX_SEED_ASSET1, DATA1_SIZE);
    write_pattern(dir, "level2.dat", OF_FIX_SEED_ASSET2, LEVEL2_SIZE);

    /* Multi-vhd fixture set (see file header). */
    write_text(dir, "s0_os.ini", OF_FIX_S0_OSINI, sizeof(OF_FIX_S0_OSINI) - 1);
    write_text(dir, "s1_os.ini", OF_FIX_S1_OSINI, sizeof(OF_FIX_S1_OSINI) - 1);
    write_pattern(dir, "s0_app.elf",        OF_FIX_SEED_S0_APP,
                  OF_FIX_S0_APP_SIZE);
    write_pattern(dir, "s0_shared_lib.dat", OF_FIX_SEED_SHARED_S0,
                  OF_FIX_SHARED_S0_SIZE);
    write_pattern(dir, "s1_shared_lib.dat", OF_FIX_SEED_SHARED_S1,
                  OF_FIX_SHARED_S1_SIZE);
    write_pattern(dir, "s0_family.dat",     OF_FIX_SEED_FAMILY,
                  OF_FIX_FAMILY_SIZE);
    write_pattern(dir, "s1_inst_only.dat",  OF_FIX_SEED_INST,
                  OF_FIX_INST_SIZE);
    write_pattern(dir, "s2_borrow.dat",     OF_FIX_SEED_BORROW,
                  OF_FIX_BORROW_SIZE);
    for (uint32_t i = 1; i <= OF_FIX_FAMILY_N_COUNT; i++) {
        char name[32];
        snprintf(name, sizeof(name), "s0_family%02u.dat", i);
        write_pattern(dir, name, OF_FIX_SEED_FAMILY_N + i,
                      OF_FIX_FAMILY_N_SIZE);
    }

    /* CD-music streaming fixture: >=2 MB wad (legacy image + S2 /assets/)
     * and the S1 dyn-table fillers that push it into the overflow registry
     * on the multi-vhd mounts (see fixture_pattern.h). */
    write_pattern(dir, "doommus.wad", OF_FIX_SEED_WAD, OF_FIX_WAD_SIZE);
    for (uint32_t i = 1; i <= OF_FIX_S1_FILL_COUNT; i++) {
        char name[32];
        snprintf(name, sizeof(name), "s1_fill%02u.dat", i);
        write_pattern(dir, name, OF_FIX_SEED_S1_FILL + i,
                      OF_FIX_S1_FILL_SIZE);
    }

    /* Preallocated (zero-filled, full-capacity) per-game settings file,
     * baked as /config/doom.cfg into the legacy, S0 and S1 images. */
    {
        char path[1024];
        snprintf(path, sizeof(path), "%s/cfg_prealloc.bin", dir);
        FILE *f = fopen(path, "wb");
        if (!f) { perror(path); exit(1); }
        static const uint8_t zeros[8192];
        uint32_t done = 0;
        while (done < OF_FIX_CFG_PREALLOC_SIZE) {
            uint32_t n = OF_FIX_CFG_PREALLOC_SIZE - done;
            if (n > sizeof(zeros)) n = sizeof(zeros);
            if (fwrite(zeros, 1, n, f) != n) { perror("fwrite"); exit(1); }
            done += n;
        }
        fclose(f);
    }

    printf("genfix: wrote legacy set (app.elf %u, data1.bin %u, level2.dat %u)"
           " + multi-vhd set (%u family files)\n",
           APP_ELF_SIZE, DATA1_SIZE, LEVEL2_SIZE, OF_FIX_FAMILY_N_COUNT);
    return 0;
}
