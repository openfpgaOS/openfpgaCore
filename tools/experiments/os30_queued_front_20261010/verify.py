#!/usr/bin/env python3
"""Check package bytes, exact source-patch round trips and local evidence."""
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
    with tempfile.TemporaryDirectory(prefix='os30-queued-patch-') as temp:
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
        for direction in ['--forward', '--reverse']:
            run = subprocess.run(['patch', '-p1', '--batch', direction, '--fuzz=0',
                                  '-i', str(package / safe_relative(name))],
                                 cwd=destination, capture_output=True, text=True)
            log = run.stdout + run.stderr
            assert run.returncode == 0 and 'offset' not in log and 'fuzz' not in log, log
            expected_files = set()
            for relative, hashes in item['files'].items():
                expected = hashes['after_sha256' if direction == '--forward' else 'before_sha256']
                target = destination / safe_relative(relative)
                if expected is None:
                    assert not target.exists(), target
                else:
                    assert digest(target) == expected, relative
                    expected_files.add(relative)
            actual_files = {str(p.relative_to(destination)) for p in destination.rglob('*') if p.is_file()}
            assert actual_files == expected_files, (actual_files ^ expected_files)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--patches', action='store_true', help='Apply/reverse all patches only in temporary directories.')
    parser.add_argument('--evidence', action='store_true', help='Verify external immutable experiment inputs; requires original local layout.')
    args = parser.parse_args()
    package = Path(__file__).resolve().parent
    manifest = json.loads((package / 'manifest.json').read_text())
    excluded = set(manifest['excluded_inventory'])
    actual = {str(p.relative_to(package)): digest(p) for p in package.rglob('*')
              if p.is_file() and p.name not in excluded and '__pycache__' not in p.parts}
    assert actual == manifest['files'], 'Package file inventory/hash mismatch'
    physical = dict(manifest['qualified_baseline_sources_sha256'])
    for name, hashes in manifest['patches']['core.patch']['files'].items():
        if name.startswith('frozen/'):
            physical[name[len('frozen/'):]] = hashes['after_sha256']
    assert physical == manifest['physical_post_sources_sha256']
    assert len(physical) == 361
    project = (package / 'project/ap_core.qsf').read_text()
    for line in project.splitlines():
        if line.startswith('set_global_assignment ') and '_FILE ' in line and '../frozen/' in line:
            name = posixpath.normpath(line.split('../frozen/', 1)[1].strip().strip('"'))
            assert name in physical, name
    forbidden = ['.elf', '.rbf', '.rbf_r', '.bin', '.z64', '.n64', '.v64', '.o', '.a', '.qdb']
    assert not any(Path(name).suffix in forbidden for name in actual), 'Binary/game asset in source package'
    if args.patches:
        for name, item in manifest['patches'].items():
            roundtrip(package, name, item)
    if args.evidence:
        for name, expected in manifest['external_evidence_sha256'].items():
            assert digest(Path(name)) == expected, name
        for patch in manifest['patches'].values():
            for hashes in patch['files'].values():
                source = resolve(hashes['postimage_source'], package)
                assert digest(source) == hashes['after_sha256'], source
        native = Path(manifest['native_model'])
        prepared = json.loads((native / 'prepared.json').read_text())
        for name, expected in prepared['source_sha256'].items():
            assert digest(native / safe_relative(name)) == expected, name
        baseline = Path(manifest['physical_baseline'])
        for name, expected in manifest['qualified_baseline_sources_sha256'].items():
            assert digest(baseline / 'frozen' / safe_relative(name)) == expected, name
        stage = Path(manifest['physical_stage'])
        for name, expected in physical.items():
            assert digest(stage / 'frozen' / safe_relative(name)) == expected, name
        staged_qsf = stage / 'build/ap_core.qsf'
        assert digest(staged_qsf) == manifest['physical_qsf_sha256']
        assert project.replace('../frozen', str(stage / 'frozen')) == staged_qsf.read_text()
        for name in manifest['test_manifests']:
            evidence = json.loads(Path(name).read_text())
            for source, expected in evidence.get('inputs_sha256', {}).items():
                assert digest(Path(source)) == expected, source
    print('PASS: complete source-only package inventory' +
          ('; exact zero-fuzz forward/reverse patch round trips' if args.patches else '') +
          ('; app/native/physical/test evidence hashes' if args.evidence else ''))


if __name__ == '__main__':
    main()
