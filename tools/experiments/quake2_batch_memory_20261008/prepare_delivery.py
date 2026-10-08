#!/usr/bin/env python3
"""Freeze an incremental game update and a guarded sibling deployment plan."""
from pathlib import Path
import hashlib
import json
import shutil
import zipfile

R = Path(__file__).resolve().parents[3]
P = R / 'docs/measurements/quake2-batch-memory-20261008'
W = P / 'work/combined'
D = P / 'delivery-v1'
Q = Path('/home/alberto/Repos/Quake2')

def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None

def write(p, v):
    p.write_text(json.dumps(v, indent=2) + '\n')

before = json.loads((P / 'before.json').read_text())
composition = json.loads((P / 'composition-combined/manifest.json').read_text())
acceptance = json.loads((P / 'acceptance.json').read_text())
assert acceptance['passed'] and acceptance['selected'] == 'combined'
for path, digest in acceptance['evidence_sha256'].items():
    assert sha(R / path) == digest, path
baseline = dict(before['source_sha256'], **before['artifacts_sha256'])
for rel, digest in baseline.items():
    assert sha(Q / rel) == digest, rel
assert sha(P / 'build-combined/app.elf') == acceptance['elf_sha256']
D.mkdir(exist_ok=False)
(D / 'sibling').mkdir()
files = []
for rel, values in composition['files'].items():
    assert baseline[rel] == values['before'] and sha(W / rel) == values['after'], rel
    files.append(dict(path=rel, source=str(W / rel), **values))
for rel in ['.obj/quake2/app.elf', 'build/pocket/quake2/Assets/quake2/common/quake2.elf']:
    files.append(dict(path=rel, source=str(P / 'build-combined/app.elf'),
                      before=baseline[rel], after=acceptance['elf_sha256']))
doc = 'docs/quake2-batch-memory-optimization-20261008.txt'
assert not (Q / doc).exists()
files.append(dict(path=doc, source=str(P / 'audit.txt'), before=None, after=sha(P / 'audit.txt')))
changed = {f['path'] for f in files}
evidence = {str(R / path): digest for path, digest in acceptance['evidence_sha256'].items()}
evidence[str(P / 'acceptance.json')] = sha(P / 'acceptance.json')
evidence[str(P / 'audit.txt')] = sha(P / 'audit.txt')
plan = dict(destination=str(Q), files=files,
            guards={rel: digest for rel, digest in baseline.items() if rel not in changed},
            evidence=evidence)
write(D / 'sibling/plan.json', plan)
with zipfile.ZipFile(D / 'quake2-batch-memory-update.zip', 'w', compression=zipfile.ZIP_DEFLATED) as archive:
    info = zipfile.ZipInfo('Assets/quake2/common/quake2.elf', date_time=(2026, 10, 8, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o100644 << 16
    archive.writestr(info, (P / 'build-combined/app.elf').read_bytes())
with zipfile.ZipFile(D / 'quake2-batch-memory-update.zip') as archive:
    assert archive.namelist() == ['Assets/quake2/common/quake2.elf']
    assert hashlib.sha256(archive.read(archive.namelist()[0])).hexdigest() == acceptance['elf_sha256']
shutil.copy2(P / 'audit.txt', D / 'audit.txt')
write(D / 'prepared.json', dict(passed=True, elf_sha256=acceptance['elf_sha256'],
    sibling_plan_sha256=sha(D / 'sibling/plan.json'),
    update_zip_sha256=sha(D / 'quake2-batch-memory-update.zip'),
    changed_paths=sorted(changed), source_guard_count=len(plan['guards']),
    sd_deployed=False, archive_contents=['Assets/quake2/common/quake2.elf']))
print((D / 'prepared.json').read_text())
