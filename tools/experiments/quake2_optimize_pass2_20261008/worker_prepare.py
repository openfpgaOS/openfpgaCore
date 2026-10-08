#!/usr/bin/env python3
"""Prepare and check lazy geometry-worker setup, preserving CPU clip planes."""
from pathlib import Path
import difflib
import hashlib
import json
import os
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / 'docs/measurements/quake2-optimize-pass2-20261008'
BASE = OUT / 'work/base'
WORK = OUT / 'work/worker'
COMP = OUT / 'components/worker-v1'
REL = Path('src/quake2/openfpga/of_gl1.c')
BASE_SHA = 'a920bb512a578029265ab2f756dd273faac6e641fa2a4ff75cf9a266e8e843fb'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def put(path, value):
    Path(path).write_text(json.dumps(value, indent=2) + '\n')


def apply(text):
    substitutions = [
        ('static unsigned ofgl_world_prepared;\n',
         'static unsigned ofgl_world_prepared;\nstatic int ofgl_world_geometry;\n'),
        ('\tif (ofgl_world_prepared == ofgl_world_epoch) return;\n'
         '\tofgl_world_prepared = ofgl_world_epoch;\n',
         '\tint geometry = OFQ2_Supports(OFQ2_CAP_GEOMETRY);\n'
         '\tif (ofgl_world_prepared == ofgl_world_epoch &&\n'
         '\t\tofgl_world_geometry == geometry) return;\n'
         '\tofgl_world_prepared = ofgl_world_epoch;\n'
         '\tofgl_world_geometry = geometry;\n'),
        ('\t/* The raw worker divides 2^38 by cw for depth, requiring cw = z*256\n',
         '\t/* CPU-only cores use the planes above, but never the worker state.\n'
         '\t * Keep capability in the cache key so a route change rebuilds it. */\n'
         '\tofgl_world_raw_matrix = 0;\n'
         '\tif (!geometry) return;\n\n'
         '\t/* The raw worker divides 2^38 by cw for depth, requiring cw = z*256\n'),
    ]
    for old, new in substitutions:
        assert text.count(old) == 1
        text = text.replace(old, new)
    return text


def main():
    assert sha(BASE / REL) == BASE_SHA
    assert not WORK.exists() and not COMP.exists(), 'Preserve previous experiment'
    before = (BASE / REL).read_text()
    after = apply(before)
    shutil.copytree(BASE, WORK)
    COMP.mkdir(parents=True)
    (WORK / REL).write_text(after)
    (COMP / 'of_gl1.c').write_text(after)
    (COMP / 'change.patch').write_text(''.join(difflib.unified_diff(
        before.splitlines(True), after.splitlines(True), fromfile=str(REL), tofile=str(REL))))
    shutil.copy2(__file__, COMP / 'recipe.py')
    put(COMP / 'prepared.json', dict(baseline_sha256=BASE_SHA, candidate_sha256=sha(WORK / REL),
                                     work=str(WORK), changes=[str(REL)],
                                     scope='Keep CPU world planes exact. Skip unused worker matrix on GEO0; capability joins epoch cache key.'))
    checks = COMP / 'checks'
    checks.mkdir()
    game = WORK / 'src/quake2'
    includes = [game, game / 'src/common/header',
                game / 'src/client/refresh/gl1/glad-gles1/include', WORK / 'src/sdk/include']
    flags = ['cc', '-std=gnu11', '-O1', '-g', '-DOF_PC', '-DYQ2_OPENFPGA=1',
             '-DYQ2_GL1_GLES=1', '-ffunction-sections', '-fdata-sections',
             '-DYQ2OSTYPE="test"', '-DYQ2ARCH="host"', '-fno-pie', '-no-pie',
             '-fsanitize=address,undefined', '-fno-omit-frame-pointer']
    flags += ['-I' + str(path) for path in includes]
    env = dict(os.environ, ASAN_OPTIONS='detect_leaks=0', UBSAN_OPTIONS='halt_on_error=1')
    commands = []

    def run(cmd, name):
        commands.append(dict(name=name, argv=cmd))
        with (checks / (name + '.log')).open('w') as log:
            subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT, env=env, check=True)

    renderer = WORK / 'tools/tests/quake2_renderer_test.c'
    run(flags + [str(renderer), '-Wl,--gc-sections', '-lm', '-o', str(checks / 'renderer')],
        'renderer-build')
    run([str(checks / 'renderer')], 'renderer-run')
    start = before.index('static void\nofgl_world_prepare(void)\n')
    end = before.index('\nstatic int\nofgl_geometry_tri(', start)
    reference = before[start:end].replace('ofgl_world_prepare(void)', 'reference_world_prepare(void)', 1)
    driver = '''#define main renderer_main
#include "RENDERER"
#undef main
REFERENCE
static unsigned prng = 13;
static float sample(void)
{
    prng = prng * 1664525u + 1013904223u;
    return ((int)(prng >> 16) - 32768) / 4096.0f;
}
int main(void)
{
    ofgl_plane_t expected_planes[5];
    float expected_abs[5][3];
    ofq2_object_state_t expected_object;
    for (int n = 0; n < 2048; n++) {
        for (int j = 0; j < 16; j++) OFGL_MV[j] = sample();
        ofgl_pc_xs = 100.0f + fabsf(sample()) * 50;
        ofgl_pc_ys = 100.0f + fabsf(sample()) * 50;
        ofgl_pc_xc = 159.5f + sample();
        ofgl_pc_yc = 119.5f + sample();
        ofgl_pc_near = 1.0f + fabsf(sample());
        ofgl_build_clip_planes();
        ofgl_world_epoch++;
        ofgl_world_prepared = 0;
        reference_world_prepare();
        memcpy(expected_planes, ofgl_world_planes, sizeof(expected_planes));
        memcpy(expected_abs, ofgl_world_abs, sizeof(expected_abs));
        expected_object = ofgl_world_object;
        int expected_raw = ofgl_world_raw_matrix;
        float center[3] = {sample(), sample(), sample()};
        float extent[3] = {fabsf(sample()), fabsf(sample()), fabsf(sample())};
        float expected_min = 0, expected_max = 0;
        int expected_bounds = ofgl_box_bounds(center, extent, &expected_min, &expected_max);
        /* Switch capability at an unchanged epoch: retained host/API contract. */
        ofgl_world_prepared = 0;
        for (int cap = 0; cap < 3; cap++) {
            gpu_geometry = cap == 1;
            memset(&ofgl_world_object, 0x5a, sizeof(ofgl_world_object));
            ofq2_object_state_t untouched = ofgl_world_object;
            ofgl_world_prepare();
            assert(!memcmp(expected_planes, ofgl_world_planes, sizeof(expected_planes)));
            assert(!memcmp(expected_abs, ofgl_world_abs, sizeof(expected_abs)));
            float actual_min = 0, actual_max = 0;
            assert(ofgl_box_bounds(center, extent, &actual_min, &actual_max) == expected_bounds);
            assert(!memcmp(&actual_min, &expected_min, sizeof(float)));
            assert(!memcmp(&actual_max, &expected_max, sizeof(float)));
            if (gpu_geometry) {
                assert(ofgl_world_raw_matrix == expected_raw);
                assert(!memcmp(&ofgl_world_object, &expected_object, sizeof(expected_object)));
            } else {
                assert(ofgl_world_raw_matrix == 0);
                assert(!memcmp(&ofgl_world_object, &untouched, sizeof(untouched)));
            }
        }
    }
    puts("6144 old/new preparations: planes/bounds exact; GEO1 state exact; GEO0 worker untouched");
    return 0;
}
'''.replace('RENDERER', str(renderer)).replace('REFERENCE', reference)
    (checks / 'differential.c').write_text(driver)
    run(flags + [str(checks / 'differential.c'), '-Wl,--gc-sections', '-lm',
                 '-o', str(checks / 'differential')], 'differential-build')
    run([str(checks / 'differential')], 'differential-run')
    put(checks / 'commands.json', commands)
    put(COMP / 'result.json', dict(passed=True, sanitizers=['ASan', 'UBSan'],
                                  actual_renderer=True, differential_preparations=6144,
                                  capability_transitions_same_epoch=True,
                                  performance_measured=False, sibling_modified=False))
    put(COMP / 'manifest.json', dict(passed=True, evidence_sha256={str(path.relative_to(ROOT)): sha(path)
                                      for path in sorted(COMP.rglob('*')) if path.is_file()}))
    print((COMP / 'prepared.json').read_text())
    print('manifest', sha(COMP / 'manifest.json'))


if __name__ == '__main__':
    main()
