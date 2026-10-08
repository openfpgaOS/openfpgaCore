#!/usr/bin/env python3
"""Validate P2 without presenting modeled timing as Pocket measurements."""
import csv
import hashlib
import json
from pathlib import Path
import statistics

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / 'docs/measurements/sm64-render-perf-20261004'
P1 = ROOT / 'docs/measurements/sm64-device-perf-20261003'
STAGE = OUT / 'SM64'
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
load = lambda p: json.loads(p.read_text())
csv_rows = lambda p: list(csv.DictReader(p.open()))
baseline = csv_rows(P1 / 'capture-diag-hidden/frames.csv')
hidden = csv_rows(OUT / 'capture-final-hidden/frames.csv')
windows = []
for f in range(360, 720, 60):
    old = statistics.mean(int(r['est_cycles']) for r in baseline[f:f+60])
    new = statistics.mean(int(r['est_cycles']) for r in hidden[f:f+60])
    windows.append(dict(first_frame=f, p1_cpu_ms=old/100000,
                        p2_cpu_ms=new/100000, modeled_overhead_percent=100*(new/old-1)))

elf = sha(STAGE / '.obj/sm64-perfdiag/app.elf')
for label in ('final-hidden', 'final-panel'):
    path = OUT / ('capture-' + label)
    assert load(path / 'inputs.json')['success']
    assert sha(path / 'sm64.elf') == elf
    assert (path / 'scene.csv').read_bytes() == (P1 / 'capture-diag-hidden/scene.csv').read_bytes()
    assert len(csv_rows(path / 'frames.csv')) == 721
    assert load(path / 'summary.json')['gpu_sync']['ring_overflows'] == 0
    snapshots = csv_rows(path / 'diagnostic.csv')
    valid = [r for r in snapshots if r['stat0'] == '1']
    assert valid and all(r['stat18'] == '2' and int(r['stat19']) > 0 for r in valid)
    for row in valid:
        # Displayed bucket averages are independently truncated to tenths.
        total = sum(int(row[f'stat{i}']) for i in range(21, 29))
        assert 0 <= int(row['stat20']) - total < 8
    if label == 'final-panel':
        frozen = [r for r in snapshots if 603 <= int(r['frame']) <= 679]
        assert len(frozen) == 77
        assert len({tuple(r[f'stat{i}'] for i in range(30)) for r in frozen}) == 1

prior = {r['frame']: r for r in load(ROOT / 'docs/measurements/os30-worker-20261003/replay-worker-v6/results.json') if r['mode'] == 1}
replays = load(OUT / 'replay-results-final-hidden-final-panel-worker.json')
assert len(replays) == 12
pairs = 0
for r in replays:
    assert r['hardware_sha256'] == prior[r['frame'] if r['frame'] in prior else 600]['exe_sha256']
    if r['label'] == 'final-hidden' or r['frame'] in (360, 720):
        for field in ('framebuffer_sha256', 'depth_sha256'):
            assert r[field] == prior[r['frame']][field], (r['label'], r['frame'], field)
        pairs += 1
assert pairs == 10

disabled = load(OUT / 'disabled-loadable-check.json')
assert disabled['only_assertion_line_numbers_changed']
assert disabled['disabled_sha256'] == sha(STAGE / '.obj/sm64/app.elf')
timing = load(OUT / 'timing-tests.json')
assert timing['source_sha256'] == sha(STAGE / 'src/sm64/pocket/perf_pocket.c')
assert timing['header_sha256'] == sha(STAGE / 'src/sm64/pocket/perf_pocket.h')
changed = ['src/sm64/pocket/perf_pocket.h', 'src/sm64/pocket/perf_pocket.c',
           'src/sm64/sm64/src/pc/gfx/gfx_pc.c', 'src/sm64/sm64/src/pc/gfx/gfx_gpu.c',
           'PERFORMANCE-TEST.txt']
source = {rel: sha(STAGE / rel) for rel in changed}
unchanged = {rel: digest for rel, digest in load(OUT / 'before.json').items()
             if rel not in changed and not rel.startswith(('.obj/', 'build/'))}
for rel, digest in unchanged.items():
    assert sha(STAGE / rel) == digest, rel
report = dict(app_sha256=elf, source_sha256=source, unchanged_sha256=unchanged,
              cpu_windows=windows, paired_framebuffer_depth_comparisons=pairs,
              scene_records_per_capture=721, frozen_snapshot_records=77,
              timing_tests=timing, disabled_loadable_check=disabled,
              hardware_fps_measured=False, fpga_changed=False, full_seed_sweep_run=False,
              limitations=[
                  'P2 adds about 3-4% CPU cost in this model; on-device overhead remains unmeasured.',
                  'Frame clocks and eager GPU scheduling in qsim do not measure Pocket FPS or real CPU/GPU overlap.',
                  'One frame in eight is profiled; detailed averages can differ from the all-frame REND average.',
                  'SEND includes backend conversion, packing, MMIO and ring waits, not isolated GPU execution time.',
                  'The current app stores textures in SDRAM; it does not select CRAM1/PSRAM textures.'])
(OUT / 'results.json').write_text(json.dumps(report, indent=2) + '\n')
print(json.dumps(dict(app_sha256=elf, comparisons=pairs, cpu_windows=windows), indent=2))
