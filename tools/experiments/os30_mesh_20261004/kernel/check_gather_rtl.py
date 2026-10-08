#!/usr/bin/env python3
"""GATHER50 versus independent clip classification and original indexed data."""
import hashlib
import json
from pathlib import Path
import random
import struct
import subprocess
import sys

from reference import word
HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE.parent/'client'))
from mesh_transform import OUT,read,transform_vertex,clip_code


def main():
    dest=HERE/'results/gather-rtl-final';dest.mkdir(exist_ok=False)
    count=scene=0
    with (dest/'vectors.bin').open('wb') as f:
        def add(vertices,ordinal,packed):
            nonlocal count
            index=(packed>>((ordinal&1)*16))&32767
            selected=[vertices[(index>>(5*i))&31] for i in range(3)]
            codes=[clip_code(v) for v in selected]
            malformed=any(not -(1<<29)<=x<=(1<<29) for v in selected for x in v[:4])
            values=[ordinal,packed,*[x for v in selected for x in v],codes[0]|codes[1]|codes[2]|(int(malformed)<<31),codes[0]&codes[1]&codes[2]]
            f.write(struct.pack('<25I',*map(word,values)));count+=1
        for path in sorted((OUT/'run-360-8').glob('mesh-*.bin')):
            _,jobs=read(path)
            for job in jobs:
                vertices=[transform_vertex(job['vertices'][i*4:i*4+4],job['state']) for i in range(job['header'][3])]
                indices=job['indices']
                for i,index in enumerate(indices):
                    lo=indices[i&~1];hi=indices[(i&~1)+1] if (i&~1)+1<len(indices) else 0
                    add(vertices,i,lo|(hi<<16));scene+=1
        rng=random.Random(0x50061004)
        for n in range(10000):
            vertices=[[rng.randrange(-2**31,2**31) for _ in range(7)] for _ in range(32)]
            if n%5==0:
                # Coincident/on-plane points and INT_MIN W probe strict
                # inequalities and the full33-bit negation that scalar32 lacks.
                w=[-2**31,-1,0,1,2**31-1][(n//5)%5]
                for v in vertices:v[0:4]=[w,0,w,w]
            add(vertices,n%64,rng.getrandbits(32))
    sources=[HERE/'gpu_mesh_gather.v',HERE/'gather_tb.v',HERE/'gather_test.cpp']
    for p in sources:(dest/p.name).write_bytes(p.read_bytes())
    command=['verilator','--cc','--exe','--build','-j','2','-Wno-fatal','--top-module','gather_tb',
             '--Mdir',str(dest/'obj'),'-CFLAGS','-std=c++17 -O2',*[str(dest/p.name) for p in sources]]
    with (dest/'build.log').open('w') as log:subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,check=True)
    done=subprocess.run([str(dest/'obj/Vgather_tb'),str(dest/'vectors.bin')],capture_output=True,text=True)
    (dest/'run.log').write_text(done.stdout+done.stderr)
    if done.returncode:raise RuntimeError(done.stdout+done.stderr)
    result=dict(passed=True,cases=count,scene_triangles=scene,result=done.stdout,
                sources={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sources})
    (dest/'result.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))


if __name__=='__main__':main()
