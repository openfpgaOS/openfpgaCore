#!/usr/bin/env python3
"""Actual shared-DSP plane sequencer versus original-RTL scenes and full ranges."""
import hashlib
import argparse
import json
from pathlib import Path
import random
import struct
import subprocess

from check_microcode import records
from reference import decode_inputs, order, sat32, signed, word

HERE=Path(__file__).resolve().parent


def contexts(inputs):
    vertices,clip,options=decode_inputs(inputs)
    vertices=order(vertices)
    attrs=[[signed((v.s*v.zi)>>16),signed((v.t*v.zi)>>16),v.zi] for v in vertices]
    if options['normalize']:
        for _ in range(16):
            if any(not -(1<<28)<=a<(1<<28) for triplet in attrs for a in triplet):break
            attrs=[[signed(a<<1) for a in triplet] for triplet in attrs]
    for v,a in zip(vertices,attrs):
        a.extend([((v.rgb>>5)&63)<<16,((v.rgb>>11)&31)<<16,(v.rgb&31)<<16,v.depth])
        if options['combine']:
            a.extend([((v.additive>>11)&31)<<16,((v.additive>>5)&63)<<16,(v.additive&31)<<16])
    a,b,c=vertices;dx1,dx2,dy1,dy2=b.x-a.x,c.x-a.x,b.y-a.y,c.y-a.y
    det=dx1*dy2-dx2*dy1;mag=max(1,abs(det));small=options['subpixel'] and mag<=8192
    shift=31 if small else 44
    recip=min(((1<<shift)+(mag>>1))//mag,0x7fffffff)
    context=[dx1,dx2,dy1,dy2,recip,int(small),int(det<0),int(options['subpixel']),a.x,a.y]
    for values in zip(*attrs):
        yield [*map(word,values),*map(word,context),*plane(values,context)]


def plane(values,context):
    a0,a1,a2=values
    dx1,dx2,dy1,dy2,recip,small,negative,subpixel,x0,y0=context
    da1,da2=sat32(a1-a0),sat32(a2-a0)
    nu=16*(da1*dy2-da2*dy1)
    nv=(da2*dx1-da1*dx2)*(16 if subpixel else 1)
    sign=-1 if negative else 1;shift=31 if small else 44
    du=sat32(((nu*recip)>>shift)*sign);dv=sat32(((nv*recip)>>shift)*sign)
    origin=a0-((du*x0)>>4)-((dv*y0)>>(4 if subpixel else 0))+(du>>1)+(dv>>1)
    return list(map(word,(origin,du,dv)))


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--out-name',default='plane-rtl')
    args=parser.parse_args()
    dest=HERE/'results'/args.out_name;dest.mkdir(parents=True,exist_ok=False)
    count=0
    with (dest/'vectors.bin').open('wb') as f:
        for source in sorted((HERE/'results/reference').glob('*/kernel.bin')):
            for inputs,expected in records(source):
                vertices,clip,options=decode_inputs(inputs)
                for index,record in enumerate(contexts(inputs)):
                    # Compare final ABI-truncated raw plane to retained original
                    # RTL output wherever that triangle survives Y clipping.
                    if expected:
                        width=32 if index in (0,1,2,6) else 24
                        got=[word(signed(x,width)) for x in record[-3:]]
                        assert got==expected[10+index*3:13+index*3]
                    f.write(struct.pack('<16I',*record));count+=1
        rng=random.Random(0x461504)
        for trial in range(20000):
            values=[signed(rng.getrandbits(32)) for _ in range(3)]
            if trial%7==0:values=[-(1<<31),(1<<31)-1,0]
            context=[rng.randrange(-65535,65536) for _ in range(4)]
            context += [rng.getrandbits(31),rng.randrange(2),rng.randrange(2),rng.randrange(2),
                        rng.randrange(-32768,32768),rng.randrange(-32768,32768)]
            f.write(struct.pack('<16I',*map(word,values+context),*plane(values,context)));count+=1
    sources=[HERE/'gpu_mesh_plane.v',HERE/'plane_tb.v',HERE/'plane_test.cpp']
    for source in sources:(dest/source.name).write_bytes(source.read_bytes())
    command=['verilator','--cc','--exe','--build','-j','2','-Wno-fatal','--top-module','plane_tb',
             '--Mdir',str(dest/'obj'),'-CFLAGS','-std=c++17 -O2',*map(str,[dest/p.name for p in sources])]
    with (dest/'build.log').open('w') as log:subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,check=True)
    result=subprocess.run([str(dest/'obj/Vplane_tb'),str(dest/'vectors.bin')],capture_output=True,text=True)
    (dest/'run.log').write_text(result.stdout+result.stderr)
    if result.returncode:raise RuntimeError(result.stdout+result.stderr)
    summary=dict(passed=True,cases=count,result=result.stdout,sources={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sources})
    (dest/'result.json').write_text(json.dumps(summary,indent=2)+'\n');print(json.dumps(summary))


if __name__=='__main__':main()
