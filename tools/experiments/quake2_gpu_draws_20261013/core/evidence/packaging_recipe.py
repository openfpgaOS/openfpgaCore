#!/usr/bin/env python3
"""Package a selected source/fit/gate set; no fit, deployment, or seed sweep.

The output is self-contained. Selection of an acceptable physical candidate is
the caller's responsibility: negative timing slack remains a recorded failure.
Use --check-only while selection is still pending.
"""
import argparse
import gzip
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import tarfile

HERE = Path(__file__).resolve().parent
WORKER = 'src/fpga/common/gpu_geometry_worker.v'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inventory(directory):
    result = {}
    for path in sorted(directory.rglob('*')):
        if path.is_symlink():
            raise ValueError('Symlink in source inputs: ' + str(path))
        if path.is_file():
            result[str(path.relative_to(directory))] = sha(path)
    return result


def check_archive(path, pins):
    seen = set()
    with tarfile.open(path, 'r:gz') as archive:
        for member in archive:
            name = member.name
            p = PurePosixPath(name)
            if not name or p.is_absolute() or '..' in p.parts or str(p) != name:
                raise ValueError('Unsafe archive member: ' + name)
            if not member.isfile() or name in seen:
                raise ValueError('Non-file or duplicate archive member: ' + name)
            seen.add(name)
            if hashlib.sha256(archive.extractfile(member).read()).hexdigest() != pins.get(name):
                raise ValueError('Physical report hash differs: ' + name)
    if seen != set(pins):
        raise ValueError('Physical report inventory differs')


def load(path):
    return json.loads(path.read_text())


def check(a):
    fit = load(a.fit / 'build-receipt.json')
    physical = load(a.physical / 'result.json')
    gates = load(a.gates / 'receipt.json')
    sources = inventory(a.candidate)
    if sources != fit['source_sha256']:
        raise ValueError('Candidate differs from selected prebuild source pins')
    if fit['recipe_sha256'] != sha(a.fit_recipe):
        raise ValueError('Selected executed fit recipe differs')
    if sha(a.fit / 'build-receipt.json') != sha(a.physical / 'build-receipt.json'):
        raise ValueError('Collected physical evidence is not the selected fit')
    if physical['source_recipe_sha256'] != fit['recipe_sha256']:
        raise ValueError('Physical recipe receipt differs')
    codes = fit['returncodes']
    if set(codes) != {'map', 'fit', 'asm', 'sta', 'corners'}:
        raise ValueError('Selected physical build is incomplete')
    if any(codes[phase] for phase in ('map', 'fit', 'asm', 'sta')):
        raise ValueError('Selected build did not complete fit, assembly, and timing')
    if physical['phase_returncodes'] != codes or not physical['fit_successful']:
        raise ValueError('Selected physical phase results differ or fit failed')
    if physical['constrained_timing_passed'] != fit['timing_passed']:
        raise ValueError('Selected physical timing flag differs')
    if bool(fit['timing_passed']) != (codes['corners'] == 0):
        raise ValueError('Timing pass flag contradicts all-corner result')
    if fit['seed'] != 2 or fit['configured_mhz'] != 100:
        raise ValueError('Unexpected seed or configured clock')
    if physical['seed'] != fit['seed'] or physical['configured_clock_MHz'] != fit['configured_mhz']:
        raise ValueError('Physical seed or clock differs')
    if 'Version 25.1std.0 Build 1129' not in fit['quartus_version']:
        raise ValueError('Unexpected recorded Quartus version')
    artifact = sha(a.fit / 'os30.rbf_r')
    if not (artifact == fit['bitstream_sha256'] == physical['bitstream_sha256']):
        raise ValueError('Selected bitstream differs from receipts')
    reverse = bytes(int(f'{i:08b}'[::-1], 2) for i in range(256))
    raw = hashlib.sha256((a.fit / 'os30.rbf_r').read_bytes().translate(reverse)).hexdigest()
    if raw != physical['raw_rbf_sha256']:
        raise ValueError('Bitstream does not match assembled raw RBF')
    check_archive(a.physical / 'reports.tar.gz', physical['report_sha256'])
    if physical['report_sha256']['build-receipt.json'] != sha(a.fit / 'build-receipt.json'):
        raise ValueError('Archived build receipt differs')
    if not gates['passed'] or gates['worker_sha256'] != sources[WORKER]:
        raise ValueError('Selected passing worker gate does not cover fitted worker')
    for mode in ('enabled', 'legacy-disabled'):
        testroot = a.tests if mode == 'enabled' else a.tests / mode
        for name in ('geometry_worker_tb.v', 'geometry_worker.cpp'):
            if sha(testroot / name) != gates[mode]['sources'][name]:
                raise ValueError('Selected worker test differs: ' + mode + '/' + name)
        if (a.gates / mode / 'result.txt').read_text() != gates[mode]['result']:
            raise ValueError('Selected worker transcript differs: ' + mode)
    return fit, physical, gates


def source_archive(path, source, pins):
    with path.open('wb') as raw:
        with gzip.GzipFile(filename='', mode='wb', fileobj=raw, mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode='w') as archive:
                for name, digest in sorted(pins.items()):
                    data = (source / name).read_bytes()
                    if hashlib.sha256(data).hexdigest() != digest:
                        raise ValueError('Source changed during packaging: ' + name)
                    entry = tarfile.TarInfo(name)
                    entry.size, entry.mode, entry.mtime = len(data), 0o644, 0
                    archive.addfile(entry, io.BytesIO(data))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('candidate', 'fit', 'physical', 'tests', 'gates'):
        p.add_argument('--' + name, type=Path, required=True)
    p.add_argument('--fit-recipe', type=Path, default=HERE / 'build_candidate.py')
    p.add_argument('--abi', type=Path, default=HERE / 'particle-abi.txt')
    p.add_argument('--output', type=Path)
    p.add_argument('--check-only', action='store_true')
    a = p.parse_args()
    for name in ('candidate', 'fit', 'physical', 'tests', 'gates', 'fit_recipe', 'abi'):
        setattr(a, name, getattr(a, name).resolve())
    fit, physical, gates = check(a)
    qsf = (a.candidate / 'src/fpga/targets/pocket/ap_core.qsf').read_text()
    macros = sorted(line.split()[-1] for line in qsf.splitlines()
                    if line.startswith('set_global_assignment -name VERILOG_MACRO '))
    additive_combine = 'INCLUDE_COMBINE' in macros
    summary_keys = ('fit_successful', 'ALMs', 'M10Ks', 'DSPs', 'LABs', 'worker',
                    'configured_clock_MHz', 'main_clock_restricted_Fmax_MHz',
                    'constrained_timing_passed', 'worst_setup_ns', 'worst_hold_ns')
    summary = {key: physical[key] for key in summary_keys if key in physical}
    if a.check_only:
        print(json.dumps(dict(checked=True, source_inputs=len(fit['source_sha256']),
                              worker_sha256=gates['worker_sha256'], physical=summary), indent=2))
        return
    if not a.output:
        p.error('--output is required unless --check-only is used')
    out = a.output.resolve()
    if out.exists() or any(source == out or source in out.parents
                           for source in (a.candidate, a.fit, a.tests, a.gates, a.physical)):
        raise ValueError('Output must be a new directory outside the supplied inputs')
    out.mkdir(parents=True)

    def copy(source, target):
        dest = out / target
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, dest)

    for name in ('package.py', 'build_core.py'):
        copy(HERE / 'core-package-templates' / name, name)
    copy(HERE / 'core-package-templates/run_worker_tests.py', 'worker-tests/run.py')
    copy(Path(__file__), 'evidence/packaging_recipe.py')
    copy(a.fit_recipe, 'evidence/executed_fit_recipe.py')
    copy(a.tests / 'run.py', 'evidence/original_worker_runner.py')
    copy(a.abi, 'particle-abi.txt')
    copy(a.fit / 'os30.rbf_r', 'os30.rbf_r')
    copy(a.candidate / WORKER, 'worker-tests/gpu_geometry_worker.v')
    copy(a.gates / 'receipt.json', 'evidence/worker/receipt.json')
    for mode in ('enabled', 'legacy-disabled'):
        testroot = a.tests if mode == 'enabled' else a.tests / mode
        prefix = 'worker-tests/' + ('' if mode == 'enabled' else mode + '/')
        for name in ('geometry_worker_tb.v', 'geometry_worker.cpp'):
            copy(testroot / name, prefix + name)
        for name in ('build.log', 'result.txt'):
            copy(a.gates / mode / name, 'evidence/worker/' + mode + '/' + name)
    for name in ('result.json', 'build-receipt.json', 'reports.tar.gz',
                 'summary.tsv', 'clocks.tsv', 'ap_core.fit.summary', 'ap_core.sta.summary'):
        if (a.physical / name).is_file():
            copy(a.physical / name, 'evidence/physical/' + name)
    source_archive(out / 'source.tar.gz', a.candidate, fit['source_sha256'])
    restriction = ('' if additive_combine else
        'This is a Quake2-only image: the additive texel*C+D color combiner is\n'
        'compiled out and its hardware capability is cleared. Normal RGB\n'
        'texture modulation, alpha blending, lighting colors, and depth remain.\n'
        'It is NOT a replacement for the separate SM64 image.\n\n')
    (out / 'README.txt').write_text('Native particle geometry experiment, os30 only\n\n' + restriction + '''

This package contains the selected exact prebuild sources, bitstream, recorded
worker gates, ABI, and physical reports. It is not a Pocket performance result
or a deployment receipt. Full-application image/throughput evidence belongs to
the accompanying experiment release. Historical source preparation receipts
inside the source archive are provenance only, not evidence for this candidate.

GPU_STATUS bit 13 advertises native camera-space particle billboards. The GPU
reuses the geometry worker multiplier/divider; the particle path preserves draw
order, alpha, clip rectangle, material, and depth state. Unsupported particles
must use the app's existing fallback. See particle-abi.txt for fixed-point limits
and the explicitly bounded difference from the former floating-point frontend.
General near-plane geometry clipping is not included. This os30 experiment keeps
the existing silent-audio configuration without the mixer.

Verification and fresh staging (run from this package directory):
  python3 package.py verify
  python3 package.py stage /tmp/quake2-particle-source
  python3 package.py check-stage /tmp/quake2-particle-source
  python3 worker-tests/run.py /tmp/quake2-particle-worker-tests

Build once with Quartus 25.1std.0 Build 1129, seed 2, configured 100 MHz:
  python3 build_core.py /tmp/quake2-particle-source /tmp/quake2-particle-fit
Use --prepare-only to validate copying without invoking Quartus. A negative
timing check returns failure even when the bitstream assembled successfully.
The portable build recipe is adapted for this self-contained archive; the
actually executed original recipe is retained under evidence/. No commands
here deploy files or sweep seeds. Source staging must remain immutable; build
outputs go to a separate new directory.

Recorded physical result (read evidence/physical/result.json for all corners):
''' + json.dumps(summary, indent=2) + '\n')
    m = dict(format_version=1, experiment='quake2_gpu_draws_20261013',
             source_archive='source.tar.gz', source_sha256=fit['source_sha256'],
             artifact='os30.rbf_r', artifact_sha256=fit['bitstream_sha256'],
             recorded_fit='evidence/physical/build-receipt.json',
             physical_result='evidence/physical/result.json', physical_summary=summary,
             quartus='Version 25.1std.0 Build 1129', seed=fit['seed'],
             configured_mhz=fit['configured_mhz'], deployed=False,
             verilog_macros=macros, additive_color_combiner=additive_combine,
             application_scope='Quake2-only' if not additive_combine else 'Quake2 particle experiment',
             worker_scope=gates['scope'], files=inventory(out))
    (out / 'manifest.json').write_text(json.dumps(m, indent=2, sort_keys=True) + '\n')
    subprocess.run([sys.executable, str(out / 'package.py'), 'verify'], check=True)
    print('Packaged selected evidence at', out)


if __name__ == '__main__':
    main()
