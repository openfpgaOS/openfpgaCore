#!/usr/bin/env python3
"""Create a new native core bundle, preserving prepared and fitted source pins."""
import argparse
import difflib
import gzip
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile


def sha(data):
    return hashlib.sha256(data).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for arg in ('template', 'source', 'baseline', 'fit-receipt', 'measurement',
                'tests', 'worker-receipt', 'output'):
        parser.add_argument('--' + arg, type=Path, required=True)
    parser.add_argument('--revision', required=True)
    parser.add_argument('--status', required=True)
    parser.add_argument('--without-mixer', action='store_true')
    args = parser.parse_args()
    root = args.output.absolute()
    if root.exists() or root.is_symlink():
        parser.error('Output must not exist')
    if not args.revision or any(c not in 'abcdefghijklmnopqrstuvwxyz0123456789-' for c in args.revision):
        parser.error('Revision must be a simple lowercase label')
    here = Path(__file__).resolve().parent
    helper = here / 'core_package.py'
    if not helper.is_file():
        helper = here / 'package.py'  # Generator copied into its portable bundle.
    sys.dont_write_bytecode = True
    spec = importlib.util.spec_from_file_location('native_core_package', helper)
    package = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(package)
    subprocess.run([sys.executable, str(args.template / 'package.py'), 'verify'], check=True)
    source = args.source.resolve()
    if any(p.is_symlink() for p in source.rglob('*')):
        raise ValueError('Linked prepared source refused')
    members = {str(p.relative_to(source)): (p.read_bytes(), p.stat().st_mode & 0o777)
               for p in sorted(source.rglob('*')) if p.is_file()}
    pins = {name: sha(data) for name, (data, _) in members.items()}
    fit = json.loads(args.fit_receipt.read_text())
    build_args = ['--without-mixer'] if args.without_mixer else []
    if package.fit_inputs(members, build_args) != fit['source_sha256']:
        raise ValueError('Prepared source plus explicit transformations differs from fit input')
    if fit['mixer'] != (not args.without_mixer) or fit['silent_audio'] != args.without_mixer:
        raise ValueError('Fit recipe mode differs from requested package mode')
    if sha((args.measurement / 'build_core.py').read_bytes()) != fit['recipe_sha256']:
        raise ValueError('Build recipe differs from the fit receipt')
    if sha((args.tests / 'gpu_geometry_worker.v').read_bytes()) != pins['src/fpga/common/gpu_geometry_worker.v']:
        raise ValueError('Worker test RTL differs from prepared core')
    prior = json.loads(args.worker_receipt.read_text())
    tests = prior.get('portable_test_receipt', prior)
    if not prior['passed'] or not tests['passed']:
        raise ValueError('Prior worker tests did not pass')
    for name, expected in tests['source_sha256'].items():
        package.safe_name(name)
        if sha((args.tests / name).read_bytes()) != expected:
            raise ValueError('Prior worker test source differs: ' + name)

    shutil.copytree(args.template, root)  # Exclusive destination creation.
    (root / 'prepared-source.tar.gz').unlink()
    shutil.rmtree(root / 'delta')
    shutil.rmtree(root / 'worker-tests')
    for path in root.glob('fit-input-*.json'):
        path.unlink()
    for name in ('ABI.txt', 'build_core.py'):
        shutil.copyfile(args.measurement / name, root / name)
    shutil.copyfile(helper, root / 'package.py')
    shutil.copyfile(Path(__file__), root / 'create_core_bundle.py')
    shutil.copytree(args.tests, root / 'worker-tests')
    with (root / 'prepared-source.tar.gz').open('wb') as raw:
        with gzip.GzipFile(fileobj=raw, mode='wb', filename='', mtime=0) as zipped:
            with tarfile.open(fileobj=zipped, mode='w', format=tarfile.PAX_FORMAT) as archive:
                for name, (data, mode) in members.items():
                    info = tarfile.TarInfo(name)
                    info.size, info.mode, info.mtime = len(data), mode, 0
                    archive.addfile(info, io.BytesIO(data))
    delta, patch = {}, ''
    for name in ('src/fpga/common/gpu_geometry_worker.v', 'src/fpga/common/os30/gpu_core.v',
                 'src/fpga/targets/pocket/os30/core_top.v', 'src/fpga/targets/pocket/ap_core.qsf'):
        before = (args.baseline / name).read_bytes()
        after = members[name][0]
        entry = {'before_sha256': sha(before), 'after_sha256': sha(after)}
        for prefix, data, key in (('before', before, 'preimage'), ('after', after, 'postimage')):
            relative = 'delta/' + prefix + '/' + name
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            entry[key] = relative
        delta[name] = entry
        patch += ''.join(difflib.unified_diff(before.decode().splitlines(keepends=True),
                         after.decode().splitlines(keepends=True), fromfile='a/' + name, tofile='b/' + name))
    (root / 'native-mesh.patch').write_text(patch)
    fit_name = 'fit-input-' + args.revision + '.json'
    (root / fit_name).write_text(json.dumps({k: fit[k] for k in (
        'source', 'output', 'mixer', 'silent_audio', 'recipe_sha256', 'source_sha256', 'quartus_version')}, indent=2) + '\n')
    reuse = {'passed': True, 'reexecuted': False,
             'reason': 'All worker, wrapper and test source pins equal the recorded, previously executed tests.',
             'prior_receipt_sha256': sha(args.worker_receipt.read_bytes()),
             'prior_receipt': prior,
             'test_directory_sha256': {str(p.relative_to(args.tests)): sha(p.read_bytes())
                                       for p in sorted(args.tests.rglob('*')) if p.is_file()}}
    (root / 'worker-test-reuse.json').write_text(json.dumps(reuse, indent=2) + '\n')
    suffix = ' --without-mixer' if args.without_mixer else ''
    (root / 'README.txt').write_text(
        'Quake2 native MD2 core ' + args.revision + '\n\nSTATUS: ' + args.status + '\n\n'
        'No bitstream is included. Source qualification and physical selection remain pending.\n'
        'The archive preserves every prepared source byte and its original preparation receipt.\n'
        'Verification derives the exact fit input by renaming build-receipt.json to\n'
        'parent-preparation-receipt.json and, for --without-mixer, replacing the one\n'
        'INCLUDE_HW_MIXER QSF macro with NATIVE_MESH_SILENT_AUDIO. All resulting file\n'
        'hashes must equal the recorded fit input; no other transformation is accepted.\n\n'
        'python3 package.py verify\n'
        'python3 package.py stage /tmp/native-source-NEW\n'
        'python3 package.py check-stage /tmp/native-source-NEW\n'
        'python3 worker-tests/run_worker_tests.py /tmp/native-tests-NEW\n'
        'python3 build_core.py /tmp/native-source-NEW /tmp/native-fit-NEW' + suffix + '\n\n'
        'All destinations must be new. Build requires pinned Quartus 25.1; strict\n'
        'all-corner checks retain failures. No deployment or seed sweep is performed.\n'
        'create_core_bundle.py preserves the reproducible packaging generator.\n'
        'Worker tests are reused only after every tested source hash matches the recorded suite.\n'
        'Silent audio checks and physical timing evidence are recorded separately.\n'
        'Historical parent receipts and review notes do not qualify this revision.\n'
        'Keep new build/test outputs outside this immutable source package.\n')
    manifest = {'schema': 2, 'experiment': 'quake2-native-mesh-core-' + args.revision,
                'source_only': True, 'qualification': args.status,
                'source': {'archive': 'prepared-source.tar.gz', 'sha256': pins},
                'build_args': build_args, 'fit_input_receipt': fit_name,
                'fit_input_sha256': fit['source_sha256'],
                'baseline': 'worker-q2-performance-seed2', 'patch': 'native-mesh.patch', 'delta': delta,
                'files': {str(p.relative_to(root)): sha(p.read_bytes())
                          for p in sorted(root.rglob('*')) if p.is_file() and p.name != 'manifest.json'}}
    (root / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    subprocess.run([sys.executable, str(root / 'package.py'), 'verify'], check=True)
    print(root)


if __name__ == '__main__':
    main()
