#!/usr/bin/env python3
"""Run frozen A component fixtures against private late-ALU RTL."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT/'docs/measurements/os30-fps25-20261005/late-alu-v1/validation'
OLD = ROOT/'docs/measurements/os30-dual-20261004/validation'
SCRIPTS = ROOT/'tools/experiments/os30_dual_20261004/validation'
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()


def module(name):
    spec = importlib.util.spec_from_file_location('late_alu_'+name, SCRIPTS/(name+'.py'))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.OUT = OUT
    if hasattr(mod, 'FIRMWARE'): mod.FIRMWARE = OUT/'frontend-hazards'
    if hasattr(mod, 'FW'): mod.FW = OUT/('fp-legality' if name == 'fp_legality' else 'decode-traps')
    return mod


def prepare():
    OUT.mkdir(parents=True, exist_ok=False)
    baseline = json.loads((OLD/'baseline128/build-manifest.json').read_text())
    for path, expected in baseline['sources'].items():
        assert sha(Path(path)) == expected, path
    assert sha(OLD/'baseline128/obj/Vtb_system') == baseline['simulator_sha256']
    for name in ['firmware', 'frontend-hazards', 'decode-traps', 'fp-legality']:
        shutil.copytree(OLD/name, OUT/name)
    shutil.copy2(OLD/'baseline128/results.json', OUT/'baseline128-results.json')
    shutil.copy2(OLD/'baseline128/build-manifest.json', OUT/'baseline128-build-manifest.json')
    report = dict(scope=__doc__, frozen_fixtures=True, baseline_sources_still_exact=True,
        baseline_simulator_sha256=baseline['simulator_sha256'],
        scripts={p.name: sha(p) for p in SCRIPTS.glob('*.py')},
        inputs={str(p.relative_to(OUT)): sha(p) for p in OUT.rglob('*') if p.is_file()})
    (OUT/'fixture-manifest.json').write_text(json.dumps(report, indent=2)+'\n')


def compare():
    base = json.loads((OUT/'baseline128-results.json').read_text())['results']
    cand = json.loads((OUT/'candidate/results.json').read_text())['results']
    report = []
    for a, b in zip(base, cand):
        assert (a['program'], a['mode'], a['program_sha256']) == (b['program'], b['mode'], b['program_sha256'])
        row = dict(program=a['program'], mode=a['mode'], baseline_cycles=a['cycles_to_pass'],
                   candidate_cycles=b['cycles_to_pass'], speedup=a['cycles_to_pass']/b['cycles_to_pass']-1)
        if a['program'] == 'kernels':
            row['kernels'] = []
            for ka, kb in zip(a['kernels'], b['kernels']):
                assert (ka['name'], ka['hash']) == (kb['name'], kb['hash'])
                row['kernels'].append(dict(name=ka['name'], baseline_cycles=ka['cycles'],
                    candidate_cycles=kb['cycles'], speedup=ka['cycles']/kb['cycles']-1))
        report.append(row)
    (OUT/'comparison.json').write_text(json.dumps(dict(scope=__doc__,
        limitation='Component cycles only, no whole-game or FPGA timing claim.', results=report), indent=2)+'\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare', 'build', 'run', 'extra', 'compare'])
    action = parser.parse_args().action
    if action == 'prepare': prepare()
    elif action == 'build': module('run').build('candidate', OUT.parent/'VexiiRiscv_late-alu.v')
    elif action == 'run': module('run').execute('candidate', ['stress', 'i2f', 'kernels'], ['quiet', 'scanout'])
    elif action == 'extra':
        for name in ['frontend_hazards', 'decode_traps', 'fp_legality']: module(name).execute('candidate')
    else: compare()
