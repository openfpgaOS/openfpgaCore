#!/usr/bin/env python3
"""Freeze the published SM64 source for simple, single-issue experiments."""
import hashlib
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / 'docs/measurements/os30-simple-20261005'
SOURCE = ROOT.parent / 'SM64'
STAGE = OUT / 'SM64'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    STAGE.mkdir(exist_ok=False)
    for directory in ('src', 'tools'):
        shutil.copytree(SOURCE / directory, STAGE / directory,
                        ignore=shutil.ignore_patterns('.git', '__pycache__'))
    for name in ('Makefile', 'Mario64.z64'):
        shutil.copy2(SOURCE / name, STAGE / name)
    baseline = OUT / 'baseline'
    baseline.mkdir()
    for name in ('gfx_pc.c', 'gfx_gpu.c', 'gfx_gpu.h', 'gfx_geometry.h'):
        path = SOURCE / 'src/sm64/sm64/src/pc/gfx' / name
        shutil.copy2(path, baseline / name)
    files = {str(path.relative_to(STAGE)): sha(path)
             for path in STAGE.rglob('*') if path.is_file()}
    apps = {}
    for label, path in (
        ('normal', SOURCE / '.obj/sm64/app.elf'),
        ('diagnostic', SOURCE / 'build/pocket/sm64/Assets/sm64/common/sm64.elf'),
    ):
        dest = baseline / (label + '.elf')
        shutil.copy2(path, dest)
        apps[label] = {'source': str(path), 'sha256': sha(dest)}
    record = {'source': str(SOURCE), 'stage': str(STAGE), 'files': files,
              'apps': apps, 'scope': 'Private software experiments on retained A single-issue core; no FPGA or SD writes.'}
    (OUT / 'source-baseline.json').write_text(json.dumps(record, indent=2) + '\n')
    print(json.dumps({'stage': str(STAGE), 'files': len(files), 'apps': apps}, indent=2))


if __name__ == '__main__':
    main()
