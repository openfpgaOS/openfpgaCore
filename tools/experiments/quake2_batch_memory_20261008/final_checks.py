#!/usr/bin/env python3
"""Check final frame semantics and conditional producer/GPU replay evidence."""
from pathlib import Path
import csv
import hashlib
import json

R = Path(__file__).resolve().parents[3]
P = R / 'docs/measurements/quake2-batch-memory-20261008'
S = R / 'docs/measurements/quake2-optimize-20261008/demo-screen-v2'
T = P / 'timed-replay-v3'
D = P / 'final-checks-v1'
BASE = 'thorough_combined6'
FINAL = 'batchmem_combined'

def sha(p):
    with p.open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()

def read(p):
    return json.loads(p.read_text())

evidence = {}
def bind(p):
    evidence[str(p.relative_to(R))] = sha(p)

assert not D.exists()
rows = {}
for label in [BASE, FINAL]:
    path = S / label / 'frames.csv'
    rows[label] = list(csv.DictReader(path.open()))
    bind(path)
fields = ['frame', 'host_framecount', 'buffer', 'gpu_cmd_words', 'gpu_pixels',
          'hash_cmd', 'hash_px', 'hash_z', 'hash_pal', 'mem_hazards',
          'gpu_barriers', 'gpu_wait_px']
assert len(rows[BASE]) == len(rows[FINAL]) == 362
for a, b in zip(rows[BASE], rows[FINAL]):
    for key in fields:
        assert a[key] == b[key], (key, a['frame'], a[key], b[key])

canonical_path = R / 'docs/measurements/quake2-comprehensive-20261008/thorough_combined6-vs-pass2_trio-gpu-v1/result.json'
canonical = {x['frame']: x for x in read(canonical_path)['sampled_rtl']}
bind(canonical_path)
samples = []
for frame in [20, 80, 180, 300]:
    inputs = {}
    results = {}
    for label in [BASE, 'batchmem_clear_kick', FINAL]:
        folder = T / label
        path = folder / f'frame_{frame:05}.inputs.json'
        inputs[label] = read(path)
        bind(path)
        bind(folder / f'frame_{frame:05}.vec')
        bind(folder / f'frame_{frame:05}.schedule')
        for scale in [0, 1.0, 0.5]:
            stem = f'frame_{frame:05}-scale-{str(scale).replace(".", "p")}'
            result_path = folder / (stem + '.json')
            value = read(result_path)
            bind(result_path)
            data_path = folder / (stem + '.bin')
            data = data_path.read_bytes()
            bind(data_path)
            bind(folder / (stem + '.log'))
            assert value['passed'] and sha(data_path) == value['output_sha256']
            assert inputs[label]['zstride'] == 640 and inputs[label]['zstep'] == 2
            assert len(data) == 307204
            depth, color = data[:153600], data[153600:307200]
            assert hashlib.sha256(depth).hexdigest() == canonical[frame]['depth_sha256']
            assert hashlib.sha256(color).hexdigest() == canonical[frame]['color_sha256']
            stats = value['stats']
            if scale:
                assert stats['delay'] == stats['fence_wait'] + stats['transport_late']
                assert stats['cycles'] == int(inputs[label]['cpu_frame_model_cycles'] * scale) + stats['delay'] + 128
            results[label, scale] = value
    for label in ['batchmem_clear_kick', FINAL]:
        assert inputs[label]['flattened_commands'] == inputs[BASE]['flattened_commands']
        for key in ['zaddr', 'zstride', 'zstep', 'framebuffer']:
            assert inputs[label][key] == inputs[BASE][key]
        assert results[label, 0]['stats']['cycles'] == results[BASE, 0]['stats']['cycles']
        for scale in [0, 1.0, 0.5]:
            assert results[label, scale]['output_sha256'] == results[BASE, scale]['output_sha256']
        for scale in [1.0, 0.5]:
            a, b = results[BASE, scale]['stats'], results[label, scale]['stats']
            samples.append(dict(frame=frame, candidate=label, producer_scale=scale,
                cycles_before=a['cycles'], cycles_after=b['cycles'],
                conditional_cycle_reduction_percent=100 * (1 - b['cycles'] / a['cycles']),
                fence_wait_before=a['fence_wait'], fence_wait_after=b['fence_wait'],
                fence_wait_saved=a['fence_wait'] - b['fence_wait']))

for label in [BASE, 'batchmem_clear_kick', FINAL]:
    for path in [T / label / 'result.json', P / 'publish-trace-v1' / (label + '-result.json')]:
        assert read(path)['passed']
        bind(path)
for path in [T / 'prepared.json', T / 'replay.cpp', T / 'timed_gpu', Path(__file__)]:
    bind(path)
D.mkdir()
result = dict(passed=True, baseline=BASE, candidate=FINAL, full_demo_frames_equal=362,
              exact_frame_fields=fields, full_rtl_color_depth_outputs_checked=36,
              all_outputs_match_previous_qualified_rtl=True,
              saturated_gpu_work_unchanged=True, sampled_conditional_timing=samples,
              limits=read(T / 'prepared.json')['limits'])
(D / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
bind(D / 'result.json')
(D / 'manifest.json').write_text(json.dumps(dict(passed=True, evidence_sha256=evidence), indent=2) + '\n')
print(json.dumps(result, indent=2))
