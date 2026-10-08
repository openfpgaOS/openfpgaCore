#!/usr/bin/env python3
"""Compare real strided lightmap updates with original tightly packed uploads."""
from pathlib import Path
import hashlib
import json
import os
import subprocess

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / 'docs/measurements/quake2-comprehensive-20261008'
WORK = OUT / 'work/lightmap_rect'
CHECKS = OUT / 'components/lightmap-rect-v1/checks'
CHECKS.mkdir(exist_ok=False)
old = (OUT / 'work/base/src/quake2/openfpga/of_gl1.c').read_text()
start = old.index('static void\nofgl_lm_store(')
end = old.index('\nstatic inline uint16_t\nofgl_lm_sample', start)
reference = old[start:end].replace('ofgl_lm_store(', 'reference_store(', 1)
source = '''#define main renderer_regression_main
#include "quake2_renderer_test.c"
#undef main
#include <limits.h>
''' + reference + r'''
static uint32_t seed = 0x36ac8319;
static uint32_t next32(void) { seed ^= seed << 13; seed ^= seed >> 17; seed ^= seed << 5; return seed; }
static uint16_t reference_page[128 * 128], candidate_page[128 * 128];
static uint32_t input[160 * 128], packed[128 * 128];
static void select_page(uint16_t *page, uint32_t serial) {
    ofgl_lm[5] = page; ofgl_lm_serial = serial; ofgl_lm_version[5] = serial;
}
int main(void) {
    renderer_regression_main(0, NULL);
    uint16_t *saved = ofgl_lm[5];
    unsigned cases = 0;
    for (unsigned n = 0; n < 4000; n++) {
        int w = 1 + next32() % 128, h = 1 + next32() % 128;
        int pitch = w + next32() % 33;
        int x = (int)(next32() % 400) - 160, y = (int)(next32() % 400) - 160;
        if (n % 31 == 0) x = INT_MIN;
        if (n % 37 == 0) y = INT_MAX;
        for (int row = 0; row < h; row++) for (int col = 0; col < pitch; col++) {
            uint32_t v = next32(); if ((col + row) % 17 == 0) v = 0;
            input[row * pitch + col] = v;
            if (col < w) packed[row * w + col] = v;
        }
        for (unsigned i = 0; i < 128 * 128; i++) reference_page[i] = candidate_page[i] = (uint16_t)next32();
        uint32_t serial = n % 11 ? next32() : UINT32_MAX;
        select_page(reference_page, serial); reference_store(5, x, y, w, h, packed);
        uint32_t version = ofgl_lm_version[5], next = ofgl_lm_serial;
        select_page(candidate_page, serial);
        OFGL_UpdateLightmapRect(OFGL_TEXNUM_LIGHTMAPS + 5 + (n % 3) * OFGL_LM_BLOCKS,
                               x, y, w, h, pitch, input);
        assert(!memcmp(reference_page, candidate_page, sizeof reference_page));
        assert(ofgl_lm_version[5] == version && ofgl_lm_serial == next);
        cases++;
    }
    /* Match the real dynamic-update contract: source pages persist, only
     * the tracked dirty rectangle changes, and each page is fully uploaded
     * at initialization before narrow updates. Compare the entire page. */
    for (unsigned i = 0; i < 128 * 128; i++) input[i] = next32();
    select_page(reference_page, 0); reference_store(5, 0, 0, 128, 128, input);
    select_page(candidate_page, 0); ofgl_lm_store(5, 0, 0, 128, 128, input);
    for (unsigned n = 0; n < 1000; n++) {
        int x = next32() % 128, y = next32() % 128;
        int w = 1 + next32() % (128 - x), h = 1 + next32() % (128 - y);
        for (int row = y; row < y + h; row++) for (int col = x; col < x + w; col++) input[row * 128 + col] = next32();
        select_page(reference_page, n); reference_store(5, 0, y, 128, h, input + y * 128);
        uint32_t version = ofgl_lm_version[5];
        select_page(candidate_page, n);
        OFGL_UpdateLightmapRect(OFGL_TEXNUM_LIGHTMAPS + 5, x, y, w, h, 128, input + y * 128 + x);
        assert(!memcmp(reference_page, candidate_page, sizeof reference_page));
        assert(ofgl_lm_version[5] == version);
        cases++;
    }
    ofgl_lm[5] = saved;
    printf("PASS %u lightmap rectangles and persistent dirty updates\n", cases);
    return 0;
}
'''
(CHECKS / 'lightmap-rect-test.c').write_text(source)
game = WORK / 'src/quake2'
includes = [WORK / 'tools/tests', game, game / 'src/common/header',
            game / 'src/client/refresh/gl1/glad-gles1/include', WORK / 'src/sdk/include']
flags = ['cc', '-std=gnu11', '-O1', '-g', '-DOF_PC', '-DYQ2_OPENFPGA=1',
         '-DYQ2_GL1_GLES=1', '-DYQ2OSTYPE="test"', '-DYQ2ARCH="host"',
         '-ffunction-sections', '-fdata-sections', '-fsanitize=address,undefined',
         '-fno-omit-frame-pointer', '-fno-pie', '-no-pie'] + ['-I'+str(p) for p in includes]
commands = [flags + [str(CHECKS / 'lightmap-rect-test.c'), '-Wl,--gc-sections',
                    '-lm', '-o', str(CHECKS / 'test')], [str(CHECKS / 'test')]]
env = dict(os.environ, ASAN_OPTIONS='detect_leaks=0', UBSAN_OPTIONS='halt_on_error=1')
for index, command in enumerate(commands):
    with (CHECKS / ('build.log' if index == 0 else 'run.log')).open('w') as log:
        subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, env=env, check=True)
(CHECKS / 'result.json').write_text(json.dumps(dict(passed=True, cases=5000,
    commands=commands, source_sha256=hashlib.sha256(source.encode()).hexdigest()), indent=2)+'\n')
print((CHECKS / 'run.log').read_text())
