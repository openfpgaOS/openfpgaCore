#!/usr/bin/env python3
"""Capture and profile the first level, with repeatable entrance/walk/camera input."""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import subprocess

ROOT=Path(__file__).resolve().parents[3]
OUT=ROOT/'docs/measurements/sm64-bob-20261002'
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--label',default='capture')
p.add_argument('--vectors',default='360,380,400,420,440,460,480,500,520,540,560,580,600,620,660,700,720')
a=p.parse_args()
dest=OUT/a.label
if dest.exists():
    raise SystemExit(f'Refusing to overwrite {dest}; choose a fresh label')
dest.mkdir()
exe=OUT/'qsim/qsim'
elf=OUT/'inputs/sm64.elf'
assets=ROOT.parent/'SM64/build/pocket/sm64/Assets/sm64/common'
cmd=[str(exe),str(elf),'--app',str(OUT/'sm64.app'),'--data-dir',str(assets),
     '--out',str(dest),'--frames','721','--profile','--gpuvec',a.vectors,
     '--gpu-sched','eager','--max-insns','5000000000']
inputs=dict(command=cmd,sha256={str(f):hashlib.sha256(f.read_bytes()).hexdigest()
    for f in (exe,elf,OUT/'sm64.app',assets/'bank.ofsf')},success=False)
(dest/'run-inputs.json').write_text(json.dumps(inputs,indent=2)+'\n')
with (dest/'run.log').open('w') as log:
    subprocess.run(cmd,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=600)
scenes=list(csv.DictReader((dest/'scene.csv').open()))
assert len(scenes)==721
assert all(int(r['level'])==9 and int(r['area'])==1 and int(r['act'])==1
           and int(r['action'],16)!=0 for r in scenes[360:])
assert float(scenes[360]['x']) < -6400 and float(scenes[600]['x']) > -3100
inputs['success']=True
(dest/'run-inputs.json').write_text(json.dumps(inputs,indent=2)+'\n')
print(dest)
