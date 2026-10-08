#!/usr/bin/env python3
"""Run current-A coupled GPU/CRAM1 windows with an explicit immutable ELF."""
import argparse,csv,hashlib,json,os,re,shutil,subprocess,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
OUT=ROOT/'docs/measurements/os30-overlap-20261006/coupled'
CAMP=ROOT/'docs/measurements/os30-fps25-20261005'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('label');p.add_argument('--elf',type=Path,default=CAMP/'phase7/combined-v1/app.elf')
    p.add_argument('--start',type=int,default=360);p.add_argument('--frames',type=int,default=8)
    p.add_argument('--traffic',type=int,choices=[0,1,2],default=1)
    a=p.parse_args();assert re.fullmatch(r'[a-z0-9][a-z0-9_-]*',a.label)
    dest=OUT/('run-'+a.label);dest.mkdir(exist_ok=False)
    elf=dest/'sm64.elf';shutil.copy2(a.elf,elf)
    exe=OUT/'model/obj/Vtb_gpu';b=json.loads((OUT/'model/build.json').read_text())
    assert sha(exe)==b['simulator_sha256']
    app=ROOT/'docs/measurements/sm64-bob-20261002/sm64.app'
    assets=ROOT/'docs/measurements/sm64-device-perf-20261003/SM64/build/pocket/sm64/Assets/sm64/common'
    cmd=[str(exe),str(elf),'--app',str(app),'--data-dir',str(assets),'--out',str(dest),
         '--frames',str(a.start+a.frames),'--profile','--gpu-sched','eager','--max-insns','5000000000']
    env=os.environ.copy()
    for k in ['SM64_COMMAND_DMA','SM64_PERF_PANEL','SM64_WORKER_CAP','SM64_ATTRIBUTES_CAP']:
        env.pop(k,None)
    settings=dict(SM64_GEOMETRY_CAP='0',SM64_AUDIO='1',COUPLED_START_FRAME=str(a.start),MEMORY_TRAFFIC=str(a.traffic))
    env.update(settings)
    m=dict(label=a.label,start=a.start,frames=a.frames,traffic=a.traffic,command=cmd,environment=settings,
        source_elf=str(a.elf.resolve()),sha256={str(f):sha(f) for f in [exe,elf,app,assets/'bank.ofsf']},
        build_sha256=sha(OUT/'model/build.json'),script_sha256=sha(Path(__file__)),success=False,
        limits='Estimated CPU/cache timing, actual GPU/SDRAM/CRAM1 RTL; synthetic traffic, no real CPU bus feedback or kernel PCM/interrupt costs. Fixed game step, not hardware FPS.')
    (dest/'inputs.json').write_text(json.dumps(m,indent=2)+'\n')
    start=time.monotonic()
    with (dest/'run.log').open('x') as log:subprocess.run(cmd,env=env,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=3600)
    rows=list(csv.DictReader((dest/'scene.csv').open()))
    assert len(rows)==a.start+a.frames
    assert all(int(x['level'])==9 and int(x['area'])==1 for x in rows[a.start:])
    status=json.loads((dest/'coupled.json').read_text())
    assert status['frames']==a.frames and status['presented']==a.frames,status
    assert status['texture_beats']==4*status['texture_fills'] and not status['cram1_errors']
    console=(dest/'console.log').read_text();assert 'caps=2b136753' in console and 'geometry accelerator=0' in console
    m.update(success=True,wall_seconds=time.monotonic()-start,
       results_sha256={str(p.relative_to(dest)):sha(p) for p in dest.rglob('*') if p.is_file() and p.name!='inputs.json'})
    (dest/'inputs.json').write_text(json.dumps(m,indent=2)+'\n');print(dest,flush=True)
if __name__=='__main__':main()
