#!/usr/bin/env python3
"""CPU sensitivity and GPU vectors for the privately built worker client."""
import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
ROOT=Path(__file__).resolve().parents[3]
OUT=ROOT/'docs/measurements/os30-worker-20261003'
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('label');p.add_argument('--no-worker',action='store_true');p.add_argument('--dc128',action='store_true')
a=p.parse_args();dest=OUT/('capture-'+a.label);dest.mkdir(exist_ok=False)
elf=dest/'sm64.elf';shutil.copy2(OUT/'SM64/.obj/sm64/app.elf',elf)
exe=OUT/'qsim/qsim'
assets=ROOT/'docs/measurements/sm64-device-perf-20261003/SM64/build/pocket/sm64/Assets/sm64/common'
app=ROOT/'docs/measurements/sm64-bob-20261002/sm64.app'
cmd=[str(exe),str(elf),'--app',str(app),'--data-dir',str(assets),'--out',str(dest),
     '--frames','721','--profile','--gpuvec','40,120,200,360,400,500,600,720',
     '--gpu-sched','eager','--max-insns','5000000000']
env={k:v for k,v in os.environ.items() if not k.startswith(('SM64_','QSIM_AUDIT_'))}
env.update(SM64_AUDIO='1',SM64_GEOMETRY_CAP='1',QSIM_AUDIT_DC_SETS='1024' if a.dc128 else '512')
if not a.no_worker:env['SM64_WORKER_CAP']='1'
inputs=dict(command=cmd,worker=not a.no_worker,dcache_kib=128 if a.dc128 else 64,
    elf_sha256=hashlib.sha256(elf.read_bytes()).hexdigest(),qsim_sha256=hashlib.sha256(exe.read_bytes()).hexdigest(),success=False)
(dest/'inputs.json').write_text(json.dumps(inputs,indent=2)+'\n')
with (dest/'run.log').open('w') as f:subprocess.run(cmd,env=env,cwd=dest,stdout=f,stderr=subprocess.STDOUT,check=True,timeout=900)
assert len(list(csv.DictReader((dest/'frames.csv').open())))==721
assert (dest/'scene.csv').read_bytes()==(ROOT/'docs/measurements/os30-feature-audit-20261003/cpu-baseline/scene.csv').read_bytes()
assert json.loads((dest/'summary.json').read_text())['gpu_sync']['ring_overflows']==0
inputs['success']=True;(dest/'inputs.json').write_text(json.dumps(inputs,indent=2)+'\n')
print(dest)
