#!/usr/bin/env python3
"""Verify the selected app and report CPU-model results, never hardware FPS."""
import csv
import hashlib
import json
from pathlib import Path
import statistics

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / 'docs/measurements/sm64-triangle-cache-20261004'
P2 = ROOT / 'docs/measurements/sm64-render-perf-20261004'
STAGE = OUT / 'SM64'
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
load = lambda p: json.loads(p.read_text())
rows = lambda p: list(csv.DictReader(p.open()))


def compare_cpu(old, new):
    a, b = rows(old / 'frames.csv'), rows(new / 'frames.csv')
    assert len(a) == len(b) == 721
    windows = []
    for first in range(360, 720, 60):
        before = statistics.mean(int(x['est_cycles']) for x in a[first:first+60])
        after = statistics.mean(int(x['est_cycles']) for x in b[first:first+60])
        windows.append(dict(first_frame=first, baseline_ms=before/100000,
                            candidate_ms=after/100000,
                            cpu_time_reduction_percent=100*(1-after/before)))
    before = statistics.mean(int(x['est_cycles']) for x in a[360:720])
    after = statistics.mean(int(x['est_cycles']) for x in b[360:720])
    return dict(windows=windows, baseline_mean_ms=before/100000,
                candidate_mean_ms=after/100000,
                cpu_time_reduction_percent=100*(1-after/before))


def main():
    normal = OUT / 'capture-cache-v6-fixed'
    tagged = OUT / 'capture-final-tagged'
    panel = OUT / 'capture-final-panel'
    normal_sha = sha(STAGE / '.obj/sm64/app.elf')
    diagnostic_sha = sha(STAGE / '.obj/sm64-perfdiag/app.elf')
    assert normal_sha == sha(normal / 'sm64.elf')
    assert diagnostic_sha == sha(tagged / 'sm64.elf') == sha(panel / 'sm64.elf')
    baseline_scene = (OUT / 'capture-baseline/scene.csv').read_bytes()
    baseline_frames = rows(OUT/'capture-baseline/frames.csv')
    normal_frames = rows(normal/'frames.csv')
    assert [x['gpu_cmd_words'] for x in baseline_frames] == [x['gpu_cmd_words'] for x in normal_frames]
    assert rows(OUT/'capture-baseline/geometry.csv') == rows(normal/'geometry.csv')
    for path in (normal, tagged, panel, OUT/'capture-baseline-fallback', OUT/'capture-final-fallback'):
        assert load(path / 'inputs.json')['success']
        assert (path / 'scene.csv').read_bytes() == baseline_scene, path
        assert load(path / 'summary.json')['gpu_sync']['ring_overflows'] == 0
    for path in (tagged, panel):
        valid = [x for x in rows(path/'diagnostic.csv') if x['stat0'] == '1']
        assert valid and all(x['stat18'] == '2' and int(x['stat19']) > 0 for x in valid)
        for x in valid:
            assert 0 <= int(x['stat20'])-sum(int(x[f'stat{i}']) for i in range(21,29)) < 8
    frozen = [x for x in rows(panel/'diagnostic.csv') if 603 <= int(x['frame']) <= 679]
    assert len(frozen) == 77
    assert len({tuple(x[f'stat{i}'] for i in range(30)) for x in frozen}) == 1

    baseline = {x['frame']: x for x in load(OUT/'replay-results-baseline-cache-v1-worker.json')
                if x['label'] == 'baseline'}
    candidates = load(OUT/'replay-results-cache-v6-fixed-worker.json')
    candidates += load(OUT/'replay-results-final-tagged-final-panel-worker.json')
    comparisons = []
    for x in candidates:
        if x['label'] == 'final-panel' and x['frame'] not in (360,720):
            continue  # The panel intentionally draws over the game.
        b = baseline[x['frame']]
        assert x['hardware_sha256'] == b['hardware_sha256']
        for field in ('framebuffer_sha256', 'depth_sha256'):
            assert x[field] == b[field], (x['label'], x['frame'], field)
        comparisons.append(dict(label=x['label'], frame=x['frame']))
    fallback = load(OUT/'replay-results-baseline-fallback-final-fallback-worker.json')
    reference = {x['frame']:x for x in fallback if x['label'] == 'baseline-fallback'}
    for x in fallback:
        if x['label'] != 'final-fallback':
            continue
        for field in ('hardware_sha256','framebuffer_sha256','depth_sha256'):
            assert x[field] == reference[x['frame']][field], (x['frame'],field)
        comparisons.append(dict(label=x['label'], frame=x['frame']))
    assert len(comparisons) == 22

    boundary = load(OUT/'boundary-checks.json')
    visibility = load(OUT/'visibility-checks.json')
    gfx = STAGE/'src/sm64/sm64/src/pc/gfx'
    for checks in (boundary, visibility):
        for name,digest in checks['source_sha256'].items():
            assert sha(gfx/name) == digest, name
    assert 'cached TRI2' in boundary['tests'][0]['output']
    assert sha(gfx/'gfx_geometry_cache.h') == sha(Path(__file__).with_name('gfx_geometry_cache.h'))
    perf = STAGE/'src/sm64/pocket/perf_pocket.c'
    # The panel title is the only profiler change; probes and timing stay equal.
    assert perf.read_text() == (P2/'SM64/src/sm64/pocket/perf_pocket.c').read_text().replace(
        'PERF P2 SNAPSHOT','PERF P2 CACHE')

    changed = ['src/sm64/sm64/src/pc/gfx/gfx_pc.c',
               'src/sm64/sm64/src/pc/gfx/gfx_geometry.h',
               'src/sm64/sm64/src/pc/gfx/gfx_geometry_cache.h',
               'src/sm64/pocket/perf_pocket.c', 'PERFORMANCE-TEST.txt']
    source = {rel: sha(STAGE/rel) for rel in changed}
    untouched = {rel: digest for rel,digest in load(OUT/'before.json').items()
                 if rel not in changed and not rel.startswith(('.obj/','build/'))}
    for rel,digest in untouched.items():
        assert sha(STAGE/rel) == digest, rel
    report = dict(normal_app_sha256=normal_sha, diagnostic_app_sha256=diagnostic_sha,
        source_sha256=source, unchanged_sha256=untouched,
        normal_cpu=compare_cpu(OUT/'capture-baseline',normal),
        diagnostic_cpu=compare_cpu(P2/'capture-final-hidden',tagged),
        fallback_cpu=compare_cpu(OUT/'capture-baseline-fallback',OUT/'capture-final-fallback'),
        cache_disabled_cpu=compare_cpu(OUT/'capture-cache-off',normal),
        comparisons=comparisons, paired_framebuffer_depth_comparisons=len(comparisons),
        frozen_snapshot_records=len(frozen), scene_records_per_capture=721,
        gpu_word_counts_and_geometry_counters_match_all_721_frames=True,
        cache_storage_bytes=1024*24+8192*16+16, fpga_changed=False,
        full_seed_sweep_run=False, hardware_fps_measured=False,
        limitations=[
            'The single-issue CPU model estimates instruction/cache cost; it is not FPGA cycle-accurate.',
            'Synthetic frame clocks, eager GPU and simplified mixer lifetimes do not measure Pocket FPS.',
            'One scripted first-level route and selected RTL frame replays do not cover every game scene.',
            'Immutable commands/faces are cached; visibility and material are evaluated every draw.',
            'Cache consumes about 152 KiB of existing SDRAM; no FPGA memory blocks or logic are added.',
            'The existing P2 probes remain enabled in the packaged app for a like-for-like device test.'])
    (OUT/'results.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({k:report[k] for k in ('normal_cpu','diagnostic_cpu','fallback_cpu',
        'paired_framebuffer_depth_comparisons','normal_app_sha256','diagnostic_app_sha256')},indent=2))


if __name__ == '__main__':
    main()
