#!/usr/bin/env python3
"""Validate the diagnostic without mistaking synthetic clocks for hardware FPS."""
import csv
import hashlib
import json
from pathlib import Path
import statistics

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / 'docs/measurements/sm64-device-perf-20261003'
PRIOR = ROOT / 'docs/measurements/sm64-geometry-fast-20261003'
load = lambda p: json.loads(p.read_text())
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
frames = (40, 120, 200, 360, 400, 500, 600, 720)

def cpu(path):
    rows = list(csv.DictReader((path / 'frames.csv').open()))
    assert len(rows) == 721
    return rows, [statistics.mean(int(r['est_cycles']) for r in rows[f:f+60])/100000
                  for f in range(360, 720, 60)]

baseline_rows, baseline_ms = cpu(PRIOR / 'capture-final-candidate')
captures = {}
for label in ('audio-current', 'diag-hidden', 'diag-panel', 'diag-disabled'):
    path = OUT / ('capture-' + label)
    assert load(path / 'inputs.json')['success'], label
    assert (path / 'scene.csv').read_bytes() == (PRIOR / 'capture-final-candidate/scene.csv').read_bytes(), label
    assert load(path / 'summary.json')['gpu_sync']['ring_overflows'] == 0, label
    rows, ms = cpu(path)
    captures[label] = dict(cpu_ms=ms, elf_sha256=sha(path / 'sm64.elf'))
    if label == 'diag-disabled':
        assert all(a['est_cycles'] == b['est_cycles'] for a, b in zip(rows, baseline_rows))

prior = {r['frame']: r for r in load(PRIOR / 'replay-results-final-candidate-prefetch.json') if r['mode'] == 1}
rows = load(OUT / 'replay-results-diag-hidden-diag-panel-prefetch.json') + load(OUT / 'replay-results-diag-disabled-prefetch.json')
index = {(r['label'], r['frame']): r for r in rows}
pairs = 0
for label in ('diag-hidden', 'diag-disabled'):
    for f in frames:
        for field in ('framebuffer_sha256', 'depth_sha256', 'hardware_sha256'):
            assert index[label, f][field] == prior[f][field], (label, f, field)
        pairs += 1
for f in (360, 720):
    for field in ('framebuffer_sha256', 'depth_sha256'):
        assert index['diag-panel', f][field] == prior[f][field], ('panel closed', f, field)
    pairs += 1
assert index['diag-panel', 660]['framebuffer_sha256'] != prior[600]['framebuffer_sha256']

timing = load(OUT / 'timing-tests.json')
assert timing['source_sha256'] == sha(OUT / 'SM64/src/sm64/pocket/perf_pocket.c')
assert timing['result'].startswith('PASS:')
disabled = load(OUT / 'disabled-loadable-check.json')
assert disabled['only_assertion_line_numbers_changed']
assert disabled['disabled_sha256'] == captures['diag-disabled']['elf_sha256']
app = sha(OUT / 'SM64/.obj/sm64-perfdiag/app.elf')
assert app == captures['diag-hidden']['elf_sha256'] == captures['diag-panel']['elf_sha256']
names = ['src/sm64/Makefile', 'src/sm64/pocket/perf_pocket.h', 'src/sm64/pocket/perf_pocket.c',
         'src/sm64/pocket/controller_pocket.c', 'src/sm64/sm64/src/pc/pc_main.c',
         'src/sm64/sm64/src/game/game_init.c', 'src/sm64/sm64/src/pc/gfx/gfx_pc.c']
source = {rel: sha(OUT / 'SM64' / rel) for rel in names}
before = load(OUT / 'before.json')
unchanged = {rel: value for rel, value in before.items() if rel not in source}
for rel, value in unchanged.items():
    assert sha(OUT / 'SM64' / rel) == value, rel
hidden = captures['diag-hidden']['cpu_ms']
windows = [dict(first_frame=360+i*60, baseline_cpu_ms=baseline_ms[i],
    audio_cpu_ms=captures['audio-current']['cpu_ms'][i], diagnostic_cpu_ms=hidden[i],
    diagnostic_overhead_percent=100*(hidden[i]/baseline_ms[i]-1)) for i in range(6)]
assert max(r['diagnostic_overhead_percent'] for r in windows) < 1.0
report = dict(status='Diagnostic ready; user reports no noticeable hardware improvement',
    hardware_fps_measured=False, app_sha256=app,
    baseline_app_sha256=captures['audio-current']['elf_sha256'],
    disabled_app_sha256=captures['diag-disabled']['elf_sha256'],
    source_sha256=source, unchanged_sha256=unchanged,
    notes_sha256=sha(OUT / 'SM64/PERFORMANCE-TEST.txt'),
    paired_framebuffer_depth_comparisons=pairs, scene_records_matched_per_capture=721,
    capture_count=4, disabled_model_cycles_match_all_frames=True,
    disabled_loadable_check=disabled, timing_tests=timing,
    cpu_windows=windows, full_seed_sweep_run=False, hardware_deployment_run=False,
    limitations=[
        'Functional capture clock forces one simulation step per frame; panel numbers in simulation are not real FPS.',
        'CPU model does not include hardware GPU backpressure, CPU/GPU overlap or real LCD cadence.',
        'Optional audio runs real CPU sequencing/sample preparation with simplified voice lifetimes; kernel mixing/interrupt cost is omitted.',
        'Readout provides two-second averages, not individual LCD frame intervals.',
        'Diagnostic overhead is modeled below 1%; on-device overhead is unmeasured.',
        'Unchanged os30 bitstream runs at 100 MHz but its saved-seed timing report is 91.04 MHz Fmax.',
        'No hardware performance improvement has been established.'])
(OUT / 'results.json').write_text(json.dumps(report, indent=2) + '\n')
print(json.dumps(dict(comparisons=pairs, cpu_windows=windows, app_sha256=app), indent=2))
