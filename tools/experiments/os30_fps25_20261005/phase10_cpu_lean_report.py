#!/usr/bin/env python3
"""Seal the private smaller-CPU correctness and area/cost screen."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
P = ROOT / 'docs/measurements/os30-fps25-20261005/phase10'
OUT = P / 'cpu-lean-v1'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, value):
    with path.open('x') as stream:
        json.dump(value, stream, indent=2)
        stream.write('\n')


def main():
    recipe = json.loads((OUT / 'recipe.json').read_text())
    generated = json.loads((OUT / 'generated.json').read_text())
    regression = json.loads((OUT / 'regression/result.json').read_text())
    mapping = json.loads((OUT / 'map-gpu-direct/result.json').read_text())
    model = json.loads((OUT / 'cpu-model/build.json').read_text())
    assert regression['passed'] and all(row['passed'] for row in regression['runs'])
    assert mapping['passed_map']
    cpu_hash = sha(OUT / 'VexiiRiscv.v')
    assert generated['rtl_sha256'] == regression['cpu_sha256'] == mapping['cpu_sha256'] == cpu_hash
    assert model['cpu_sha256'] == cpu_hash
    assert model['simulator_sha256'] == regression['simulator_sha256'] == sha(OUT / 'cpu-model/obj/Vtb_system')
    for rel, expected in model['source_sha256'].items():
        assert sha(OUT / 'cpu-model' / rel) == expected, rel
    for rel, expected in mapping['source_sha256'].items():
        assert sha(OUT / 'map-gpu-direct/frozen' / rel) == expected, rel
    entities = {row['entity']: row for row in mapping['entities']}
    assert int(entities['apf_top']['aluts'].split()[0]) == 29083
    assert int(entities['cpu_system']['aluts'].split()[0]) == 6895
    result = {
        'passed_correctness': True,
        'recommended_for_deployment': False,
        'whole_core_fit_attempted': False,
        'changes': recipe['changes'],
        'retained': ['full ISA including all AMOs and LR/SC', '128 KiB data cache',
                     '32 KiB instruction cache', 'BTB, RAS and gshare predictors'],
        'map_delta_vs_original_gpu_direct_v1': {
            'vexii_aluts': 6227 - 6732, 'vexii_registers': 4967 - 5241,
            'cpu_system_aluts': 6895 - 7415, 'cpu_system_registers': 6225 - 6516,
            'whole_aluts': 29083 - 29520, 'whole_registers': 26486 - 26777,
        },
        'map_delta_vs_queue_only': {
            'cpu_system_aluts': 6895 - 7171, 'cpu_system_registers': 6225 - 6360,
            'whole_aluts': 29083 - 29421, 'whole_registers': 26486 - 26620,
        },
        'performance': regression['runs'],
        'interpretation': 'The combined lean CPU saves only 276 additional CPU ALUTs beyond smaller queues while adding measurable vector, frontend and stress-kernel cost. It is not selected. The full GPU-direct baseline still needs 1348 fewer physical ALMs and 168 fewer LABs; synthesis ALUT changes cannot establish that placement will fit.',
        'limits': [
            'No fitted ALM/LAB count, timing or bitstream was produced for this candidate.',
            'These actual CPU and retained memory-system kernel timings are not complete SM64 or Quake2 frame timings.',
            'The combined candidate does not isolate the cost of fetch width, divider radix or I2F implementation.',
            'Single-entry generated AXI buses omit zero-width ID ports; the private wrapper fixes these IDs at zero.',
            'The generation helper inherited a smaller-queues scope label; recipe.json and this result state every actual change.',
        ],
    }
    save(OUT / 'result.json', result)
    report = '''The private lean CPU passes native correctness but is not selected.

Changes: instruction refill 2 to 1; data refill/writeback 2 to 1; store buffer 2 slots/16 operations to 1 slot/4 operations; fetch data width 64 to 32; radix-4 to radix-2 division; serial rather than pipelined integer-to-float conversion. All ISA operations, 128 KiB data cache, 32 KiB instruction cache and predictors remain present. The private AXI wrapper assigns constant-zero IDs for the generated single-entry buses.

Actual CPU plus retained SDRAM/arbiter tests pass integer/FPU/atomics/traps/cache stress, compressed frontend redirects and interrupts, I2F rounding/flags/hazards, and all 14 kernel value/hash checks in quiet and scanout conditions. No memory protocol checker errors were observed.

Map-only cpu_system change: -520 ALUTs/-291 registers versus original GPU-direct v1, only -276 ALUTs/-135 registers beyond the queue-only candidate. Complete-core synthesis change is -437 ALUTs/-291 registers, with 45 DSPs unchanged. These are synthesis counts, not physical fitted ALMs or LABs. No fit, timing or deployment is claimed.

Measured component costs versus retained A: vector kernels +9.01 to +9.09%; triangles +2.27 to +2.47%; compressed frontend suite +11.85 to +12.28%; stress +9.70 to +11.62%; kernel suite including setup +4.21% quiet/+5.60% scanout. Unchanged memory/cache components do not erase the CPU regressions. The less aggressive queue-only screen has smaller triangle cost (+0.14 to +0.28%), but neither screen establishes a solution to the 1348-ALM/168-LAB full-core deficit. Complete game and offload timing remain unmeasured for both CPUs.
'''
    with (OUT / 'REPORT.txt').open('x') as stream:
        stream.write(report)
    paths = set()
    for directory in ['regression', 'helpers']:
        paths.update(path for path in (OUT / directory).rglob('*') if path.is_file())
    for name in ['recipe.json', 'queue-preparation.json', 'cache-preparation.json',
                 'sources.json', 'generate.log', 'generated.json', 'VexiiRiscv.v',
                 'result.json', 'REPORT.txt', 'cpu-model/build.json',
                 'cpu-model/build.log', 'map-gpu-direct/map.log',
                 'map-gpu-direct/result.json']:
        paths.add(OUT / name)
    paths.update(OUT.glob('*.pl.log'))
    paths.update((OUT / 'map-gpu-direct/job/src/fpga/targets/pocket/output_files').glob('*.map.rpt'))
    paths.add(OUT / 'map-gpu-direct/job/src/fpga/targets/pocket/ap_core.qsf')
    paths.add(P / 'cpu-queues-v1/final-manifest.json')
    paths.add(P / 'gpu-direct-core-v1/final-manifest.json')
    for name in ['phase10_cpu_queues.py', 'phase10_cpu_queue_test.py',
                 'phase10_cpu_queue_adapter.py', 'phase10_cpu_lean.py',
                 'phase10_cpu_lean_map.py', 'phase10_cpu_lean_report.py']:
        paths.add(ROOT / 'tools/experiments/os30_fps25_20261005' / name)
    evidence = {str(path.relative_to(ROOT)): sha(path) for path in sorted(paths)}
    save(OUT / 'final-manifest.json', {
        'passed_correctness': True,
        'recommended_for_deployment': False,
        'evidence_sha256': evidence,
    })
    print(sha(OUT / 'final-manifest.json'))


if __name__ == '__main__':
    main()
