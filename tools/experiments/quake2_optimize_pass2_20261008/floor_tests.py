#!/usr/bin/env python3
"""Bitwise differential tests of the extracted UV helper, including RV32 execution."""
from pathlib import Path
import argparse
import hashlib
import json
import os
import subprocess
import time

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / 'docs/measurements/quake2-optimize-pass2-20261008/components/floor-v1'
BASE = OUT.parents[1] / 'work/base'
SDK = BASE / 'src/sdk'
QSIM = ROOT / 'docs/measurements/quake2-optimize-20261008/demo-screen-v2/qsim/qsim'

TEST = r'''#include <stdint.h>
#include <string.h>
#include <stdio.h>
#include <math.h>
#if defined(__SSE__)
#include <xmmintrin.h>
#endif
#include "helper.h"

static float (*volatile original_floor)(float) = floorf;
static uint32_t count, errors, oracle_errors, checksum;

/* Integer-only oracle, independent of both FCVT and libm. Nonfinite values
 * are checked against libm only, since NaN payload behavior belongs to it. */
static uint32_t finite_floor_bits(uint32_t bits) {
    uint32_t magnitude = bits & 0x7fffffffu;
    if (magnitude == 0 || magnitude >= 0x4b000000u) return bits;
    if (magnitude < 0x3f800000u) return bits >> 31 ? 0xbf800000u : 0;
    unsigned shift = 150u - (magnitude >> 23);
    uint32_t mask = (1u << shift) - 1;
    if (!(bits & mask)) return bits;
    return (bits + (bits >> 31 ? mask : 0)) & ~mask;
}

static void check(uint32_t bits) {
    float input, expected, actual;
    uint32_t e, a;
    memcpy(&input, &bits, 4);
    expected = original_floor(input);
    actual = ofgl_uv_floor(input);
    memcpy(&e, &expected, 4);
    memcpy(&a, &actual, 4);
    if (e != a) {
        if (errors < 8) printf("mismatch input=%08x expected=%08x actual=%08x\n", bits, e, a);
        errors++;
    }
    if ((bits & 0x7fffffffu) < 0x7f800000u && a != finite_floor_bits(bits))
        oracle_errors++;
    checksum = (checksum ^ a) * 16777619u;
    count++;
}

static void rounding_mode(unsigned mode) {
#if defined(__riscv)
    __asm__ volatile ("fsrm zero, %0" : : "r"(mode));
#else
    (void)mode;
#endif
}

int main(void) {
#if defined(__SSE__)
    /* GCC's host fast-math startup enables DAZ/FTZ. RISC-V F retains
     * subnormals, so restore IEEE host input handling for the bit oracle. */
    _mm_setcsr(_mm_getcsr() & ~0x8040u);
#endif
    static const uint32_t mantissas[] = {0, 1, 2, 3, 0x1fffff, 0x200000,
        0x200001, 0x3fffff, 0x400000, 0x400001, 0x7ffffc, 0x7ffffd, 0x7ffffe, 0x7fffff};
    for (unsigned mode = 0; mode < 5; mode++) {
        rounding_mode(mode);
        /* Both signs around every exponent transition, infinities, and quiet
         * and signaling NaNs with varied payloads, including signed zero. */
        for (unsigned exponent = 0; exponent < 256; exponent++)
            for (unsigned m = 0; m < sizeof(mantissas)/sizeof(mantissas[0]); m++)
                for (unsigned sign = 0; sign < 2; sign++)
                    check((sign << 31) | (exponent << 23) | mantissas[m]);
        /* Every integer around common UV coordinates and its immediate float
         * neighbours catches negative-fraction and integer-boundary cases. */
        for (int i = -8192; i <= 8192; i++) {
            float integer = (float)i; uint32_t bits;
            memcpy(&bits, &integer, 4);
            check(bits); check(bits - 1); check(bits + 1);
        }
        uint32_t random = 0x1a37f519u;
        for (unsigned i = 0; i < 262144; i++) {
            random ^= random << 13; random ^= random >> 17; random ^= random << 5;
            check(random);
        }
    }
    rounding_mode(0);
    printf("FLOOR_TEST cases=%u errors=%u oracle_errors=%u checksum=%08x\n",
        count, errors, oracle_errors, checksum);
    return errors || oracle_errors ? 1 : 0;
}
'''

def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def run(cmd, dest):
    started = time.monotonic()
    env = dict(os.environ)
    # The sandbox uses ptrace, which LeakSanitizer cannot inspect; this
    # fixed-array math unit has no heap allocations. Keep ASan and UBSan.
    if dest.startswith('host-sanitize'):
        env['ASAN_OPTIONS'] = 'detect_leaks=0'
    with (OUT / (dest + '.log')).open('w') as log:
        p = subprocess.run(cmd, env=env, stdout=log, stderr=subprocess.STDOUT)
    result = dict(command=cmd, returncode=p.returncode, seconds=time.monotonic()-started)
    (OUT / (dest + '.json')).write_text(json.dumps(result, indent=2) + '\n')
    assert p.returncode == 0, result
    return result

def main():
    ap = argparse.ArgumentParser(); ap.add_argument('action', choices=['prepare', 'host', 'target'])
    a = ap.parse_args()
    if a.action == 'prepare':
        assert not (OUT/'floor_test.c').exists()
        (OUT/'floor_test.c').write_text(TEST)
        print(OUT/'floor_test.c')
        return
    if a.action == 'host':
        for name, flags in [('host', ['-O3', '-ffast-math']),
                            ('host-sanitize', ['-O1', '-fsanitize=address,undefined', '-fno-omit-frame-pointer'])]:
            run(['cc', '-std=c11', *flags, '-fno-builtin-floorf', str(OUT/'floor_test.c'),
                 '-lm', '-o', str(OUT/name)], name+'-build')
            run([str(OUT/name)], name+'-run')
        return
    compiler = ['docker', 'run', '--rm', '--network', 'none', '--user', f'{os.getuid()}:{os.getgid()}',
                '-v', f'{ROOT}:{ROOT}', '-w', str(OUT), '-e', 'CPATH=', '--entrypoint',
                'riscv64-unknown-elf-gcc', 'openfpgaos-firmware']
    cmd = [*compiler, '-march=rv32imafc_zicsr', '-mabi=ilp32f', '-O3', '-flto=auto',
        '-ffast-math', '-fno-math-errno', '-fno-trapping-math', '-fsingle-precision-constant',
        '-fno-builtin-floorf', '-fno-tree-loop-distribute-patterns', '-ffunction-sections',
        '-fdata-sections', '-falign-functions=64', '-falign-loops=8', '-nostdinc',
        '-isystem', str(SDK/'musl/include'), '-nostdlib', '-static', '-T', str(SDK/'app.ld'),
        '-L'+str(SDK/'musl/lib'), '-Wl,--gc-sections', '-Wl,--no-warn-rwx-segments',
        '-o', str(OUT/'floor-target.elf'),
        *[str(SDK/'musl/lib'/p) for p in ['crt1.o','crti.o','crtn.o']],
        str(OUT/'floor_test.c'), '-Wl,--start-group', '-lc', '-lm', '-lgcc', '-Wl,--end-group']
    run(cmd, 'target-build')
    dest = OUT/'target-run'; assert not dest.exists()
    run([str(QSIM), str(OUT/'floor-target.elf'), '--out', str(dest), '--max-insns', '1e9',
         '--gpu-sched', 'eager', '--mem', 'flat', '--no-normalize-build-stamp'], 'target-run')
    summary = json.loads((dest/'summary.json').read_text())
    assert summary['stop_reason'] == 'guest exit(0)', summary
    console = (dest/'console.log').read_text()
    assert 'errors=0 oracle_errors=0' in console, console
    (OUT/'tests.json').write_text(json.dumps(dict(passed=True,
        helper_sha256=sha(OUT/'helper.h'), test_source_sha256=sha(OUT/'floor_test.c'),
        target_elf_sha256=sha(OUT/'floor-target.elf'), qsim_sha256=sha(QSIM),
        console=console, target_rounding_modes=['RNE','RTZ','RDN','RUP','RMM'],
        reference='Actual bundled musl floorf via volatile function pointer and independent finite bit oracle',
        caveat='Functionally executes the RV32 instructions; this test is not a CPU timing measurement.'), indent=2)+'\n')
    print((OUT/'tests.json').read_text())

if __name__ == '__main__':
    main()
