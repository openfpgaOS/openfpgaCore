#!/usr/bin/env python3
"""Render captures on the fitted geometry RTL plus real Pocket SDRAM path.

Append a depth-buffer dump to each vector. ELF addresses vary between builds,
so framebuffer and depth bytes are compared independently of full-memory hash.
CPU/GPU overlap is not modeled; GPU cycles are workload time, not game FPS.
"""
import argparse
import concurrent.futures
import hashlib
import json
from pathlib import Path
import re
import struct
import subprocess

ROOT=Path(__file__).resolve().parents[4]
OUT=ROOT/'docs/measurements/sm64-batch-cull-20261005/validation'

def with_depth(source,dest):
    data=source.read_bytes();pos=0;z=set();counts={}
    def u32():
        nonlocal pos
        v=struct.unpack_from('<I',data,pos)[0];pos+=4;return v
    while pos<len(data):
        tag=data[pos:pos+4];pos+=4
        if tag==b'MEM1':
            u32();n=u32();pos+=(n+3)&~3
        elif tag==b'TLUT':pos+=32768
        elif tag==b'PALB':pos+=4
        elif tag in (b'FLSH',b'FENC',b'HASH'):pass
        elif tag==b'DUMP':pos+=8
        elif tag==b'RAND':pos+=12
        elif tag==b'CMDS':
            n=u32();words=struct.unpack_from('<'+'I'*n,data,pos);pos+=4*n
            i=0
            while i<n:
                h=words[i];op=h>>24;size=h&8191
                assert i+1+size<=n,(source,i,n,size)
                counts[hex(op)]=counts.get(hex(op),0)+1
                if op==0x4a and size==17 and words[i+12]:z.add(words[i+12])
                i+=size+1
        elif tag==b'END.':
            assert pos==len(data)
            break
        else:raise ValueError((source,tag,pos))
    assert len(z)<=1,(source,z)
    depth=b'DUMP'+struct.pack('<II',next(iter(z)),320*240*2) if z else b''
    dest.write_bytes(data[:-4]+depth+b'END.')
    return counts,bool(z)

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('labels',nargs='+')
    p.add_argument('--modes',default='1')
    p.add_argument('--exe',type=Path,default=OUT/'replay-selected-dual/obj/Vtb_gpu')
    p.add_argument('--hardware-label',default='selected-dual')
    a=p.parse_args()
    assert re.fullmatch(r'[A-Za-z0-9_-]+',a.hardware_label)
    exe=a.exe.resolve()
    hardware_sha256=hashlib.sha256(exe.read_bytes()).hexdigest()
    suffix='' if a.hardware_label=='geometry' else '-'+a.hardware_label
    jobs=[]
    for label in a.labels:
        src=(ROOT/'docs/measurements/sm64-bob-20261002/capture') if label=='original' else OUT/('capture-'+label)
        if label!='original':
            assert json.loads((src/'inputs.json').read_text())['success'],('Incomplete capture',label)
        for mode in map(int,a.modes.split(',')):
            for vec in sorted((src/'gpuvec').glob('*.vec')):
                if label=='original' and int(vec.stem.split('_')[1]) not in (360,400,500,600,720):continue
                jobs.append((label,mode,vec))
    def run(job):
        label,mode,vec=job
        dest=OUT/('replays'+suffix)/label/str(mode);dest.mkdir(parents=True,exist_ok=True)
        stem=dest/vec.stem
        counts,depth=with_depth(vec,stem.with_suffix('.vec'))
        cmd=[str(exe),str(stem.with_suffix('.vec')),str(stem.with_suffix('.bin')),f'+memory_traffic={mode}']
        with stem.with_suffix('.log').open('w') as log:
            subprocess.run(cmd,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=600)
        log=stem.with_suffix('.log').read_text()
        stats={k:int(v) for k,v in re.findall(r'(\w+)=(\d+)',log)}
        data=stem.with_suffix('.bin').read_bytes()
        assert len(data)==153612+(153600 if depth else 0),(stem,len(data))
        row=dict(label=label,mode=mode,frame=int(vec.stem.split('_')[1]),stats=stats,
            hardware_label=a.hardware_label,hardware_sha256=hardware_sha256,
            framebuffer_sha256=hashlib.sha256(data[:153600]).hexdigest(),
            depth_sha256=hashlib.sha256(data[153612:]).hexdigest() if depth else None,
            vector_sha256=hashlib.sha256(vec.read_bytes()).hexdigest(),commands=counts)
        print(label,mode,row['frame'],stats,flush=True)
        return row
    rows=[]
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        for row in pool.map(run,jobs):
            rows.append(row)
            (OUT/('replay-results-'+'-'.join(a.labels)+suffix+'.json')).write_text(json.dumps(rows,indent=2)+'\n')

if __name__=='__main__':main()
