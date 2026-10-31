#!/usr/bin/env python3
"""Reproduce the isolated os25 clamp staging A/B; never edit production inputs."""
import argparse
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
sys.path.insert(0, str(ROOT / 'tools'))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')


def audit(out, inputs):
    manifests = [json.loads((out / label / 'os25/manifest.json').read_text())
                 for label in ('baseline', 'candidate')]
    a, b = manifests
    for key in ('parameters', 'cflags', 'flags', 'sources'):
        if a[key] != b[key]:
            raise ValueError('A/B inputs differ: ' + key)
    if a['variant'] != inputs['baseline_variant']:
        raise ValueError('Baseline feature configuration changed')
    expected = copy.deepcopy(inputs['baseline_variant'])
    expected['defs'] += ' INCLUDE_GPU_CLAMP_PIPE'
    if b['variant'] != expected:
        raise ValueError('Candidate differs by more than clamp staging')
    for label in ('baseline', 'candidate'):
        frozen = out / label / 'frozen'
        hashes = json.loads((out / label / 'frozen-sources.json').read_text())
        changed = [name for name, digest in hashes.items() if sha(frozen / name) != digest]
        if changed:
            raise ValueError('Frozen input drift: ' + ', '.join(changed))
    result = dict(change='Only enable INCLUDE_GPU_CLAMP_PIPE in exact os25',
                  all_pass=True, physical_pending=True, tests={})
    for prefix in ('', 'perf-'):
        for mode in ('quiet', 'delayed'):
            name = prefix + mode
            left = (out / 'baseline/os25' / (name + '.log')).read_bytes()
            right = (out / 'candidate/os25' / (name + '.log')).read_bytes()
            if left != right:
                raise ValueError('Rendering/cycles differ: ' + name)
            row = dict(byte_identical=True, sha256=hashlib.sha256(left).hexdigest())
            text = left.decode()
            if prefix:
                row['cycles'] = [int(n) for n in re.findall(r'cycles=\s*(\d+)', text)]
            else:
                match = re.search(r'Acceptance Results: (\d+) passed, (\d+) failed', text)
                if not match or int(match[2]):
                    raise ValueError('Acceptance did not pass: ' + name)
                row.update(passed=int(match[1]), failed=int(match[2]),
                           cycles=int(re.search(r'Total sim_time: (\d+)', text)[1]) // 2)
            result['tests'][name] = row
    expected_tests = json.loads((HERE / 'functional-results.json').read_text())['tests']
    if result['tests'] != expected_tests:
        raise ValueError('Results differ from the qualified receipt')
    save(out / 'reproduction-results.json', result)
    print(json.dumps(result, indent=2))


def check(out, inputs):
    out.mkdir(parents=True, exist_ok=False)
    spec = importlib.util.spec_from_file_location('gpu_matrix', ROOT / 'tools/check_gpu_target_matrix.py')
    matrix = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(matrix)
    # Pin the original feature list even after a candidate is promoted.
    matrix.inventory = lambda **kwargs: [copy.deepcopy(inputs['baseline_variant'])]
    for label in ('baseline', 'candidate'):
        sys.argv = [str(ROOT / 'tools/check_gpu_target_matrix.py'), '--variant', 'os25',
                    '--jobs', '1', '--output', str(out / label)]
        if label == 'candidate':
            sys.argv += ['--extra-macro', 'INCLUDE_GPU_CLAMP_PIPE']
        matrix.main()
    source = out / 'tb_gpu_perf_main.cpp'
    shutil.copyfile(ROOT / 'src/fpga/test/tb_gpu_perf_main.cpp', source)
    version = subprocess.check_output(['verilator', '-V'], text=True)
    vroot = Path(re.search(r'^\s*VERILATOR_ROOT\s*=\s*(\S+)', version, re.M)[1])
    commands = {}
    for label in ('baseline', 'candidate'):
        dest = out / label / 'os25'
        obj = dest / 'obj'
        command = ['g++', '-std=c++17', '-O2', '-DGPU_PERF_POCKET', '-I' + str(obj),
                   '-I' + str(vroot / 'include'), '-I' + str(vroot / 'include/vltstd'),
                   str(source), *[str(obj / n) for n in ('verilated.o', 'verilated_vcd_c.o',
                       'verilated_threads.o', 'Vtb_gpu__ALL.a')], '-pthread', '-latomic',
                   '-o', str(dest / 'perf')]
        commands[label] = command
        with (dest / 'perf-build.log').open('w') as log:
            subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=True)
        for mode, args in [('quiet', []), ('delayed', ['+gpu_rd_latency=24',
                          '+gpu_rd_latency_var=1', '+gpu_wr_latency=17'])]:
            with (dest / ('perf-' + mode + '.log')).open('w') as log:
                subprocess.run([str(dest / 'perf'), *args], stdout=log,
                               stderr=subprocess.STDOUT, check=True, timeout=300)
    save(out / 'perf-build.json', dict(source_sha256=sha(source), commands=commands))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('check', 'audit'))
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    inputs = json.loads((HERE / 'inputs.json').read_text())
    if args.action == 'check':
        changed = [name for name, digest in inputs['source_sha256'].items()
                   if sha(ROOT / name) != digest]
        if changed:
            raise ValueError('Review changed source inputs before retesting: ' + ', '.join(changed))
        check(args.output.resolve(), inputs)
    audit(args.output.resolve(), inputs)


if __name__ == '__main__':
    main()
