#!/usr/bin/env python3
"""Freeze a private, math-preserving reduction of SM64 clipper bookkeeping."""
import argparse
import difflib
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
DEFAULT = ROOT / 'docs/measurements/os30-simple-20261005'
SOURCE_SHA = '4d8bd5cdef7b663adf86233763b54fda13d977abfaf225bdc8c0797d8e854e84'

def digest(data):
    return hashlib.sha256(data).hexdigest()

def extract(source):
    start = source.index('static inline bool gfx_clip_triangle(')
    end = source.index('\n}\n#endif\n', start) + 2
    return start, end, source[start:end]

def transform(source):
    assert digest(source.encode()) == SOURCE_SHA
    start, end, old = extract(source)
    new = old
    def replace(a, b):
        nonlocal new
        assert new.count(a) == 1, a
        new = new.replace(a, b)
    replace('    struct LoadedVertex v_buf[2][12] = { { *v1, *v2, *v3 } };',
            '    /* Only populated polygon entries are read. Avoid clearing the\n'
            '     * other 21 vertices before copying the three input vertices. */\n'
            '    struct LoadedVertex v_buf[2][12];\n'
            '    v_buf[0][0] = *v1; v_buf[0][1] = *v2; v_buf[0][2] = *v3;')
    replace('        const int8_t *plane = c_planes[plane_idx];\n\n        for (int i = 0; i < num_verts; ++i) {',
            '        const int8_t *plane = c_planes[plane_idx];\n'
            '        /* Retain the original floating expression and evaluation\n'
            '         * order; the preceding edge already calculated this point. */\n'
            '        rspv_t d1 = plane[0] * v_in[0].x + plane[1] * v_in[0].y + plane[2] * v_in[0].z + plane[3] * v_in[0].w;\n\n'
            '        for (int i = 0; i < num_verts; ++i) {')
    replace('            const struct LoadedVertex *vnext = &v_in[(i + 1) % num_verts];',
            '            const struct LoadedVertex *vnext = &v_in[i + 1 == num_verts ? 0 : i + 1];')
    replace('            const rspv_t d1 = plane[0] * vthis->x + plane[1] * vthis->y + plane[2] * vthis->z + plane[3] * vthis->w;\n', '')
    replace('                struct LoadedVertex *xv = &v_out[v_num[outidx]++];\n',
            '                struct LoadedVertex *xv = &v_out[v_num[outidx]++];\n'
            '                xv->clip_rej = 0; /* Scratch vertices go directly to submission. */\n')
    replace('                    xv->clip_rej = 0;\n', '')
    replace('            }\n        }\n\n        if (v_num[outidx] < 3)',
            '            }\n            d1 = d2;\n        }\n\n        if (v_num[outidx] < 3)')
    assert old.count('RSPV_DIV(') == new.count('RSPV_DIV(') == 2
    assert old.count('RSPV_RCP(') == new.count('RSPV_RCP(') == 2
    return source[:start] + new + source[end:], old, new

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', type=Path, default=DEFAULT/'clip-exact')
    a = p.parse_args()
    parent = DEFAULT/'baseline/gfx_pc.c'
    source = parent.read_text()
    candidate, old, new = transform(source)
    a.output.mkdir(parents=True, exist_ok=True)
    for name, value in [('gfx_pc.c', candidate), ('reference-clip.c', old+'\n'),
                        ('candidate-clip.c', new+'\n')]:
        path = a.output/name
        assert not path.exists() or path.read_text() == value, f'Frozen output differs: {path}'
        path.write_text(value)
    patch = ''.join(difflib.unified_diff(source.splitlines(True), candidate.splitlines(True),
                    fromfile='a/gfx_pc.c', tofile='b/gfx_pc.c'))
    (a.output/'clip-exact.patch').write_text(patch)
    (a.output/'prepare_clip.py').write_bytes(Path(__file__).read_bytes())
    record = dict(parent=str(parent), parent_sha256=SOURCE_SHA,
                  candidate_sha256=digest(candidate.encode()),
                  recipe_sha256=digest(Path(__file__).read_bytes()),
                  changed_scope='gfx_clip_triangle only; all other source bytes identical',
                  changes=['Replace 864-byte aggregate initialization with three full input copies',
                           'Replace positive bounded next-index modulo with conditional wrap',
                           'Reuse preceding edge distance with the original FP expression',
                           'Initialize generated clip_rej in both crossing branches'],
                  unchanged=['Plane order and coefficient expressions', 'Intersection arithmetic and direction',
                             'Color/UV/depth interpolation and reciprocal', 'Fan order and submission'],
                  limits=['Generated clip_rej previously stale in entering branch; not read by submission',
                          'Padding bytes are not renderer data and are not compared',
                          'No simplified w±axis expression; preserve original FP/FMA grouping'],
                  status='source frozen; focused and full application validation pending')
    (a.output/'source-manifest.json').write_text(json.dumps(record, indent=2)+'\n')
    print(json.dumps(record, indent=2))

if __name__ == '__main__':
    main()
