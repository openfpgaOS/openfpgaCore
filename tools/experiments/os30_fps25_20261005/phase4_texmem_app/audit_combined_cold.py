#!/usr/bin/env python3
"""Independently check completed combined active-route captures/replays; no rerun."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import struct

ROOT = Path(__file__).resolve().parents[4]
CAMP = ROOT / 'docs/measurements/os30-fps25-20261005'
PHASE = CAMP / 'phase4'
parser = argparse.ArgumentParser()
parser.add_argument('--capture-root', type=Path, default=PHASE / 'texmem-capture-v2')
parser.add_argument('--replay-root', type=Path, default=PHASE / 'combined-cold/active')
parser.add_argument('--output', type=Path, default=PHASE / 'combined-cold-active-independent-review.json')
args = parser.parse_args()

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def read(path):
    return json.loads(path.read_text())

def inspect(data):
    pos, depth, fence, uploads, routes, snapshots = 0, set(), None, 0, [], []
    while pos < len(data):
        tag = data[pos:pos + 4]
        pos += 4
        if tag in (b'MEM1', b'TEX1'):
            address, count = struct.unpack_from('<II', data, pos)
            pos += 8 + ((count + 3) & ~3)
            if tag == b'TEX1':
                assert address % 4 == count % 4 == 0
                assert address + count <= 0x200000
                snapshots.append(count)
        elif tag == b'CMDS':
            n, = struct.unpack_from('<I', data, pos)
            pos += 4
            end = pos + 4 * n
            while pos < end:
                header, = struct.unpack_from('<I', data, pos)
                op, nwords = header >> 24, header & 8191
                words = struct.unpack_from(f'<{nwords}I', data, pos + 4)
                if op == 0x4a:
                    assert nwords == 17
                    if words[11]:
                        depth.add(words[11])
                elif op == 0x42:
                    fence = words[1]
                elif op == 0x02:
                    fence = words[0]
                pos += 4 * (nwords + 1)
            assert pos == end
        elif tag == b'MMIO':
            off, value = struct.unpack_from('<II', data, pos)
            pos += 8
            assert off in (0x08, 0x38, 0x3c)
            if off == 0x38:
                routes.append(value)
            uploads += off == 0x3c
        elif tag == b'TLUT':
            pos += 32768
        elif tag == b'PALB':
            pos += 4
        elif tag == b'DUMP':
            pos += 8
        elif tag == b'RAND':
            pos += 12
        else:
            assert tag in (b'FLSH', b'FENC', b'HASH', b'END.')
            if tag == b'END.':
                assert pos == len(data)
        assert pos <= len(data)
    assert len(depth) <= 1 and len(snapshots) == 1 and routes == [1]
    assert fence is not None
    return depth, fence, uploads, snapshots[0]

rows, pins = [], {}
for mode in ('normal', 'fps'):
    capture = args.capture_root / ('capture-combined-cold-' + mode)
    inputs = read(capture / 'inputs.json')
    assert inputs['success']
    for relative, expected in inputs['results_sha256'].items():
        assert sha(capture / relative) == expected
    report_path = args.replay_root / f'replay-{mode}.json'
    report = read(report_path)
    assert len(report) == 16
    assert {(x['traffic'], x['frame']) for x in report} == {(t, f) for t in (1, 2) for f in (40, 120, 200, 360, 400, 500, 600, 720)}
    pins[mode] = dict(elf_sha256=sha(capture / 'sm64.elf'), capture_inputs_sha256=sha(capture / 'inputs.json'), replay_report_sha256=sha(report_path))
    for row in report:
        stem = f"frame_{row['frame']:05d}"
        original = capture / 'gpuvec' / (stem + '.vec')
        current = args.replay_root / 'replays' / mode / str(row['traffic']) / stem
        baseline = CAMP / 'validation/replays-single-A' / ('phase2-exact-v1-' + mode) / str(row['traffic']) / (stem + '.bin')
        assert sha(original) == row['vector_sha256']
        assert sha(current.with_suffix('.vec')) == row['adapted_vector_sha256']
        assert sha(current.with_suffix('.bin')) == row['output_sha256']
        assert sha(current.with_suffix('.log')) == row['log_sha256']
        assert sha(baseline) == row['baseline_output_sha256']
        assert sha(Path(row['command'][0])) == row['simulator_sha256']
        assert '+tagged_tex=1' in row['command']
        assert f"+memory_traffic={row['traffic']}" in row['command']
        source = original.read_bytes()
        depth, fence, uploads, resident = inspect(source)
        extra = b'DUMP' + struct.pack('<II', next(iter(depth)), 153600) if depth else b''
        assert current.with_suffix('.vec').read_bytes() == source[:-4] + extra + b'END.'
        data, reference = current.with_suffix('.bin').read_bytes(), baseline.read_bytes()
        assert len(data) == len(reference) == 153612 + (153600 if depth else 0)
        assert data[:153600] == reference[:153600]
        assert data[153612:] == reference[153612:]
        assert struct.unpack_from('<I', data, 153600)[0] == fence
        stats = {k: int(v) for k, v in re.findall(r'(\w+)=(\d+)', current.with_suffix('.log').read_text())}
        assert stats == row['stats']
        assert stats['cram_errors'] == 0 and stats['words'] == uploads
        assert stats['readback_words'] == uploads
        before = row['baseline_stats']['cycles']
        assert stats['cycles'] < before
        rows.append(dict(mode=mode, traffic=row['traffic'], frame=row['frame'], color_depth_exact=True,
                         expected_candidate_fence=fence, fence_exact=True, live_upload_words=uploads,
                         resident_bytes=resident, gpu_cycles=stats['cycles'], baseline_gpu_cycles=before,
                         gpu_cycle_saving_percent=100 * (1 - stats['cycles'] / before)))

result = dict(passed=True, pins=pins, independent_script_sha256=sha(Path(__file__)), comparisons=len(rows),
              all_active_route=True, all_color_depth_exact=True, all_candidate_fences_exact=True,
              all_upload_readbacks_exact=True, rows=rows,
              limits=['SDRAM hashes intentionally differ with changed ELF/source/address layout; color/depth are the cross-build equality gate.',
                      'This does not establish actual CPU polling time or CPU/GPU producer overlap.',
                      'Current combined CPU model has a small regression; GPU gains do not establish net device FPS.',
                      'No rendering beyond the eight captured frames or die1/boundary memory qualification is claimed.'])
args.output.write_text(json.dumps(result, indent=2) + '\n')
print(json.dumps({'passed': True, 'comparisons': len(rows), 'minimum_gpu_gain_percent': min(x['gpu_cycle_saving_percent'] for x in rows), 'maximum_gpu_gain_percent': max(x['gpu_cycle_saving_percent'] for x in rows)}))
