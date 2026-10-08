#!/usr/bin/env python3
"""Verify the captured diagnostic ELF equals the actual ready-to-copy package."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / 'docs/measurements/sm64-device-perf-20261003'
DEST = ROOT.parent / 'SM64'
load = lambda p: json.loads(p.read_text())
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None
report = load(OUT / 'results.json')
publication = load(OUT / 'published.json')
backup = Path(publication['backup'])
assert load(backup / 'files.json') == publication['files']
for row in publication['files']:
    assert sha(DEST / row['path']) == row['after'], row['path']
    assert sha(backup / row['path']) == row['before'], ('Backup', row['path'])
assert sha(backup / 'app.elf') == report['baseline_app_sha256']
for rel, value in {**report['source_sha256'], **report['unchanged_sha256']}.items():
    assert sha(DEST / rel) == value, rel
package = 'build/pocket/sm64/'
files = {
    '.obj/sm64-perfdiag/app.elf': report['app_sha256'],
    package + 'Assets/sm64/common/sm64.elf': report['app_sha256'],
    package + 'Assets/sm64/common/os.bin': report['unchanged_sha256']['runtime/pocket/os.bin'],
    package + 'Cores/ThinkElastic.SM64/os30.rbf_r': report['unchanged_sha256']['runtime/pocket/os30.rbf_r']}
for rel, value in files.items():
    assert sha(DEST / rel) == value, ('Package mismatch', rel)
for line in (DEST / 'runtime/MANIFEST').read_text().splitlines():
    if line and not line.startswith('#'):
        digest, rel = line.split()
        assert hashlib.md5((DEST / 'runtime' / rel).read_bytes()).hexdigest() == digest, rel
result = dict(verified=True, package=str(DEST / package), files=files,
    source_sha256=report['source_sha256'], runtime_unchanged=True,
    rollback_backup_verified=True, hardware_deployment_run=False, full_seed_sweep_run=False)
(OUT / 'publication-verified.json').write_text(json.dumps(result, indent=2) + '\n')
print('Verified: captured diagnostic ELF == rebuilt ELF == packaged ELF; runtime unchanged; rollback verified.')
