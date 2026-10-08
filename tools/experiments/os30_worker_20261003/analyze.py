#!/usr/bin/env python3
"""Summarize retained worker fits and replays without running more experiments."""
import csv
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / 'docs/measurements/os30-worker-20261003'
BASE = ROOT / 'docs/measurements/os30-feature-audit-20261003'
load = lambda p: json.loads(p.read_text())
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()


def fit_summary(directory):
    result = load(directory / 'result.json')
    text = (directory / 'ap_core.fit.rpt').read_text(errors='replace')
    sta = (directory / 'ap_core.sta.rpt').read_text(errors='replace')
    number = lambda pattern: int(re.search(pattern, text)[1].replace(',', ''))
    fmax = [float(m[1]) for line in sta.splitlines()
            if 'mp_ram' in line
            for m in [re.match(r';\s*([\d.]+) MHz\s*;', line)] if m]
    assert result['valid'] and result['seed'] == 2 and fmax
    return dict(alms=result['alms'], m10ks=result['m10ks'],
                labs=number(r'Total LABs:.*?;\s*([\d,]+)\s*/'),
                dsps=number(r'Total DSP Blocks\s*;\s*([\d,]+)\s*/'),
                main_fmax_mhz=min(fmax), worst=result['worst'],
                timing_closed=result['all_checks_pass'],
                fit_sha256=sha(directory / 'ap_core.fit.rpt'),
                result=str(directory.relative_to(ROOT) / 'result.json'))


def frame_cycles(path):
    return {int(r['frame']): int(r['est_cycles'])
            for r in csv.DictReader((path / 'frames.csv').open())}


def profile(path):
    def read(frame):
        return {r['function']: int(r['self_cycles']) for r in
                csv.DictReader((path / f'profile-{frame:04}.tsv').open(), delimiter='\t')}
    start, end = read(360), read(720)
    return {name: (value-start.get(name, 0))/360/100000 for name, value in end.items()}


def main():
    fits = {'installed': fit_summary(ROOT / 'docs/measurements/'
            'sm64-geometry-fast-20261003/fit-prefetch-v1/results/pocket-os30-s2')}
    for name in ('dc64', 'dc64_queues', 'dc64_queues_shifts', 'worker',
                 'worker-batch-v3', 'worker-full-cache-v5', 'worker-v6'):
        fits[name] = fit_summary(OUT / f'fit-{name}/results/pocket-os30-s2')

    baseline = frame_cycles(BASE / 'cpu-baseline')
    cpu = {}
    functions = ('gfx_geometry_run', 'geometry_prepare', 'gfx_load_geometry_attributes',
                 'gfx_gpu_geometry_batch', 'gfx_run_dl', 'gpu_draw_triangles')
    for name in ('cpu-fallback', 'cpu-cull-offload', 'cpu-large-batch',
                 'cpu-large-batch-dc128', 'cpu-batch-only'):
        path = OUT / ('capture-' + name)
        inputs, cycles = load(path / 'inputs.json'), frame_cycles(path)
        assert inputs['success'] and len(cycles) == 721
        assert (path / 'scene.csv').read_bytes() == (BASE / 'cpu-baseline/scene.csv').read_bytes()
        assert load(path / 'summary.json')['gpu_sync']['ring_overflows'] == 0
        percent = lambda a, b: 100*(sum(cycles[f] for f in range(a, b)) /
                                    sum(baseline[f] for f in range(a, b))-1)
        cost = profile(path)
        cpu[name] = dict(extra_cpu_time_percent=percent(360, 720),
                         windows_60_frames=[percent(f, f+60) for f in range(360, 720, 60)],
                         profile_self_ms_per_frame={n: cost[n] for n in functions},
                         dcache_kib=inputs['dcache_kib'], worker=inputs['worker'],
                         elf_sha256=inputs['elf_sha256'],
                         rendering_validated=name == 'cpu-batch-only')

    gpu = {}
    for name in ('worker-v6', 'batch-only-v6'):
        rows = load(OUT / ('replay-' + name) / 'results.json')
        assert len(rows) == 16 and all(r['match'] for r in rows)
        modes = []
        for mode in (1, 2):
            gameplay = [r for r in rows if r['mode'] == mode and r['frame'] >= 360]
            old = sum(r['baseline_cycles'] for r in gameplay)
            new = sum(r['cycles'] for r in gameplay)
            modes.append(dict(mode=mode, gameplay_throughput_gain_percent=100*(old/new-1),
                              gameplay_time_reduction_percent=100*(1-new/old),
                              per_frame_time_change_percent=[r['extra_gpu_time_percent'] for r in gameplay]))
        gpu[name] = dict(color_depth_matches=16, traffic_modes=modes)

    acceptance = {name: load(OUT / ('accept-' + name) / 'results.json')
                  for name in ('legacy', 'worker-v6')}
    for variants in acceptance.values():
        for schedules in variants.values():
            assert len(schedules) == 3
            assert all(r['failed'] == 0 and r['exit_code'] == 0 for r in schedules.values())

    # Only two documentation comments changed after the final fit was frozen.
    # Confirm its executable RTL still matches the reviewed working tree.
    frozen = OUT / 'fit-worker-v6/frozen/src/fpga/common'
    if not frozen.exists():
        frozen = OUT / 'fit-worker-v6/jobs/pocket-os30-s2/src/fpga/common'
    strip_comments = lambda s: '\n'.join(line.rstrip() for line in
        re.sub(r'//[^\n]*|/\*.*?\*/', '', s, flags=re.S).splitlines() if line.strip())
    source_hashes = {}
    for name in ('gpu_core.v', 'gpu_geometry_worker.v'):
        current = ROOT / 'src/fpga/common' / name
        assert strip_comments(current.read_text()) == strip_comments((frozen / name).read_text()), name
        source_hashes[name] = dict(current=sha(current), fitted=sha(frozen / name))

    report = dict(scope='Independent fixed-function geometry worker prototype; no programmable second CPU',
                  selected='worker-v6, 128 KiB CPU D-cache, 512-word output FIFO',
                  fits=fits, cpu=cpu, gpu=gpu, acceptance=acceptance, sources=source_hashes,
                  unit=load(OUT / 'unit/verified.json'),
                  configured_mhz=100, seeds_tested=[2], full_seed_sweep_run=False,
                  published_to_sm64=False, device_fps_improvement_verified=False,
                  conclusion='Correct overlap and modest GPU improvement; insufficient for a substantial whole-game gain.',
                  limitations=[
                      'CPU sensitivity model has no real GPU backpressure, LCD scheduling or full audio interrupt timing.',
                      'GPU replay uses Pocket SDRAM RTL with synthetic traffic and SDRAM textures; device CRAM1 and coupled CPU traffic differ.',
                      'Only the retained 721-frame route and eight sampled images were checked; no full game or device qualification.',
                      'Lighting, clipping, attribute preparation and display-list interpretation still run on the main CPU.',
                      'Saved-seed fit succeeds but 100 MHz setup timing remains negative.'])
    (OUT / 'summary.json').write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(dict(final_fit=fits['worker-v6'], cpu=cpu['cpu-batch-only'], gpu=gpu), indent=2))


if __name__ == '__main__':
    main()
