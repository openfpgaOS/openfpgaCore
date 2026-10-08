#!/usr/bin/env python3
"""Compare private triangle candidates using the retained Pocket A GPU RTL."""
import argparse
import concurrent.futures
import importlib.util
import json
from pathlib import Path
import re
import struct
import subprocess

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('triangle_experiments', HERE / 'phase7_experiment.py')
e = importlib.util.module_from_spec(spec)
spec.loader.exec_module(e)


def run(label, mode, frames):
    directory = e.OUT / label
    capture = directory / ('capture-' + mode)
    elf = directory / ('app.elf' if mode == 'normal' else 'diagnostic.elf')
    e.verify_capture(capture, e.sha(elf))
    rtl = e.CAMP / 'phase4/texmem/extended/model'
    exe = rtl / 'obj/Vtb_gpu'
    assert e.sha(exe) == json.loads((rtl / 'build.json').read_text())['simulator_sha256']
    adapter = e.module('triangle_depth', HERE / 'phase4_combined_validate.py')
    prior_rows = None
    if label != 'control':
        if mode == 'normal':
            prior_dir = e.OUT / 'control'
            prior_rows = json.loads((prior_dir / 'replay-normal.json').read_text())
        else:
            prior_dir = e.CAMP / 'phase6'
            prior_rows = json.loads((prior_dir / 'replay-fps.json').read_text())
        prior_rows = {(r['traffic'], r['frame']): r for r in prior_rows}

    def one(job):
        traffic, frame = job
        source = capture / 'gpuvec' / f'frame_{frame:05}.vec'
        dest = directory / 'replays' / mode / str(traffic)
        dest.mkdir(parents=True, exist_ok=True)
        stem = dest / source.stem
        assert not stem.with_suffix('.log').exists(), stem
        commands, depth, tags = adapter.with_depth(source, stem.with_suffix('.vec'))
        assert depth
        command = [str(exe), str(stem.with_suffix('.vec')), str(stem.with_suffix('.bin')),
                   f'+memory_traffic={traffic}', '+tagged_tex=1']
        with stem.with_suffix('.log').open('x') as log:
            subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=600)
        raw = stem.with_suffix('.bin').read_bytes()
        assert len(raw) == 307212
        stats = {k: int(v) for k, v in re.findall(r'(\w+)=(\d+)', stem.with_suffix('.log').read_text())}
        assert stats['cram_errors'] == 0 and stats['words'] == stats['readback_words']
        result = dict(traffic=traffic, frame=frame, commands=commands, tags=tags,
                      command=command, stats=stats, simulator_sha256=e.sha(exe),
                      adapted_vector_sha256=e.sha(stem.with_suffix('.vec')),
                      output_sha256=e.sha(stem.with_suffix('.bin')),
                      log_sha256=e.sha(stem.with_suffix('.log')))
        if prior_rows is not None:
            prior = prior_rows[traffic, frame]
            path = prior_dir / 'replays' / ('normal' if mode == 'normal' else 'fps') / str(traffic) / stem.with_suffix('.bin').name
            assert e.sha(path) == prior['output_sha256']
            old = path.read_bytes()
            result.update(color_exact=raw[:153600] == old[:153600],
                          depth_exact=raw[153612:] == old[153612:],
                          fence_exact=raw[153600:153604] == old[153600:153604],
                          reference_output_sha256=e.sha(path),
                          gpu_cycle_change_percent=100*(stats['cycles']/prior['stats']['cycles']-1))
        return result

    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(one, [(t, f) for t in (1, 2) for f in frames]))
    e.write(directory / f'replay-{mode}.json', results)
    if prior_rows is not None:
        assert all(r['color_exact'] and r['depth_exact'] and r['fence_exact'] for r in results), 'Rendering changed; inspect retained results'
    print(json.dumps(dict(candidate=label, mode=mode, comparisons=len(results),
                         exact_to_baseline=prior_rows is not None,
                         gpu_cycle_changes=[r.get('gpu_cycle_change_percent') for r in results])))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('candidate')
    parser.add_argument('--mode', choices=['normal', 'diagnostic'], default='normal')
    parser.add_argument('--frames', default='360,600,720')
    args = parser.parse_args()
    assert re.fullmatch(r'[a-z0-9][a-z0-9_-]*', args.candidate)
    run(args.candidate, args.mode, [int(n) for n in args.frames.split(',')])
