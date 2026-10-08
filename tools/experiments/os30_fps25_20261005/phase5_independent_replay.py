#!/usr/bin/env python3
"""Read-only F7 capture/replay audit; writes only the independent review JSON."""
import csv
import hashlib
import json
from pathlib import Path
import re
import struct

ROOT = Path(__file__).resolve().parents[3]
CAMP = ROOT / 'docs/measurements/os30-fps25-20261005'
OUT = CAMP / 'phase5'
OLD = CAMP / 'phase4'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text())


def rows(path):
    with path.open() as f:
        return list(csv.DictReader(f))


def capture(directory, elf_digest, panel=False):
    inputs = read(directory / 'inputs.json')
    assert inputs['success'] and inputs['panel'] == panel
    assert inputs['audio'] and not inputs['geometry_capability'] and not inputs['command_dma']
    assert inputs['hw_features'] == '0x2b136753'
    assert inputs['command'][inputs['command'].index('--gpu-sched') + 1] == 'eager'
    for path, digest in inputs['sha256'].items():
        assert sha(Path(path)) == digest, path
    for path, digest in inputs['results_sha256'].items():
        assert sha(directory / path) == digest, (directory, path)
    assert sha(directory / 'sm64.elf') == elf_digest
    frames = rows(directory / 'frames.csv')
    assert len(frames) == 721
    return inputs, frames


def vector(data):
    pos, depth, fence, uploads = 0, set(), None, 0
    snapshots, routes, hardware, palettes, texture = [], [], [], [], []
    while pos < len(data):
        start = pos
        tag = data[pos:pos + 4]
        pos += 4
        if tag in (b'MEM1', b'TEX1'):
            addr, n = struct.unpack_from('<II', data, pos)
            pos += 8 + ((n + 3) & ~3)
            if tag == b'TEX1':
                assert addr % 4 == n % 4 == 0 and addr + n <= 0x200000
                snapshots.append((addr, n))
                texture.append(data[start + 12:pos])
        elif tag == b'CMDS':
            n, = struct.unpack_from('<I', data, pos)
            pos += 4
            end = pos + 4 * n
            while pos < end:
                header, = struct.unpack_from('<I', data, pos)
                op, count = header >> 24, header & 8191
                words = struct.unpack_from(f'<{count}I', data, pos + 4)
                if op == 0x4a:
                    assert count == 17
                    if words[11]:
                        depth.add(words[11])
                elif op == 0x42:
                    fence = words[1]
                elif op == 0x02:
                    fence = words[0]
                pos += 4 * (count + 1)
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
            palettes.append(struct.unpack_from('<I', data, pos)[0])
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
        if tag != b'MEM1':
            # The diagnostic changes SDRAM placement of the palette. Preserve
            # the PALB record/order, and validate the actual output separately.
            hardware.append(tag if tag == b'PALB' else data[start:start + 12] if tag == b'TEX1' else data[start:pos])
    assert len(depth) == 1 and fence is not None
    assert len(snapshots) == 1 and routes == [1]
    return dict(depth=next(iter(depth)), fence=fence, uploads=uploads,
                resident_bytes=snapshots[0][1], hardware=hardware, palettes=palettes,
                texture=texture[0])


baseline = read(OUT / 'baseline.json')
normal = OUT / 'candidate/app.elf'
diagnostic = OUT / 'candidate/diagnostic.elf'
assert sha(normal) == baseline['normal_elf_sha256'] == sha(CAMP / baseline['parent'] / 'app.elf')
for mode, elf in (('normal', normal), ('diagnostic', diagnostic)):
    build = read(OUT / f'candidate/build-{mode}.json')
    assert build['returncode'] == 0 and build['elf_sha256'] == sha(elf)
    for entry in build['inputs'].values():
        assert sha(Path(entry['source'])) == entry['sha256']

manifest = read(OUT / 'qsim-manifest.json')
assert sha(OUT / 'qsim/qsim') == manifest['simulator_sha256']
assert sha(OLD / 'texmem-capture-v2/manifest.json') == manifest['parent_manifest_sha256']
changed_simulator_sources = []
for name, digest in manifest['sources'].items():
    assert sha(OUT / 'qsim' / name) == digest
    if sha(OLD / 'texmem-capture-v2/qsim' / name) != digest:
        changed_simulator_sources.append(name)
assert changed_simulator_sources == ['main.c']

parent = OLD / 'texmem-capture-v2/capture-combined-cold-fps'
control, current, panel = (OUT / n for n in ('capture-f6-control', 'capture-f7', 'capture-f7-panel'))
capture(parent, baseline['diagnostic_elf_sha256'])
_, control_frames = capture(control, baseline['diagnostic_elf_sha256'])
_, current_frames = capture(current, sha(diagnostic))
capture(panel, sha(diagnostic), panel=True)
names = ['frames.csv', 'scene.csv', 'geometry.csv', 'diagnostic.csv', 'culling.csv']
names += sorted(p.name for p in parent.glob('profile*.tsv'))
names += sorted(str(p.relative_to(parent)) for p in (parent / 'gpuvec').glob('*.vec'))
for name in names:
    assert sha(parent / name) == sha(control / name), name
for name in ('scene.csv', 'geometry.csv', 'culling.csv'):
    assert sha(control / name) == sha(current / name) == sha(panel / name), name
assert [r['gpu_cmd_words'] for r in control_frames] == [r['gpu_cmd_words'] for r in current_frames]

snapshot_checks = {}
for directory in (current, panel):
    snapshots = rows(directory / 'diagnostic.csv')
    assert len(snapshots) == 721 and len(snapshots[0]) == 49
    valid = 0
    for row in snapshots:
        s = [int(row[f'stat{i}']) for i in range(48)]
        if not s[0]:
            continue
        valid += 1
        assert s[18] == 7 and s[19:39] == [0] * 20
        assert 0 <= s[10] - sum(s[39:43]) <= 3
        assert s[43] <= (s[3] + 15) // 16 and s[46] == 16
        assert s[44] + s[45] <= s[47]
        if s[43] == 0:
            assert s[44] == s[45] == s[47] == 0
    assert valid
    snapshot_checks[directory.name] = dict(valid_rows=valid, partition_and_sample_invariants=True)
    if directory == panel:
        # The fixture toggles the panel at 600 and 680; compare every snapshot
        # word while displayed, excluding the observer's frame sequence column.
        values = [[row[f'stat{i}'] for i in range(48)] for row in snapshots]
        assert all(v == values[600] for v in values[600:680])
        snapshot_checks[directory.name]['all_48_words_frozen_frames600_679'] = True

report = read(OUT / 'replay-fps.json')
old_report_path = OLD / 'combined-cold/active/replay-fps.json'
old_report = {(x['traffic'], x['frame']): x for x in read(old_report_path)}
assert len(report) == 16 and {(r['traffic'], r['frame']) for r in report} == set(old_report)
results = []
for row in report:
    traffic, frame = row['traffic'], row['frame']
    stem = f'frame_{frame:05d}'
    source = current / 'gpuvec' / (stem + '.vec')
    target = OUT / 'replays/fps' / str(traffic) / stem
    old = OLD / 'combined-cold/active/replays/fps' / str(traffic) / stem
    prior = old_report[traffic, frame]
    for path, digest in ((source, row['vector_sha256']),
                         (target.with_suffix('.vec'), row['adapted_vector_sha256']),
                         (target.with_suffix('.bin'), row['output_sha256']),
                         (target.with_suffix('.log'), row['log_sha256']),
                         (old.with_suffix('.bin'), prior['output_sha256']),
                         (old.with_suffix('.log'), prior['log_sha256']),
                         (Path(row['command'][0]), row['simulator_sha256'])):
        assert sha(path) == digest, path
    assert row['simulator_sha256'] == prior['simulator_sha256']
    assert '+tagged_tex=1' in row['command'] and f'+memory_traffic={traffic}' in row['command']
    info = vector(source.read_bytes())
    old_source = parent / 'gpuvec' / (stem + '.vec')
    assert sha(old_source) == prior['vector_sha256']
    old_info = vector(old_source.read_bytes())
    ignored = ('palettes', 'texture')
    assert {k: v for k, v in info.items() if k not in ignored} == {k: v for k, v in old_info.items() if k not in ignored}, (traffic, frame, 'GPU commands, texture extents, MMIO or ordering changed')
    assert len(info['palettes']) == len(old_info['palettes']) == 1
    texture_changes = [dict(offset=i, f7=struct.unpack_from('<I', info['texture'], i)[0],
                            f6=struct.unpack_from('<I', old_info['texture'], i)[0])
                       for i in range(0, len(info['texture']), 4)
                       if info['texture'][i:i+4] != old_info['texture'][i:i+4]]
    expected_texture_changes = [] if frame < 360 else [
        dict(offset=140188, f7=0x78d4, f6=0),
        dict(offset=140236, f7=0xa8d4, f6=0),
        dict(offset=140260, f7=0xa8dc, f6=0)]
    assert texture_changes == expected_texture_changes
    expected = source.read_bytes()[:-4] + b'DUMP' + struct.pack('<II', info['depth'], 153600) + b'END.'
    assert target.with_suffix('.vec').read_bytes() == expected
    data, reference = target.with_suffix('.bin').read_bytes(), old.with_suffix('.bin').read_bytes()
    assert len(data) == len(reference) == 307212
    assert data[:153600] == reference[:153600]
    assert data[153612:] == reference[153612:]
    assert struct.unpack_from('<I', data, 153600)[0] == info['fence']
    stats = {k: int(v) for k, v in re.findall(r'(\w+)=(\d+)', target.with_suffix('.log').read_text())}
    old_stats = {k: int(v) for k, v in re.findall(r'(\w+)=(\d+)', old.with_suffix('.log').read_text())}
    assert stats == row['stats'] == old_stats == prior['stats']
    assert stats['cram_errors'] == 0
    assert stats['words'] == stats['readback_words'] == info['uploads']
    results.append(dict(traffic=traffic, frame=frame, color_depth_exact=True,
                        commands_mmio_order_and_texture_extents_exact_to_f6=True,
                        texture_snapshot_differing_words=texture_changes,
                        palette_base=info['palettes'][0], f6_palette_base=old_info['palettes'][0],
                        all_simulator_statistics_exact_to_f6=True,
                        expected_candidate_fence=info['fence'], fence_exact=True,
                        live_upload_words=info['uploads'], resident_bytes=info['resident_bytes'],
                        gpu_cycles=stats['cycles'], output_sha256=sha(target.with_suffix('.bin')),
                        f6_output_sha256=sha(old.with_suffix('.bin'))))

totals = {}
for key in ('insns', 'est_cycles', 'gpu_cmd_words'):
    a, b = (sum(int(r[key]) for r in frame_rows[360:]) for frame_rows in (control_frames, current_frames))
    totals[key] = dict(f6=a, f7=b, percent_change=(b / a - 1) * 100)

pin_paths = ['baseline.json', 'candidate/build-normal.json', 'candidate/build-diagnostic.json',
             'qsim-manifest.json', 'capture-f6-control/inputs.json', 'capture-f7/inputs.json',
             'capture-f7-panel/inputs.json', 'analysis.json', 'replay-fps.json',
             'wait-tests-v2/results.json', 'perf-tests.json', 'independent-source-review-v2.json']
result = dict(passed=True, comparisons=len(results), normal_whole_elf_exact_to_f6=True,
              normal_elf_sha256=sha(normal), diagnostic_elf_sha256=sha(diagnostic),
              independent_script_sha256=sha(Path(__file__)),
              pins={name: sha(OUT / name) for name in pin_paths},
              evidence_sha256={str((OUT / name).relative_to(ROOT)): sha(OUT / name) for name in pin_paths},
              f6_replay_report_sha256=sha(old_report_path),
              control_observer_noninterference=dict(passed=True, exact_files=len(names), only_changed_source='main.c'),
              gameplay_geometry_culling_exact=True, gpu_word_counts_exact_all_721_frames=True,
              snapshot_checks=snapshot_checks, gameplay_frames360_through720=totals,
              all_active_route=True, all_color_depth_exact=True, all_candidate_fences_exact=True,
              all_upload_readbacks_exact=True, all_gpu_statistics_exact_to_f6=True, rows=results,
              limits=[
                  'Read-only audit of existing outputs; no reruns, builds, fits, source changes or publication.',
                  'Eager qsim does not exercise hardware backpressure. Diagnostic CPU overhead is modeled, not measured Pocket FPS.',
                  'Actual retained-A GPU replay uses synthetic traffic, not the actual CPU producer or live CPU/GPU overlap.',
                  'Texture snapshot preload is outside replay timing; captured subsequent uploads use real MMIO and validated readback.',
                  'Only eight captured frames under two traffic modes are compared; all resident extents fit the model first 2 MiB.',
                  'SDRAM hash bytes are excluded from cross-build equality because diagnostic code and data memory differ.',
                  'PALB palette base relocates with diagnostic memory layout. Three resident texture words differ from frame360 onward; every difference is recorded, and no whole-resident-memory equivalence is claimed. Their cause is not established by this audit.',
                  'Commands, MMIO uploads, texture extents and record ordering are identical to F6. Actual sampled color/depth outputs, not full TEX1 identity, are the rendering equality gate.',
                  'Panel capture validates accounting and frozen snapshot state; panel rendering is an intentional visual change.',
                  'HOST includes cache/memory stalls, submission MMIO and uncounted upload/LUT waits; it is not pure CPU execution time.'
              ])
path = OUT / 'independent-replay-review.json'
path.write_text(json.dumps(result, indent=2) + '\n')
print(json.dumps(dict(passed=True, comparisons=len(results), report=str(path), report_sha256=sha(path))))
