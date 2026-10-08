#!/usr/bin/env python3
"""Exhaust the exact UV arithmetic domain before changing vertex storage.

This is a host binary32 reference check, not a CPU performance measurement.
The original expression depends only on d = signed16_U - 8 * uint16_tile.
Every possible d lies in one contiguous range of 589,816 integers. Testing
that range with both filter biases covers all source/tile combinations.
"""
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / 'docs/measurements/os30-fps25-20261005/phase10/packed-uv-math-v1'
SOURCE = r'''
#include <assert.h>
#include <fenv.h>
#include <float.h>
#include <limits.h>
#include <math.h>
#include <stdint.h>
#include <stdio.h>

int main(void) {
    _Static_assert(FLT_RADIX == 2 && FLT_MANT_DIG == 24, "binary32 required");
    _Static_assert(sizeof(float) == 4, "binary32 required");
    const int modes[] = { FE_TONEAREST, FE_DOWNWARD, FE_UPWARD, FE_TOWARDZERO };
    uint64_t checks = 0;
    int64_t minimum = INT64_MAX, maximum = INT64_MIN;
    for (unsigned mode = 0; mode < 4; ++mode) {
        assert(fesetround(modes[mode]) == 0);
        for (int d = -32768 - 65535 * 8; d <= 32767; ++d) {
            for (int filtered = 0; filtered <= 1; ++filtered) {
                /* Volatile intermediates retain the original binary32
                 * storage/conversion boundary, including directed rounding. */
                volatile float difference = (float)d;
                volatile float scaled = difference / 32.0f;
                volatile float texel = scaled + (filtered ? 0.5f : 0.0f);
                volatile float fixed = texel * 65536.0f;
                int64_t integer = (int64_t)d * 2048 + filtered * 32768;
                assert(integer >= INT32_MIN && integer <= INT32_MAX);
                assert((double)fixed == (double)integer);
                assert(lrintf(fixed) == integer);
                if (integer < minimum) minimum = integer;
                if (integer > maximum) maximum = integer;
                ++checks;
            }
        }
    }
    printf("{\"checks\":%llu,\"minimum\":%lld,\"maximum\":%lld,"
           "\"rounding_modes\":4,\"mismatches\":0}\n",
           (unsigned long long)checks, (long long)minimum, (long long)maximum);
    return 0;
}
'''


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    OUT.mkdir(exist_ok=False)
    source, binary = OUT / 'reference.c', OUT / 'reference'
    source.write_text(SOURCE)
    command = ['cc', '-std=c11', '-O2', '-frounding-math', '-ffp-contract=off',
               '-Wall', '-Wextra', str(source), '-lm', '-o', str(binary)]
    subprocess.run(command, check=True)
    result = subprocess.run([str(binary)], check=True, text=True, capture_output=True)
    record = json.loads(result.stdout)
    assert record['checks'] == 589816 * 2 * 4
    record.update(
        passed=True, compile_command=command, source_sha256=sha(source),
        binary_sha256=sha(binary), recipe_sha256=sha(Path(__file__)),
        scope='Exhaustive arithmetic reference; no packed vertex implementation '
              'or native CPU/rendering/performance proof.',
        proof='signed16_U and uint16_tile are exactly representable in binary32. '
              'Their difference is an integer of magnitude at most 557048. '
              'Division by 32, adding 0 or 0.5, and multiplication by 65536 '
              'all remain exact. Power-of-two products have no discarded '
              'significand bits under the retained FMA product truncation.')
    (OUT / 'result.json').write_text(json.dumps(record, indent=2) + '\n')
    print(result.stdout.strip())


if __name__ == '__main__':
    main()
