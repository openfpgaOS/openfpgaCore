#!/usr/bin/env python3
"""Publish a qualified SM64 candidate with retained single-issue core A; dry-run by default."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / 'docs/measurements/os30-simple-20261005'
DEST = ROOT.parent / 'SM64'
BACKUP = OUT / 'publication-backup'
SELECTION = OUT / 'selection.json'
GFX = 'src/sm64/sm64/src/pc/gfx/'
INPUTS = ('gfx_pc.c', 'gfx_gpu.c', 'gfx_gpu.h', 'gfx_geometry.h')
ALLOWED_SOURCES = {GFX + name for name in ('gfx_pc.c', 'gfx_gpu.c')}
CORE_A = 'd3d90e20c7116304108a2f94b65d3a69335347603fab388b905cafc3fc6f5f9f'
CORE_B = 'b4d55466283320c798047effa1dd9895d990fd6f297857e52ad99ec9bbf3778d'
CORE_SOURCE = ROOT / ('docs/measurements/os30-sm64-ab-20261005/package/'
                      'A-single/Cores/ThinkElastic.SM64/os30.rbf_r')
CORE_PATHS = ('runtime/pocket/os30.rbf_r',
              'build/pocket/sm64/Cores/ThinkElastic.SM64/os30.rbf_r')
APP_PATHS = ('.obj/sm64/app.elf', '.obj/sm64-perfdiag/app.elf',
             'build/pocket/sm64/Assets/sm64/common/sm64.elf')


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def load(path):
    return json.loads(path.read_text())


def check_sha(path, expected):
    require(sha(path) == expected, f'File changed or missing: {path}')


def local_path(base, relative):
    path = Path(relative)
    require(not path.is_absolute() and '..' not in path.parts and str(path) != '.',
            f'Expected a relative local path: {relative}')
    result = base / path
    require(result.resolve().is_relative_to(base.resolve()), f'Path escapes root: {result}')
    return result


def atomic_copy(source, destination, expected):
    check_sha(source, expected)
    with tempfile.NamedTemporaryFile(dir=destination.parent,
                                     prefix='.' + destination.name + '.', delete=False) as f:
        temporary = Path(f.name)
        try:
            with source.open('rb') as incoming:
                shutil.copyfileobj(incoming, f)
            f.flush()
            os.fsync(f.fileno())
            shutil.copymode(source, temporary)
            check_sha(temporary, expected)
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
    check_sha(destination, expected)


def write_json(path, value):
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.' + path.name + '.',
                                     mode='w', delete=False) as f:
        temporary = Path(f.name)
        try:
            json.dump(value, f, indent=2)
            f.write('\n')
            f.flush()
            os.fsync(f.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


def before_inventory():
    before = load(OUT / 'publication-before.json')
    require(before['destination'] == str(DEST), 'Unexpected destination')
    check_sha(OUT / 'source-baseline.json', before['source_baseline_sha256'])
    return before['files']


def check_destination(before, after=None):
    after = after or {}
    for path, expected in before.items():
        check_sha(local_path(DEST, path), after.get(path, expected))


def selected_files(before):
    selected = load(SELECTION)
    require(selected['qualified'] is True, 'Candidate is not qualified for publication')
    name = selected['candidate']
    require(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]*', name) is not None,
            'Invalid candidate name')
    require(selected['core'] == 'A-single' and selected['core_sha256'] == CORE_A,
            'Only the retained single-issue A core is allowed')
    require(selected['evidence_sha256'], 'Missing validation evidence')
    for path, expected in selected['evidence_sha256'].items():
        check_sha(local_path(ROOT, path), expected)
    candidate = OUT / name
    sources = selected['source_sha256']
    require(sources and set(sources) <= ALLOWED_SOURCES, 'Unexpected source publication scope')
    staged = {path: candidate / Path(path).name for path in sources}
    after = dict(sources)
    for label, diagnostic, filename, app_hash in (
        ('normal', False, 'app.elf', selected['normal_app_sha256']),
        ('diagnostic', True, 'diagnostic.elf', selected['diagnostic_app_sha256']),
    ):
        record = load(candidate / f'build-{label}.json')
        require(record['returncode'] == 0 and record['diagnostic'] is diagnostic,
                f'Invalid {label} build record')
        require(record['elf_sha256'] == app_hash, f'Unexpected {label} app hash')
        check_sha(candidate / filename, app_hash)
        require(set(record['inputs']) == set(INPUTS), 'Unexpected build inputs')
        for basename, info in record['inputs'].items():
            path = GFX + basename
            expected = sources.get(path, before[path])
            require(info['sha256'] == expected, f'{label} build source mismatch: {path}')
            source = candidate / basename
            if not source.exists():
                source = OUT / 'baseline' / basename
            check_sha(source, expected)
    staged[APP_PATHS[0]] = candidate / 'app.elf'
    after[APP_PATHS[0]] = selected['normal_app_sha256']
    for path in APP_PATHS[1:]:
        staged[path] = candidate / 'diagnostic.elf'
        after[path] = selected['diagnostic_app_sha256']
    for path in CORE_PATHS:
        require(before[path] == CORE_B, 'Expected the frozen default dual-issue bitstream')
        staged[path] = CORE_SOURCE
        after[path] = CORE_A
    require(set(after) <= set(before), 'Publication would introduce an unrecorded file')
    for path, expected in after.items():
        check_sha(staged[path], expected)
    return selected, staged, after


def rollback(apply):
    record = load(BACKUP / 'files.json')
    require(record['destination'] == str(DEST), 'Unexpected rollback destination')
    allowed = ALLOWED_SOURCES | set(APP_PATHS) | set(CORE_PATHS)
    require({row['path'] for row in record['files']} <= allowed, 'Unexpected rollback scope')
    for row in record['files']:
        check_sha(local_path(BACKUP, row['path']), row['before'])
        require(sha(local_path(DEST, row['path'])) in (row['before'], row['after']),
                f"Destination was edited after publication: {row['path']}")
    print(json.dumps({'mode': 'rollback' if apply else 'rollback dry-run',
                      'destination': str(DEST), 'files': record['files']}, indent=2))
    if not apply:
        return
    # Mixed before/after states are accepted to recover an interrupted publication.
    for row in record['files']:
        destination = local_path(DEST, row['path'])
        require(sha(destination) in (row['before'], row['after']),
                f"Destination changed during rollback: {row['path']}")
        atomic_copy(local_path(BACKUP, row['path']), destination, row['before'])
    write_json(OUT / 'rolled-back.json', record)
    print('Restored previous SM64 sources, apps and local B bitstream. Pocket SD was not touched.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--apply', action='store_true')
    group.add_argument('--verify', action='store_true')
    group.add_argument('--check-before', action='store_true')
    parser.add_argument('--rollback', action='store_true',
                        help='check rollback; combine with --apply to restore')
    args = parser.parse_args()
    require(not args.rollback or not (args.verify or args.check_before),
            '--rollback cannot be combined with --verify or --check-before')
    if args.rollback:
        rollback(args.apply)
        return
    before = before_inventory()
    if args.check_before:
        check_destination(before)
        print(f'PASS: {len(before)} frozen SM64 source, SDK, runtime and package hashes unchanged.')
        return
    selected, staged, after = selected_files(before)
    check_destination(before, after if args.verify else None)
    if args.verify:
        record = load(OUT / 'published.json')
        check_sha(SELECTION, record['selection_sha256'])
        check_sha(OUT / 'publication-before.json', record['before_inventory_sha256'])
        require({row['path']: row['after'] for row in record['files']} == after,
                'Publication record differs from selected files')
        for row in record['files']:
            check_sha(local_path(BACKUP, row['path']), row['before'])
        print('PASS: selected sources/apps, both A bitstreams, backups and protected files.')
        return
    rows = [dict(path=path, before=before[path], after=value)
            for path, value in after.items()]
    record = dict(destination=str(DEST), backup=str(BACKUP), candidate=selected['candidate'],
                  files=rows, selection_sha256=sha(SELECTION),
                  before_inventory_sha256=sha(OUT / 'publication-before.json'),
                  core='A-single', clock_configured_mhz=100, new_fpga_build=False,
                  pocket_sd_updated=False, quake2_updated=False)
    print(json.dumps(dict(mode='apply' if args.apply else 'dry-run', **record), indent=2))
    if not args.apply:
        return
    require(not (OUT / 'published.json').exists(), 'Publication already recorded')
    BACKUP.mkdir(exist_ok=False)
    for row in rows:
        saved = local_path(BACKUP, row['path'])
        saved.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(local_path(DEST, row['path']), saved)
        check_sha(saved, row['before'])
    # Persist the recovery list before replacing any destination file.
    write_json(BACKUP / 'files.json', record)
    for row in rows:
        destination = local_path(DEST, row['path'])
        check_sha(destination, row['before'])
        atomic_copy(staged[row['path']], destination, row['after'])
    check_destination(before, after)
    write_json(OUT / 'published.json', record)
    print('Published SM64 sources/apps and retained A core locally. Pocket SD and Quake2 were not touched.')


if __name__ == '__main__':
    main()
