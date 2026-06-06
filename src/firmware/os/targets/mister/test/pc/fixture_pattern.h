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

#endif /* OF_FIXTURE_PATTERN_H */
