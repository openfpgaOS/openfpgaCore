#!/usr/bin/env python3
"""Validate the frozen baseline/mesh/cache windows and report full frame cost."""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import struct

ROOT = Path(__file__).resolve().parents[4]
OUT = ROOT / 'docs/measurements/os30-mesh-20261004'
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
rows = lambda p: list(csv.DictReader(p.open()))

def analyze(label):
    path = OUT / ('coupled-run-' + label)
    manifest = json.loads((path / 'inputs.json').read_text())
    assert manifest['success']
    for name, digest in manifest['sha256'].items():
        assert sha(Path(name)) == digest, name
    status = json.loads((path / 'coupled.json').read_text())
    assert status['frames'] == status['presented'] == manifest['frames']
    assert status['memory_errors'] == 0 and not status['ring_overflow'] and not status.get('mesh_fault')
    data = rows(path / 'counters.csv')
    category = ['input_starved', 'output_blocked', 'vertex_math', 'cull_math', 'worker_other']
    if not manifest['baseline']:
        category[2:4] = ['engine_unblocked', 'unused']
    for row in data:
        assert sum(int(row['all_' + k]) for k in category) == int(row['all_total'])
        assert int(row['max_upload']) <= 4095
        assert int(row['max_output']) <= (511 if manifest['baseline'] else 1024)
    submit = [r for r in data if r['kind'] == 'submit']
    assert len(submit) == manifest['frames']
    delta = {k: int(submit[-1][k]) - int(submit[0][k]) for k in submit[0] if k != 'kind'}
    divisor = (manifest['frames'] - 1) * 100000
    result = dict(label=label, start=manifest['start'], frames=manifest['frames'],
                  traffic=manifest['traffic'], cpu_data_cache_kib=manifest['cpu_data_cache_kib'],
                  mean_submit_interval_ms=delta['cycle'] / divisor,
                  uploaded_words_per_frame=delta['uploaded_words'] / (manifest['frames'] - 1),
                  counter_ms_per_frame={k[4:]: v / divisor for k, v in delta.items()
                                        if k.startswith('all_') and not k.endswith('_sum')},
                  max_upload=int(submit[-1]['max_upload']), max_output=int(submit[-1]['max_output']),
                  validation=status)
    return path, result

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--baseline', default='baseline-p4-360-t1')
    p.add_argument('--candidate', default='mesh-v3-360-t1')
    p.add_argument('--cache64', default='mesh-v3-dc64-360-t1')
    p.add_argument('--out', type=Path, default=OUT / 'coupled-comparison-v1.json')
    a = p.parse_args()
    bp, baseline = analyze(a.baseline)
    cp, candidate = analyze(a.candidate)
    dp, cache = analyze(a.cache64)
    assert rows(bp / 'scene.csv') == rows(cp / 'scene.csv') == rows(dp / 'scene.csv')
    frame_results = []
    for frame in range(candidate['start'], candidate['start'] + candidate['frames']):
        before = (bp / f'frame_{frame:05d}.bin').read_bytes()
        after = (cp / f'frame_{frame:05d}.bin').read_bytes()
        cache_after = (dp / f'frame_{frame:05d}.bin').read_bytes()
        assert len(before) == len(after) == 307200 and after == cache_after
        old_color = struct.unpack_from('<76800H', before)
        new_color = struct.unpack_from('<76800H', after)
        changed = sum(x != y for x, y in zip(old_color, new_color))
        row = dict(frame=frame, changed_color_pixels=changed, color_pixels=76800,
                   changed_depth_pixels=sum(x != y for x, y in zip(
                       struct.unpack_from('<76800H', before, 153600),
                       struct.unpack_from('<76800H', after, 153600))),
                   candidate_sha256=hashlib.sha256(after).hexdigest(), cache64_byte_identical=True)
        replay = OUT / 'runtime-gpu-v3' / f'frame-{frame}'
        if replay.exists():
            info = json.loads((replay / 'inputs.json').read_text())
            output = (replay / 'mesh.bin').read_bytes()
            color = next(v for v in info['output_ranges'] if v['kind'] == 'dump')
            depth = next(v for v in info['output_ranges'] if v['kind'] == 'depth_dump')
            replay_frame = output[color['offset']:color['offset'] + color['length']]
            replay_frame += output[depth['offset']:depth['offset'] + depth['length']]
            row['matches_functional_capture_gpu_replay'] = replay_frame == after
        frame_results.append(row)
    report = dict(scope=__doc__, baseline=baseline, candidate=candidate, cache64=cache,
                  scene_trace_identical=True, frames=frame_results,
                  throughput_gain_percent=100 * (baseline['mean_submit_interval_ms'] / candidate['mean_submit_interval_ms'] - 1),
                  cache64_frame_time_change_percent=100 * (cache['mean_submit_interval_ms'] / candidate['mean_submit_interval_ms'] - 1),
                  limitations=[
                      'CPU instruction/cache costs are estimated; CPU memory transactions, PCM and interrupts are not RTL.',
                      'GPU mesh, raster, DMA and physical SDRAM run in RTL; unrelated memory traffic is a synthetic schedule.',
                      'Identical fixed game-step scene trace, not device FPS. Current mesh image is not byte-identical to production.',
                      'Comparison uses frozen v3 app and pre-OUTBLOCK/SCOPY/projection optimizations; no deployment qualification.'])
    a.out.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({k: report[k] for k in ['throughput_gain_percent', 'cache64_frame_time_change_percent', 'frames']}, indent=2))

if __name__ == '__main__':
    main()
