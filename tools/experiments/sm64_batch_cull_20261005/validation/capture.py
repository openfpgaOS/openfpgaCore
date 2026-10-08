#!/usr/bin/env python3
"""Exercise the hardware snapshot on the established first-level CPU harness.

GPU commands are captured for separate RTL replay. Optional functional mixer
voice lifetimes let the real audio sequence code run, without modeling kernel
mixing cost. The synthetic clock and eager GPU cannot measure hardware FPS.
"""
import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

ROOT=Path(__file__).resolve().parents[4]
OUT=ROOT/'docs/measurements/sm64-batch-cull-20261005/validation'
PRIOR=ROOT/'docs/measurements/sm64-bob-20261002'


def prepare():
    return OUT/'qsim/qsim'


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('label')
    p.add_argument('--elf',type=Path,required=True)
    p.add_argument('--exe',type=Path,default=OUT/'qsim-observed/qsim')
    p.add_argument('--geometry',type=int,choices=(0,1),required=True)
    p.add_argument('--command-dma',action='store_true')
    p.add_argument('--audio',action='store_true')
    p.add_argument('--panel',action='store_true')
    p.add_argument('--vectors',default='40,120,200,360,400,500,600,720')
    a=p.parse_args()
    exe=a.exe.resolve();dest=OUT/('capture-'+a.label);dest.mkdir(exist_ok=False)
    elf=dest/'sm64.elf';shutil.copy2(a.elf,elf)
    assets=ROOT/'docs/measurements/sm64-device-perf-20261003/SM64/build/pocket/sm64/Assets/sm64/common'
    cmd=[str(exe),str(elf),'--app',str(PRIOR/'sm64.app'),'--data-dir',str(assets),
         '--out',str(dest),'--frames','721','--profile','--gpuvec',a.vectors,
         '--gpu-sched','eager','--max-insns','5000000000']
    env=os.environ.copy();env.pop('SM64_GEOMETRY_CAP',None)
    env.pop('SM64_COMMAND_DMA',None)
    env.pop('SM64_AUDIO',None)
    env.pop('SM64_PERF_PANEL',None)
    for name in ['SM64_WORKER_CAP','SM64_ATTRIBUTES_CAP']:env.pop(name,None)
    if a.panel:env['SM64_PERF_PANEL']='1'
    if a.audio:env['SM64_AUDIO']='1'
    if a.command_dma:env['SM64_COMMAND_DMA']='1'
    env['SM64_GEOMETRY_CAP']=str(a.geometry)
    inputs=dict(command=cmd,geometry_capability=bool(a.geometry),hw_features='0x2b136753',command_dma=a.command_dma,audio=a.audio,panel=a.panel,success=False,
        sha256={str(f):hashlib.sha256(f.read_bytes()).hexdigest()
            for f in (exe,elf,PRIOR/'sm64.app',assets/'bank.ofsf')})
    (dest/'inputs.json').write_text(json.dumps(inputs,indent=2)+'\n')
    with (dest/'run.log').open('w') as log:
        subprocess.run(cmd,env=env,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=900)
    rows=list(csv.DictReader((dest/'scene.csv').open()))
    assert len(rows)==721
    assert all(int(r['level'])==9 and int(r['area'])==1 and int(r['action'],16)!=0 for r in rows[360:])
    console=(dest/'console.log').read_text()
    assert 'gpu caps=2b136753' in console,console[:2000]
    inputs['results_sha256']={str(f.relative_to(dest)):hashlib.sha256(f.read_bytes()).hexdigest() for f in dest.rglob('*') if f.is_file() and f.name!='inputs.json'}
    inputs['success']=True
    (dest/'inputs.json').write_text(json.dumps(inputs,indent=2)+'\n')
    print(dest)


if __name__=='__main__':main()
