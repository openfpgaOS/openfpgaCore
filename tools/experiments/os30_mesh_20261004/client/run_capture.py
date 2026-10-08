#!/usr/bin/env python3
from pathlib import Path
import argparse,hashlib,json,os,subprocess
ROOT=Path(__file__).resolve().parents[4]
OUT=ROOT/'docs/measurements/os30-mesh-20261004/SM64/mesh-shadow'
p=argparse.ArgumentParser();p.add_argument('--start',type=int,default=360);p.add_argument('--frames',type=int,default=8)
p.add_argument('--label',default='')
p.add_argument('--elf',type=Path)
a=p.parse_args();dest=OUT/(a.label or f'run-{a.start}-{a.frames}');dest.mkdir(exist_ok=False)
elf=a.elf or OUT.parent/'.obj/sm64-perfdiag/app.elf'
exe=OUT/'qsim/qsim'
cmd=[str(exe),str(elf),'--app',str(ROOT/'docs/measurements/sm64-bob-20261002/sm64.app'),
     '--data-dir',str(ROOT/'docs/measurements/sm64-device-perf-20261003/SM64/build/pocket/sm64/Assets/sm64/common'),
     '--out',str(dest),'--frames',str(a.start+a.frames),'--gpu-sched','eager','--max-insns','5000000000']
env=dict(os.environ,SM64_GEOMETRY_CAP='1',SM64_AUDIO='1',MESH_CAPTURE_START=str(a.start))
for key in ('SM64_WORKER_CAP','SM64_ATTRIBUTES_CAP','SM64_COMMAND_DMA','SM64_PERF_PANEL'):env.pop(key,None)
manifest=dict(command=cmd,elf_sha256=hashlib.sha256(elf.read_bytes()).hexdigest(),
              scope='Functional CPU producer fixture; original renderer active; not accelerator timing or rendering validation.')
(dest/'inputs.json').write_text(json.dumps(manifest,indent=2)+'\n')
(dest/'app.elf').write_bytes(elf.read_bytes())
with (dest/'run.log').open('w') as log:
    subprocess.run(cmd,env=env,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=1800)
assert len(list(dest.glob('mesh-*.bin')))==a.frames
print(dest)
