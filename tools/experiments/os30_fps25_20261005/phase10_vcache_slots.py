#!/usr/bin/env python3
"""Exercise upper slots, bank isolation and duplicate indices on actual GPU RTL."""
import concurrent.futures
import hashlib
import json
from pathlib import Path
import re
import struct
import subprocess

ROOT=Path(__file__).resolve().parents[3]
OUT=ROOT/'docs/measurements/os30-fps25-20261005/phase10/vcache-narrow-v1'
DEST=OUT/'slot-tests'

def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def write(p,x):p.write_text(json.dumps(x,indent=2)+'\n')
def packet(words):return struct.pack('<'+'I'*len(words),*words)

def adapt(data, mode):
    pos=0;out=bytearray();stats=dict(loads=0,triangles=0,shadow_loads=0,duplicate_triangles=0)
    slots=set();indices=set()
    while pos<len(data):
        start=pos;tag=data[pos:pos+4];pos+=4
        if tag==b'CMDS':
            count,=struct.unpack_from('<I',data,pos);pos+=4;end=pos+count*4;new=[]
            while pos<end:
                h,=struct.unpack_from('<I',data,pos);n=1+(h&8191);w=list(struct.unpack_from('<'+'I'*n,data,pos));pos+=n*4
                op=h>>24
                if op==0x56:
                    assert n==9 and w[1]&31<16
                    stats['loads']+=1
                    if mode=='upper':w[1]^=16
                    slots.add(w[1]&31)
                    new+=w
                    if mode=='shadow':
                        # Contrasting attributes in a different cache slot must
                        # not corrupt any lower slot, including its depth/UV.
                        w=w.copy();w[1]^=16;w[5]^=0xffffffff;w[6]^=0x80010000;w[7]^=0xffff;w[8]^=0x13579bdf
                        slots.add(w[1]&31);new+=w;stats['shadow_loads']+=1
                elif op==0x54:
                    assert n==2
                    old=[(w[1]>>s)&31 for s in (0,5,10)]
                    assert max(old)<16
                    stats['triangles']+=1
                    if mode=='upper':w[1]^=(16|(16<<5)|(16<<10))
                    else:
                        # Fully duplicate index triples (including upper bank)
                        # are zero-area. Their fetches must not disturb the
                        # following ordinary triangle's attribute staging.
                        for v in (old[0],old[0]+16):
                            new += [h, v|(v<<5)|(v<<10)]
                            indices.add(v);stats['duplicate_triangles']+=1
                    indices.update((w[1]>>s)&31 for s in (0,5,10));new+=w
                else:new+=w
            assert pos==end
            # Preserve publication ordering and do not exceed the unchanged
            # 4095-word ring packet contract when shadow work enlarges a batch.
            block=[];i=0
            while i<len(new):
                n=1+(new[i]&8191)
                if len(block)+n>4095:
                    out+=b'CMDS'+struct.pack('<I',len(block))+packet(block);block=[]
                block+=new[i:i+n];i+=n
            if block:out+=b'CMDS'+struct.pack('<I',len(block))+packet(block)
            continue
        elif tag in (b'MEM1',b'TEX1'):
            addr,n=struct.unpack_from('<II',data,pos);pos+=8+((n+3)&~3)
        elif tag in (b'MMIO',b'DUMP'):pos+=8
        elif tag==b'RAND':pos+=12
        elif tag==b'PALB':pos+=4
        elif tag==b'TLUT':pos+=32768
        else:assert tag in (b'FLSH',b'FENC',b'HASH',b'END.'),tag
        out+=data[start:pos]
    stats.update(loaded_slots=sorted(slots),indexed_slots=sorted(indices))
    return bytes(out),stats


def main():
    DEST.mkdir(exist_ok=False)
    source=OUT/'replays/frame_00360.vec'
    inputs={}
    for mode in ('upper','shadow'):
        p=DEST/(mode+'.vec');data,stats=adapt(source.read_bytes(),mode);p.write_bytes(data)
        inputs[mode]=dict(sha256=sha(p),stats=stats)
    write(DEST/'inputs.json',dict(source_sha256=sha(source),variants=inputs))
    sims={'baseline':ROOT/'docs/measurements/os30-fps25-20261005/phase4/texmem/extended/model/obj/Vtb_gpu','narrow':OUT/'model/obj/Vtb_gpu'}
    def one(job):
        mode,model,traffic=job
        stem=DEST/f'{mode}-{model}-{traffic}'
        cmd=[str(sims[model]),str(DEST/(mode+'.vec')),str(stem.with_suffix('.bin')),f'+memory_traffic={traffic}','+tagged_tex=1']
        with stem.with_suffix('.log').open('x') as log:subprocess.run(cmd,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=600)
        raw=stem.with_suffix('.bin').read_bytes();assert len(raw)==307212
        stats={k:int(v) for k,v in re.findall(r'(\w+)=(\d+)',stem.with_suffix('.log').read_text())}
        assert stats['cram_errors']==0 and stats['words']==stats['readback_words']
        return dict(mode=mode,model=model,traffic=traffic,stats=stats,output_sha256=sha(stem.with_suffix('.bin')),log_sha256=sha(stem.with_suffix('.log')),simulator_sha256=sha(sims[model]),command=cmd)
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        rows=list(pool.map(one,[(m,s,t) for m in inputs for s in sims for t in (0,1,2)]))
    checks=[]
    for mode in inputs:
        for traffic in (0,1,2):
            b=(DEST/f'{mode}-baseline-{traffic}.bin').read_bytes();c=(DEST/f'{mode}-narrow-{traffic}.bin').read_bytes();ref=(OUT/f'replays/baseline/{traffic}/frame_00360.bin').read_bytes()
            select=lambda a:a[:153604]+a[153612:]
            checks.append(dict(mode=mode,traffic=traffic,pixel_depth_fence_exact=select(b)==select(c),original_frame_exact=select(b)==select(ref)))
    write(DEST/'result.json',dict(passed=all(r['pixel_depth_fence_exact'] and r['original_frame_exact'] for r in checks),checks=checks,runs=rows,recipe_sha256=sha(Path(__file__))))
    assert all(r['pixel_depth_fence_exact'] and r['original_frame_exact'] for r in checks)
    print(json.dumps(checks))

if __name__=='__main__':main()
