#!/usr/bin/env python3
"""Bind complete command-removal proof to meaningful selected-GPU samples."""
import argparse
import importlib.util
import json
from pathlib import Path
import re
import struct
import clear_trace as ct

R, P, S = ct.R, ct.P, ct.S
path = R / 'tools/experiments/quake2_optimize_pass2_20261008/compare_demo.py'
spec = importlib.util.spec_from_file_location('immutable_compare', path)
old = importlib.util.module_from_spec(spec)
spec.loader.exec_module(old)

def commands(records):
    words = struct.unpack('<' + 'I'*(len(old.command_bytes(records))//4), old.command_bytes(records))
    result = []
    pos = 0
    while pos < len(words):
        count = words[pos] & 8191
        assert pos+1+count <= len(words)
        result.append((0, tuple(words[pos:pos+1+count])))
        pos += 1+count
    return result

def compare(candidate, baseline):
    evidence = set(old.PINS)
    for source, expected in old.PINS.items():
        assert old.sha(source) == expected
    raw, metrics = {}, {}
    for label in (baseline, candidate):
        raw[label], metrics[label] = old.software(label, evidence)
    for field in ('frame', 'host_framecount', 'buffer', 'hash_px', 'hash_pal'):
        assert [row[field] for row in raw[baseline]] == [row[field] for row in raw[candidate]], field
    trace_dir = ct.O / (candidate + '-vs-' + baseline)
    proof = json.loads((trace_dir / 'result.json').read_text())
    assert proof['passed'] and proof['normal_frame_count_hash_bindings'] == 362
    trace_manifest = json.loads((trace_dir / 'manifest.json').read_text())
    for rel, expected in trace_manifest['evidence_sha256'].items():
        assert ct.sha(R / rel) == expected, rel
    samples = []
    for frame in old.FRAMES:
        a, _ = old.replay(baseline, frame, evidence)
        b, _ = old.replay(candidate, frame, evidence)
        for field in ('color_sha256', 'depth_sha256', 'rendered_depth_nonzero_pixels'):
            assert a[field] == b[field], (frame, field)
        records = {label: old.records(S / label / 'gpuvec' / f'frame_{frame:05}.vec') for label in (baseline,candidate)}
        for tag in (b'MMIO', b'TEX1', b'TLUT', b'PALB', b'DUMP', b'FENC', b'HASH'):
            assert [data for t,data in records[baseline] if t==tag] == [data for t,data in records[candidate] if t==tag], (frame,tag)
        removed = ct.prove(commands(records[baseline]), commands(records[candidate]))
        assert removed
        cycles = {}
        clear_cycles = {}
        for label in (baseline,candidate):
            log = (old.GPU / label / f'frame_{frame:05}.log').read_text()
            cycles[label] = int(re.search(r'^RESULT cycles=(\d+)', log, re.M).group(1))
            clear_cycles[label] = int(re.search(r'^BN state 8 (\d+)', log, re.M).group(1))
        samples.append(dict(frame=frame, color_sha256=a['color_sha256'], depth_sha256=a['depth_sha256'],
            rendered_depth_nonzero_pixels=a['rendered_depth_nonzero_pixels'], depth_clear_retained=True,
            removed_clears=len(removed), other_commands_exact=True, texture_mmio_exact=True,
            rtl_cycles=cycles, clear_word_state_cycles=clear_cycles,
            rtl_cycles_saved=cycles[baseline]-cycles[candidate],
            rtl_percent_saved=100*(cycles[baseline]-cycles[candidate])/cycles[baseline]))
    total_base = sum(s['rtl_cycles'][baseline] for s in samples)
    saved = sum(s['rtl_cycles_saved'] for s in samples)
    cpu_base = metrics[baseline]['est_cycles']['mean']
    cpu_saved = cpu_base - metrics[candidate]['est_cycles']['mean']
    result = dict(passed=True, rendering_equivalent=True, baseline=baseline,candidate=candidate,
        software_frame_parity=362, complete_command_proof=proof['removed_clear_count'],
        sampled_rtl=samples, sum_rtl_cycles_saved=saved, sampled_rtl_percent_saved=100*saved/total_base,
        modeled_cpu_percent_saved=100*cpu_saved/cpu_base, metrics=metrics,
        limits=['Modeled CPU and saturated GPU replay measure separate engines, not device FPS.',
                'Replay includes initial/next-frame clears and a depth-snapshot drain; it is not an exact full-game GPU timeline.',
                'qsim hash_z also hashes RGB565 color clears through a fallback size heuristic; actual RTL pre-clear depth is the meaningful depth check.',
                'Host lifecycle tests cover CPU writes and context changes; the complete command trace proves no other GPU command was changed or removed.'])
    dest = P / (candidate + '-vs-' + baseline + '-gpu-v1')
    dest.mkdir(exist_ok=False)
    ct.put(dest / 'result.json', result)
    evidence.update((Path(__file__).resolve(), path, trace_dir / 'manifest.json', trace_dir / 'result.json',
                     ct.O / (candidate+'-result.json'), ct.O / (baseline+'-result.json'), dest / 'result.json'))
    ct.put(dest / 'manifest.json', dict(passed=True, evidence_sha256={str(p.relative_to(R)):ct.sha(p) for p in sorted(evidence)}))
    print(json.dumps(dict(output=str(dest), passed=True, sampled_rtl_percent_saved=result['sampled_rtl_percent_saved'],
                         modeled_cpu_percent_saved=result['modeled_cpu_percent_saved']), indent=2))

if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('candidate')
    parser.add_argument('--baseline',default='pass2_trio')
    args=parser.parse_args()
    compare(args.candidate,args.baseline)
