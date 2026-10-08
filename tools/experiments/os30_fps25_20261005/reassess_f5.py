#!/usr/bin/env python3
"""Bind F4/F5 device observations to the existing matched CPU profiles."""
import csv
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[3]
BASE = ROOT / 'docs/measurements/os30-fps25-20261005'
OUT = BASE / 'phase3/reassessment'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    evidence = {}

    def bind(path):
        evidence[str(path.relative_to(ROOT))] = sha(path)
        return path

    observations = {}
    for label, directory in [('F4', 'render-v6-20250623_022744'),
                             ('F5', 'exact-v1-20250623_034545')]:
        directory = BASE / 'hardware-feedback' / directory
        observation = json.loads(bind(directory / 'observation.json').read_text())
        image = bind(directory / Path(observation['source']).name)
        assert sha(image) == observation['screenshot_sha256']
        steps_per_frame = observation['simulation_fps'] / observation['fps']
        observations[label] = dict(
            fps=observation['fps'], game_ms=observation['game_ms'],
            audio_ms=observation['audio_ms'], render_ms=observation['render_ms'],
            simulation_hz=observation['simulation_fps'],
            steps_per_frame=steps_per_frame,
            game_ms_per_step=observation['game_ms'] / steps_per_frame,
            game_audio_ms_per_step=(observation['game_ms'] + observation['audio_ms']) / steps_per_frame,
            ring_polls_per_frame=observation['ring_polls_per_render_frame'])

    profiles = {}
    for label, name in [('F4', 'render-v6-fps'), ('F5', 'phase2-exact-v1-fps')]:
        directory = BASE / 'validation' / ('capture-' + name)
        inputs = json.loads(bind(directory / 'inputs.json').read_text())
        assert inputs['success'] and inputs['audio'] and not inputs['geometry_capability']
        snapshots = []
        for frame in (360, 720):
            txt = bind(directory / f'profile-{frame:04d}.txt')
            tsv = bind(directory / f'profile-{frame:04d}.tsv')
            for p in (txt, tsv):
                assert sha(p) == inputs['results_sha256'][p.name]
            counts = {m[1].strip(): int(m[2]) for line in txt.read_text().splitlines()
                      if (m := re.match(r'^\s{2,}(.+?)\s{2,}(\d+)', line))}
            with tsv.open() as f:
                functions = {r['function']: r for r in csv.DictReader(f, delimiter='\t')}
            snapshots.append((counts, functions))
        (start, fs), (end, fe) = snapshots
        counts = {k: end[k] - v for k, v in start.items()}
        functions = {name: {k: int(r[k]) - int(fs.get(name, {}).get(k, 0))
                           for k in ('self_insns', 'self_cycles', 'calls')}
                     for name, r in fe.items()}
        assert sum(r['self_cycles'] for r in functions.values()) == counts['est_cycles']
        hot = sorted(functions.items(), key=lambda item: -item[1]['self_cycles'])[:12]
        profiles[label] = dict(counters=counts, hottest=[dict(function=n, **r,
            modeled_cycle_percent=100 * r['self_cycles'] / counts['est_cycles']) for n, r in hot])

    f5 = observations['F5']
    render_budget = 1000 / 25 - f5['game_audio_ms_per_step'] * 30 / 25
    report = dict(
        hardware_target_verified=False, target_fps=25,
        observations=observations, profiles=profiles,
        planning=dict(frame_time_gap_ms=1000 / f5['fps'] - 1000 / 25,
            render_budget_ms_at_25fps_30hz_simulation=render_budget,
            render_saving_needed_ms=f5['render_ms'] - render_budget,
            assumptions='Constant work per simulation tick; excludes unshown tail and sampling/rounding differences.'),
        conclusions=[
            'F5 is visible on device. Both snapshots show 22.2 FPS; no net FPS improvement is established.',
            'Render time falls 0.8 ms while game time rises 0.7 ms. These are uncontrolled windows, not a causal A/B.',
            'REND includes CPU rendering and memory/ring stalls. WAIT 0 only concerns the presentation acquire wait.',
            'RING is failed space polls per render, not milliseconds.',
            'The matched CPU model identifies vertex processing as the largest function; actual RTL timing is the next check.',
            'Approximate CPU cycle counts and independent GPU replays cannot establish physical CPU/GPU memory contention.'
        ], evidence_sha256=evidence)
    OUT.mkdir(parents=True, exist_ok=True)
    destination = OUT / 'f4-f5.json'
    destination.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(dict(report=str(destination.relative_to(ROOT)),
        observations=observations, planning=report['planning']), indent=2))


if __name__ == '__main__':
    main()
