//------------------------------------------------------------------------------
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileType: SOURCE
// SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
//------------------------------------------------------------------------------

/*
 * Deterministic fixture byte patterns, shared by the fixture generator
 * (genfix.c) and the verifier (test_main.c) so reads can be checked
 * byte-exactly without keeping a separate golden copy.
 *
 * of_fix_byte(seed, off) is a cheap reversible hash of the offset, salted by
 * a per-file seed.  It is NOT cryptographic — just well-mixed enough that an
 * off-by-one / wrong-cluster read is overwhelmingly likely to mismatch.
 */

#ifndef OF_FIXTURE_PATTERN_H
#define OF_FIXTURE_PATTERN_H

#include <stdint.h>

static inline uint8_t of_fix_byte(uint32_t seed, uint32_t off) {
    uint32_t x = off * 2654435761u + seed * 40503u + 0x9E3779B9u;
    x ^= x >> 15;
    x *= 0x2C1B3C6Du;
    x ^= x >> 12;
    x *= 0x297A2D39u;
    x ^= x >> 15;
    return (uint8_t)(x & 0xFFu);
}

/* Per-fixture seeds (arbitrary but fixed). */
#define OF_FIX_SEED_APP    0x00A99001u  /* /app.elf */
#define OF_FIX_SEED_ASSET1 0x00B12345u  /* /assets/data1.bin */
#define OF_FIX_SEED_ASSET2 0x00C0FFEEu  /* /assets/level2.dat */

/* Multi-vhd (S0/S1/S2) fixtures.  Sizes live here so genfix.c and
 * test_main.c can never drift. */
#define OF_FIX_SEED_S0_APP     0x0D00D001u  /* s0:/app.elf (family ELF)   */
#define OF_FIX_SEED_SHARED_S0  0x05A5A5A5u  /* s0:/assets/shared_lib.dat  */
#define OF_FIX_SEED_SHARED_S1  0x015B15B1u  /* s1:/assets/shared_lib.dat  */
#define OF_FIX_SEED_FAMILY     0x0FA111E5u  /* s0:/assets/family.dat      */
#define OF_FIX_SEED_FAMILY_N   0x0FA20000u  /* + index: family01..14.dat  */
#define OF_FIX_SEED_INST       0x01257AC1u  /* s1:/assets/inst_only.dat   */
#define OF_FIX_SEED_BORROW     0x0B0880D0u  /* s2:/assets/borrow.dat      */

#define OF_FIX_S0_APP_SIZE     90017u
#define OF_FIX_SHARED_S0_SIZE  4099u
#define OF_FIX_SHARED_S1_SIZE  2061u   /* differs from S0's copy on purpose */
#define OF_FIX_FAMILY_SIZE     3007u
#define OF_FIX_FAMILY_N_SIZE   777u
#define OF_FIX_FAMILY_N_COUNT  14u     /* family01.dat .. family14.dat */
#define OF_FIX_INST_SIZE       3001u
#define OF_FIX_BORROW_SIZE     5003u

/* CD-music streaming fixture (the DOOMMUS.WAD field scenario): a large
 * file at /assets/ on the S2 borrow image, streamed in 4 KB chunks for
 * hundreds of iterations while saves/config/FIL-cache churn interleave.
 * Also baked into the legacy image so the same stream runs single-volume. */
#define OF_FIX_SEED_WAD        0x0DA7A57Bu  /* /assets/doommus.wad */
#define OF_FIX_WAD_SIZE        (2u * 1024u * 1024u + 1337u)  /* >=2 MB, non-round */

/* S1 dyn-table fillers: 11 extra instance files so the 16-entry dynamic
 * slot table is FULL by the time S2's /assets/ is enumerated (S1 owns
 * inst_only + shared_lib + 11 fillers + shared.cfg + duke3d.cfg + doom.cfg
 * = 16) — forcing doommus.wad (and every S2/S0 asset) to resolve through
 * the overflow registry (ids 32+), exactly like the field setup. */
#define OF_FIX_SEED_S1_FILL    0x0F111000u  /* + index: s1:/assets/fillNN.dat */
#define OF_FIX_S1_FILL_SIZE    601u
#define OF_FIX_S1_FILL_COUNT   11u

/* Preallocated per-game settings file (/config/doom.cfg on the legacy, S0
 * and S1 images): 256 KB of zeros, the image-builder contract for every
 * nonvolatile file (written in place at runtime, never created/extended). */
#define OF_FIX_CFG_PREALLOC_SIZE (256u * 1024u)

/* os.ini texts (byte-exact verification targets). */
#define OF_FIX_S0_OSINI \
    "[os]\n"                                     \
    "ELF=app.elf\n"                              \
    "name=family-library\n"                      \
    "; S0 fallback os.ini for the multi-vhd tests\n"
#define OF_FIX_S1_OSINI \
    "[os]\n"                                     \
    "ELF=app.elf\n"                              \
    "name=instance-volume\n"                     \
    "ARGS=-instance\n"                           \
    "; S1 instance os.ini must shadow S0's\n"

#endif /* OF_FIXTURE_PATTERN_H */
