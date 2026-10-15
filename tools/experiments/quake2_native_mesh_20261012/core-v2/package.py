#!/usr/bin/env python3
"""Verify the pinned native-mesh core or stage it into a NEW directory."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import subprocess
import tarfile
import tempfile

HERE = Path(__file__).resolve().parent


def sha(data):
    return hashlib.sha256(data).hexdigest()


def safe_name(name):
    p = PurePosixPath(name)
    if not name or not p.parts or p.is_absolute() or str(p) != name or any(x in ('', '.', '..') for x in p.parts):
        raise ValueError('Unsafe package path: ' + name)


def checked(path, expected):
    if path.is_symlink() or not path.is_file():
        raise ValueError('Missing or linked file: ' + str(path))
    data = path.read_bytes()
    if sha(data) != expected:
        raise ValueError('Hash differs: ' + str(path))
    return data


def source_members(manifest):
    spec = manifest['source']
    safe_name(spec['archive'])
    seen = set()
    with tarfile.open(HERE / spec['archive'], 'r:gz') as archive:
        for member in archive:
            safe_name(member.name)
            if not member.isfile() or member.name in seen or member.name not in spec['sha256']:
                raise ValueError('Unexpected, duplicate or non-file member: ' + member.name)
            seen.add(member.name)
            data = archive.extractfile(member).read()
            if sha(data) != spec['sha256'][member.name]:
                raise ValueError('Source archive hash differs: ' + member.name)
            yield member.name, data, member.mode & 0o777
    if seen != set(spec['sha256']):
        raise ValueError('Incomplete source archive')


def verify(manifest):
    actual = {str(p.relative_to(HERE)) for p in HERE.rglob('*') if p.is_file() or p.is_symlink()}
    if actual != set(manifest['files']) | {'manifest.json'}:
        raise ValueError('Package inventory differs')
    for name, expected in manifest['files'].items():
        safe_name(name)
        checked(HERE / name, expected)
    source = {name: (data, mode) for name, data, mode in source_members(manifest)}
    fit_pins = manifest['fit_input_sha256']
    normalized = {('parent-preparation-receipt.json' if name == 'build-receipt.json' else name): sha(data)
                  for name, (data, _) in source.items()}
    if normalized != fit_pins:
        raise ValueError('Source differs from the recorded physical fit input')
    with tempfile.TemporaryDirectory(prefix='q2-native-core-delta-') as tmp:
        tree = Path(tmp)
        for name, spec in manifest['delta'].items():
            safe_name(name)
            for key in ('preimage', 'postimage'):
                safe_name(spec[key])
            before = checked(HERE / spec['preimage'], spec['before_sha256'])
            after = checked(HERE / spec['postimage'], spec['after_sha256'])
            if source[name][0] != after:
                raise ValueError('Source archive/postimage mismatch: ' + name)
            target = tree / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(before)
        safe_name(manifest['patch'])
        command = ['patch', '--batch', '--fuzz=0', '-p1', '-i', str(HERE / manifest['patch'])]
        subprocess.run(command, cwd=tree, check=True, capture_output=True)
        for name, spec in manifest['delta'].items():
            checked(tree / name, spec['after_sha256'])
        subprocess.run(command[:1] + ['--reverse'] + command[1:], cwd=tree, check=True, capture_output=True)
        for name, spec in manifest['delta'].items():
            checked(tree / name, spec['before_sha256'])
    return source


def check_stage(directory, pins):
    for name, expected in pins.items():
        safe_name(name)
        checked(directory / name, expected)


def stage(source, directory, pins):
    directory = directory.absolute()
    if directory.exists() or directory.is_symlink():
        raise ValueError('Destination must not exist: ' + str(directory))
    directory.parent.mkdir(parents=True, exist_ok=True)
    directory.mkdir()  # Exclusive, including a destination created after the check.
    try:
        for name, (data, mode) in source.items():
            target = directory / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            target.chmod(mode)
        check_stage(directory, pins)
    except Exception:
        print('FAILED; newly created partial stage remains:', directory)
        raise
    print('Staged pinned native-mesh core:', directory)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest='action', required=True)
    subs.add_parser('verify')
    for action in ('stage', 'check-stage'):
        subs.add_parser(action).add_argument('directory', type=Path)
    args = parser.parse_args()
    path = HERE / 'manifest.json'
    if path.is_symlink():
        raise ValueError('Linked manifest refused')
    manifest = json.loads(path.read_text())
    source = verify(manifest)
    if args.action == 'stage':
        stage(source, args.directory, manifest['source']['sha256'])
    elif args.action == 'check-stage':
        check_stage(args.directory, manifest['source']['sha256'])
        print('PASS: staged source pins')
    else:
        print('PASS: package inventory, source/fit-input pins and forward/reverse RTL delta')


if __name__ == '__main__':
    main()
