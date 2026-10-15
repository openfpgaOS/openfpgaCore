#!/usr/bin/env python3
"""One authorized clean worker-only map/seed2 NORMAL fit/STA qualification."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import time

HERE = Path(__file__).resolve().parent
PARENT = HERE.parent / 'geometry-cpu-combo'
EXPERIMENT = HERE.parents[1]
WORKER_NAME = 'src/fpga/common/gpu_geometry_worker.v'


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def inventory(root):
    result = {}
    for path in sorted(root.rglob('*')):
        assert not path.is_symlink(), path
        if path.is_file():
            result[str(path.relative_to(root))] = sha(path)
        else:
            assert path.is_dir(), path
    return result


def save(path, value):
    path.write_text(json.dumps(value, indent=2) + '\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('worker', type=Path)
    parser.add_argument('--sha256', required=True)
    parser.add_argument('--proof', type=Path, required=True)
    args = parser.parse_args()
    assert not (HERE / 'run.json').exists(), 'This single trial was already attempted'
    assert not (HERE / 'source-ready').exists()
    assert not (HERE / 'work').exists()
    assert sha(args.worker) == args.sha256
    proof_bytes = args.proof.read_bytes()
    proof = json.loads(proof_bytes)
    assert proof['passed'] and proof['worker_sha256'] == args.sha256, 'Matching passed worker qualification required'
    parent = json.loads((PARENT / 'source-ready-manifest.json').read_text())['files']
    assert len(parent) == 128 and inventory(PARENT / 'source-ready') == parent
    shutil.copytree(PARENT / 'source-ready', HERE / 'source-ready')
    shutil.copyfile(args.worker, HERE / 'source-ready' / WORKER_NAME)
    pins = inventory(HERE / 'source-ready')
    assert {name for name in pins if pins[name] != parent[name]} == {WORKER_NAME}
    assert pins[WORKER_NAME] == args.sha256 and sha(args.worker) == args.sha256
    fingerprint = hashlib.sha256(json.dumps(pins, sort_keys=True).encode()).hexdigest()
    save(HERE / 'source-ready-manifest.json', dict(files=pins, frozen_sha256=fingerprint,
                                                 parent_source_pins=parent,
                                                 changes=[WORKER_NAME]))
    (HERE / 'worker-proof.json').write_bytes(proof_bytes)
    shutil.copytree(HERE / 'source-ready', HERE / 'work')
    recipe = HERE / 'timing-recipe'
    recipe.mkdir()
    for name in ['sweep-tooling/report_sweep_timing.tcl', 'sweep-tooling/results.py',
                 'sweep-tooling/sweep.py', 'sweep-tooling/test_sweep.py',
                 'area-optimization/cpu-scope-policy-final-receipt.json',
                 'area-optimization/four-job-policy-final-receipt.json',
                 'area-optimization/ddio-policy-final-receipt.json']:
        shutil.copyfile(EXPERIMENT / name, recipe / Path(name).name)
    recipe_pins = inventory(recipe)
    assert recipe_pins['report_sweep_timing.tcl'] == '2c66004df7b54c2e62a0c47ae976cf7c61da684ec034a169cb077ca00cdca34e'
    assert recipe_pins['results.py'] == '192828cad6b134c0f6cacfd484611e1ec9db5ab27cf379974bd737296d4f110b'
    assert recipe_pins['sweep.py'] == '570a77c4d8aec800c2e90e9241721ec27cfa479f1638ddd91561c0fd834b46f2'
    save(recipe / 'pins.json', recipe_pins)
    (HERE / 'temporary').mkdir()
    project = HERE / 'work/src/fpga/targets/pocket'
    quartus = Path('/home/alberto/altera_lite/25.1std/quartus/bin')
    version = subprocess.check_output([str(quartus / 'quartus_sh'), '--version'], text=True)
    assert 'Version 25.1std.0 Build 1129' in version
    phases = [
        ('map', [str(quartus / 'quartus_map'), 'ap_core', '--write_settings_files=off', '--parallel=4']),
        ('fit', [str(quartus / 'quartus_fit'), 'ap_core', '--recompile=off',
                 '--write_settings_files=off', '--seed=2', '--parallel=4']),
        ('sta', [str(quartus / 'quartus_sta'), 'ap_core']),
        ('corners', [str(quartus / 'quartus_sta'), '-t', str(recipe / 'report_sweep_timing.tcl'),
                     'ap_core', str(HERE / 'timing')]),
    ]
    receipt = dict(state='running', seed=2, effort='NORMAL', phases=[],
                   source_sha256=pins, source_fingerprint=fingerprint,
                   timing_recipe_sha256=recipe_pins, worker_proof_sha256=sha(HERE / 'worker-proof.json'),
                   runner_sha256=sha(Path(__file__)), quartus_version=version,
                   started_unix=time.time())
    path = HERE / 'run.json'
    save(path, receipt)
    for label, command in phases:
        assert all(sha(HERE / 'work' / name) == digest for name, digest in pins.items())
        start = time.time()
        with (HERE / (label + '.log')).open('w') as log:
            result = subprocess.run(command, cwd=project,
                                    env=dict(os.environ, TMPDIR=str(HERE / 'temporary')),
                                    stdout=log, stderr=subprocess.STDOUT)
        receipt['phases'].append(dict(name=label, command=command, returncode=result.returncode,
                                      elapsed_seconds=time.time() - start,
                                      log_sha256=sha(HERE / (label + '.log'))))
        save(path, receipt)
        if label in ('map', 'fit') and result.returncode:
            receipt.update(state=label + '_failed', elapsed_seconds=time.time() - receipt['started_unix'])
            save(path, receipt)
            raise SystemExit(result.returncode)
        if label == 'map':
            subprocess.run(['cp', '-a', '--reflink=auto', str(HERE / 'work'),
                            str(HERE / 'post-map-work')], check=True)
            mapped, cloned = inventory(HERE / 'work'), inventory(HERE / 'post-map-work')
            assert mapped == cloned
            save(HERE / 'post-map-receipt.json', dict(passed=True, files=cloned))
    spec = importlib.util.spec_from_file_location('frozen_results', recipe / 'results.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    parsed = module.parse(project, HERE / 'timing')
    save(HERE / 'qualification-results.json', parsed)
    receipt.update(state='complete', elapsed_seconds=time.time() - receipt['started_unix'],
                   qualification_results_sha256=sha(HERE / 'qualification-results.json'),
                   post_physical_source_changes={name: sha(HERE / 'work' / name)
                                                 for name, digest in pins.items()
                                                 if sha(HERE / 'work' / name) != digest})
    save(path, receipt)
    print(json.dumps({key: parsed[key] for key in [
        'resources', 'minimum_main_restricted_fmax_mhz', 'worst_slack_ns',
        'cross_clock_setup_clean', 'input_setup_clean', 'eligible_above_90_mhz_estimate',
        'timing_closed_at_configured_100_mhz']}, indent=2))


if __name__ == '__main__':
    main()
