#!/usr/bin/env python3
"""Verify the rebuilt SM64 app, ready-to-copy package, runtime and rollback."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / 'docs/measurements/sm64-geometry-fast-20261003'
DEST = ROOT.parent / 'SM64'
load = lambda p: json.loads(p.read_text())
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None
report = load(OUT / 'results.json')
publication = load(OUT / 'published.json')
backup = Path(publication['backup'])
assert load(backup / 'files.json') == publication['files']
for row in publication['files']:
    assert sha(DEST / row['path']) == row['after'], row['path']
    assert sha(backup / row['path']) == row['before'], ('backup', row['path'])
for rel, digest in {**report['source_sha256'], **report['runtime_sha256']}.items():
    assert sha(DEST / rel) == digest, rel
package = DEST / 'build/pocket/sm64'
files = {
    '.obj/sm64/app.elf': report['app_sha256'],
    'build/pocket/sm64/Assets/sm64/common/sm64.elf': report['app_sha256'],
    'build/pocket/sm64/Assets/sm64/common/os.bin': report['os_bin_unchanged_sha256'],
    'build/pocket/sm64/Cores/ThinkElastic.SM64/os30.rbf_r': report['runtime_sha256']['runtime/pocket/os30.rbf_r']}
for rel, digest in files.items():
    assert sha(DEST / rel) == digest, ('Package mismatch', rel)
for line in (DEST / 'runtime/MANIFEST').read_text().splitlines():
    if line and not line.startswith('#'):
        digest, rel = line.split()
        assert hashlib.md5((DEST / 'runtime' / rel).read_bytes()).hexdigest() == digest, rel
expected = load(OUT / 'before.json')
assert sha(DEST / 'src/sm64/Makefile') == expected['src/sm64/Makefile']
assert sha(DEST / 'src/sm64/sm64/src/pc/gfx/gfx_gpu.h') == expected['src/sm64/sm64/src/pc/gfx/gfx_gpu.h']
result = dict(verified=True, package=str(package), files=files,
              source_sha256=report['source_sha256'], runtime_manifest_verified=True,
              rollback_backup_verified=True, hardware_deployment_run=False, full_seed_sweep_run=False)
(OUT / 'publication-verified.json').write_text(json.dumps(result, indent=2) + '\n')
print('Verified: captured ELF == rebuilt ELF == packaged ELF; runtime, manifest and rollback hashes match.')
