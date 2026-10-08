#!/usr/bin/env python3
"""Compose exact nonoverlapping baseline-relative candidate edits privately."""
from pathlib import Path
import difflib
import hashlib
import json
import shutil
import sys

ROOT = Path(__file__).resolve().parents[3]
P = ROOT / 'docs/measurements/quake2-batch-memory-20261008'
B = P / 'work/base'
name = sys.argv[1]
candidates = sys.argv[2:]
assert name.replace('_', '').isalnum() and candidates
W = P / 'work' / name
O = P / ('composition-' + name)
assert not W.exists() and not O.exists()

def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()

edits = {}
inputs = {}
for candidate in candidates:
    tree = P / 'work' / candidate
    for directory in ('src', 'tools'):
        for file in sorted((B / directory).rglob('*')):
            if not file.is_file():
                continue
            rel = file.relative_to(B)
            other = tree / rel
            assert other.is_file(), other
            if sha(file) == sha(other):
                continue
            old, new = file.read_text().splitlines(True), other.read_text().splitlines(True)
            for tag, a, b, c, d in difflib.SequenceMatcher(a=old, b=new, autojunk=False).get_opcodes():
                if tag != 'equal':
                    edits.setdefault(rel, []).append((a, b, new[c:d], candidate))
            inputs[str(other)] = sha(other)

O.mkdir()
shutil.copytree(B, W, ignore=shutil.ignore_patterns('.obj', 'build', '__pycache__'))
files, patches = {}, []
for rel, operations in sorted(edits.items()):
    old = (B / rel).read_text().splitlines(True)
    operations.sort(key=lambda x: (x[0], x[1]))
    for left, right in zip(operations, operations[1:]):
        assert left[1] <= right[0] and left[:2] != right[:2], (rel, left, right)
    new = old[:]
    for a, b, lines, candidate in reversed(operations):
        new[a:b] = lines
    (W / rel).write_text(''.join(new))
    files[str(rel)] = dict(before=sha(B / rel), after=sha(W / rel))
    patches.extend(difflib.unified_diff(old, new, fromfile='a/' + str(rel), tofile='b/' + str(rel)))
(O / 'source.patch').write_text(''.join(patches))
(O / 'manifest.json').write_text(json.dumps(dict(worktree=str(W), candidates=candidates,
    recipe_sha256=sha(Path(__file__)), inputs_sha256=inputs, files=files), indent=2) + '\n')
print(json.dumps(dict(worktree=str(W), candidates=candidates, changed_files=list(files))))
