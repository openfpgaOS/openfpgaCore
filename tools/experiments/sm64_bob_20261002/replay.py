#!/usr/bin/env python3
"""Replay first-level frames on frozen baseline and opt-in os30 GPU RTL models."""
import concurrent.futures
import hashlib
import json
from pathlib import Path
import re
import subprocess

ROOT=Path(__file__).resolve().parents[3]
OUT=ROOT/'docs/measurements/sm64-bob-20261002'
PRIOR=OUT.parent/'os30-depth-prefetch-20261002'
models={'baseline':PRIOR/'replay-baseline', 'prefetch':PRIOR/'replay-final'}
vectors=sorted((OUT/'capture/gpuvec').glob('*.vec'))
assert len(vectors)==17

def run(args):
    label,mode,vec=args
    exe=models[label]/'obj/Vtb_gpu'
    dest=OUT/'replays'/label/str(mode)
    dest.mkdir(parents=True,exist_ok=True)
    stem=dest/vec.stem
    cmd=[str(exe),str(vec),str(stem.with_suffix('.bin')),f'+memory_traffic={mode}']
    with stem.with_suffix('.log').open('w') as log:
        subprocess.run(cmd,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=600)
    log=stem.with_suffix('.log').read_text()
    stats={k:int(v) for k,v in re.findall(r'(\w+)=(\d+)',log)}
    counters={kind:{int(k):int(v) for k,v in re.findall(rf'^BN {kind} (\d+) (\d+)$',log,re.M)}
              for kind in ('state','fbss','cstate')}
    for kind in ('fp','mem'):
        match=re.search(rf'^BN {kind} (.+)$',log,re.M)
        counters[kind]={k:int(v) for k,v in re.findall(r'(\w+) (\d+)',match[1])}
    row=dict(label=label,mode=mode,frame=int(vec.stem.split('_')[1]),stats=stats,counters=counters,
        output_sha256=hashlib.sha256(stem.with_suffix('.bin').read_bytes()).hexdigest(),
        vector_sha256=hashlib.sha256(vec.read_bytes()).hexdigest())
    print(label,mode,row['frame'],stats,flush=True)
    return row

(OUT/'replay-inputs.json').write_text(json.dumps({label:{'executable':str(p/'obj/Vtb_gpu'),
    'sha256':hashlib.sha256((p/'obj/Vtb_gpu').read_bytes()).hexdigest(),
    'manifest_sha256':hashlib.sha256((p/'manifest.json').read_bytes()).hexdigest()}
    for label,p in models.items()},indent=2)+'\n')
rows=[]
with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
    for row in pool.map(run,[(label,mode,v) for label in models for mode in (0,1,2) for v in vectors]):
        rows.append(row)
        (OUT/'replay-results.json').write_text(json.dumps(rows,indent=2)+'\n')
baseline={(r['mode'],r['frame']):r for r in rows if r['label']=='baseline'}
for row in (r for r in rows if r['label']=='prefetch'):
    before=baseline[row['mode'],row['frame']]
    assert row['vector_sha256']==before['vector_sha256']
    assert row['output_sha256']==before['output_sha256'], ('rendering mismatch',row['mode'],row['frame'])
print('PASS: all 51 paired framebuffer, fence and full-memory hashes match.',flush=True)
