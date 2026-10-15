#!/usr/bin/env python3
"""Choose source/receipt metadata for the final bundle; exclude game fixtures."""
from pathlib import Path
import json

C = Path(__file__).resolve().parent.parent
copies = {}

def add(path):
    path = path.resolve()
    assert path.is_file()
    rel = str(path.relative_to(C))
    key = 'evidence/'+rel
    assert key not in copies
    copies[key] = str(path)

for name in ['analysis', 'design/broad-handback-review-v1', 'analysis/state-liveness-v1',
             'analysis/caller-liveness-v1']:
    # analysis already recursively includes the latter two directories.
    if name.startswith('analysis/'):
        continue
    for path in sorted((C/name).rglob('*')):
        if not path.is_file() or '__pycache__' in path.parts:
            continue
        if path.name in ('ledger.json', 'events.json', 'conclusion-v1.json'):
            continue
        if path.suffix in ('.py', '.json', '.txt', '.md'):
            add(path)
for path in sorted((C/'validation').glob('*.py')):
    add(path)
for name in ['f28-dl-binding-v1.json', 'dl-observer-readiness-v1.json']:
    add(C/'validation'/name)
for path in sorted((C/'validation').glob('*supersession*.json')):
    add(path)
for role in ('control', 'candidate', 'single'):
    for path in sorted((C/f'validation/{role}-v4').glob('check-independent-v3.*')):
        add(path)
for prefix in ['independent-negative-v', 'f28-owner-diagnostic-v', 'f28-strict-attempts-v',
               'observer-binding-v', 'dl-observer-']:
    for directory in sorted((C/'validation').glob(prefix+'*')):
        if not directory.is_dir():
            continue
        for path in sorted(directory.rglob('*')):
            if path.is_file() and path.suffix in ('.py', '.json', '.txt', '.log') and '__pycache__' not in path.parts:
                add(path)
for v in (2, 3, 4):
    base = C/f'work-app-v{v}/evidence'
    for name in ['scope-v1.json', 'freeze_scope.py', f'f{23+v}-to-f{24+v}.patch']:
        path = base/name
        if path.is_file():
            add(path)
for name in ['physical/review-v1.json', 'physical/resource-source-audit-v1.json',
             'package/packing-review-v3.json', 'package/prior-verification-v1/result.json',
             'package/finalize.py', 'package/prepare_copies.py', 'build_native.py']:
    add(C/name)
for path in sorted((C/'package/prior-verification-v1').iterdir()):
    if path.is_file() and path.name != 'result.json':
        add(path)
out = C/'package/extra-copies-final.json'
assert not out.exists()
out.write_text(json.dumps(copies, indent=2)+'\n')
print(f'{len(copies)} source/receipt metadata files selected')
