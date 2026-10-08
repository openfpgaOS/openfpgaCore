#!/usr/bin/env python3
"""Correct the isolated capability fixture to match retained Pocket firmware."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import struct
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[3]
CAMP=ROOT/'docs/measurements/os30-fps25-20261005'
OLD=CAMP/'phase4/texmem-capture'
OUT=CAMP/'phase4/texmem-capture-v2'
REF=CAMP/'validation/capture-phase2-exact-v1-fps'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()


def prepare():
    manifest=json.loads((OLD/'manifest.json').read_text())
    assert sha(OLD/'qsim/qsim')==manifest['simulator_sha256']
    OUT.mkdir(parents=True,exist_ok=True);q=OUT/'qsim';q.mkdir(exist_ok=False)
    for name,h in manifest['sources'].items():
        assert sha(OLD/'qsim'/name)==h,name
        shutil.copy2(OLD/'qsim'/name,q/name)
    p=q/'env.c';s=p.read_text()
    old='0x00000900u, 100000000u, 0x10000000u, 0x50000000u, ENV_GPU_BASE, 0, 1,'
    assert s.count(old)==1
    s=s.replace(old,'0x00000900u, 100000000u, 0x10000000u, 0x50000000u, ENV_GPU_BASE, 0x01000000u, 1,')
    p.write_text(s)
    with (OUT/'build.log').open('w') as f:
        subprocess.run(['make','-j3'],cwd=q,stdout=f,stderr=subprocess.STDOUT,check=True)
    (OUT/'manifest.json').write_text(json.dumps(dict(parent_manifest_sha256=sha(OLD/'manifest.json'),
        simulator_sha256=sha(q/'qsim'),sources={name:sha(q/name)for name in manifest['sources']},
        script_sha256=sha(Path(__file__)),changed_source='env.c',changed_capability={'index':23,'name':'tex_fast_size','before':0,'after':16777216},
        firmware_evidence={str(p.relative_to(ROOT)):sha(p)for p in [ROOT/'src/firmware/os/kernel/caps_table.c',ROOT/'src/firmware/os/targets/pocket/target_platform.h']},
        scope='Same functional fast-memory model; fix advertised capacity to match A capability bit25. Busy is still functional/instantaneous; real upload timing belongs to RTL.'),indent=2)+'\n')


def vector_capability_only(before,after):
    a=before.read_bytes();b=after.read_bytes();assert len(a)==len(b)
    pos=0;changes=[]
    # Resolve the actual capability object address; field23 is+92 bytes.
    env=(OUT/'qsim/env.h').read_text()
    import re
    cap_addr=int(re.search(r'#define\s+ENV_CAPS_ADDR\s+(0x[0-9A-Fa-f]+)',env)[1],16)&0x03ffffff
    cap_offset=cap_addr+23*4
    while pos<len(a):
        tag=a[pos:pos+4];assert tag==b[pos:pos+4];pos+=4
        if tag==b'MEM1':
            address,n=struct.unpack_from('<II',a,pos);assert a[pos:pos+8]==b[pos:pos+8];pos+=8
            end=pos+((n+3)&~3)
            aa=bytearray(a[pos:end]);bb=bytearray(b[pos:end])
            if address<=cap_offset and cap_offset+4<=address+n:
                offset=cap_offset-address
                if aa[offset:offset+4]!=bb[offset:offset+4]:
                    assert struct.unpack_from('<I',aa,offset)[0]==0
                    assert struct.unpack_from('<I',bb,offset)[0]==0x01000000
                    changes.append(dict(vector_offset=pos+offset,sdram_offset=cap_offset))
                    bb[offset:offset+4]=aa[offset:offset+4]
            assert aa==bb,('Unexpected memory change',before.name,address)
            pos=end
        else:
            start=pos
            if tag==b'CMDS':n,=struct.unpack_from('<I',a,pos);pos+=4+4*n
            elif tag==b'TLUT':pos+=32768
            elif tag==b'PALB':pos+=4
            elif tag==b'DUMP':pos+=8
            elif tag==b'RAND':pos+=12
            elif tag in (b'FLSH',b'FENC',b'HASH',b'END.'):pass
            else:raise AssertionError(tag)
            assert a[start:pos]==b[start:pos],('Unexpected vector change',tag)
    assert changes and pos==len(a)
    return changes


def capture(label,elf):
    p=ROOT/'tools/experiments/sm64_batch_cull_20261005/validation/capture.py'
    spec=importlib.util.spec_from_file_location('capture_v2',p);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);m.OUT=OUT
    sys.argv=[str(Path(__file__)),label,'--elf',str(elf),'--exe',str(OUT/'qsim/qsim'),'--geometry','0','--audio'];m.main()
    if label=='f5-control':
        dest=OUT/('capture-'+label);identical={};vectors={}
        files=[REF/n for n in ['frames.csv','scene.csv','geometry.csv','diagnostic.csv','culling.csv']]+list(REF.glob('profile*.tsv'))
        for p in files:
            if p.is_file():
                rel=p.relative_to(REF);assert sha(p)==sha(dest/rel),str(rel);identical[str(rel)]=sha(p)
        for p in (REF/'gpuvec').glob('*.vec'):
            other=dest/'gpuvec'/p.name
            vectors[p.name]=dict(before_sha256=sha(p),after_sha256=sha(other),only_capability_changes=vector_capability_only(p,other))
        (OUT/'f5-noninterference.json').write_text(json.dumps(dict(passed=True,elf_sha256=sha(elf),
            identical_results=identical,vectors=vectors,manifest_sha256=sha(OUT/'manifest.json')) ,indent=2)+'\n')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=['prepare','capture']);p.add_argument('--label');p.add_argument('--elf',type=Path);a=p.parse_args()
    if a.action=='prepare':prepare()
    else:
        assert a.label and a.elf
        capture(a.label,a.elf)
