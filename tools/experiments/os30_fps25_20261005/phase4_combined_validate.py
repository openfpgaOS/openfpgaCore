#!/usr/bin/env python3
"""Compare the captured combined F6 application with F5 using retained A RTL."""
import argparse
import concurrent.futures
import csv
import hashlib
import json
from pathlib import Path
import re
import struct
import subprocess

ROOT=Path(__file__).resolve().parents[3]
CAMP=ROOT/'docs/measurements/os30-fps25-20261005'
OUT=CAMP/'phase4/combined'
CAP=CAMP/'phase4/texmem-capture'
RTL=CAMP/'phase4/texmem/extended'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
read=lambda p:json.loads(p.read_text())


def with_depth(source,dest):
    data=source.read_bytes();pos=0;z=set();counts={};tags={};ended=False
    def u32():
        nonlocal pos
        v=struct.unpack_from('<I',data,pos)[0];pos+=4;return v
    while pos<len(data):
        tag=data[pos:pos+4];pos+=4;tags[tag.decode('ascii')]=tags.get(tag.decode('ascii'),0)+1
        if tag in (b'MEM1',b'TEX1'):
            u32();n=u32();pos+=(n+3)&~3
        elif tag==b'MMIO':pos+=8
        elif tag==b'TLUT':pos+=32768
        elif tag==b'PALB':pos+=4
        elif tag in (b'FLSH',b'FENC',b'HASH'):pass
        elif tag==b'DUMP':pos+=8
        elif tag==b'RAND':pos+=12
        elif tag==b'CMDS':
            n=u32();words=struct.unpack_from(f'<{n}I',data,pos);pos+=4*n;i=0
            while i<n:
                h=words[i];op=h>>24;size=h&8191
                assert i+1+size<=n,(source,i,n,size)
                counts[hex(op)]=counts.get(hex(op),0)+1
                if op==0x4a and size==17 and words[i+12]:z.add(words[i+12])
                i+=size+1
        elif tag==b'END.':
            assert pos==len(data);ended=True;break
        else:raise ValueError((source,tag,pos))
        assert pos<=len(data)
    assert ended and len(z)<=1,(source,z)
    depth=b'DUMP'+struct.pack('<II',next(iter(z)),320*240*2) if z else b''
    dest.write_bytes(data[:-4]+depth+b'END.')
    return counts,bool(z),tags


def replay(mode,memory_modes):
    exe=RTL/'model/obj/Vtb_gpu';build=read(RTL/'model/build.json');assert sha(exe)==build['simulator_sha256']
    src=CAP/('capture-combined-'+mode);m=read(src/'inputs.json');assert m['success']
    for rel,h in m['results_sha256'].items():assert sha(src/rel)==h,rel
    jobs=[(traffic,vec)for traffic in memory_modes for vec in sorted((src/'gpuvec').glob('*.vec'))]
    def run(job):
        traffic,vec=job;dest=OUT/'replays'/mode/str(traffic);dest.mkdir(parents=True,exist_ok=True);stem=dest/vec.stem
        assert not stem.with_suffix('.log').exists(),stem
        commands,depth,tags=with_depth(vec,stem.with_suffix('.vec'))
        command=[str(exe),str(stem.with_suffix('.vec')),str(stem.with_suffix('.bin')),f'+memory_traffic={traffic}','+tagged_tex=1']
        with stem.with_suffix('.log').open('w') as f:
            subprocess.run(command,stdout=f,stderr=subprocess.STDOUT,check=True,timeout=600)
        log=stem.with_suffix('.log').read_text();stats={k:int(v)for k,v in re.findall(r'(\w+)=(\d+)',log)}
        data=stem.with_suffix('.bin').read_bytes();assert len(data)==153612+(153600 if depth else 0)
        parent=CAMP/'validation/replays-single-A'/('phase2-exact-v1-'+mode)/str(traffic)/(vec.stem+'.bin')
        old=parent.read_bytes();assert len(old)==len(data)
        assert data[:153600]==old[:153600],('Color changed',mode,traffic,vec.name)
        assert data[153612:]==old[153612:],('Depth changed',mode,traffic,vec.name)
        assert stats['cram_errors']==0,stats
        oldlog=parent.with_suffix('.log').read_text();before={k:int(v)for k,v in re.findall(r'(\w+)=(\d+)',oldlog)}
        return dict(mode=mode,traffic=traffic,frame=int(vec.stem.split('_')[1]),stats=stats,baseline_stats=before,
            gpu_cycle_saving_percent=100*(1-stats['cycles']/before['cycles']),
            color_depth_exact=True,depth_present=depth,commands=commands,tags=tags,
            vector_sha256=sha(vec),adapted_vector_sha256=sha(stem.with_suffix('.vec')),
            output_sha256=sha(stem.with_suffix('.bin')),baseline_output_sha256=sha(parent),
            log_sha256=sha(stem.with_suffix('.log')),simulator_sha256=sha(exe),command=command)
    rows=[]
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        for row in pool.map(run,jobs):
            rows.append(row);(OUT/f'replay-{mode}.json').write_text(json.dumps(rows,indent=2)+'\n')
            print(mode,row['traffic'],row['frame'],row['stats']['cycles'],round(row['gpu_cycle_saving_percent'],3),flush=True)


def analyze(mode):
    parent=CAMP/'validation'/('capture-phase2-exact-v1-'+mode)
    current=CAP/('capture-combined-'+mode)
    aspect=CAMP/'phase4/division'/('capture-aspect-'+mode)
    inputs=read(current/'inputs.json');assert inputs['success']
    for rel,h in inputs['results_sha256'].items():assert sha(current/rel)==h,rel
    exact={n:sha(parent/n)==sha(current/n)for n in ('scene.csv','geometry.csv','culling.csv')}
    assert all(exact.values()),exact
    frames=lambda p:list(csv.DictReader((p/'frames.csv').open()))
    a,b,c=frames(parent),frames(current),frames(aspect);assert len(a)==len(b)==len(c)==721
    totals={}
    for k in ('insns','est_cycles','gpu_cmd_words','icache_miss_sdram','dcache_miss','mispredict'):
        x,y,z=(sum(int(r[k])for r in rows[360:])for rows in (a,b,c))
        totals[k]=dict(f5=x,combined=y,aspect=z,saving_vs_f5_percent=100*(1-y/x),delta_vs_aspect=y-z)
    diagnostics={}
    if (current/'diagnostic.csv').exists():
        aa=list(csv.DictReader((parent/'diagnostic.csv').open()));bb=list(csv.DictReader((current/'diagnostic.csv').open()));assert len(aa)==len(bb)==721
        for x,y in zip(aa,bb):
            for k in x:
                if x[k]!=y[k]:diagnostics[k]=diagnostics.get(k,0)+1
        # Costs/FPS and ring/fence diagnostics can change; preserve gameplay using scene/culling above.
    result=dict(mode=mode,scene_geometry_culling_exact=exact,gameplay_frames360_through720=totals,
        diagnostic_changed_fields=diagnostics,capture_inputs_sha256=sha(current/'inputs.json'),
        baseline_inputs_sha256=sha(parent/'inputs.json'),aspect_inputs_sha256=sha(aspect/'inputs.json'),
        scope='Actual guest instruction counts; qsim CPU cost model and eager GPU. Fast upload busy is zero in qsim. Real controller cost is evaluated separately by RTL replay. No whole-device FPS claim.')
    (OUT/f'cpu-{mode}.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=['replay','analyze']);p.add_argument('mode',choices=['normal','fps']);p.add_argument('--memory-modes',default='1,2');a=p.parse_args()
    if a.action=='replay':replay(a.mode,list(map(int,a.memory_modes.split(','))))
    else:analyze(a.mode)
