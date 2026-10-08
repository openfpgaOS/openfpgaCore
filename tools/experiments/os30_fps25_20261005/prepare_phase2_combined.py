#!/usr/bin/env python3
"""Compose reviewed software experiments without changing the build stage."""
import argparse
import difflib
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / 'docs/measurements/os30-fps25-20261005/phase2'
FILES = ('gfx_pc.c', 'gfx_gpu.c', 'gfx_gpu.h', 'gfx_geometry.h',
         'perf_pocket.h', 'perf_pocket.c', 'Makefile.sm64')
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('candidate')
    p.add_argument('--batch')
    a = p.parse_args()
    for name in (a.candidate, a.batch):
        assert name is None or ('/' not in name and name not in ('.', '..'))
    base = OUT / 'baseline'
    parent = OUT / a.batch if a.batch else base
    contents = {n: (parent / n).read_text() for n in FILES}
    baseline_pc = (base / 'gfx_pc.c').read_text()
    guarded_pc = (OUT / 'guardband-v2/gfx_pc.c').read_text()
    anchor = '#ifdef GFX_MANUAL_CLIPPING\n    if (!gfx_clip_triangle(v1, v2, v3, clip_and))'
    start = baseline_pc.index(anchor)
    end = guarded_pc.index('    if (!gfx_clip_triangle(v1, v2, v3, clip_and))', start)
    guard_block = guarded_pc[start:end]
    assert guarded_pc == baseline_pc.replace('#ifdef GFX_MANUAL_CLIPPING\n    if (!gfx_clip_triangle',
        guard_block + '    if (!gfx_clip_triangle')
    assert contents['gfx_pc.c'].count(anchor) == 1
    contents['gfx_pc.c'] = contents['gfx_pc.c'].replace(anchor,
        guard_block + '    if (!gfx_clip_triangle(v1, v2, v3, clip_and))')

    viewport = (OUT / 'viewport-dirty-v2/gfx_gpu.c').read_text()
    setter = '    g_cy = (float)(SCR_H - y - height) + g_hh;\n'
    s = viewport.index(setter) + len(setter)
    e = viewport.index('    g_clip_cx =', s)
    assert contents['gfx_gpu.c'].count(setter) == 1
    contents['gfx_gpu.c'] = contents['gfx_gpu.c'].replace(setter, setter + viewport[s:e])
    old = ('    if (!g_vc_vp_valid || g_vc_vp[0] != g_cx || g_vc_vp[1] != g_cy ||\n'
           '        g_vc_vp[2] != g_hw || g_vc_vp[3] != g_hh) {')
    assert contents['gfx_gpu.c'].count(old) == (2 if a.batch else 1)
    contents['gfx_gpu.c'] = contents['gfx_gpu.c'].replace(old, '    if (!g_vc_vp_valid) {')
    panel = contents['perf_pocket.c']
    assert panel.count('PERF F4 FPS') == 1
    assert panel.count('LVL %u CPU %u MHZ  ') == 1
    contents['perf_pocket.c'] = panel.replace('PERF F4 FPS', 'PERF F5 FPS').replace(
        'LVL %u CPU %u MHZ  ', 'MAP %u CPU %u M  ')

    dest = OUT / a.candidate
    dest.mkdir(exist_ok=False)
    patch = ''
    for name, source in contents.items():
        (dest / name).write_text(source)
        patch += ''.join(difflib.unified_diff((base / name).read_text().splitlines(True),
            source.splitlines(True), fromfile='baseline/' + name, tofile=a.candidate + '/' + name))
    (dest / 'change.patch').write_text(patch)
    (dest / 'manifest.json').write_text(json.dumps({
        'candidate': a.candidate, 'parent': 'render-v6', 'qualified': False,
        'components': ['guardband-v2', 'viewport-dirty-v2'] + ([a.batch] if a.batch else []),
        'panel': 'F5 FPS; readable MAP/CPU labels',
        'rendering': 'Guardband changes side-clipping interpolation and edge sampling. No claim of bit-identical output.',
        'required_checks': ['Paired scene and CPU capture', 'GPU color/depth review against guardband-v2',
                            'Source composition and build input closure'],
        'inputs': {n: {'baseline_sha256': sha(base/n), 'candidate_sha256': sha(dest/n)} for n in FILES},
        'generator_sha256': sha(Path(__file__))}, indent=2) + '\n')
    print(dest)


if __name__ == '__main__':
    main()
