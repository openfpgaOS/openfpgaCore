#!/usr/bin/env python3
"""Build and validate F7 wait attribution in private storage."""
import argparse
import csv
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]
CAMP = ROOT / 'docs/measurements/os30-fps25-20261005'
OUT = CAMP / 'phase5'
OLD = CAMP / 'phase4/texmem-capture-v2'
BASE = json.loads((OUT / 'baseline.json').read_text())
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
read = lambda p: json.loads(p.read_text())


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def build(mode):
    m = module('f7_builder', Path(__file__).with_name('build.py'))
    m.OUT, m.STAGE, m.INPUTS = OUT, OUT / 'SM64', BASE['inputs']
    sys.argv = [str(Path(__file__)), 'candidate', '--diagnostic', str(int(mode == 'fps'))]
    m.main()
    if mode == 'normal':
        assert sha(OUT / 'candidate/app.elf') == BASE['normal_elf_sha256'], 'Normal executable changed'


def prepare_sim():
    manifest = read(OLD / 'manifest.json')
    assert sha(OLD / 'qsim/qsim') == manifest['simulator_sha256']
    dest = OUT / 'qsim'
    dest.mkdir(exist_ok=False)
    for name, digest in manifest['sources'].items():
        assert sha(OLD / 'qsim' / name) == digest, name
        shutil.copy2(OLD / 'qsim' / name, dest / name)
    p = dest / 'main.c'
    s = p.read_text()
    # Passive observer only: old snapshots keep their39 fields. F7 appends
    # fields; use the ELF symbol size, bounded by a small host-side buffer.
    needle = '                    if(!diagnostic_csv) {'
    assert s.count(needle) == 1
    s = s.replace(needle, '                    unsigned diagnostic_words=diagnostic->size/4u;\n'
                  '                    if(diagnostic_words<39u || diagnostic_words>64u) abort();\n' + needle)
    assert s.count('k<39;k++') == 2
    s = s.replace('k<39;k++', 'k<diagnostic_words;k++')
    assert s.count('uint32_t data[39]={0};') == 1
    s = s.replace('uint32_t data[39]={0};', 'uint32_t data[64]={0};')
    needle = '                        memcpy(data+30,qsim_mem_ptr(E.cpu,diagnostic->addr+30*4,9*4),9*4);'
    assert s.count(needle) == 1
    s = s.replace(needle, needle + '\n                    if(diagnostic_words>39u)\n'
                  '                        memcpy(data+39,qsim_mem_ptr(E.cpu,diagnostic->addr+39*4,(diagnostic_words-39)*4),(diagnostic_words-39)*4);')
    p.write_text(s)
    with (OUT / 'qsim-build.log').open('w') as f:
        subprocess.run(['make', '-j3'], cwd=dest, stdout=f, stderr=subprocess.STDOUT, check=True)
    (OUT / 'qsim-manifest.json').write_text(json.dumps({
        'parent_manifest_sha256': sha(OLD / 'manifest.json'),
        'simulator_sha256': sha(dest / 'qsim'),
        'sources': {name: sha(dest / name) for name in manifest['sources']},
        'scope': 'Only passive snapshot observer size changes. Eager GPU cannot measure physical wait time.'
    }, indent=2) + '\n')


def capture(label):
    m = module('f7_capture', ROOT / 'tools/experiments/sm64_batch_cull_20261005/validation/capture.py')
    m.OUT = OUT
    elf = (CAMP / 'phase4/combined-cold/aspect-texmem-cold-v1/diagnostic.elf'
           if label == 'f6-control' else OUT / 'candidate/diagnostic.elf')
    manifest = read(OUT / 'qsim-manifest.json')
    assert sha(OUT / 'qsim/qsim') == manifest['simulator_sha256']
    sys.argv = [str(Path(__file__)), label, '--elf', str(elf), '--exe', str(OUT / 'qsim/qsim'),
                '--geometry', '0', '--audio']
    if label == 'f7-panel':
        sys.argv += ['--panel', '--vectors', '640']
    m.main()


def analyze():
    parent = OLD / 'capture-combined-cold-fps'
    control, current = OUT / 'capture-f6-control', OUT / 'capture-f7'
    for directory in (parent, control, current):
        inputs = read(directory / 'inputs.json')
        assert inputs['success']
        for rel, digest in inputs['results_sha256'].items():
            assert sha(directory / rel) == digest, (directory, rel)
    names = ['frames.csv', 'scene.csv', 'geometry.csv', 'diagnostic.csv', 'culling.csv']
    names += [p.name for p in parent.glob('profile*.tsv')]
    names += [str(p.relative_to(parent)) for p in (parent / 'gpuvec').glob('*.vec')]
    unchanged = {name: sha(parent / name) == sha(control / name) for name in names}
    assert all(unchanged.values()), unchanged
    behavior = {name: sha(control / name) == sha(current / name)
                for name in ('scene.csv', 'geometry.csv', 'culling.csv')}
    assert all(behavior.values()), behavior
    rows = lambda directory: list(csv.DictReader((directory / 'frames.csv').open()))
    a, b = rows(control), rows(current)
    assert len(a) == len(b) == 721
    totals = {}
    for key in ('insns', 'est_cycles', 'gpu_cmd_words', 'icache_miss_sdram', 'dcache_miss'):
        x, y = (sum(int(r[key]) for r in data[360:]) for data in (a, b))
        totals[key] = dict(f6=x, f7=y, delta=y-x, percent_change=(y/x-1)*100)
    assert totals['gpu_cmd_words']['delta'] == 0
    diagnostic = list(csv.DictReader((current / 'diagnostic.csv').open()))
    assert len(diagnostic) == 721 and len(diagnostic[0]) == 49
    valid = 0
    for row in diagnostic:
        s = [int(row[f'stat{i}']) for i in range(48)]
        if not s[0]:
            continue
        valid += 1
        assert s[18] == 7 and s[19:39] == [0]*20, row
        assert sum(s[39:43]) <= s[10] and s[10]-sum(s[39:43]) <= 3, row
        assert s[43] <= (s[3]+15)//16 and s[46] == 16, row
        assert s[44]+s[45] <= s[47], row
        if s[43] == 0:
            assert s[44] == s[45] == s[47] == 0, row
    assert valid > 0
    result = dict(control_observer_noninterference=unchanged, gameplay_geometry_culling_exact=behavior,
                  gameplay_frames360_through720=totals,
                  appended_snapshot_fields=len(diagnostic[0])-40,
                  snapshot_partition_and_sample_checks=dict(passed=True, valid_rows=valid),
                  capture_inputs_sha256={p.name: sha(p / 'inputs.json') for p in (control, current)},
                  scope='CPU cost is modeled; eager GPU does not exercise hardware backpressure. Explicit wait paths are tested separately.')
    (OUT / 'analysis.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))


def replay():
    # Reuse the unchanged actual-A RTL simulator and depth/color comparison.
    # The reference pixels are also exact between F5 and F6, established by F6.
    m = module('f7_replay', Path(__file__).with_name('phase4_combined_validate.py'))
    aliases = OUT / 'capture-aliases'
    aliases.mkdir(exist_ok=True)
    alias = aliases / 'capture-combined-fps'
    if not alias.exists():
        alias.symlink_to(OUT / 'capture-f7', target_is_directory=True)
    assert alias.resolve() == OUT / 'capture-f7'
    m.OUT, m.CAP = OUT, aliases
    m.replay('fps', [1, 2])


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=['build-normal', 'build-fps', 'prepare-sim', 'control', 'capture', 'panel', 'analyze', 'replay'])
    a = p.parse_args().action
    if a.startswith('build-'):
        build(a.split('-')[1])
    elif a == 'prepare-sim':
        prepare_sim()
    elif a in ('control', 'capture'):
        capture('f6-control' if a == 'control' else 'f7')
    elif a == 'panel':
        capture('f7-panel')
    elif a == 'analyze':
        analyze()
    else:
        replay()
