#!/usr/bin/env python3
"""Summarize hash-bound depth-ordering experiments without an FPS claim."""
import csv
import hashlib
import json
from pathlib import Path

D = Path(__file__).resolve().parent
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
inputs = {str(Path(__file__)): sha(Path(__file__))}

def read(p):
    inputs[str(p)] = sha(p)
    return json.loads(p.read_text())

def text(p):
    inputs[str(p)] = sha(p)
    return p.read_text()

runs = {}
for label in ['baseline-v1', 'legacy-srw-v1', 'depth-v3', 'priority-v1']:
    r = D/label/'run'
    check = read(D/label/'check.json')
    assert check['all_FCSR_and_FP_registers_exact']
    assert check['untouched_buffer_history_exact']
    build = read(D/label/'build.json')
    for name, h in build['inputs_sha256'].items():
        assert sha(Path(name)) == h, name
    model = Path(build['model'])
    prep = read(model/'prepared.json')
    for rel, h in prep['source_sha256'].items():
        assert sha(model/rel) == h, rel
    manifest = read(r/'manifest.json')
    for name, h in manifest['outputs_sha256'].items():
        assert sha(r/name) == h, name
    result = read(r/'result.json')
    assert result['status'] == 0
    b = [list(map(int, x.split())) for x in text(r/'boundaries.tsv').splitlines()]
    intervals = [dict(cycles=y[1]-x[1], retired=y[2]-x[2], upload_words=y[3]-x[3])
                 for x, y in zip(b, b[1:])]
    profile = read(r/'gpu-profile.json')
    w = profile['windows']['2']
    partition = dict(zip(profile['partition_names'], w['partition']))
    events = list(csv.DictReader(text(r/'gpu-profile-events.csv').splitlines()))
    events = [x for x in events if x['window'] == '2']
    start = int(next(x['cycle'] for x in events if x['name'] == 'sm64_perf_render_begin'))
    end = int(next(x['cycle'] for x in events if x['name'] == 'sm64_perf_render_end'))
    runs[label] = dict(intervals=intervals, render_cycles_warm=end-start,
        gpu_partition_warm=partition, gpu_metrics_warm=w['metrics'],
        gpu_reads_warm=w['reads'], model=str(model),
        oracle='Exact color/depth, buffer history, draw commands, surface pool, FP/FCSR; only known timestamp register differs.',
        validation_receipt=str(D/label/'check.json'))
baseline = runs['baseline-v1']['intervals']
for r in runs.values():
    r['cycle_change_percent'] = [100*(x['cycles']/y['cycles']-1)
                               for x,y in zip(r['intervals'],baseline)]
    r['cycle_change'] = [x['cycles']-y['cycles'] for x,y in zip(r['intervals'],baseline)]

test = read(D/'tests/summary.json')
source_audit = read(D/'review/cache-priority-source-audit-v1.json')
result_audit = read(D/'review/results-audit-v1.json')
for p in sorted((D/'tests').glob('*/manifest.json')):
    read(p)
for p in [D/'work/rtl-notes.json', D/'work-v2/rtl-notes.json',
          D/'work/patch-verification.json', D/'work-v2/patch-verification.json']:
    read(p)
out = dict(status='COMPLETE_RETAIN_BASELINE', runs=runs,
    recommendation='Retain the simpler qualified core. No measured gain large enough to justify additional logic or timing risk.',
    selected_production_change=None, production_modified=False,
    quartus_run=False, full_sweep_run=False, deployed=False,
    prototype=dict(depth_gate_added_state_bits=129,
                   cache_priority_added_state_bits=0,
                   additional_ram_required_by_source=0,
                   area_timing_qualified=False),
    tests=test,
    interpretation=[
        'The smaller depth gate saves8104 warm cycles (0.1856%). The reference broader selective wait saves8248 (0.1889%).',
        'The guarded cache priority variant takes1051 more warm cycles (0.0241%) than baseline; effectively unchanged.',
        'Most early depth-read admission savings reappear as response waiting. Read priority reduces AR wait further but shifts pressure back to write/drain paths.',
        'Cache miss/writeback counts remain nearly unchanged, so this changes scheduling rather than eliminating significant memory work.',
        'These results rule out this barrier relaxation as a worthwhile standalone frame-speed optimization in the sampled workload.'
    ],
    excluded=[
        dict(label='depth-v1',reason='Frame execution completed, but inlined effective depth predicate was not reflected in the first passive observer. Do not use its partition attribution.'),
        dict(label='depth-v2',reason='Observer compile failed because the disabled write-combiner busy signal was optimized away. No replay ran.')
    ],
    limitations=[
        'Two fixed SM64 native replay intervals, synthetic display/audio and bounded guest services; not sustained hardware FPS or other games.',
        'Cycle and partition measurements are model results. No FPGA fit/timing, Pocket deployment, or full seed sweep.',
        'Queue unit tests extract the actual RTL block; cache tests use the actual cache with an independent memory oracle. Full p3 tag wiring is covered by native replay and source review.',
        'AXI wait counters overlap and cannot be added or treated as independently removable frame time.'
    ], inputs_sha256=inputs)
assert not (D/'summary.json').exists()
(D/'summary.json').write_text(json.dumps(out,indent=2)+'\n')
print(json.dumps({label:dict(intervals=r['intervals'], cycle_change_percent=r['cycle_change_percent'])
                  for label,r in runs.items()},indent=2))
