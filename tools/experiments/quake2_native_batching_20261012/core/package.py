#!/usr/bin/env python3
"""Verify or stage the exact, immutable MAC-stream + CB4 experiment."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import tarfile

HERE = Path(__file__).resolve().parent


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def safe_name(name):
    p = PurePosixPath(name)
    if p.is_absolute() or '..' in p.parts or str(p) != name:
        raise ValueError('Unsafe source path: ' + name)
    return name


def manifest():
    return json.loads((HERE / 'manifest.json').read_text())


def members(m):
    seen = set()
    with tarfile.open(HERE / m['source_archive'], 'r:gz') as archive:
        for item in archive:
            name = safe_name(item.name)
            if not item.isfile() or name in seen:
                raise ValueError('Non-file or duplicate member: ' + name)
            seen.add(name)
            data = archive.extractfile(item).read()
            if hashlib.sha256(data).hexdigest() != m['source_sha256'].get(name):
                raise ValueError('Source archive hash differs: ' + name)
            yield name, data
    if seen != set(m['source_sha256']):
        raise ValueError('Source archive inventory differs')


def verify():
    m = manifest()
    actual = {str(p.relative_to(HERE)) for p in HERE.rglob('*') if p.is_file()
              and p.name != 'manifest.json' and '__pycache__' not in p.parts}
    if actual != set(m['files']):
        raise ValueError('Package inventory differs')
    for name, digest in m['files'].items():
        safe_name(name)
        if sha(HERE / name) != digest:
            raise ValueError('Package hash differs: ' + name)
    list(members(m))
    fit = json.loads((HERE / m['recorded_fit']).read_text())
    physical = json.loads((HERE / m['physical_result']).read_text())
    if fit['source_sha256'] != m['source_sha256']:
        raise ValueError('Recorded fit source pins differ')
    if sha(HERE / 'evidence/executed_fit_recipe.py') != fit['recipe_sha256']:
        raise ValueError('Executed recipe differs')
    if not (sha(HERE / m['artifact']) == m['artifact_sha256'] ==
            fit['bitstream_sha256'] == physical['bitstream_sha256']):
        raise ValueError('Artifact receipt differs')
    tests = json.loads((HERE / 'evidence/worker/receipt.json').read_text())
    for name, digest in tests['source_sha256'].items():
        if sha(HERE / 'worker-tests' / name) != digest:
            raise ValueError('Executed worker test source differs: ' + name)
    worker = sha(HERE / 'worker-tests/gpu_geometry_worker.v')
    if worker != m['source_sha256']['src/fpga/common/gpu_geometry_worker.v']:
        raise ValueError('Worker tests do not cover fitted worker')
    if sha(HERE / 'replay/rtl/common/gpu_geometry_worker.v') != worker:
        raise ValueError('Replay worker differs')
    replay = json.loads((HERE / 'evidence/replays/mac-cb4/result.json').read_text())
    if not replay['passed'] or not all(r['whole_output_exact'] for r in replay['frames']):
        raise ValueError('Recorded exact replay did not pass')
    return m


def check_stage(directory, m=None):
    m = m or verify()
    actual = {str(p.relative_to(directory)) for p in directory.rglob('*') if p.is_file()}
    if actual != set(m['source_sha256']):
        raise ValueError('Staged inventory differs')
    for name, digest in m['source_sha256'].items():
        if (directory / name).is_symlink() or sha(directory / name) != digest:
            raise ValueError('Staged hash differs: ' + name)


def stage(directory):
    m = verify()
    if directory.exists():
        raise ValueError('Destination must not exist')
    data = list(members(m))  # Validate all members before any writes.
    directory.mkdir(parents=True)
    for name, content in data:
        target = directory / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    check_stage(directory, m)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['verify', 'stage', 'check-stage'])
    parser.add_argument('directory', type=Path, nargs='?')
    args = parser.parse_args()
    if args.action == 'verify':
        verify()
    elif not args.directory:
        parser.error('directory required')
    elif args.action == 'stage':
        stage(args.directory.resolve())
    else:
        check_stage(args.directory.resolve())
    print('PASS', args.action)
