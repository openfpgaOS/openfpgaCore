#!/usr/bin/env python3
"""Matched actual-GPU/LOAD windows for stack-owned adjacent LOAD overlap v2."""
import argparse
import csv
import json
import os
import shutil
import sys
from pathlib import Path
from phase10_experiment import CAMP, ROOT, module, sha, write

P = CAMP / 'phase10'
OLD = P / 'cache-write-lod5-coupled-v2'
OUT = P / 'load-submit-stack-coupled-v2'
APP = P / 'load-submit-stack-v2/app.elf'
FUNCTIONAL = P / 'load-submit-stack-functional-v2'


def prepare():
    assert sha(OLD / 'manifest.json') == 'c46755893729024837ec5958017e2b8a4f5ee80b0c10387c6146a202388415b3'
    for n, h in json.loads((OLD / 'manifest.json').read_text())['files'].items():
        assert sha(OLD / n) == h, n
    build = json.loads((APP.parent / 'build-normal.json').read_text())
    assert sha(APP) == build['elf_sha256']
    OUT.mkdir(exist_ok=False)
    shutil.copytree(OLD / 'model/frozen', OUT / 'model/frozen')
    shutil.copytree(OLD / 'qsim', OUT / 'qsim', ignore=shutil.ignore_patterns('build', 'qsim'))
    (OUT / 'model/obj').mkdir()
    shutil.copyfile(OLD / 'model/obj/Vtb_gpu', OUT / 'model/obj/Vtb_gpu')
    shutil.copymode(OLD / 'model/obj/Vtb_gpu', OUT / 'model/obj/Vtb_gpu')
    oldbuild = json.loads((OLD / 'model/build.json').read_text())
    assert sha(OUT / 'model/obj/Vtb_gpu') == oldbuild['simulator_sha256']
    write(OUT / 'model/build.json', dict(oldbuild,
        parent_build_sha256=sha(OLD / 'model/build.json'), reused_byte_identical=True))
    shutil.copyfile(__file__, OUT / 'recipe.py')
    write(OUT / 'prepared.json', dict(scope=__doc__, elf_sha256=sha(APP),
        source_elf_record_sha256=sha(APP.parent / 'source.json'),
        baseline_manifest_sha256=sha(OLD / 'manifest.json'),
        baseline_elf_sha256=json.loads((OLD / 'prepared.json').read_text())['elf_sha256'],
        simulator_sha256=oldbuild['simulator_sha256'],
        source_sha256={str(p.relative_to(OUT)): sha(p) for p in OUT.rglob('*') if p.is_file()},
        helper_sha256=sha(Path(__file__)), rtl_changed=False, deployment_ready=False))
    print(OUT)


def run(window):
    proof = json.loads((FUNCTIONAL / 'result-normal.json').read_text())
    assert proof['passed'] and proof['elf_sha256'] == sha(APP)
    assert sha(FUNCTIONAL / 'app.elf') == sha(APP)
    prep = json.loads((OUT / 'prepared.json').read_text())
    assert sha(APP) == prep['elf_sha256']
    for n, h in prep['source_sha256'].items():
        assert sha(OUT / n) == h, n
    m = module('load_submit_coupled_runner', ROOT / 'tools/experiments/os30_overlap_20261006/run.py')
    m.OUT = OUT
    os.environ['IDEAL_GPU_MEMORY'] = '0'
    os.environ['LOD5_MODEL_MODE'] = 'normal'
    sys.argv = [str(Path(__file__)), window, '--elf', str(APP), '--start',
                '360' if window == 'warm' else '234', '--frames', '8']
    m.main()
    d = OUT / f'run-{window}'
    assert 'FATAL' not in (d / 'run.log').read_text()
    path = d / 'inputs.json'
    inv = json.loads(path.read_text())
    inv['environment'].update(IDEAL_GPU_MEMORY='0', LOD5_MODEL_MODE='normal')
    inv['functional_normal_sha256'] = sha(FUNCTIONAL / 'result-normal.json')
    path.write_text(json.dumps(inv, indent=2) + '\n')
    summarize(window)


def summarize(window):
    start = 360 if window == 'warm' else 234
    a, b = OLD / f'run-{window}', OUT / f'run-{window}'
    for d in (a, b):
        inv = json.loads((d / 'inputs.json').read_text())
        assert inv['success'] and inv['start'] == start and inv['frames'] == 8 and inv['traffic'] == 1
        for n, h in inv['results_sha256'].items():
            assert sha(d / n) == h, n
    for n in ('scene.csv', 'geometry.csv', 'culling.csv'):
        assert (a / n).read_bytes() == (b / n).read_bytes(), n
    images = []
    for i in range(start, start + 8):
        n = f'frame_{i:05}.bin'
        assert (a / n).read_bytes() == (b / n).read_bytes(), n
        images.append(dict(frame=i, sha256=sha(a / n)))
    timing = {}
    for label, d in [('baseline', a), ('overlap', b)]:
        c = json.loads((d / 'coupled.json').read_text())
        assert c['presented'] == c['frames'] == 8
        assert not c['memory_errors'] and not c['cram1_errors'] and not c['ring_overflow']
        cnt = list(csv.DictReader((d / 'counters.csv').open()))[-1]
        timing[label] = dict(frame_ms=c['cpu_cycles'] / 800000,
            gpu_busy_ms=int(cnt['all_gpu_busy']) / 800000,
            rdptr_polls=int(cnt['rdptr_polls']), publications=int(cnt['publications']),
            uploaded_words=int(cnt['uploaded_words']))
    assert timing['baseline']['uploaded_words'] == timing['overlap']['uploaded_words']
    x, y = timing['baseline']['frame_ms'], timing['overlap']['frame_ms']
    result = dict(passed=True, window=window, timing=timing, images=images,
        all8_actual_color_depth_exact=True, all_histories_exact=True,
        frame_saving_ms=x-y, frame_saving_percent=100*(1-y/x),
        simulator_byte_identical_to_control=True, deployment_ready=False)
    v1 = P / 'load-submit-coupled-v1' / f'result-{window}.json'
    reference = json.loads(v1.read_text())
    result['prior_static_overlap_ms'] = reference['timing']['overlap']['frame_ms']
    result['saving_vs_prior_static_ms'] = result['prior_static_overlap_ms'] - y
    result['prior_static_result_sha256'] = sha(v1)
    write(OUT / f'result-{window}.json', result)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('action', choices=['prepare', 'run', 'summarize'])
    p.add_argument('--window', choices=['warm', 'cold'], default='warm')
    a = p.parse_args()
    prepare() if a.action == 'prepare' else globals()[a.action](a.window)
