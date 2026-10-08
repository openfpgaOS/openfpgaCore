#!/usr/bin/env python3
"""Private, separately measured software triangle-path experiments over F8."""
import argparse
import csv
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]
CAMP = ROOT / 'docs/measurements/os30-fps25-20261005'
OUT = CAMP / 'phase7'
BASE = json.loads((OUT / 'baseline.json').read_text())


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def write(path, data):
    with path.open('x') as stream:
        json.dump(data, stream, indent=2)
        stream.write('\n')


def prepare(label):
    directory = OUT / label
    assert directory.is_dir()
    for name in BASE['inputs']:
        assert (directory / name).is_file(), name
    stage = directory / 'SM64'
    assert not stage.exists(), 'Keep existing private stage'
    subprocess.run(['cp', '-a', '--reflink=auto', str(CAMP / 'phase6/SM64'), str(stage)], check=True)
    write(directory / 'prepared.json', dict(parent_qualified_sha256=BASE['parent_qualified_sha256'],
          source_sha256={name: sha(directory / name) for name in BASE['inputs']}))


def build(label, mode):
    directory, diagnostic = OUT / label, mode == 'diagnostic'
    if not (directory / 'SM64').exists():
        prepare(label)
    assert not (directory / f'build-{mode}.json').exists(), 'Keep prior build attempt evidence'
    builder = module('triangle_builder', Path(__file__).with_name('build.py'))
    builder.OUT, builder.STAGE, builder.INPUTS = OUT, directory / 'SM64', BASE['inputs']
    objects = builder.STAGE / '.obj' / ('sm64-perfdiag' if diagnostic else 'sm64')
    assert not objects.is_symlink() and objects.parent == directory / 'SM64/.obj'
    if objects.exists():
        shutil.rmtree(objects)
    sys.argv = [str(Path(__file__)), label, '--diagnostic', str(int(diagnostic))]
    builder.main()


def simulator():
    manifest = json.loads((CAMP / 'phase6/qsim-manifest.json').read_text())
    exe = Path(manifest['simulator'])
    assert sha(exe) == manifest['simulator_sha256']
    for name, digest in manifest['unchanged_sources'].items():
        assert sha(exe.parent / name) == digest
    return exe


def capture(label, mode, panel=False):
    directory = OUT / label
    m = module('triangle_capture', ROOT / 'tools/experiments/sm64_batch_cull_20261005/validation/capture.py')
    m.OUT = directory
    name = 'app.elf' if mode == 'normal' else 'diagnostic.elf'
    tag = mode + ('-panel' if panel else '')
    sys.argv = [str(Path(__file__)), tag, '--elf', str(directory / name),
                '--exe', str(simulator()), '--geometry', '0', '--audio']
    if panel:
        assert mode == 'diagnostic'
        sys.argv += ['--panel', '--vectors', '640']
    m.main()


def verify_capture(directory, elf_hash):
    inputs = json.loads((directory / 'inputs.json').read_text())
    assert inputs['success'] and inputs['audio'] and not inputs['geometry_capability']
    assert inputs['hw_features'] == '0x2b136753' and not inputs['command_dma']
    assert sha(directory / 'sm64.elf') == elf_hash
    for path, digest in inputs['sha256'].items():
        assert sha(Path(path)) == digest, path
    for rel, digest in inputs['results_sha256'].items():
        assert sha(directory / rel) == digest, rel
    return list(csv.DictReader((directory / 'frames.csv').open()))


def analyze(label, mode):
    directory = OUT / label
    baseline = OUT / 'control/capture-normal' if mode == 'normal' else CAMP / 'phase6/capture-f8'
    current = directory / ('capture-' + mode)
    a = verify_capture(baseline, BASE['normal_elf_sha256'] if mode == 'normal' else BASE['diagnostic_elf_sha256'])
    b = verify_capture(current, sha(directory / ('app.elf' if mode == 'normal' else 'diagnostic.elf')))
    assert len(a) == len(b) == 721
    exact = {name: sha(baseline / name) == sha(current / name)
             for name in ('scene.csv', 'geometry.csv', 'culling.csv')}
    totals = {}
    for key in ('insns', 'est_cycles', 'gpu_cmd_words', 'icache_miss_sdram', 'dcache_miss', 'mispredict'):
        x, y = (sum(int(row[key]) for row in data[360:]) for data in (a, b))
        totals[key] = dict(baseline=x, candidate=y, delta=y-x, saving_percent=100*(1-y/x))
    windows = []
    for lo, hi in ((360, 480), (480, 600), (600, 721)):
        x, y = (sum(int(row['est_cycles']) for row in data[lo:hi]) for data in (a, b))
        windows.append(dict(first=lo, last=hi-1, baseline=x, candidate=y, saving_percent=100*(1-y/x)))
    result = dict(candidate=label, mode=mode, scene_geometry_culling_exact=exact,
                  gameplay_frames360_through720=totals, windows=windows,
                  baseline_capture_sha256=sha(baseline/'inputs.json'),
                  candidate_capture_sha256=sha(current/'inputs.json'),
                  source_sha256={name: sha(directory/name) for name in BASE['inputs']},
                  rendering_qualified=False,
                  scope='Modeled CPU ranking only; exact GPU output and physical FPS require separate validation.')
    write(directory / f'analysis-{mode}.json', result)
    print(json.dumps(result, indent=2))
    assert all(exact.values()), 'Behavioral counters changed; investigate before selecting'


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare', 'build', 'capture', 'analyze'])
    parser.add_argument('candidate')
    parser.add_argument('--mode', choices=['normal', 'diagnostic'], default='normal')
    parser.add_argument('--panel', action='store_true')
    args = parser.parse_args()
    assert re.fullmatch(r'[a-z0-9][a-z0-9_-]*', args.candidate)
    if args.action == 'prepare':
        prepare(args.candidate)
    elif args.action == 'build':
        build(args.candidate, args.mode)
    elif args.action == 'capture':
        capture(args.candidate, args.mode, args.panel)
    else:
        analyze(args.candidate, args.mode)
