#!/usr/bin/env python3
"""Compose nonoverlapping changes against the same frozen source baseline."""
import argparse
import difflib
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / 'docs/measurements/os30-simple-20261005'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('label')
    parser.add_argument('parts', nargs='+')
    args = parser.parse_args()
    for name in [args.label, *args.parts]:
        assert '/' not in name and name not in ('.', '..')
    dest = OUT / args.label
    dest.mkdir(exist_ok=False)
    manifest = {'components': args.parts, 'files': {}, 'qualified': False}
    for name in ('gfx_pc.c', 'gfx_gpu.c', 'gfx_gpu.h', 'gfx_geometry.h'):
        baseline = (OUT / 'baseline' / name).read_text().splitlines(True)
        edits = []
        inputs = {}
        for part in args.parts:
            path = OUT / part / name
            if not path.exists():
                continue
            candidate = path.read_text().splitlines(True)
            inputs[part] = sha(path.read_bytes())
            for tag, i, j, a, b in difflib.SequenceMatcher(None, baseline, candidate, autojunk=False).get_opcodes():
                if tag != 'equal':
                    edits.append((i, j, candidate[a:b], part))
        if not edits:
            continue
        edits.sort(key=lambda edit: (edit[0], edit[1]))
        for previous, current in zip(edits, edits[1:]):
            # An insertion immediately before a replacement is independent;
            # two insertions at the same point still need manual resolution.
            boundary_insert = previous[0] == previous[1] == current[0] < current[1]
            assert previous[1] <= current[0] and (previous[0] != current[0] or boundary_insert), (name, previous, current)
        result = baseline[:]
        for i, j, lines, part in reversed(edits):
            result[i:j] = lines
        text = ''.join(result)
        (dest / name).write_text(text)
        (dest / (name + '.patch')).write_text(''.join(difflib.unified_diff(
            baseline, result, fromfile='a/' + name, tofile='b/' + name)))
        manifest['files'][name] = {'baseline_sha256': sha(''.join(baseline).encode()),
                                  'component_sha256': inputs,
                                  'candidate_sha256': sha(text.encode()),
                                  'independent_hunks': len(edits)}
    (dest / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps(manifest, indent=2))


if __name__ == '__main__':
    main()
