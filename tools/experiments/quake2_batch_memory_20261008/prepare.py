#!/usr/bin/env python3
"""Snapshot the accepted Quake2 baseline without modifying sibling products."""
from pathlib import Path
import hashlib, json, shutil, subprocess

R = Path(__file__).resolve().parents[3]
P = R / 'docs/measurements/quake2-batch-memory-20261008'
Q = Path('/home/alberto/Repos/Quake2')
OLD = R / 'docs/measurements/quake2-comprehensive-20261008'

def sha(p):
    with p.open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()

plan = json.loads((OLD / 'delivery-v1/sibling/plan.json').read_text())
done = json.loads((OLD / 'delivery-v1/sibling/complete.json').read_text())
assert done['passed']
for f in plan['files']:
    assert sha(Q / f['path']) == f['after'], f['path']
for rel, digest in plan['guards'].items():
    assert sha(Q / rel) == digest, rel
P.mkdir(exist_ok=False)
B = P / 'work/base'
B.mkdir(parents=True)
for directory in ['src', 'tools', 'scripts', 'runtime', 'dist', 'docs']:
    shutil.copytree(Q / directory, B / directory, ignore=shutil.ignore_patterns('__pycache__'))
for rel in ['Makefile', 'README.md', '.gitignore', '.obj/quake2/app.elf']:
    target = B / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(Q / rel, target)
sources = {str(f.relative_to(B)): sha(f) for f in sorted(B.rglob('*'))
           if f.is_file() and '.obj' not in f.relative_to(B).parts}
artifacts = {rel: sha(Q / rel) for rel in [
    '.obj/quake2/app.elf', 'build/pocket/quake2/Assets/quake2/common/quake2.elf',
    'build/diagnostics/quake2-gpuprofile.elf', 'runtime/pocket/os30.rbf_r',
    'build/pocket/quake2/Cores/thinkelastic.quake2/os30.rbf_r']}
(P / 'before.json').write_text(json.dumps(dict(sibling=str(Q), source_sha256=sources,
    artifacts_sha256=artifacts, previous_acceptance_sha256=sha(OLD / 'acceptance.json'),
    baseline_label='thorough_combined6', baseline_elf_sha256=artifacts['.obj/quake2/app.elf']), indent=2) + '\n')
(P / 'before-status.txt').write_text(subprocess.check_output(['git','-C',str(Q),'status','--short'], text=True))
print(json.dumps(dict(output=str(P), files=len(sources), elf_sha256=artifacts['.obj/quake2/app.elf'])))
