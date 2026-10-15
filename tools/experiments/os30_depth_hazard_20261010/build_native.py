#!/usr/bin/env python3
"""Reproduce an isolated native model from this source-only package."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import time

sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()


def main():
    package = Path(__file__).resolve().parent
    manifest = json.loads((package / 'manifest.json').read_text())
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', type=Path, default=Path(manifest['baseline_model']))
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--variant', choices=['baseline', 'legacy', 'depth', 'priority'], required=True)
    parser.add_argument('--prepare-only', action='store_true')
    args = parser.parse_args()
    base, out = args.baseline.resolve(), args.out.resolve()
    assert not out.exists(), out
    assert sha(base / 'prepared.json') == manifest['baseline_prepared_sha256']
    bound = json.loads((base / 'prepared.json').read_text())
    for relative, expected in bound['source_sha256'].items():
        assert sha(base / relative) == expected, relative
    for name, expected in manifest['files'].items():
        assert sha(package / name) == expected, name
    shutil.copytree(base / 'frozen', out / 'frozen')
    variant = manifest['variants'][args.variant]
    for name in variant['patches']:
        patch = manifest['patches'][name]
        for relative, hashes in patch['files'].items():
            assert sha(out / 'frozen' / relative) == hashes['before_sha256']
        run = subprocess.run(['patch', '-p1', '--batch', '--forward', '--fuzz=0',
            '-i', str(package / name)], cwd=out / 'frozen', capture_output=True, text=True)
        assert run.returncode == 0 and 'offset' not in run.stdout and 'fuzz' not in run.stdout, run.stdout + run.stderr
        for relative, hashes in patch['files'].items():
            assert sha(out / 'frozen' / relative) == hashes['after_sha256']
    # Prepared baseline command contains original absolute paths; relocation
    # also supports a moved baseline directory when its exact inputs match.
    original_base = manifest['baseline_model']
    command = [arg.replace(original_base, str(out)) for arg in bound['command']]
    for define in variant['defines']:
        command.insert(command.index('--cc'), '-D' + define)
    files = {str(p.relative_to(out)): sha(p) for p in (out / 'frozen').rglob('*') if p.is_file()}
    assert files.keys() == bound['source_sha256'].keys()
    prepared = dict(command=command, source_sha256=files, baseline_model=str(base),
        baseline_prepared_sha256=sha(base / 'prepared.json'),
        changed_inputs=[name for name, value in files.items() if bound['source_sha256'][name] != value],
        added_defines=variant['defines'], experimental=True,
        package_manifest_sha256=sha(package / 'manifest.json'),
        recipe_sha256=sha(Path(__file__)),
        limitations=['Native simulation only; no FPGA fit, timing or hardware qualification.'])
    (out / 'prepared.json').write_text(json.dumps(prepared, indent=2) + '\n')
    if args.prepare_only:
        print('Prepared', args.variant, out)
        return
    start = time.monotonic()
    with (out / 'build.log').open('x') as stream:
        code = subprocess.run(command, cwd=out / 'frozen/src/fpga/test',
                              stdout=stream, stderr=subprocess.STDOUT).returncode
    for relative, expected in files.items():
        assert sha(out / relative) == expected, relative
    (out / 'build.json').write_text(json.dumps(dict(returncode=code,
        seconds=time.monotonic() - start, command=command,
        prepared_sha256=sha(out / 'prepared.json')), indent=2) + '\n')
    print('Native build', code)
    raise SystemExit(code)


if __name__ == '__main__':
    main()
