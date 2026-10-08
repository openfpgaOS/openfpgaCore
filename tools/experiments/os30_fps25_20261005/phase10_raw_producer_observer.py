#!/usr/bin/env python3
"""Observe actual private dry descriptor buffers without guest-side overhead."""
import argparse
import csv
import json
from pathlib import Path
import shutil
import struct
import subprocess
import sys
from elftools.elf.elffile import ELFFile
from phase10_experiment import CAMP,ROOT,module,sha,write

OUT=CAMP/'phase10/raw-producer-observer'
BASE=CAMP/'phase10/raw-producer-v1'
SOURCE=CAMP/'phase5/qsim'
C=r'''
                const qsim_sym *rs=qsim_syms_lookup(&E.syms,"raw_dry_stats");
                if(rs) {
                    if(!raw_dry_csv) {
                        snprintf(path,sizeof path,"%s/raw-dry.csv",out);
                        raw_dry_csv=fopen(path,"w");fprintf(raw_dry_csv,"frame,words");
                        for(unsigned k=0;k<32;k++)fprintf(raw_dry_csv,",stat%u",k);
                        fputc('\n',raw_dry_csv);
                        snprintf(path,sizeof path,"%s/raw-dry.bin",out);
                        raw_dry_bin=fopen(path,"wb");if(!raw_dry_bin)abort();
                    }
                    uint32_t data[32],words=0;
                    const qsim_sym *rw=qsim_syms_lookup(&E.syms,"raw_dry_words");
                    const qsim_sym *rb=qsim_syms_lookup(&E.syms,"raw_dry_streams");
                    if(!rw||!rb)abort();
                    memcpy(data,qsim_mem_ptr(E.cpu,rs->addr,sizeof data),sizeof data);
                    memcpy(&words,qsim_mem_ptr(E.cpu,rw->addr,4),4);
                    if(data[11]>=3||words>8192)abort();
                    uint32_t header[4]={0x52415731u,fr->seq,data[11],words};
                    fwrite(header,sizeof header,1,raw_dry_bin);
                    fwrite(data,sizeof data,1,raw_dry_bin);
                    fwrite(qsim_mem_ptr(E.cpu,rb->addr+data[11]*8192*4,words*4),4,words,raw_dry_bin);
                    fprintf(raw_dry_csv,"%llu,%u",(unsigned long long)fr->seq,words);
                    for(unsigned k=0;k<32;k++)fprintf(raw_dry_csv,",%u",data[k]);
                    fputc('\n',raw_dry_csv);
                }
'''

def prepare():
    OUT.mkdir(exist_ok=False);q=OUT/'qsim';q.mkdir()
    for p in SOURCE.iterdir():
        if p.suffix in ('.c','.h') or p.name=='Makefile':shutil.copy2(p,q/p.name)
    p=q/'main.c';s=p.read_text()
    for old,new in [('    FILE *culling_csv=NULL;','    FILE *culling_csv=NULL;\n    FILE *raw_dry_csv=NULL,*raw_dry_bin=NULL;'),
        ('                const qsim_sym *diagnostic=',C+'\n                const qsim_sym *diagnostic='),
        ('    if(culling_csv)fclose(culling_csv);','    if(culling_csv)fclose(culling_csv);\n    if(raw_dry_csv)fclose(raw_dry_csv);\n    if(raw_dry_bin)fclose(raw_dry_bin);')]:
        assert s.count(old)==1;s=s.replace(old,new)
    p.write_text(s)
    with (OUT/'build.log').open('w') as f:subprocess.run(['make','-j4'],cwd=q,stdout=f,stderr=subprocess.STDOUT,check=True)
    write(OUT/'observer.json',dict(elf_sha256=sha(BASE/'app.elf'),helper_sha256=sha(Path(__file__)),simulator_sha256=sha(q/'qsim'),source_sha256={p.name:sha(p) for p in q.iterdir() if p.suffix in ('.c','.h') or p.name=='Makefile'}))

def capture():
    m=module('raw_dry_capture',ROOT/'tools/experiments/sm64_batch_cull_20261005/validation/capture.py');m.OUT=OUT
    sys.argv=[str(Path(__file__)),'normal','--elf',str(BASE/'app.elf'),'--exe',str(OUT/'qsim/qsim'),'--geometry','0','--audio'];m.main()

def analyze():
    with (BASE/'app.elf').open('rb') as f:
        e=ELFFile(f);t=e.get_section_by_name('.text');lo=t['sh_addr'];data=t.data();hi=lo+len(data)
    def memory(address,size):
        assert lo<=address<=hi-size,(hex(address),size)
        return data[address-lo:address-lo+size]
    blob=(OUT/'capture-normal/raw-dry.bin').read_bytes();offset=0;frames=[];previous=[0]*32
    while offset<len(blob):
        magic,frame,slot,n=struct.unpack_from('<4I',blob,offset);offset+=16
        assert magic==0x52415731 and slot<3 and n<=8192
        stats=list(struct.unpack_from('<32I',blob,offset));offset+=128
        words=struct.unpack_from('<%dI'%n,blob,offset);offset+=4*n
        assert not stats[9] and not stats[15]
        delta=[a-b for a,b in zip(stats,previous)];previous=stats
        present=owned=cursor=triangles=vertices=0;ops={i:0 for i in range(1,6)}
        while cursor<len(words):
            w=words[cursor];op=w>>24;ops[op]+=1;cursor+=1
            if op in (1,2,3):
                assert w&0xffffff=={1:17,2:22,3:3}[op]
                present|=1<<op;cursor+=w&0xffffff
            elif op==4:
                assert present==14
                count=w&255;first=w>>8&255;assert count and first+count<=32
                memory(words[cursor],24*count);cursor+=1
                owned|=((1<<count)-1)<<first;vertices+=count
            elif op==5:
                commands=w&0xffffff;address,nt=words[cursor:cursor+2];cursor+=2
                source=memory(address,8*commands);actual=0
                for k in range(commands):
                    a,b=struct.unpack_from('<2I',source,k*8);opcode=a>>24
                    assert opcode in (5,6)
                    for tri in [a] if opcode==5 else [a,b]:
                        for shift in [16,8,0]:
                            index=(tri>>shift&255)//2;assert index<32 and owned&(1<<index)
                        actual+=1
                assert actual==nt;triangles+=nt
            else:raise AssertionError(op)
            assert cursor<=len(words)
        assert [ops[k] for k in range(1,6)]==delta[1:6],(frame,ops,delta)
        assert n==delta[8] and triangles==delta[16] and vertices==delta[13]
        frames.append(dict(frame=frame,words=n,triangles=triangles,vertices=vertices,operations=ops,stats=delta))
    assert len(frames)==721
    selected=frames[360:];nf=len(selected)
    metrics={k:sum(r[k] for r in selected) for k in ['words','triangles','vertices']}
    metrics['operations']={str(k):sum(r['operations'][k] for r in selected) for k in range(1,6)}
    metrics['bytes_per_frame']=metrics['words']*4/nf
    metrics['max_words_per_frame']=max(r['words'] for r in frames)
    metrics['stats']=[sum(r['stats'][k] for r in selected) for k in range(32)]
    write(OUT/'stream-validation.json',dict(passed=True,frames=len(frames),gameplay=metrics,elf_sha256=sha(BASE/'app.elf'),capture_sha256=sha(OUT/'capture-normal/inputs.json'),observer_sha256=sha(OUT/'observer.json'),binary_sha256=sha(OUT/'capture-normal/raw-dry.bin'),helper_sha256=sha(Path(__file__)),limits=['Owned immutable LOAD/RUN contexts only; material snapshots and GPU transport absent.','Dry completion uses existing renderer; real implementation needs accelerator completion fence.','Source references checked against immutable ELF, not shared live DMA.','Observer verifies descriptor syntax/source ownership; no geometry consumer or acceleration claim.']))
    print(json.dumps(metrics,indent=2))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('action',choices=['prepare','capture','analyze']);a=p.parse_args();globals()[a.action]()
