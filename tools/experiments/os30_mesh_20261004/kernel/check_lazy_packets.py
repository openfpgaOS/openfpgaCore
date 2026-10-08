#!/usr/bin/env python3
"""Capture old-capability GPU packets before/after lazy-path integration."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import argparse
import collections
import hashlib
import json
import os
import struct
import subprocess

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[3]
APP=ROOT/'docs/measurements/os30-mesh-20261004/SM64'

def packets(path):
    raw=path.read_bytes();p=0;words=[];memory=bytearray(64*1024*1024)
    while p<len(raw):
        tag=raw[p:p+4];p+=4
        if tag==b'MEM1':
            address,count=struct.unpack_from('<II',raw,p);p+=8
            memory[address:address+count]=raw[p:p+count];p+=(count+3)&~3
        elif tag==b'CMDS':
            count=struct.unpack_from('<I',raw,p)[0];p+=4
            batch=struct.unpack_from('<'+'I'*count,raw,p);p+=count*4
            at=0
            while at<count:
                header=batch[at];n=header&0xffffff;opcode=header>>24
                payload=list(batch[at+1:at+1+n]);assert len(payload)==n
                if opcode==0x4a:
                    width=payload[4];mask=payload[5]>>16
                    height=(payload[10]>>16)+1 if mask==65535 else mask+1
                    assert width<=4096 and height<=4096
                    address=payload[3]&0x3ffffff
                    texture=(hashlib.sha256(memory[address:address+width*height*2]).hexdigest(),
                             hashlib.sha256(memory[address:address+min(2048,width*height*2)]).hexdigest())
                    payload[3]=texture
                    # Z storage relocates with app BSS; the state still uses
                    # the exact same stride and depth words. Assert a single
                    # logical nonzero depth allocation, not its ELF address.
                    payload[11]=bool(payload[11])
                words.append((opcode,payload));at+=n+1
        elif tag==b'PALB':p+=4
        elif tag==b'DUMP':p+=8
        elif tag in (b'FLSH',b'FENC',b'HASH',b'END.'):pass
        else:raise ValueError((tag,p))
    return words

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reuse-capture',action='store_true')
    args=parser.parse_args()
    out=HERE/'results/lazy-packets';out.mkdir(exist_ok=args.reuse_capture)
    inputs=[('before',APP/'mesh-shadow/material-snapshot.elf'),
            ('after',APP/'.obj/sm64-perfdiag/app.elf')]
    def run(item):
        label,source=item;dest=out/label;dest.mkdir()
        elf=dest/'app.elf';elf.write_bytes(source.read_bytes())
        cmd=[str(APP/'mesh-shadow/qsim/qsim'),str(elf),'--app',str(ROOT/'docs/measurements/sm64-bob-20261002/sm64.app'),
             '--data-dir',str(ROOT/'docs/measurements/sm64-device-perf-20261003/SM64/build/pocket/sm64/Assets/sm64/common'),
             '--out',str(dest),'--frames','368','--gpu-sched','eager','--max-insns','5000000000','--gpuvec','360,367']
        env={**os.environ,'SM64_GEOMETRY_CAP':'1','SM64_AUDIO':'1','MESH_CAPTURE_START':'360'}
        for key in ('SM64_WORKER_CAP','SM64_ATTRIBUTES_CAP','SM64_COMMAND_DMA','SM64_PERF_PANEL','SM64_MESH_CAP'):env.pop(key,None)
        (dest/'inputs.json').write_text(json.dumps(dict(command=cmd,elf_sha256=hashlib.sha256(elf.read_bytes()).hexdigest()),indent=2)+'\n')
        with (dest/'run.log').open('w') as log:subprocess.run(cmd,env=env,stdout=log,stderr=subprocess.STDOUT,check=True)
        return dest
    if not args.reuse_capture:
        with ThreadPoolExecutor(max_workers=2) as pool:list(pool.map(run,inputs))
    results=[]
    for frame in (360,367):
        left=packets(out/'before/gpuvec'/f'frame_{frame:05}.vec')
        right=packets(out/'after/gpuvec'/f'frame_{frame:05}.vec')
        assert len(left)==len(right)
        padding=[]
        for i,(a,b) in enumerate(zip(left,right)):
            if a==b:continue
            # Original SM64 star display list deliberately loads32x64 from
            # its32x32 asset. The dead tail contains relocated code pointers.
            # Verify the asset prefix and every used indexed vertex UV before
            # excluding this known source overread from rendering comparison.
            assert a[0]==b[0]==0x4a and a[1][4:6]==b[1][4:6]==[32,0x003f001f]
            assert a[1][3][1]==b[1][3][1]
            slots={};used=[]
            for op,payload in left[i+1:]:
                if op==0x4a:break
                if op==0x56:
                    slots[payload[0]]=(payload[4],payload[5])
                elif op==0x54:
                    used.extend(slots[(payload[0]>>shift)&31] for shift in (0,5,10))
                else:assert op in (0x02,0x42),hex(op)
            assert used and all(0<=s<32*65536 and 0<=t<32*65536 for s,t in used)
            padding.append(dict(packet=i,source='actors/star/model.inc.c:59',
                before_full_texture_sha256=a[1][3][0],after_full_texture_sha256=b[1][3][0],
                used_texture_sha256=a[1][3][1],referenced_vertices=len(used),
                maximum_u=max(x[0] for x in used)/65536,maximum_v=max(x[1] for x in used)/65536))
            a[1][3]=b[1][3]=('star asset; unused source overread tail excluded',a[1][3][1])
            assert a==b
        assert left==right
        row=dict(frame=frame,packets=len(left),opcodes=dict(collections.Counter(hex(x[0]) for x in left)),
                 normalized_packet_sha256=hashlib.sha256(repr(left).encode()).hexdigest(),
                 source_padding_exceptions=padding)
        print(row);results.append(row)
    (out/'result.json').write_text(json.dumps(results,indent=2)+'\n')

if __name__=='__main__':main()
