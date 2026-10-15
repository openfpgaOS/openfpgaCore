/* Exact-value trig memo for the single-threaded application. Musl's float
 * trig internally uses software double; static entity angles and repeated
 * renderer transforms otherwise recalculate those results many times. */
#include <math.h>
#include <stdint.h>
#include <string.h>
#undef sinf
#undef cosf
#undef tanf

typedef struct {
	uint32_t argument, valid;
	float value[3];
} of_trig_entry_t;
static of_trig_entry_t of_trig_cache[512];

static inline of_trig_entry_t *of_trig_entry(float x, uint32_t *bits)
{
	memcpy(bits, &x, sizeof(*bits));
	/* Fold every exponent/sign bit before multiplication: opposite-sign
	 * cull/draw angles and powers of two must not all collide. */
	uint32_t hash = (*bits ^ (*bits >> 16)) * 0x9e3779b1u;
	unsigned slot = hash >> 23;
	of_trig_entry_t *entry = &of_trig_cache[slot];
	if (entry->argument != *bits)
	{
		entry->argument = *bits;
		entry->valid = 0;
	}
	return entry;
}

#define OF_TRIG_FUNCTION(name, index) \
float of_cached_##name(float x) \
{ \
	uint32_t bits; \
	of_trig_entry_t *entry = of_trig_entry(x, &bits); \
	/* Nonfinite calls retain libm's exceptional-value behavior. */ \
	if ((bits & 0x7f800000u) == 0x7f800000u) return name(x); \
	if (!(entry->valid & (1u << index))) \
	{ \
		entry->value[index] = name(x); \
		entry->valid |= 1u << index; \
	} \
	return entry->value[index]; \
}
OF_TRIG_FUNCTION(sinf, 0)
OF_TRIG_FUNCTION(cosf, 1)
OF_TRIG_FUNCTION(tanf, 2)
