#!/usr/bin/env python3
"""Capture and rank a separately packed private ELF, preserving linked ELF."""
import argparse
import csv
import json
from pathlib import Path
import sys
from phase10_experiment import CAMP,ROOT,module,sha,simulator,verify_capture,write
from phase8_experiment import canonical
BASE=CAMP/'phase9/checks-state-f11'

def capture(dest):
    packing=json.loads((dest/'packing.json').read_text())
    assert sha(dest/'packed.elf')==packing['packed_elf_sha256']
    assert sha(dest/'app.elf')==packing['input_elf_sha256']
    m=module('packed_capture',ROOT/'tools/experiments/sm64_batch_cull_20261005/validation/capture.py')
    m.OUT=dest
    sys.argv=[str(Path(__file__)),'packed-normal','--elf',str(dest/'packed.elf'),'--exe',str(simulator()),'--geometry','0','--audio']
    m.main()

def analyze(dest):
    before=BASE/'capture-normal';after=dest/'capture-packed-normal'
    a=verify_capture(before,sha(BASE/'app.elf'));b=verify_capture(after,sha(dest/'packed.elf'))
    assert len(a)==len(b)==721
    same={name:sha(before/name)==sha(after/name) for name in ['scene.csv','geometry.csv','culling.csv']}
    counts=[z['gpu_cmd_words'] for z in a]==[z['gpu_cmd_words'] for z in b]
    totals={}
    for key in ['insns','est_cycles','gpu_cmd_words','icache_miss_sdram','dcache_miss','mispredict']:
        x,y=(sum(int(z[key]) for z in rows[360:]) for rows in [a,b])
        totals[key]=dict(baseline=x,candidate=y,saving_percent=(1-y/x)*100)
    windows=[]
    for lo,hi in [(360,480),(480,600),(600,721)]:
        x,y=(sum(int(z['est_cycles']) for z in rows[lo:hi]) for rows in [a,b])
        windows.append(dict(first=lo,last=hi-1,baseline=x,candidate=y,saving_percent=(1-y/x)*100))
    v=module('packed_vectors',Path(__file__).with_name('phase7_validate.py'));vectors=[]
    for p in sorted((before/'gpuvec').glob('*.vec')):
        q=after/'gpuvec'/p.name;old,new=(v.v.vector(x.read_bytes()) for x in [p,q])
        vectors.append(dict(frame=p.name,ordered_command_mmio_exact=canonical(old)[0]==canonical(new)[0],depth_exact=old['depth']==new['depth'],fence_exact=old['fence']==new['fence'],uploads_exact=old['uploads']==new['uploads'],texture_exact=old['texture']==new['texture'],baseline_sha256=sha(p),candidate_sha256=sha(q)))
    result=dict(candidate=dest.name,scope='Private packed ELF eager CPU cost probe. No hardware timing/FPS inference; no publication qualification.',source_elf_sha256=sha(dest/'app.elf'),packed_elf_sha256=sha(dest/'packed.elf'),packing_sha256=sha(dest/'packing.json'),scene_geometry_culling_exact=same,all721_command_counts_exact=counts,gameplay_frames360_through720=totals,windows=windows,vectors=vectors,helper_sha256=sha(Path(__file__)),qualified=False)
    write(dest/'packed-analysis.json',result)
    print(json.dumps({k:v for k,v in result.items() if k!='vectors'},indent=2))
    print('vector checks:',json.dumps([{k:v for k,v in r.items() if k.endswith('_exact') or k=='frame'} for r in vectors]))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('action',choices=['capture','analyze']);p.add_argument('candidate');a=p.parse_args()
    dest=CAMP/'phase10'/a.candidate;assert dest.is_dir() and '/' not in a.candidate
    globals()[a.action](dest)
