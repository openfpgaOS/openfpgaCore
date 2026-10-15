/* Compare public calls and every retained state byte with the frozen original,
 * including use before seeding, lag-table wraps and repeated seed calls. */
#include <assert.h>
#include <stdio.h>
#include <string.h>
#define randk ref_randk
#define frandk ref_frandk
#define crandk ref_crandk
#define randk_seed ref_randk_seed
#define QARY ref_QARY
#define j ref_j
#define carry ref_carry
#define xs ref_xs
#define cng ref_cng
#define B64MWC ref_B64MWC
#include "quake2_rng_reference.inc"
#undef randk
#undef frandk
#undef crandk
#undef randk_seed
#undef QARY
#undef j
#undef carry
#undef xs
#undef cng
#undef B64MWC
#undef CNG
#undef XS
#undef KISS
#undef QSIZE
#include "../../src/quake2/src/common/shared/rand.c"

static void state_exact(void)
{
    assert(j == ref_j && carry == ref_carry && cng == ref_cng);
    assert(ref_xs == 0);
    assert(!memcmp(QARY, ref_QARY, sizeof(QARY)));
}
int main(void)
{
    const uint32_t values[] = {0, 1, 0x7fffffffu, 0x80000000u, 0xffffffffu};
    const uint32_t magnitudes[] = {0, 1, 0x7fffffffu, 0x80000000u, 1};
    for (unsigned k = 0; k < sizeof(values) / sizeof(values[0]); ++k)
    {
        int32_t value = randk_abs(values[k]);
        uint32_t bits;
        memcpy(&bits, &value, sizeof bits);
        assert(bits == magnitudes[k]);
    }
    unsigned calls = 0;
    static int block[131073];
    const unsigned sizes[] = {0, 1, 9, 1024, 131073};
    for (unsigned k = 0; k < sizeof(sizes) / sizeof(sizes[0]); ++k)
    {
        randk_batch(block, sizes[k]);
        for (unsigned n = 0; n < sizes[k]; ++n)
            assert(block[n] == ref_randk());
        calls += sizes[k];
        state_exact();
    }
    for (unsigned round = 0; round < 16; ++round)
    {
        if (round) { randk_seed(); ref_randk_seed(); }
        state_exact();
        for (unsigned i = 0; i < 200000 + round * 317; ++i)
        {
            if (i % 11 == 0)
            {
                int values[9];
                randk_batch(values, 0);
                randk_batch(values, 9);
                for (unsigned n = 0; n < 9; ++n)
                    assert(values[n] == ref_randk());
                calls += 8;
            }
            else if (i % 5 == 1 || i % 5 == 3)
            {
                float a = i % 5 == 1 ? frandk() : crandk();
                float b = i % 5 == 1 ? ref_frandk() : ref_crandk();
                assert(!memcmp(&a, &b, sizeof(a)));
            }
            else assert(randk() == ref_randk());
            ++calls;
        }
        state_exact();
    }
    printf("PASS: %u exact mixed RNG outputs, initial state, full lag-state equality, wraps and15 reseeds\n", calls);
}
