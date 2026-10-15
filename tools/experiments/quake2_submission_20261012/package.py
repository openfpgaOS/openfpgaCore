#!/usr/bin/env python3
"""Verify rejected experiment evidence, or stage pinned source into a NEW directory."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import tarfile
import tempfile

HERE = Path(__file__).resolve().parent

def sha(data):
    return hashlib.sha256(data).hexdigest()

def safe_name(name):
    p = PurePosixPath(name)
    if not name or p.is_absolute() or any(x in ('', '.', '..') for x in p.parts) or str(p) != name:
        raise ValueError('Unsafe member/path: ' + name)
    return p

def read_json(path):
    return json.loads(path.read_text())

def checked_bytes(path, expected):
    if not path.is_file() or path.is_symlink():
        raise ValueError('Missing/linked file: ' + str(path))
    data = path.read_bytes()
    if sha(data) != expected:
        raise ValueError('Hash differs: ' + str(path))
    return data

def baseline_members(manifest):
    pins = manifest['baseline']['sha256']
    seen = set()
    safe_name(manifest['baseline']['archive'])
    with tarfile.open(HERE / manifest['baseline']['archive'], 'r:gz') as archive:
        for member in archive:
            safe_name(member.name)
            if not member.isfile() or member.name in seen or member.name not in pins:
                raise ValueError('Unexpected, duplicate, or non-file archive member: ' + member.name)
            seen.add(member.name)
            data = archive.extractfile(member).read()
            if sha(data) != pins[member.name]:
                raise ValueError('Baseline archive bytes differ: ' + member.name)
            yield member.name, data, member.mode & 0o777
    if seen != set(pins):
        raise ValueError('Incomplete baseline archive')

def source_pins(manifest, variant):
    pins = dict(manifest['baseline']['sha256'])
    for name, item in manifest['variants'][variant]['changed'].items():
        pins[name] = item['after_sha256']
    return pins

def check_source(directory, pins):
    for name, expected in pins.items():
        safe_name(name)
        checked_bytes(directory / name, expected)

def verify(manifest, patches=True):
    listed = set(manifest['files']) | {'manifest.json'}
    actual = {str(p.relative_to(HERE)) for p in HERE.rglob('*') if p.is_file() or p.is_symlink()}
    if listed != actual:
        raise ValueError('Package inventory differs: ' + str(sorted(listed ^ actual)))
    for name, expected in manifest['files'].items():
        safe_name(name)
        checked_bytes(HERE / name, expected)
    base = {name: (data, mode) for name, data, mode in baseline_members(manifest)}
    for variant, spec in manifest['variants'].items():
        safe_name(spec['patch'])
        for name, item in spec['changed'].items():
            safe_name(name)
            safe_name(item['postimage'])
            if item['preimage'] is not None:
                safe_name(item['preimage'])
            if item['before_sha256'] is not None:
                if name not in base or sha(base[name][0]) != item['before_sha256']:
                    raise ValueError('Variant baseline differs: ' + name)
                if checked_bytes(HERE / item['preimage'], item['before_sha256']) != base[name][0]:
                    raise ValueError('Preimage differs: ' + name)
            elif name in base:
                raise ValueError('New file unexpectedly exists in baseline: ' + name)
            checked_bytes(HERE / item['postimage'], item['after_sha256'])
        if patches:
            with tempfile.TemporaryDirectory(prefix='q2-package-patch-') as tmp:
                tree = Path(tmp)
                for name, (data, mode) in base.items():
                    path = tree / name; path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(data); path.chmod(mode)
                command = ['patch', '--batch', '--fuzz=0', '-p1', '-i', str(HERE / spec['patch'])]
                subprocess.run(command, cwd=tree, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                check_source(tree, source_pins(manifest, variant))
                subprocess.run(command[:1] + ['--reverse'] + command[1:], cwd=tree, check=True,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                check_source(tree, manifest['baseline']['sha256'])
                for name, item in spec['changed'].items():
                    if item['before_sha256'] is None and (tree / name).exists():
                        raise ValueError('Reverse patch did not remove: ' + name)
    return base

def stage(manifest, variant, destination):
    base = verify(manifest)
    # mkdir is exclusive: never overwrite a user tree, even between check/create.
    destination = destination.absolute()
    if destination.exists() or destination.is_symlink():
        raise ValueError('Destination must not exist: ' + str(destination))
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.mkdir()
    try:
        for name, (data, mode) in base.items():
            path = destination / name; path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data); path.chmod(mode)
        for name, item in manifest['variants'][variant]['changed'].items():
            path = destination / name; path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(checked_bytes(HERE / item['postimage'], item['after_sha256']))
            path.chmod(item['mode'])
        check_source(destination, source_pins(manifest, variant))
        (destination / 'evidence').mkdir(exist_ok=True)
    except Exception:
        # Leave the newly created partial tree for inspection; never remove an
        # arbitrary user directory or silently imply that staging succeeded.
        print('FAILED; partial new destination remains:', destination)
        raise
    print('Staged', variant, 'at', destination)

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    sub.add_parser('verify')
    prepare = sub.add_parser('stage')
    prepare.add_argument('variant', choices=['world', 'alias'])
    prepare.add_argument('destination', type=Path)
    check = sub.add_parser('check-stage')
    check.add_argument('variant', choices=['world', 'alias'])
    check.add_argument('destination', type=Path)
    args = parser.parse_args()
    if (HERE / 'manifest.json').is_symlink():
        raise ValueError('Linked package manifest refused')
    manifest = read_json(HERE / 'manifest.json')
    if args.action == 'stage':
        stage(manifest, args.variant, args.destination)
    elif args.action == 'check-stage':
        verify(manifest)
        check_source(args.destination, source_pins(manifest, args.variant))
        print('PASS: staged source pins')
    else:
        verify(manifest)
        print('PASS: package hashes, 715 baseline files, both source overlays, forward/reverse patches')

if __name__ == '__main__':
    main()
