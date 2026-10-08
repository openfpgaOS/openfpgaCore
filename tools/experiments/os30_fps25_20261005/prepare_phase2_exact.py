#!/usr/bin/env python3
"""Compose only exact-output software candidates; do not build or publish."""
import argparse
import difflib
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / 'docs/measurements/os30-fps25-20261005/phase2'
FILES = ('gfx_pc.c', 'gfx_gpu.c', 'gfx_gpu.h', 'gfx_geometry.h',
         'perf_pocket.h', 'perf_pocket.c', 'Makefile.sm64')
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('candidate')
    p.add_argument('components', nargs='+')
    a = p.parse_args()
    for name in [a.candidate, *a.components]:
        assert '/' not in name and name not in ('.', '..')
        assert 'guard' not in name, 'Guardband rendering changes are excluded'
    base = OUT / 'baseline'
    originals = {n: (base/n).read_text() for n in FILES}
    contents = dict(originals)
    bindings = {}
    with tempfile.TemporaryDirectory(prefix='sm64-exact-compose-') as temporary:
        tmp = Path(temporary)
        for component in a.components:
            bindings[component] = {}
            for name in FILES:
                source = OUT/component/name
                bindings[component][name] = sha(source)
                new = source.read_text()
                if new == originals[name]:
                    continue
                (tmp/'current').write_text(contents[name])
                (tmp/'base').write_text(originals[name])
                (tmp/'component').write_text(new)
                result = subprocess.run(['git', 'merge-file', '--stdout', str(tmp/'current'),
                    str(tmp/'base'), str(tmp/'component')], capture_output=True, text=True)
                assert result.returncode == 0, (component, name, result.stdout, result.stderr)
                contents[name] = result.stdout
    # The new batch writer has the same viewport lifetime as its scalar peer.
    if 'viewport-dirty-v2' in a.components and any(c.startswith('indexed-batch-') for c in a.components):
        old = ('    if (!g_vc_vp_valid || g_vc_vp[0] != g_cx || g_vc_vp[1] != g_cy ||\n'
               '        g_vc_vp[2] != g_hw || g_vc_vp[3] != g_hh) {')
        assert contents['gfx_gpu.c'].count(old) == 1
        contents['gfx_gpu.c'] = contents['gfx_gpu.c'].replace(old, '    if (!g_vc_vp_valid) {')
    assert contents['perf_pocket.c'].count('PERF F4 FPS') == 1
    contents['perf_pocket.c'] = contents['perf_pocket.c'].replace('PERF F4 FPS', 'PERF F5 FPS').replace(
        'LVL %u CPU %u MHZ  ', 'MAP %u CPU %u M  ')
    assert 'Private guardband trial' not in contents['gfx_pc.c']
    dest = OUT/a.candidate
    dest.mkdir(exist_ok=False)
    patch = ''
    for name, source in contents.items():
        (dest/name).write_text(source)
        patch += ''.join(difflib.unified_diff(originals[name].splitlines(True), source.splitlines(True),
            fromfile='baseline/'+name, tofile=a.candidate+'/'+name))
    (dest/'change.patch').write_text(patch)
    (dest/'manifest.json').write_text(json.dumps({
        'candidate': a.candidate, 'parent': 'render-v6', 'qualified': False,
        'components': a.components, 'component_source_sha256': bindings,
        'panel': 'F5 FPS with readable MAP/CPU labels',
        'guardband_included': False, 'generator_sha256': sha(Path(__file__)),
        'source_sha256': {n: sha(dest/n) for n in FILES},
        'required_checks': ['Paired normal/FPS scene and cycle capture',
                            'Exact retained-A GPU color/depth replays',
                            'Final source composition, alpha, clipping and build closure review']}, indent=2)+'\n')
    print(dest)


if __name__ == '__main__':
    main()
