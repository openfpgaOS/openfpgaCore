#!/usr/bin/env python3
"""CPU-only sensitivity screen of current SM64; never a hardware FPS estimate."""
import argparse
import concurrent.futures
import csv
import hashlib
import json
import os
from pathlib import Path
import shutil
import statistics
import subprocess

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / 'docs/measurements/os30-feature-audit-20261003'
SOURCE = ROOT / 'docs/measurements/sm64-device-perf-20261003/qsim'
ELF = ROOT / 'docs/measurements/sm64-geometry-fast-20261003/capture-final-candidate/sm64.elf'
ASSETS = ROOT / 'docs/measurements/sm64-device-perf-20261003/SM64/build/pocket/sm64/Assets/sm64/common'
APP = ROOT / 'docs/measurements/sm64-bob-20261002/sm64.app'
CASES = {
    'baseline': {},
    'dc64': {'dc_sets': 512},
    'dc32': {'dc_sets': 256},
    'ic16': {'ic_sets': 128},
    'btb512': {'btb_sets': 512},
    'gshare1k': {'gshare_counters': 4096, 'gshare_hist_bits': 10},
    'dc64_btb512': {'dc_sets': 512, 'btb_sets': 512},
    'dc64_ic16_btb512': {'dc_sets': 512, 'ic_sets': 128, 'btb_sets': 512},
}
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()

def prepare():
    OUT.mkdir(parents=True, exist_ok=True)
    dst = OUT / 'qsim'
    if dst.exists():
        raise SystemExit('Private simulator already exists; use --run-only after reviewing it.')
    shutil.copytree(SOURCE, dst, ignore=shutil.ignore_patterns('build', 'qsim', '__pycache__'))
    path = dst / 'cache.c'
    s = path.read_text()
    marker = '\nvoid qsim_model_print_params('
    pos = s.index(marker)
    end = s.rfind('}', 0, pos)
    code = '\n    /* Audit-only overrides; defaults match the captured os30 model. */\n'
    for field in sorted({k for v in CASES.values() for k in v}):
        code += ('    if (getenv("QSIM_AUDIT_' + field.upper() + '")) p->' + field +
                 ' = (unsigned)strtoul(getenv("QSIM_AUDIT_' + field.upper() + '"), NULL, 10);\n')
    path.write_text(s[:end] + code + s[end:])
    files = {p.name: sha(p) for p in dst.iterdir() if p.suffix in ('.c', '.h')}
    (OUT / 'cpu-model-inputs.json').write_text(json.dumps(dict(
        baseline_cache_sha256=sha(SOURCE / 'cache.c'), files=files,
        app_sha256=sha(ELF), cases=CASES), indent=2) + '\n')
    with (OUT / 'build-cpu-model.log').open('w') as log:
        subprocess.run(['make', '-C', str(dst), '-j4'], check=True, stdout=log, stderr=subprocess.STDOUT)

def run(case):
    dest = OUT / ('cpu-' + case)
    dest.mkdir(exist_ok=False)
    elf = dest / 'sm64.elf'
    shutil.copy2(ELF, elf)
    cmd = [str(OUT / 'qsim/qsim'), str(elf), '--app', str(APP), '--data-dir', str(ASSETS),
           '--out', str(dest), '--frames', '721', '--profile', '--gpu-sched', 'eager',
           '--max-insns', '5000000000']
    env = {k: v for k, v in os.environ.items() if not k.startswith(('SM64_', 'QSIM_AUDIT_'))}
    env.update(SM64_AUDIO='1', SM64_GEOMETRY_CAP='1')
    env.update({'QSIM_AUDIT_' + k.upper(): str(v) for k, v in CASES[case].items()})
    record = dict(command=cmd, overrides=CASES[case], audio=True, success=False,
                  elf_sha256=sha(elf), qsim_sha256=sha(OUT / 'qsim/qsim'))
    (dest / 'inputs.json').write_text(json.dumps(record, indent=2) + '\n')
    with (dest / 'run.log').open('w') as log:
        subprocess.run(cmd, env=env, cwd=dest, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=600)
    rows = list(csv.DictReader((dest / 'frames.csv').open()))
    assert len(rows) == 721
    assert (dest / 'scene.csv').read_bytes() == (ROOT / 'docs/measurements/sm64-device-perf-20261003/capture-audio-current/scene.csv').read_bytes()
    summary = json.loads((dest / 'summary.json').read_text())
    assert summary['gpu_sync']['ring_overflows'] == 0
    record['success'] = True
    (dest / 'inputs.json').write_text(json.dumps(record, indent=2) + '\n')
    return dict(case=case, overrides=CASES[case], ms=[statistics.mean(int(r['est_cycles'])
        for r in rows[f:f+60])/100000 for f in range(360, 720, 60)])

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run-only', action='store_true')
    a = p.parse_args()
    if not a.run_only:
        prepare()
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        for r in pool.map(run, CASES):
            print(r, flush=True)
            results.append(r)
    base = next(r for r in results if r['case'] == 'baseline')['ms']
    for row in results:
        row['extra_cpu_time_percent'] = [100*(a/b-1) for a,b in zip(row['ms'], base)]
        row['aggregate_extra_cpu_time_percent'] = 100*(sum(row['ms'])/sum(base)-1)
    prior = ROOT / 'docs/measurements/sm64-device-perf-20261003/capture-audio-current/frames.csv'
    aa = list(csv.DictReader(prior.open()))
    bb = list(csv.DictReader((OUT / 'cpu-baseline/frames.csv').open()))
    assert all(a['est_cycles'] == b['est_cycles'] for a,b in zip(aa,bb))
    (OUT / 'cpu-screen.json').write_text(json.dumps(dict(
        scope='CPU model, synthetic one-step-per-frame clock, simplified audio; no hardware GPU backpressure or actual FPS',
        baseline_matches_prior_framewise=True, results=results), indent=2) + '\n')

if __name__ == '__main__':
    main()
