/*
 * =======================================================================
 *
 * of_mathf.h - single-precision libm routing for openfpgaOS (rv32imafc)
 *
 * The softcore has the RISC-V F extension (hardware *single*-precision
 * float) but NOT D, so every `double` libm call (sqrt, sin, floor, ...)
 * is a software-emulated softfloat routine -- tens to ~150 cycles each,
 * plus a float->double promotion (__extendsfdf2) on the argument and a
 * double->float truncation (__truncdfsf2) on the result at every call
 * boundary. `-fsingle-precision-constant` already keeps FP *literals*
 * single; this header is the complementary half: it routes the double
 * libm *functions* to their single-precision variants for the whole
 * codebase.
 *
 *   sqrt/fabs/floor/ceil/round/trunc -> hardware single ops (fsqrt.s,
 *       fabs.s, fcvt) under -ffast-math/-fno-math-errno: large, clean win.
 *   sin/cos/tan/...                   -> musl's *f still uses double
 *       internally, but the call-boundary promote/truncate pair is gone:
 *       partial win, never a loss.
 *
 * Force-included via the Makefile (-include) so it lands at the top of
 * every translation unit, AHEAD of each file's own #include <math.h>.
 * We pull <math.h> here FIRST so its real prototypes are declared with
 * the correct (double) signatures, THEN install the macros; any later
 * #include <math.h> in a TU is a guarded no-op, so the macros stay in
 * force without ever corrupting a declaration.
 *
 * The macros are function-LIKE (only `name(` is rewritten), so a bare
 * identifier -- a struct member, a variable named `exp`, taking `&sqrt`
 * -- is untouched. The one dead prototype that did collide
 * (common/shared/shared.c `double sqrt(double x);`) is guarded out under
 * YQ2_OPENFPGA.
 *
 * =======================================================================
 */

#ifndef OF_MATHF_H
#define OF_MATHF_H

#ifdef YQ2_OPENFPGA

#include <math.h>

/* #undef first: musl may already define some of these as builtin macros
 * (e.g. fabs). Re-pointing them at the *f variant is exactly what we want
 * and avoids a -Wmacro-redefined diagnostic. */

#undef  sqrt
#define sqrt(x)        sqrtf(x)
#undef  fabs
#define fabs(x)        fabsf(x)
#undef  floor
#define floor(x)       floorf(x)
#undef  ceil
#define ceil(x)        ceilf(x)
#undef  round
#define round(x)       roundf(x)
#undef  trunc
#define trunc(x)       truncf(x)

#undef  sin
#define sin(x)         sinf(x)
#undef  cos
#define cos(x)         cosf(x)
#undef  tan
#define tan(x)         tanf(x)
#undef  asin
#define asin(x)        asinf(x)
#undef  acos
#define acos(x)        acosf(x)
#undef  atan
#define atan(x)        atanf(x)
#undef  atan2
#define atan2(y, x)    atan2f(y, x)

#undef  pow
#define pow(x, y)      powf(x, y)
#undef  exp
#define exp(x)         expf(x)
#undef  log
#define log(x)         logf(x)
#undef  log10
#define log10(x)       log10f(x)
#undef  fmod
#define fmod(x, y)     fmodf(x, y)
#undef  hypot
#define hypot(x, y)    hypotf(x, y)
#undef  ldexp
#define ldexp(x, n)    ldexpf(x, n)
#undef  frexp
#define frexp(x, e)    frexpf(x, e)

#endif /* YQ2_OPENFPGA */

#endif /* OF_MATHF_H */
