#!/usr/bin/env python3
"""Seal measured priority/snapshot conclusions without estimated cycle subtraction."""
import hashlib
import json
from pathlib import Path

B = Path(__file__).resolve().parents[1]
paths = [Path(__file__).resolve()]

def read(name):
    path = B/name
    paths.append(path)
    return json.loads(path.read_text())

priority = read('analysis/native-v1.json')
snapshot = read('analysis/native-v2-f23.json')
final = read('analysis/native-v2.json')
phases = read('analysis/phases-v2.json')
strict = {}
for label, suffix in [('control-v1', 'v1'), ('candidate-v1', 'v1'),
                      ('control-v2', 'v2'), ('candidate-v2', 'v2'), ('candidate-v2-f23-a2', 'v2')]:
    d = read(f'validation/{label}/check-independent-{suffix}.json')
    assert d['status'].startswith('PASS'), label
    strict[label] = d
negative = read('validation/lifetime-negative-v2/result.json')
assert negative['status'] == 'PASS_ELEVEN_CORRUPTIONS_REJECTED'
assert negative['measured_inputs_unchanged']
assert len(negative['cases']) == 11
read('validation/lifetime-negative-v1/result.json')
read('validation/wait-coverage-v1/result.json')
read('validation/wait-coverage-v2/result.json')
read('work-core-v2/rtl-ready-v1.json')
read('work-app-v2/evidence/app-build-v2/build-result.json')
read('physical/review-v2.json')
read('analysis/app-review-v2.json')

assert priority['control']['cycles'] == snapshot['control']['cycles']
before = priority['overlap']['2']['lease_pending']
after = snapshot['overlap']['2']['lease_pending']
life = strict['candidate-v2']['async_lifetime']
result = dict(
    status='CORRECT_CPU_OUTPUT_WAIT_REMOVED_BUT_NO_NET_SPEEDUP',
    selected_native='native-v2', selected_app='F24 PRI2',
    same_F23_hardware_comparison=dict(
        priority_cycles=priority['candidate']['cycles'], snapshot_cycles=snapshot['candidate']['cycles'],
        warm_lease_wait_before=before, warm_lease_wait_after=after,
        warm_lease_wait_reduction_percent=100*(1-after/before),
        warm_whole_interval_reduction_percent=100*(1-snapshot['candidate']['cycles'][1]/priority['candidate']['cycles'][1])),
    final_same_F24_comparison=dict(
        candidate_cycles=final['candidate']['cycles'], control_cycles=final['control']['cycles'],
        combined_cycle_change_percent=final['cycle_change_percent'],
        warm_cycle_change_percent=phases['phases'][1]['cycle_change_percent'],
        warm_lease_wait_cycles=final['overlap']['2']['lease_pending'],
        warm_raw_transform_cycles=final['overlap']['2']['raw_transform'],
        warm_output_full_cycles=final['overlap']['2']['output_full']),
    actual_overlap_proof=dict(
        early_snapshot_grants=len(life['early_snapshot_grants']),
        concurrent_legacy_raw_starts=len(life['legacy_raw_starts_during_old_output']),
        snapshot_jobs=life['snapshot_jobs'], physical_snapshot_writes=life['physical_snapshot_writes'],
        original_LOAD_calls=life['original_LOAD_calls'], exact_raw_contexts=life['exact_raw_contexts'],
        negative_witness_cases=len(negative['cases'])),
    decision='Preserve isolated source/evidence experiment. Do not fit or deploy this measured regression.',
    physical_fit_run=False, seed_sweep_run=False, deployed=False, production_modified=False,
    limits=[
        'Two bounded native intervals with synthetic scan/audio and bounded guest services; not sustained Pocket FPS.',
        'One captured scene; no all-level, game or Quake2 performance claim.',
        'Warm wait reduction compares identical F23 ELF and fixture. Final performance compares identical F24 ELF and fixture.',
        'Snapshot storage reuses the AJ02 survivor block; overall candidate ALM/BRAM/timing have not been physically fitted.',
        'Cold clipping/numeric repair still blocks the CPU. This change targets ordinary packet-output ownership.',
        'Function gap buckets are diagnostic and overlapping state counters cannot be summed into saved cycles.'])
result['inputs_sha256'] = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
out = B/'analysis/conclusion-v1.json'
assert not out.exists()
out.write_text(json.dumps(result, indent=2)+'\n')
print(json.dumps({k: result[k] for k in ['status', 'same_F23_hardware_comparison', 'final_same_F24_comparison', 'actual_overlap_proof']}, indent=2))
