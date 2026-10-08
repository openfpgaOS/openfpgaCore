#!/usr/bin/env python3
"""Shared-arithmetic edge sequencer versus10k corrected original-RTL setups."""
import hashlib
import argparse
import json
from pathlib import Path
import struct
import subprocess

from reference import Vertex,order,signed,word

HERE=Path(__file__).resolve().parent


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--out-name',default='edges-rtl')
    args=parser.parse_args()
    dest=HERE/'results'/args.out_name;dest.mkdir(exist_ok=False)
    raw=(HERE/'results/edges/vectors.bin').read_bytes();words=struct.unpack('<'+'I'*(len(raw)//4),raw)
    offset=count=0
    with (dest/'vectors.bin').open('wb') as f:
        while offset<len(words):
            values=words[offset:offset+12];offset+=12
            vertices=order([Vertex(signed(values[1+2*i],16),signed(values[2+2*i],16),0,0,0,0,0) for i in range(3)])
            a,b,c=vertices;det=(b.x-a.x)*(c.y-a.y)-(c.x-a.x)*(b.y-a.y)
            expected=list(words[offset:offset+9]) if values[11] else [0]*9
            offset+=9 if values[11] else 0
            context=[a.x,a.y,b.x,b.y,c.x,c.y,values[9]|(values[10]<<16),values[0],word(det),word(det>>32)]
            header=[*context,values[7]|(values[8]<<16),values[11]]
            f.write(struct.pack('<21I',*map(word,header+expected)));count+=1
    sources=[HERE/'gpu_mesh_edges.v',HERE/'edges_tb.v',HERE/'edges_test.cpp']
    for p in sources:(dest/p.name).write_bytes(p.read_bytes())
    cmd=['verilator','--cc','--exe','--build','-j','2','-Wno-fatal','--top-module','edges_tb',
         '--Mdir',str(dest/'obj'),'-CFLAGS','-std=c++17 -O2',*[str(dest/p.name) for p in sources]]
    with (dest/'build.log').open('w') as log:subprocess.run(cmd,stdout=log,stderr=subprocess.STDOUT,check=True)
    done=subprocess.run([str(dest/'obj/Vedges_tb'),str(dest/'vectors.bin')],capture_output=True,text=True)
    (dest/'run.log').write_text(done.stdout+done.stderr)
    if done.returncode:raise RuntimeError(done.stdout+done.stderr)
    result=dict(passed=True,cases=count,result=done.stdout,sources={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sources})
    (dest/'result.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))


if __name__=='__main__':main()
