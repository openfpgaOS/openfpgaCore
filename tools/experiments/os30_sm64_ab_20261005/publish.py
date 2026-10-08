#!/usr/bin/env python3
"""Copy the frozen comparison kit into SM64 without changing its default build."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / 'docs/measurements/os30-sm64-ab-20261005'
SOURCE = OUT / 'package'
DESTINATION = ROOT.parent / 'SM64/build/experiments/os30-ab-20261005'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    inventory = json.loads((OUT / 'package-inventory.json').read_text())['files']
    manifest = json.loads((SOURCE / 'manifest.json').read_text())
    assert set(inventory) == {str(p.relative_to(SOURCE)) for p in SOURCE.rglob('*') if p.is_file()}
    for rel, expected in inventory.items():
        assert sha(SOURCE / rel) == expected, ('Package changed', rel)
    game = ROOT.parent / 'SM64/build/pocket/sm64'
    for rel, expected in manifest['protected_files'].items():
        assert sha(game / rel) == expected, ('Default SM64 input changed', rel)
    default_core = game / manifest['bitstream_path']
    runtime_core = ROOT.parent / 'SM64/runtime/pocket/os30.rbf_r'
    dual = manifest['choices']['B-dual']['sha256']
    assert sha(default_core) == dual and sha(runtime_core) == dual
    assert not DESTINATION.exists(), 'Destination already exists; do not overwrite a test record.'
    print(json.dumps(dict(destination=str(DESTINATION), files=len(inventory),
                         default_app_and_core_unchanged=True), indent=2))
    if not args.apply:
        return
    DESTINATION.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(SOURCE, DESTINATION)
    for rel, expected in inventory.items():
        assert sha(DESTINATION / rel) == expected, ('Copy failed', rel)
    for rel, expected in manifest['protected_files'].items():
        assert sha(game / rel) == expected, ('Protected SM64 input changed', rel)
    assert sha(default_core) == dual and sha(runtime_core) == dual
    (OUT / 'published.json').write_text(json.dumps(dict(destination=str(DESTINATION),
        files=inventory, default_app_and_core_unchanged=True, pocket_deployed=False), indent=2) + '\n')
    print('Comparison kit copied and verified; default SM64 app/core retained.')


if __name__ == '__main__':
    main()
