#!/usr/bin/env python3
"""Prepare an isolated projection-clamp candidate without publishing it."""
from pathlib import Path
import difflib
import hashlib
import json
import shutil

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / 'docs/measurements/quake2-optimize-pass2-20261008'
COMPONENT = OUT / 'components/clamp-v1'
REL = Path('src/quake2/openfpga/of_gl1.c')
BASE_SHA = 'a920bb512a578029265ab2f756dd273faac6e641fa2a4ff75cf9a266e8e843fb'

def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def apply(text):
    before = '''\tif (v->sx < -2047.0f) v->sx = -2047.0f;
\telse if (v->sx > 2047.0f) v->sx = 2047.0f;
\tif (v->sy < -2047.0f) v->sy = -2047.0f;
\telse if (v->sy > 2047.0f) v->sy = 2047.0f;'''
    after = '''\t/* The finite guard above makes hardware min/max equivalent here,
\t * including signed zero, without per-coordinate clamp branches. */
\tv->sx = fmaxf(-2047.0f, fminf(v->sx, 2047.0f));
\tv->sy = fmaxf(-2047.0f, fminf(v->sy, 2047.0f));'''
    assert text.count(before) == 1
    text = text.replace(before, after)
    before = '''\tif (depth < 1.0f) depth = 1.0f;
\telse if (depth > 2147483520.0f) depth = 2147483520.0f;'''
    after = '''\t/* Depth scale is always 1 or 16. Finite iw can overflow this
\t * positive product to infinity but cannot make it NaN. */
\tdepth = fmaxf(1.0f, fminf(depth, 2147483520.0f));'''
    assert text.count(before) == 1
    return text.replace(before, after)

def function(text, name):
    start = text.index('static void\nofgl_project_cvert(')
    end = text.index('\nstatic void\nofgl_build_clip_planes(', start)
    return text[start:end].replace('static void\nofgl_project_cvert(',
        '__attribute__((noinline, noclone, noipa)) void\n' + name + '(')

def main():
    base = OUT / 'work/base'; candidate = OUT / 'work/clamp'
    assert sha(base / REL) == BASE_SHA
    assert not candidate.exists() and not COMPONENT.exists()
    old = (base / REL).read_text(); new = apply(old)
    assignments = [line.strip() for line in old.splitlines()
                   if 'ofgl_depth_scale =' in line and '==' not in line]
    assert len(assignments) == 3, assignments
    assert all('= 1.0f;' in line or '= (span < 0.9f) ? 16.0f : 1.0f;' in line
               for line in assignments), assignments
    shutil.copytree(base, candidate); COMPONENT.mkdir(parents=True)
    (candidate / REL).write_text(new); (COMPONENT / 'of_gl1.c').write_text(new)
    (COMPONENT / 'change.patch').write_text(''.join(difflib.unified_diff(
        old.splitlines(True), new.splitlines(True), fromfile=str(REL), tofile=str(REL))))
    start = old.index('typedef struct\n{\n\tfloat x, y, z;')
    end = old.index('} ofgl_cvert_t;', start) + len('} ofgl_cvert_t;')
    shared = '''#include <stdint.h>
#include <string.h>
#include <math.h>
extern float ofgl_pc_xc, ofgl_pc_yc, ofgl_pc_xs, ofgl_pc_ys, ofgl_depth_scale;
static inline int ofgl_finite(float v) {
    uint32_t bits; memcpy(&bits, &v, 4);
    return (bits & 0x7f800000u) != 0x7f800000u;
}
''' + old[start:end] + '\n'
    (COMPONENT/'shared.h').write_text(shared)
    for name, text in [('old', old), ('new', new)]:
        (COMPONENT/f'projection_{name}.c').write_text(
            '#include "shared.h"\n' + function(text, f'project_{name}'))
    (COMPONENT / 'manifest.json').write_text(json.dumps(dict(
        baseline_sha256=BASE_SHA, candidate_sha256=sha(candidate / REL), candidate=str(candidate),
        changes='Only sx/sy/depth clamps within ofgl_project_cvert; original nonfinite guard retained.',
        depth_scale_assignments=assignments,
        depth_invariant='Scale is always1 or16; finite iw times finite positive2^30/2^34 cannot produceNaN. Inf overflow still clamps identically.',
        extracted_old_sha256=sha(COMPONENT/'projection_old.c'),
        extracted_new_sha256=sha(COMPONENT/'projection_new.c')), indent=2)+'\n')
    print((COMPONENT/'manifest.json').read_text())

if __name__ == '__main__':
    main()
