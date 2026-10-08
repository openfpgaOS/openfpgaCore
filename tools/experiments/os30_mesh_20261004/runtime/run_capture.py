#!/usr/bin/env python3
"""Record the actual mixed mesh/projected application stream and CPU profile."""
from pathlib import Path
import argparse,hashlib,json,os,shutil,subprocess
HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[3]
OUT=ROOT/'docs/measurements/os30-mesh-20261004'
p=argparse.ArgumentParser();p.add_argument('--label',required=True)
p.add_argument('--elf',type=Path,required=True);p.add_argument('--frames',type=int,default=368)
p.add_argument('--start',type=int,default=360);p.add_argument('--baseline',action='store_true')
p.add_argument('--dc64',action='store_true');p.add_argument('--qsim',default='runtime-qsim-v2');a=p.parse_args()
dest=OUT/a.label;dest.mkdir(exist_ok=False);shutil.copy2(a.elf,dest/'app.elf')
cmd=[str(OUT/a.qsim/'qsim'),str(dest/'app.elf'),
    '--app',str(ROOT/'docs/measurements/sm64-bob-20261002/sm64.app'),
    '--data-dir',str(ROOT/'docs/measurements/sm64-device-perf-20261003/SM64/build/pocket/sm64/Assets/sm64/common'),
    '--out',str(dest),'--frames',str(a.frames),'--gpu-sched','eager',
    '--max-insns','5000000000','--profile','--gpuvec',','.join(map(str,range(a.start,a.frames)))]
env=dict(os.environ,SM64_AUDIO='1')
for key in ('SM64_GEOMETRY_CAP','SM64_WORKER_CAP','SM64_ATTRIBUTES_CAP','SM64_MESH_CAP',
            'SM64_COMMAND_DMA','SM64_PERF_PANEL','MESH_DC64'):env.pop(key,None)
if a.baseline:env.update(SM64_GEOMETRY_CAP='1',SM64_WORKER_CAP='1',SM64_ATTRIBUTES_CAP='1')
else:env['SM64_MESH_CAP']='1'
if a.dc64:env['MESH_DC64']='1'
manifest=dict(command=cmd,elf_sha256=hashlib.sha256(a.elf.read_bytes()).hexdigest(),
    qsim_sha256=hashlib.sha256((OUT/a.qsim/'qsim').read_bytes()).hexdigest(),
    mesh_active=not a.baseline,cpu_data_cache_kib=64 if a.dc64 else 128,
    scope='Actual application CPU trace and DMA inputs. Interpreter GPU does not execute mesh geometry; no FPS claim.')
(dest/'inputs.json').write_text(json.dumps(manifest,indent=2)+'\n')
with (dest/'run.log').open('w') as log:
    subprocess.run(cmd,env=env,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=2400)
assert len(list((dest/'gpuvec').glob('*.vec')))==a.frames-a.start
print(dest)
