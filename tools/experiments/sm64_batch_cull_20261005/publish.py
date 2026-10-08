#!/usr/bin/env python3
"""Publish only the qualified SM64 sources and apps, with guarded backups."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / 'docs/measurements/sm64-batch-cull-20261005'
STAGE = OUT / 'SM64'
DEST = ROOT.parent / 'SM64'
BACKUP = OUT / 'publication-backup'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def load(path):
    return json.loads(path.read_text())


def atomic_copy(source, destination):
    with tempfile.NamedTemporaryFile(dir=destination.parent,
                                     prefix='.' + destination.name + '.', delete=False) as f:
        temporary = Path(f.name)
        try:
            with source.open('rb') as incoming:
                shutil.copyfileobj(incoming, f)
            f.flush()
            os.fsync(f.fileno())
            shutil.copymode(source, temporary)
            assert sha(temporary) == sha(source)
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--apply', action='store_true')
    group.add_argument('--verify', action='store_true')
    args = parser.parse_args()
    report = load(OUT / 'qualification.json')
    assert report['qualified'] is True
    for path, expected in report['evidence_sha256'].items():
        assert sha(OUT / path) == expected, ('Evidence changed', path)
    before = load(OUT / 'publication-before.json')
    after = dict(report['source_sha256'])
    assert set(after) <= {
        'src/sm64/sm64/src/pc/gfx/gfx_geometry.h',
        'src/sm64/sm64/src/pc/gfx/gfx_pc.c',
        'src/sm64/sm64/src/pc/gfx/gfx_vertex_cull.h',
    }
    after['.obj/sm64/app.elf'] = report['normal_app_sha256']
    after['.obj/sm64-perfdiag/app.elf'] = report['diagnostic_app_sha256']
    after['build/pocket/sm64/Assets/sm64/common/sm64.elf'] = report['diagnostic_app_sha256']
    staged = {path: STAGE / ('.obj/sm64-perfdiag/app.elf'
              if path.startswith('build/') else path) for path in after}
    for path, expected in after.items():
        assert sha(staged[path]) == expected, ('Staged file changed', path)
    for path, expected in before.items():
        assert sha(DEST / path) == (after.get(path, expected) if args.verify else expected), (
            'Destination changed', path)
    if args.verify:
        rows = load(OUT / 'published.json')['files']
        for row in rows:
            assert sha(BACKUP / row['path']) == row['before'], row['path']
        print('PASS: sources, apps, package, backups and protected SDK/runtime hashes.')
        return
    rows = [dict(path=path, before=before[path], after=value) for path, value in after.items()]
    print(json.dumps(dict(destination=str(DEST), backup=str(BACKUP), files=rows), indent=2))
    if not args.apply:
        return
    BACKUP.mkdir(exist_ok=False)
    for row in rows:
        saved = BACKUP / row['path']
        saved.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(DEST / row['path'], saved)
        assert sha(saved) == row['before']
    (BACKUP / 'files.json').write_text(json.dumps(rows, indent=2) + '\n')
    for row in rows:
        assert sha(DEST / row['path']) == row['before'], ('Destination changed', row['path'])
        atomic_copy(staged[row['path']], DEST / row['path'])
        assert sha(DEST / row['path']) == row['after']
    (OUT / 'published.json').write_text(json.dumps(dict(destination=str(DEST),
        backup=str(BACKUP), files=rows, qualification_sha256=sha(OUT / 'qualification.json')),
        indent=2) + '\n')
    print('Published qualified SM64 sources and apps. FPGA runtime unchanged.')


if __name__ == '__main__':
    main()
