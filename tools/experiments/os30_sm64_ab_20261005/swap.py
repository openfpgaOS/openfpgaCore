#!/usr/bin/env python3
"""Verify or install one saved SM64 core; no app, setting or save is changed."""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('choice', choices=['A-single', 'B-dual', 'C-geometry'])
    parser.add_argument('--card', type=Path, default=Path('/run/media/alberto/POCKETDEV'))
    parser.add_argument('--apply', action='store_true', help='Write the selected bitstream after verification.')
    args = parser.parse_args()
    package = Path(__file__).resolve().parent
    if not (package / 'manifest.json').is_file():
        package = package.parents[2] / 'docs/measurements/os30-sm64-ab-20261005/package'
    manifest = json.loads((package / 'manifest.json').read_text())
    core_rel = manifest['bitstream_path']
    choice = manifest['choices'][args.choice]
    source = package / args.choice / core_rel
    assert sha(source) == choice['sha256'], 'Selected image failed its saved checksum.'
    card = args.card.resolve()
    assert card.is_dir(), f'Card is not mounted at {card}'
    mount = json.loads(subprocess.check_output([
        'findmnt', '-J', '-o', 'TARGET,SOURCE,FSTYPE,UUID,OPTIONS', '-T', str(card)], text=True))['filesystems'][0]
    assert mount['target'] == str(card) and mount['uuid'] == '995B-1B19', ('Unexpected SD card', mount)
    assert mount['fstype'] == 'vfat', mount
    for rel, expected in manifest['protected_files'].items():
        assert sha(card / rel) == expected, ('Common test input changed', rel)
    destination = card / core_rel
    previous = sha(destination)
    known = {v['sha256']: k for k, v in manifest['choices'].items()}
    assert previous in known, 'Current bitstream is not one of the three saved test images.'
    change = previous != choice['sha256']
    summary = dict(card=str(card), previous=known[previous], selected=args.choice,
                   geometry_expected=choice['geometry'], write_needed=change,
                   sha256=choice['sha256'], app_sha256=manifest['protected_files']['Assets/sm64/common/sm64.elf'])
    print(json.dumps(summary, indent=2))
    if not args.apply:
        print('Verification only. Add --apply after quitting the core and mounting the card.')
        return
    assert 'rw' in mount['options'].split(','), 'SD card is not writable.'
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
    backup = package / 'swap-records' / stamp
    backup.mkdir(parents=True)
    shutil.copy2(destination, backup / 'before.rbf_r')
    assert sha(backup / 'before.rbf_r') == previous
    if change:
        with tempfile.NamedTemporaryFile(dir=destination.parent, prefix='.os30-ab-', delete=False) as f:
            temporary = Path(f.name)
            try:
                with source.open('rb') as incoming:
                    shutil.copyfileobj(incoming, f)
                f.flush()
                os.fsync(f.fileno())
                assert sha(temporary) == choice['sha256']
                assert sha(destination) == previous, 'Card bitstream changed during verification.'
                os.replace(temporary, destination)
            finally:
                temporary.unlink(missing_ok=True)
    subprocess.run(['sync', '-f', str(card)], check=True)
    assert sha(destination) == choice['sha256'], 'Readback checksum failed.'
    for rel, expected in manifest['protected_files'].items():
        assert sha(card / rel) == expected, ('Protected file changed', rel)
    summary.update(written=change, synced=True, verified=True,
                   backup=str(backup / 'before.rbf_r'), completed_utc=stamp)
    (backup / 'result.json').write_text(json.dumps(summary, indent=2) + '\n')
    (package / 'last-swap.json').write_text(json.dumps(summary, indent=2) + '\n')
    print('Verified. Safely eject the card, then launch the existing SM64 entry.')


if __name__ == '__main__':
    main()
