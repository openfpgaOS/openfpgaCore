#!/usr/bin/env python3
"""Verify source package, exact patch round trips, and optional local evidence."""
import argparse
import hashlib
import json
import posixpath
from pathlib import Path
import shutil
import subprocess
import tempfile


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def safe_relative(name):
    path = Path(name)
    assert not path.is_absolute() and '..' not in path.parts, name
    return path


def resolve(name, package):
    if name.startswith('$PACKAGE/'):
        return package / safe_relative(name[len('$PACKAGE/'):])
    return Path(name)


def roundtrip(package, name, item):
    with tempfile.TemporaryDirectory(prefix='os30-triangle-patch-') as temp:
        destination = Path(temp)
        for relative, hashes in item['files'].items():
            path = safe_relative(relative)
            source = Path(item['base']) / path
            target = destination / path
            target.parent.mkdir(parents=True, exist_ok=True)
            if hashes['before_sha256'] is None:
                assert not source.exists(), source
            else:
                assert digest(source) == hashes['before_sha256'], source
                shutil.copyfile(source, target)
        forward = ['patch', '-p1', '--batch', '--forward', '--fuzz=0',
                   '-i', str(package / name)]
        run = subprocess.run(forward, cwd=destination, capture_output=True, text=True)
        assert run.returncode == 0, run.stdout + run.stderr
        assert 'offset' not in run.stdout and 'fuzz' not in run.stdout, run.stdout
        for relative, hashes in item['files'].items():
            assert digest(destination / relative) == hashes['after_sha256'], relative
        reverse = ['patch', '-p1', '--batch', '--reverse', '--fuzz=0',
                   '-i', str(package / name)]
        run = subprocess.run(reverse, cwd=destination, capture_output=True, text=True)
        assert run.returncode == 0, run.stdout + run.stderr
        assert 'offset' not in run.stdout and 'fuzz' not in run.stdout, run.stdout
        for relative, hashes in item['files'].items():
            target = destination / relative
            if hashes['before_sha256'] is None:
                assert not target.exists(), target
            else:
                assert digest(target) == hashes['before_sha256'], relative


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--patches', action='store_true')
    parser.add_argument('--evidence', action='store_true')
    args = parser.parse_args()
    package = Path(__file__).resolve().parent
    manifest = json.loads((package / 'manifest.json').read_text())
    for name, expected in manifest['files'].items():
        assert digest(package / safe_relative(name)) == expected, name
    physical = dict(manifest['qualified_baseline_sources_sha256'])
    for name, hashes in manifest['patches']['core.patch']['files'].items():
        if name.startswith('frozen/'):
            physical[name[len('frozen/'):]] = hashes['after_sha256']
    assert physical == manifest['physical_post_sources_sha256']
    for line in (package / 'project/ap_core.qsf').read_text().splitlines():
        if (line.startswith('set_global_assignment ') and '_FILE ' in line
                and '../frozen/' in line):
            name = posixpath.normpath(line.split('../frozen/', 1)[1].strip().strip('"'))
            assert name in physical, name
    if args.patches:
        for name, item in manifest['patches'].items():
            roundtrip(package, name, item)
    if args.evidence:
        for name, expected in manifest['external_evidence_sha256'].items():
            assert digest(Path(name)) == expected, name
        for patch in manifest['patches'].values():
            for relative, hashes in patch['files'].items():
                source = resolve(hashes['postimage_source'], package)
                assert digest(source) == hashes['after_sha256'], source
        model = Path(manifest['native_model'])
        prepared = json.loads((model / 'prepared.json').read_text())
        for name, expected in prepared['source_sha256'].items():
            assert digest(model / safe_relative(name)) == expected, name
        baseline = Path(manifest['physical_baseline'])
        for name, expected in manifest['qualified_baseline_sources_sha256'].items():
            assert digest(baseline / 'frozen' / safe_relative(name)) == expected, name
        for name in manifest['test_manifests']:
            evidence = json.loads(Path(name).read_text())
            for source, expected in evidence.get('inputs_sha256', {}).items():
                assert digest(Path(source)) == expected, source
    print('PASS: source package integrity' +
          ('; exact forward/reverse patch round trips' if args.patches else '') +
          ('; native, physical, app and test evidence bindings' if args.evidence else ''))


if __name__ == '__main__':
    main()
