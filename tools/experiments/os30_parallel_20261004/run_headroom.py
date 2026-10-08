#!/usr/bin/env python3
"""Run a CPU-cost sensitivity case; these are ceilings, NOT candidate FPS."""
import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT/'docs/measurements/os30-parallel-20261004'
PRIOR = ROOT/'docs/measurements/os30-frontend-20261004'
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('case', choices=['control','mesh75','geometry75','renderer90'])
    p.add_argument('--start',type=int,default=360)
    p.add_argument('--traffic',type=int,choices=[1,2],default=1)
    a = p.parse_args()
    dest = OUT/f'run-{a.case}-{a.start}-t{a.traffic}'
    dest.mkdir(exist_ok=False)
    exe = OUT/'headroom/obj/Vtb_gpu'
    elf = PRIOR/'attributes-p4.elf'
    app = ROOT/'docs/measurements/sm64-bob-20261002/sm64.app'
    assets = ROOT/'docs/measurements/sm64-device-perf-20261003/SM64/build/pocket/sm64/Assets/sm64/common'
    cmd=[str(exe),str(elf),'--app',str(app),'--data-dir',str(assets),'--out',str(dest),
         '--frames',str(a.start+8),'--profile','--gpu-sched','eager','--max-insns','5000000000']
    env=os.environ.copy()
    for key in ['SM64_COMMAND_DMA','SM64_PERF_PANEL']: env.pop(key,None)
    settings=dict(SM64_GEOMETRY_CAP='1',SM64_WORKER_CAP='1',SM64_ATTRIBUTES_CAP='1',
                  SM64_AUDIO='1',COUPLED_START_FRAME=str(a.start),MEMORY_TRAFFIC=str(a.traffic),
                  PARALLEL_CASE=a.case)
    env.update(settings)
    manifest=dict(command=cmd,environment=settings,counterfactual=True,success=False,
                  sha256={str(f):sha(f) for f in [exe,elf,app,assets/'bank.ofsf']})
    (dest/'inputs.json').write_text(json.dumps(manifest,indent=2)+'\n')
    with (dest/'run.log').open('w') as log:
        subprocess.run(cmd,env=env,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=1800)
    status=json.loads((dest/'coupled.json').read_text())
    assert status['frames']==status['presented']==8 and not status['ring_overflow']
    assert status['memory_errors']==0
    base=PRIOR/f'run-release-{a.start}-t{a.traffic}'
    assert list(csv.DictReader((dest/'scene.csv').open())) == list(csv.DictReader((base/'scene.csv').open()))
    for frame in range(a.start,a.start+8):
        name=f'frame_{frame:05}.bin'
        assert (dest/name).read_bytes()==(base/name).read_bytes(),name
    if a.case=='control':
        for name in ['events.csv','counters.csv','coupled.json']:
            assert (dest/name).read_bytes()==(base/name).read_bytes(),name
    manifest['success']=True
    manifest['exact_color_and_depth_frames']=8
    (dest/'inputs.json').write_text(json.dumps(manifest,indent=2)+'\n')
    print(dest,flush=True)


if __name__=='__main__':main()
