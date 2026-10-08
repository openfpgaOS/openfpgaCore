#!/usr/bin/env python3
"""Avoid repeating unchanged state checks inside one fixed-record draw batch."""
import difflib
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
CAMP = ROOT / 'docs/measurements/os30-fps25-20261005'
PARENT = CAMP / 'phase7/combined-v1'
DEST = CAMP / 'phase9/direct-state-v1'

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def main():
    build = json.loads((PARENT / 'build-normal.json').read_text())
    assert sha(PARENT / 'app.elf') == build['elf_sha256']
    sources = {}
    for name, item in build['inputs'].items():
        assert sha(PARENT / name) == item['sha256']
        sources[name] = (PARENT / name).read_text()
    original = sources['gfx_gpu.c']
    old = '''        g_surf_alpha = v[0].a;
        emit_tri_state(textured);
'''
    new = '''        /* Other material state and texture ownership stay fixed inside
         * this batch. Bind/mark the texture on its first triangle and repeat
         * only when the per-triangle alpha changes. */
        if (tri == 0 || g_surf_alpha != v[0].a) {
            g_surf_alpha = v[0].a;
            emit_tri_state(textured);
        }
'''
    assert original.count(old) == 1
    changed = original.replace(old, new)
    sources['gfx_gpu.c'] = changed
    DEST.mkdir(parents=True, exist_ok=False)
    for name, contents in sources.items():
        (DEST / name).write_text(contents)
    (DEST / 'candidate.patch').write_text(''.join(difflib.unified_diff(
        original.splitlines(True), changed.splitlines(True), fromfile='F9/gfx_gpu.c', tofile='direct-state-v1/gfx_gpu.c')))
    record = dict(parent='phase7/combined-v1/F9', parent_elf_sha256=build['elf_sha256'],
                  source_sha256={n: sha(DEST / n) for n in sources},
                  parent_source_sha256={n: sha(PARENT / n) for n in sources},
                  recipe_sha256=sha(Path(__file__)), qualified=False,
                  scope='Skip redundant state calls after the first triangle of an uninterrupted direct-record batch when alpha is unchanged. Packet contents/order and texture mark from first triangle are retained.',
                  required=['Audit texture epoch/state lifetime across ring waits', 'Exact command stream and image comparison', 'Matched CPU and coupled performance'])
    (DEST / 'source.json').write_text(json.dumps(record, indent=2) + '\n')
    print(DEST)

if __name__ == '__main__':
    main()
