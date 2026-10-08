#!/usr/bin/env python3
"""Run a bounded mesh or untouched attributes-v6 coupled Bob-omb window."""
import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[4]
OUT = ROOT / 'docs/measurements/os30-mesh-20261004'
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('label')
    p.add_argument('--build', default='coupled-v1')
    p.add_argument('--baseline', action='store_true')
    p.add_argument('--dcache-kib', type=int, choices=[64, 128], default=128)
    p.add_argument('--elf', type=Path, required=True)
    p.add_argument('--start', type=int, default=360)
    p.add_argument('--frames', type=int, default=8)
    p.add_argument('--traffic', type=int, choices=[0, 1, 2], default=1)
    a = p.parse_args()
    dest = OUT / ('coupled-run-' + a.label)
    dest.mkdir(exist_ok=False)
    exe = (ROOT / 'docs/measurements/os30-frontend-20261004/attributes-v6/obj/Vtb_gpu'
           if a.baseline else OUT / a.build / 'obj/Vtb_gpu')
    elf = a.elf.resolve()
    if not a.baseline:
        build_manifest=json.loads((OUT/a.build/'manifest.json').read_text())
        assert build_manifest['cpu_data_cache_kib']==a.dcache_kib, 'cache label does not match compiled model'
    app = ROOT / 'docs/measurements/sm64-bob-20261002/sm64.app'
    assets = ROOT / 'docs/measurements/sm64-device-perf-20261003/SM64/build/pocket/sm64/Assets/sm64/common'
    cmd = [str(exe), str(elf), '--app', str(app), '--data-dir', str(assets), '--out', str(dest),
           '--frames', str(a.start + a.frames), '--profile', '--gpu-sched', 'eager',
           '--max-insns', '5000000000']
    env = os.environ.copy()
    for key in ['SM64_COMMAND_DMA', 'SM64_PERF_PANEL', 'SM64_WORKER_CAP',
                'SM64_ATTRIBUTES_CAP', 'SM64_GEOMETRY_CAP', 'SM64_MESH_CAP', 'MESH_DC64']:
        env.pop(key, None)
    env.update(SM64_AUDIO='1', COUPLED_START_FRAME=str(a.start), MEMORY_TRAFFIC=str(a.traffic))
    if a.baseline:
        env.update(SM64_GEOMETRY_CAP='1', SM64_WORKER_CAP='1', SM64_ATTRIBUTES_CAP='1')
    else:
        env['SM64_MESH_CAP'] = '1'
    manifest = dict(baseline=a.baseline, start=a.start, frames=a.frames, traffic=a.traffic,
                    command=cmd, cpu_data_cache_kib=a.dcache_kib,
                    environment={k: v for k, v in env.items()
                                 if k.startswith(('SM64_', 'COUPLED_', 'MEMORY_'))},
                    sha256={str(f): sha(f) for f in [exe, elf, app, assets / 'bank.ofsf']},
                    success=False,
                    limitation='Fixed game steps; estimated CPU timing/cache behavior; synthetic external SDRAM traffic; no PCM/interrupt cost.')
    (dest / 'inputs.json').write_text(json.dumps(manifest, indent=2) + '\n')
    with (dest / 'run.log').open('w') as log:
        subprocess.run(cmd, env=env, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=1800)
    rows = list(csv.DictReader((dest / 'scene.csv').open()))
    assert len(rows) == a.start + a.frames
    assert all(int(x['level']) == 9 and int(x['area']) == 1 for x in rows[a.start:])
    status = json.loads((dest / 'coupled.json').read_text())
    assert status['frames'] == a.frames and status['presented'] == a.frames, status
    manifest['success'] = True
    (dest / 'inputs.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(dest, flush=True)

if __name__ == '__main__':
    main()
