#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "../../src/quake2/openfpga/trig_of.c"

static void check(float x)
{
	float expected[3] = {sinf(x), cosf(x), tanf(x)};
	for (unsigned repeat = 0; repeat < 3; ++repeat)
	{
		float actual[3] = {of_cached_sinf(x), of_cached_cosf(x), of_cached_tanf(x)};
		assert(memcmp(actual, expected, sizeof(actual)) == 0);
	}
}
int main(void)
{
	uint32_t state = 0x638191u;
	for (unsigned i = 0; i < 100000; ++i)
	{
		state = state * 1664525u + 1013904223u;
		uint32_t bits = state;
		if ((bits & 0x7f800000u) == 0x7f800000u) continue;
		float x;
		memcpy(&x, &bits, sizeof(x));
		check(x);
		check(0.0f); check(-0.0f);
		check((float)(int)(state % 721u) * 0.017453292519943295f);
	}
	puts("PASS: exact trig results, signed zero, arbitrary finite arguments, repeats and collisions");
	return 0;
}
