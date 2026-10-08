#!/usr/bin/env python3
"""Capture a geometry-trial ELF on the established first-level CPU harness.

The qsim rasterizer is not a geometry oracle. GPU commands are captured for
separate RTL replay; the only hardware model addition here is capability bit 10.
"""
import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

ROOT=Path(__file__).resolve().parents[3]
OUT=ROOT/'docs/measurements/sm64-geometry-20261003'
PRIOR=ROOT/'docs/measurements/sm64-bob-20261002'


def prepare():
    q=OUT/'qsim'
    if (q/'qsim').exists(): return q/'qsim'
    q.mkdir(exist_ok=True)
    for f in (PRIOR/'qsim').iterdir():
        if f.suffix in ('.c','.h') or f.name=='Makefile':shutil.copy2(f,q/f.name)
    p=q/'gpu.c';s=p.read_text()
    old='| (g->cfg.cpu_ring ? 0x80u : 0)'
    assert s.count(old)==1
    s=s.replace(old,old+'\n             | (getenv("SM64_GEOMETRY_CAP") ? 0x400u : 0)')
    p.write_text(s)
    p=q/'main.c';s=p.read_text()
    old='    size_t cap_rows = 4096, nrows = 0;'
    assert s.count(old)==1
    s=s.replace(old,'''    snprintf(path,sizeof path,"%s/geometry.csv",out);
    FILE *geometry_csv=fopen(path,"w");
    if(!geometry_csv)abort();
    fprintf(geometry_csv,"frame,batches,vertices,fallbacks,triangles\\n");
'''+old)
    old='                if (profile && (nrows==360'
    assert s.count(old)==1
    s=s.replace(old,'''                const char *names[]={"sm64_geometry_batches","sm64_geometry_vertices",
                    "sm64_geometry_fallbacks","sm64_geometry_triangles"};
                uint32_t counts[4]={0};
                for(unsigned gi=0;gi<4;gi++) {
                    const qsim_sym *sym=qsim_syms_lookup(&E.syms,names[gi]);
                    if(sym)memcpy(counts+gi,qsim_mem_ptr(E.cpu,sym->addr,4),4);
                }
                fprintf(geometry_csv,"%llu,%u,%u,%u,%u\\n",(unsigned long long)fr->seq,
                    counts[0],counts[1],counts[2],counts[3]);
'''+old)
    s=s.replace('    fclose(scene_csv);','    fclose(scene_csv);\n    fclose(geometry_csv);')
    p.write_text(s)
    with (OUT/'qsim-build.log').open('w') as log:
        subprocess.run(['make','-j3'],cwd=q,stdout=log,stderr=subprocess.STDOUT,check=True)
    return q/'qsim'


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('label')
    p.add_argument('--elf',type=Path,required=True)
    p.add_argument('--no-geometry-cap',action='store_true')
    p.add_argument('--vectors',default='40,120,200,360,400,500,600,720')
    a=p.parse_args()
    exe=prepare();dest=OUT/('capture-'+a.label);dest.mkdir(exist_ok=False)
    elf=dest/'sm64.elf';shutil.copy2(a.elf,elf)
    assets=OUT/'SM64/build/pocket/sm64/Assets/sm64/common'
    cmd=[str(exe),str(elf),'--app',str(PRIOR/'sm64.app'),'--data-dir',str(assets),
         '--out',str(dest),'--frames','721','--profile','--gpuvec',a.vectors,
         '--gpu-sched','eager','--max-insns','5000000000']
    env=os.environ.copy();env.pop('SM64_GEOMETRY_CAP',None)
    if not a.no_geometry_cap:env['SM64_GEOMETRY_CAP']='1'
    inputs=dict(command=cmd,geometry_capability=not a.no_geometry_cap,success=False,
        sha256={str(f):hashlib.sha256(f.read_bytes()).hexdigest()
            for f in (exe,elf,PRIOR/'sm64.app',assets/'bank.ofsf')})
    (dest/'inputs.json').write_text(json.dumps(inputs,indent=2)+'\n')
    with (dest/'run.log').open('w') as log:
        subprocess.run(cmd,env=env,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=900)
    rows=list(csv.DictReader((dest/'scene.csv').open()))
    assert len(rows)==721
    assert all(int(r['level'])==9 and int(r['area'])==1 and int(r['action'],16)!=0 for r in rows[360:])
    inputs['success']=True
    (dest/'inputs.json').write_text(json.dumps(inputs,indent=2)+'\n')
    print(dest)


if __name__=='__main__':main()
