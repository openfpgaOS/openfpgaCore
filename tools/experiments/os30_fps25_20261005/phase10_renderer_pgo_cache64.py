#!/usr/bin/env python3
"""One I64/D64 model-only screen of the qualified renderer PGO application."""
import argparse
import csv
import difflib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from phase10_experiment import CAMP, ROOT, module, sha, write

P = CAMP / 'phase10'
OLD = P / 'renderer-pgo-coupled-v1'
OUT = P / 'renderer-pgo-cache64-v1'
APP = P / 'renderer-pgo-v1/use/app.elf'
OLD_HASH = 'f1780b7987fffe3e6b931f705a6cabc211550197b0354b83921c43d624d31c79'
ELF_HASH = 'fff132a5ee34d625a279fbf612e4abe54faa122fe519b386130f5ee230dfe63b'
REPLACEMENTS = [('p->ic_sets = 256;', 'p->ic_sets = 512;'),
                ('p->dc_sets = 1024;', 'p->dc_sets = 512;')]


def source_check():
    prep = json.loads((OUT / 'prepared.json').read_text())
    assert sha(OLD / 'manifest.json') == OLD_HASH
    assert sha(APP) == ELF_HASH
    for name, digest in prep['source_sha256'].items():
        assert sha(OUT / name) == digest, name
    original = (OLD / 'qsim/cache.c').read_text()
    for before, after in REPLACEMENTS:
        assert original.count(before) == 1
        original = original.replace(before, after)
    assert original == (OUT / 'qsim/cache.c').read_text()
    for directory in ('model/frozen', 'qsim'):
        for f in (OLD / directory).rglob('*'):
            rel = f.relative_to(OLD)
            if not f.is_file() or 'build' in rel.parts or rel == Path('qsim/qsim'):
                continue
            if rel != Path('qsim/cache.c'):
                assert sha(f) == sha(OUT / rel), rel


def prepare():
    assert sha(OLD / 'manifest.json') == OLD_HASH
    for name, digest in json.loads((OLD / 'manifest.json').read_text())['files'].items():
        assert sha(OLD / name) == digest, name
    assert sha(APP) == ELF_HASH
    OUT.mkdir(exist_ok=False)
    shutil.copytree(OLD / 'model/frozen', OUT / 'model/frozen')
    shutil.copytree(OLD / 'qsim', OUT / 'qsim', ignore=shutil.ignore_patterns('build', 'qsim'))
    f = OUT / 'qsim/cache.c'
    before = f.read_text()
    after = before
    for a, b in REPLACEMENTS:
        assert after.count(a) == 1
        after = after.replace(a, b)
    f.write_text(after)
    (OUT / 'candidate.patch').write_text(''.join(difflib.unified_diff(
        before.splitlines(True), after.splitlines(True), fromfile='parent/qsim/cache.c',
        tofile='candidate/qsim/cache.c')))
    old_build = json.loads((OLD / 'model/build.json').read_text())
    command = old_build['command']
    command_root = str(Path(command[command.index('--Mdir') + 1]).parents[1])
    command = [x.replace(command_root, str(OUT)) for x in command]
    assert str(OUT / 'model/obj') == command[command.index('--Mdir') + 1]
    write(OUT / 'build-command.json', command)
    shutil.copy2(__file__, OUT / 'recipe.py')
    write(OUT / 'prepared.json', dict(
        parent_manifest_sha256=OLD_HASH, elf_sha256=ELF_HASH,
        parent_build_sha256=sha(OLD / 'model/build.json'),
        changed_parameters={'ic_sets': [256, 512], 'dc_sets': [1024, 512]},
        unchanged={'ic_ways': 2, 'dc_ways': 2, 'ic_line': 64, 'dc_line': 64},
        source_sha256={str(f.relative_to(OUT)): sha(f) for f in OUT.rglob('*') if f.is_file()},
        rtl_changed=False, cpu_regenerated=False, hardware_fit=False, deployment_ready=False))
    source_check()
    print(OUT)


def build():
    source_check()
    command = ['make', '-j3', *[f'build/qsim/{n}.o' for n in
                              'cpu fpu cache prof qelf lint gpu app env main'.split()]]
    with (OUT / 'build-qsim.log').open('x') as stream:
        subprocess.run(command, cwd=OUT / 'qsim', stdout=stream, stderr=subprocess.STDOUT, check=True)
    command = json.loads((OUT / 'build-command.json').read_text())
    with (OUT / 'model/build.log').open('x') as stream:
        subprocess.run(command, stdout=stream, stderr=subprocess.STDOUT, check=True)
    write(OUT / 'model/build.json', dict(command=command,
        simulator_sha256=sha(OUT / 'model/obj/Vtb_gpu'),
        qsim_object_sha256={f.name: sha(f) for f in (OUT / 'qsim/build/qsim').glob('*.o')},
        parent_build_sha256=sha(OLD / 'model/build.json')))


def run(window):
    source_check()
    runner = module('renderer_cache_screen', ROOT / 'tools/experiments/os30_overlap_20261006/run.py')
    runner.OUT = OUT
    os.environ['IDEAL_GPU_MEMORY'] = '0'
    os.environ['LOD5_MODEL_MODE'] = 'normal'
    sys.argv = [__file__, window, '--elf', str(APP), '--start',
                '360' if window == 'warm' else '234', '--frames', '8', '--traffic', '1']
    runner.main()
    write(OUT / f'run-{window}/environment-proof.json', dict(
        IDEAL_GPU_MEMORY='0', LOD5_MODEL_MODE='normal',
        input_sha256=sha(OUT / f'run-{window}/inputs.json'),
        recipe_sha256=sha(OUT / 'recipe.py')))
    analyze(window)


def analyze(window):
    source_check()
    start = 360 if window == 'warm' else 234
    a, b = OLD / f'run-{window}', OUT / f'run-{window}'
    for d in (a, b):
        inv = json.loads((d / 'inputs.json').read_text())
        assert inv['success'] and inv['start'] == start and inv['frames'] == 8 and inv['traffic'] == 1
        assert sha(d / 'sm64.elf') == ELF_HASH
        for name, digest in inv['results_sha256'].items():
            assert sha(d / name) == digest, name
        assert 'FATAL' not in (d / 'run.log').read_text()
    names = ['scene.csv', 'geometry.csv', 'culling.csv',
             *[f'frame_{i:05}.bin' for i in range(start, start + 8)]]
    for name in names:
        assert (a / name).read_bytes() == (b / name).read_bytes(), name
    values = {}
    for label, d in [('baseline_i32_d128', a), ('candidate_i64_d64', b)]:
        c = json.loads((d / 'coupled.json').read_text())
        assert c['frames'] == c['presented'] == 8 and c['clock_hz'] == 100000000
        assert not c['memory_errors'] and not c['cram1_errors'] and not c['ring_overflow']
        rows = [r for r in csv.DictReader((d / 'frames.csv').open()) if start <= int(r['frame']) < start + 8]
        assert len(rows) == 8
        totals = {field: sum(int(r[field]) for r in rows) for field in
                  ('est_cycles', 'insns', 'icache_miss_sdram', 'icache_miss_bram', 'dcache_miss', 'mispredict')}
        counters = list(csv.DictReader((d / 'counters.csv').open()))[-1]
        values[label] = dict(cpu_progression_ms=c['cpu_cycles'] / 800000,
            presentation_window_ms=c['cycles'] / 800000, cpu_cycles=c['cpu_cycles'],
            presentation_cycles=c['cycles'], frame_totals=totals,
            gpu_busy_ms=int(counters['all_gpu_busy']) / 800000,
            polls=int(counters['rdptr_polls']), publications=int(counters['publications']),
            uploaded_words=int(counters['uploaded_words']))
    x, y = values.values()
    assert x['uploaded_words'] == y['uploaded_words']
    write(OUT / f'result-{window}.json', dict(passed=True, window=window, values=values,
        cpu_saving_ms=x['cpu_progression_ms'] - y['cpu_progression_ms'],
        cpu_saving_percent=100 * (1 - y['cpu_progression_ms'] / x['cpu_progression_ms']),
        presentation_saving_ms=x['presentation_window_ms'] - y['presentation_window_ms'],
        exact_buffers=8, full_histories_exact=True,
        compared_sha256={n: sha(a / n) for n in names}, deployment_ready=False))
    print(json.dumps(values, indent=2))


def seal():
    source_check()
    rows = [json.loads((OUT / f'result-{w}.json').read_text()) for w in ('warm', 'cold')]
    assert all(r['passed'] for r in rows)
    write(OUT / 'result.json', dict(passed=True, exact_buffers=16, windows=rows,
        limits=['Only modeled CPU cache set counts changed; no generated CPU, map, placement or timing qualification.',
                'Actual GPU/LOAD/SDRAM/CRAM RTL and assets are byte-identical to the parent.',
                'CPU progression and total presentation window are separate; neither is a physical Pocket FPS claim.',
                'One fixed warm and cold Bob-omb window, same in-sample PGO ELF. No broad workload conclusion.'],
        deployment_ready=False))
    report = ['I64/D64 renderer PGO model-only screen', '']
    for row in rows:
        x, y = row['values'].values()
        report += [f"{row['window']}: CPU progression {x['cpu_progression_ms']:.9f} -> {y['cpu_progression_ms']:.9f} ms/frame; saving {row['cpu_saving_ms']:.9f} ms ({row['cpu_saving_percent']:.4f}%).",
                   f"Presentation window {x['presentation_window_ms']:.9f} -> {y['presentation_window_ms']:.9f} ms/frame.",
                   f"Cache miss totals: {x['frame_totals']} -> {y['frame_totals']}", '']
    report += ['All16 full color/depth buffers and full scene/geometry/culling histories match.',
               'Only qsim/cache.c ic_sets256->512 and dc_sets1024->512 changed. Ways2/line64, ELF, arithmetic, RTL, assets, traffic and run options are unchanged.',
               'Model-only capacity screen; no actual CPU generation, resource/timing result, deployment or physical FPS claim.']
    (OUT / 'REPORT.txt').write_text('\n'.join(report) + '\n')
    shutil.copy2(__file__, OUT / 'recipe-final.py')
    write(OUT / 'manifest.json', dict(passed=True,
        files={str(f.relative_to(OUT)): sha(f) for f in OUT.rglob('*')
               if f.is_file() and f.name != 'manifest.json' and 'obj' not in f.parts and 'build' not in f.parts},
        evidence_sha256={str(p.relative_to(ROOT)): sha(p) for p in [OLD / 'manifest.json', APP]},
        deployment_ready=False))
    print(sha(OUT / 'manifest.json'))


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=['prepare', 'build', 'run', 'analyze', 'seal'])
    p.add_argument('--window', choices=['warm', 'cold'], default='warm')
    args = p.parse_args()
    globals()[args.action](args.window) if args.action in ('run', 'analyze') else globals()[args.action]()
