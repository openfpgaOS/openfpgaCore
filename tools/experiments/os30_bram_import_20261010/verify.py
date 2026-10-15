#!/usr/bin/env python3
"""Verify packaged artifacts and optionally the exact local patch preimages."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence', action='store_true')
    parser.add_argument('--patches', action='store_true')
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    manifest = json.loads((root / 'manifest.json').read_text())
    for name, expected in manifest['files'].items():
        assert digest(root / name) == expected, name
    if args.evidence:
        results = json.loads((root / 'results.json').read_text())
        evidence = Path(results['evidence_root'])
        for name, expected in results['test_evidence'].items():
            assert digest(evidence / name) == expected, name
        for run in results['runs']:
            for side in ('baseline', 'candidate'):
                item = run[side + '_run_manifest']
                assert digest(Path(item['path'])) == item['sha256'], item['path']
    if args.patches:
        for name, patch in manifest['patches'].items():
            with tempfile.TemporaryDirectory(prefix='os30-bram-check-') as directory:
                destination = Path(directory)
                for relative, hashes in patch['files'].items():
                    path = Path(relative)
                    assert not path.is_absolute() and '..' not in path.parts
                    source = Path(patch['base']) / path
                    if hashes['before_sha256'] is None:
                        assert not source.exists(), source
                    else:
                        assert digest(source) == hashes['before_sha256'], source
                        (destination / path).parent.mkdir(parents=True, exist_ok=True)
                        shutil.copyfile(source, destination / path)
                subprocess.run(['patch', '-p1', '--batch', '--forward', '--fuzz=0',
                                '-i', str(root / name)], cwd=destination,
                               check=True, stdout=subprocess.DEVNULL)
                for relative, hashes in patch['files'].items():
                    assert digest(destination / relative) == hashes['after_sha256'], relative
    print('PASS: experimental package integrity' +
          ('; local evidence bindings' if args.evidence else '') +
          ('; exact patch round trips' if args.patches else ''))


if __name__ == '__main__':
    main()
