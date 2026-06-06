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

int main(int argc, char **argv) {
    if (argc != 2) {
        fprintf(stderr, "usage: %s <output_dir>\n", argv[0]);
        return 1;
    }
    const char *dir = argv[1];

    char path[1024];
    snprintf(path, sizeof(path), "%s/os.ini", dir);
    FILE *f = fopen(path, "wb");
    if (!f) { perror(path); return 1; }
    fwrite(OS_INI_TEXT, 1, sizeof(OS_INI_TEXT) - 1, f);  /* no NUL */
    fclose(f);

    write_pattern(dir, "app.elf",    OF_FIX_SEED_APP,    APP_ELF_SIZE);
    write_pattern(dir, "data1.bin",  OF_FIX_SEED_ASSET1, DATA1_SIZE);
    write_pattern(dir, "level2.dat", OF_FIX_SEED_ASSET2, LEVEL2_SIZE);

    printf("genfix: wrote os.ini, app.elf (%u), data1.bin (%u), level2.dat (%u)\n",
           APP_ELF_SIZE, DATA1_SIZE, LEVEL2_SIZE);
    return 0;
}
