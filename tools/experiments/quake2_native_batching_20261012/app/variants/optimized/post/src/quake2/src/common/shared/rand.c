/*
 * KISS PRNG (c) 2011 Shinobu
 *
 * This file was optained from zuttobenkyou.wordpress.com
 * and modified by the Yamagi Quake II developers.
 *
 * LICENSE: Public domain
 *
 * =======================================================================
 *
 * KISS PRNG, as devised by Dr. George Marsaglia
 *
 * =======================================================================
 */

#include <stdint.h>

#ifdef YQ2_OPENFPGA
/* The upstream 2M-entry (16 MB!) lag table eats a third of the app's
 * SDRAM window. 128K entries (1 MB) still gives an astronomically long
 * MWC period — far beyond anything gameplay can consume — and seeds
 * 16x faster at boot. */
#define QSIZE 0x20000
#else
#define QSIZE 0x200000
#endif
#define CNG (cng = 6906969069ULL * cng + 13579)
/* xs was static-zero-initialized and was never assigned a nonzero seed.
 * Its xorshift recurrence therefore returned zero on every call, including
 * every reseed. Omit that dead component while preserving the complete MWC
 * and CNG state (and thus the exact sequence across future randk_seed calls). */
#define KISS (B64MWC() + CNG)

static uint64_t QARY[QSIZE];
static int j;
static uint64_t carry;
static uint64_t cng;

static uint64_t
B64MWC(void)
{
	uint64_t t, x;

	j = (j + 1) & (QSIZE - 1);
	x = QARY[j];
	t = (x << 28) + carry;
	carry = (x >> 36) - (t < x);
	return QARY[j] = t - x;
}

/* Preserve the original RV32 two's-complement result for INT32_MIN
 * without invoking signed-negation overflow. The bit copy also avoids an
 * implementation-defined out-of-range unsigned-to-signed conversion. */
static inline int
randk_abs(uint32_t value)
{
    uint32_t magnitude = (value >> 31) ? 0u - value : value;
    int32_t result;
    __builtin_memcpy(&result, &magnitude, sizeof result);
    return result;
}

/*
 * Generate a pseudorandom
 * integer >0.
 */
/* Preserve the existing call boundary: inlining the smaller generator under
 * fast-math can unroll particle loops and change their float reassociation. */
int __attribute__((noinline))
randk(void)
{
	return randk_abs((uint32_t)KISS);
}

/* Consume exactly count outputs while retaining generator state in registers.
 * Callers must reserve their output work before calling: unused values cannot
 * be discarded without changing the global gameplay random stream. */
void __attribute__((noinline))
randk_batch(int *out, unsigned count)
{
    int index = j;
    uint64_t local_carry = carry;
    uint64_t local_cng = cng;
    for (unsigned n = 0; n < count; ++n)
    {
        index = (index + 1) & (QSIZE - 1);
        uint64_t x = QARY[index];
        uint64_t t = (x << 28) + local_carry;
        local_carry = (x >> 36) - (t < x);
        QARY[index] = t - x;
        local_cng = 6906969069ULL * local_cng + 13579;
        out[n] = randk_abs((uint32_t)(QARY[index] + local_cng));
    }
    j = index;
    carry = local_carry;
    cng = local_cng;
}

/*
 * Generate a pseudorandom
 * signed float between
 * 0 and 1.
 */
float
frandk(void)
{
	return (randk()&32767)* (1.0/32767);
}

/* Generate a pseudorandom
 * float between -1 and 1.
 */
float
crandk(void)
{
	return (randk()&32767)* (2.0/32767) - 1;
}

/*
 * Seeds the PRNG
 */
void
randk_seed(void)
{
	uint64_t i;

	/* Seed QARY[] with CNG (the original XS component was always zero): */
	for (i = 0; i < QSIZE; i++)
	{
		QARY[i] = CNG;
	}

	/* Run through several rounds
	   to warm up the state */
	for (i = 0; i < 256; i++)
	{
		randk();
	}
}

