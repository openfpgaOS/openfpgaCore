#!/usr/bin/env python3
"""Verify package hashes, exact patch round trips, and optional local evidence."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()


def safe(name):
    path = Path(name)
    assert not path.is_absolute() and '..' not in path.parts, name
    return path


def roundtrip(package, name, patch):
    with tempfile.TemporaryDirectory(prefix='os30-depth-patch-') as directory:
        root = Path(directory)
        for relative, hashes in patch['files'].items():
            source = Path(patch['base']) / safe(relative)
            assert sha(source) == hashes['before_sha256'], source
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
        for reverse, expected in ((False, 'after_sha256'), (True, 'before_sha256')):
            command = ['patch', '-p1', '--batch', '--fuzz=0',
                       '--reverse' if reverse else '--forward', '-i', str(package / name)]
            run = subprocess.run(command, cwd=root, text=True, capture_output=True)
            assert run.returncode == 0, run.stdout + run.stderr
            assert 'offset' not in run.stdout and 'fuzz' not in run.stdout, run.stdout
            for relative, hashes in patch['files'].items():
                assert sha(root / relative) == hashes[expected], relative


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--patches', action='store_true')
    parser.add_argument('--evidence', action='store_true')
    args = parser.parse_args()
    package = Path(__file__).resolve().parent
    manifest = json.loads((package / 'manifest.json').read_text())
    listed = set(manifest['files']) | {'manifest.json'}
    actual = {str(p.relative_to(package)) for p in package.rglob('*') if p.is_file()}
    assert listed == actual, (listed - actual, actual - listed)
    for name, expected in manifest['files'].items():
        assert sha(package / safe(name)) == expected, name
    if args.patches:
        for name, patch in manifest['patches'].items():
            roundtrip(package, name, patch)
    if args.evidence:
        for name, expected in manifest['external_evidence_sha256'].items():
            assert sha(Path(name)) == expected, name
        for model_name in manifest['native_models']:
            model = Path(model_name)
            prepared = json.loads((model / 'prepared.json').read_text())
            for name, expected in prepared['source_sha256'].items():
                assert sha(model / safe(name)) == expected, (model_name, name)
        for name in manifest['test_manifests']:
            test = json.loads(Path(name).read_text())
            for source, expected in test['inputs_sha256'].items():
                assert sha(Path(source)) == expected, source
    print('PASS: package hashes' + ('; exact forward/reverse patches' if args.patches else '')
          + ('; native and test evidence bindings' if args.evidence else ''))


if __name__ == '__main__':
    main()
