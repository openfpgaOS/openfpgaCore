#!/usr/bin/env python3
"""Verify or stage the exact native particle experiment; never deploy it."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import tarfile

HERE = Path(__file__).resolve().parent
WORKER = 'src/fpga/common/gpu_geometry_worker.v'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def safe_name(name):
    path = PurePosixPath(name)
    if not name or path.is_absolute() or '..' in path.parts or str(path) != name:
        raise ValueError('Unsafe package path: ' + name)
    return name


def inventory(directory, ignore_manifest=False):
    result = {}
    for path in sorted(directory.rglob('*')):
        if path.is_symlink():
            raise ValueError('Symlink in package or stage: ' + str(path))
        name = str(path.relative_to(directory))
        if path.is_file() and not (ignore_manifest and name == 'manifest.json'):
            result[name] = sha(path)
    return result


def archive_members(path, pins):
    seen = set()
    with tarfile.open(path, 'r:gz') as archive:
        for item in archive:
            name = safe_name(item.name)
            if not item.isfile() or name in seen:
                raise ValueError('Non-file or duplicate archive member: ' + name)
            seen.add(name)
            data = archive.extractfile(item).read()
            if hashlib.sha256(data).hexdigest() != pins.get(name):
                raise ValueError('Archive member hash differs: ' + name)
            yield name, data
    if seen != set(pins):
        raise ValueError('Archive inventory differs: ' + str(path))


def verify():
    m = json.loads((HERE / 'manifest.json').read_text())
    for name in m['files']:
        safe_name(name)
    if inventory(HERE, ignore_manifest=True) != m['files']:
        raise ValueError('Package inventory or hashes differ')
    sources = dict(archive_members(HERE / m['source_archive'], m['source_sha256']))
    qsf = sources['src/fpga/targets/pocket/ap_core.qsf'].decode()
    macros = sorted(line.split()[-1] for line in qsf.splitlines()
                    if line.startswith('set_global_assignment -name VERILOG_MACRO '))
    if macros != m['verilog_macros'] or m['additive_color_combiner'] != ('INCLUDE_COMBINE' in macros):
        raise ValueError('Advertised macro configuration differs from staged QSF')
    fit = json.loads((HERE / m['recorded_fit']).read_text())
    physical = json.loads((HERE / m['physical_result']).read_text())
    if fit['source_sha256'] != m['source_sha256']:
        raise ValueError('Recorded fit source pins differ')
    if sha(HERE / 'evidence/executed_fit_recipe.py') != fit['recipe_sha256']:
        raise ValueError('Executed fit recipe differs')
    if physical['source_recipe_sha256'] != fit['recipe_sha256']:
        raise ValueError('Physical recipe pin differs')
    if physical['phase_returncodes'] != fit['returncodes']:
        raise ValueError('Physical phase results differ')
    if physical['constrained_timing_passed'] != fit['timing_passed']:
        raise ValueError('Timing result differs')
    if m['physical_summary'] != {key: physical[key] for key in m['physical_summary']}:
        raise ValueError('Manifest physical summary differs')
    list(archive_members(HERE / 'evidence/physical/reports.tar.gz', physical['report_sha256']))
    if physical['report_sha256']['build-receipt.json'] != sha(HERE / m['recorded_fit']):
        raise ValueError('Physical report receipt differs')
    if not (sha(HERE / m['artifact']) == m['artifact_sha256'] ==
            fit['bitstream_sha256'] == physical['bitstream_sha256']):
        raise ValueError('Artifact receipt differs')
    reverse = bytes(int(f'{i:08b}'[::-1], 2) for i in range(256))
    raw_digest = hashlib.sha256((HERE / m['artifact']).read_bytes().translate(reverse)).hexdigest()
    if raw_digest != physical['raw_rbf_sha256']:
        raise ValueError('Bit-reversed artifact does not match assembled raw RBF')
    gates = json.loads((HERE / 'evidence/worker/receipt.json').read_text())
    worker = sha(HERE / 'worker-tests/gpu_geometry_worker.v')
    if not gates['passed'] or worker != gates['worker_sha256'] or worker != m['source_sha256'][WORKER]:
        raise ValueError('Worker gate does not cover fitted worker')
    for mode in ('enabled', 'legacy-disabled'):
        for name, digest in gates[mode]['sources'].items():
            path = HERE / 'worker-tests' / ('' if mode == 'enabled' else mode) / safe_name(name)
            if sha(path) != digest:
                raise ValueError('Executed worker test source differs: ' + str(path))
        if (HERE / 'evidence/worker' / mode / 'result.txt').read_text() != gates[mode]['result']:
            raise ValueError('Worker gate transcript differs: ' + mode)
    return m


def check_stage(directory, m=None):
    m = m or verify()
    if inventory(directory) != m['source_sha256']:
        raise ValueError('Staged inventory or source hashes differ')


def stage(directory):
    m = verify()
    if directory.exists():
        raise ValueError('Destination must not exist')
    data = list(archive_members(HERE / m['source_archive'], m['source_sha256']))
    directory.mkdir(parents=True)
    for name, content in data:
        path = directory / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    check_stage(directory, m)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=['verify', 'stage', 'check-stage'])
    p.add_argument('directory', type=Path, nargs='?')
    args = p.parse_args()
    if args.action == 'verify':
        verify()
    elif not args.directory:
        p.error('directory required')
    elif args.action == 'stage':
        stage(args.directory.resolve())
    else:
        check_stage(args.directory.resolve())
    print('PASS', args.action)
