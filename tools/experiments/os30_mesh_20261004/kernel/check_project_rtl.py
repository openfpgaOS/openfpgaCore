#!/usr/bin/env python3
"""Projected outputs versus RAW worker integer reference across meshes/ranges."""
import hashlib
import json
from pathlib import Path
import random
import struct
import subprocess
import sys

from project_reference import project
from reference import word
HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE.parent/'client'))
from mesh_transform import OUT,read,transform_vertex,project as client_project


def main():
    dest=HERE/'results/project-rtl';dest.mkdir(exist_ok=False)
    count=scene=0
    with (dest/'vectors.bin').open('wb') as f:
        def add(vertex,view):
            nonlocal count
            expected=project(vertex,view)
            f.write(struct.pack('<18I',*map(word,vertex+view+expected)));count+=1
        for path in sorted((OUT/'run-360-8').glob('mesh-*.bin')):
            _,jobs=read(path)
            for job in jobs:
                state=job['state'];vx,vy=state[28]&65535,state[28]>>16
                width,height=state[29]&65535,state[29]>>16;hw,hh=width//2,height//2
                view=[vx+hw,(240-vy-height+hh)<<4,hw,hh<<4]
                for i in range(job['header'][3]):
                    vertex=transform_vertex(job['vertices'][i*4:i*4+4],state)
                    old=client_project(vertex,state);expected=project(vertex,view)
                    assert expected[:5]==[word(((old[1]&65535)<<16)|(old[0]&65535)),word(old[2]),word(old[3]),old[4],old[5]]
                    add(vertex,view);scene+=1
        rng=random.Random(0x47061004)
        for n in range(10000):
            vertex=[rng.getrandbits(32) for _ in range(7)]
            if n%5==0:vertex[3]=[0,1,255,256,257,0x7fffffff,0x80000000,0xffffffff][(n//5)%8]
            view=[rng.getrandbits(32) for _ in range(4)] if n%3 else [160,120<<4,160,120<<4]
            add(vertex,view)
    sources=[HERE/'gpu_mesh_project.v',HERE/'project_tb.v',HERE/'project_test.cpp']
    for p in sources:(dest/p.name).write_bytes(p.read_bytes())
    command=['verilator','--cc','--exe','--build','-j','2','-Wno-fatal','--top-module','project_tb',
             '--Mdir',str(dest/'obj'),'-CFLAGS','-std=c++17 -O2',*[str(dest/p.name) for p in sources]]
    with (dest/'build.log').open('w') as log:subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,check=True)
    done=subprocess.run([str(dest/'obj/Vproject_tb'),str(dest/'vectors.bin')],capture_output=True,text=True)
    (dest/'run.log').write_text(done.stdout+done.stderr)
    if done.returncode:raise RuntimeError(done.stdout+done.stderr)
    result=dict(passed=True,cases=count,scene_vertices=scene,result=done.stdout,
                sources={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sources})
    (dest/'result.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))


if __name__=='__main__':main()
