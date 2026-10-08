#!/usr/bin/env python3
"""Build an executable LOAD consumer from real produced context records."""
import csv
import json
from collections import Counter,defaultdict
from pathlib import Path
import struct
import subprocess
from elftools.elf.elffile import ELFFile
from phase10_experiment import CAMP,ROOT,sha,write
OUT=CAMP/'phase10/raw-consumer-capture';BASE=CAMP/'phase10/raw-producer-v2';CAP=OUT/'capture-normal'

def main():
    with (BASE/'app.elf').open('rb') as f:
        e=ELFFile(f);t=e.get_section_by_name('.text');lo=t['sh_addr'];data=t.data();hi=lo+len(data)
    def memory(a,n):
        assert lo<=a<=hi-n;return data[a-lo:a-lo+n]
    loaded=defaultdict(list);b=(CAP/'raw-loaded.bin').read_bytes();o=0
    while o<len(b):
        h=struct.unpack_from('<4I',b,o);o+=16;d=b[o:o+36*h[2]];o+=len(d);loaded[h[0]].append((h,d))
    b=(CAP/'raw-dry.bin').read_bytes();o=0;jobs=[]
    while o<len(b):
        magic,frame,slot,n=struct.unpack_from('<4I',b,o);o+=16+128
        assert magic==0x52415731
        w=struct.unpack_from('<%dI'%n,b,o);o+=n*4;c=0;ctx={};framejobs=[]
        while c<n:
            head=w[c];c+=1;op=head>>24
            if op in (1,2,3):
                count=head&0xffffff;ctx[op]=w[c:c+count];c+=count
            elif op==4:
                count=head&255;first=head>>8&255;address=w[c];c+=1
                framejobs.append(((frame,address,count,first),dict(ctx),memory(address,24*count)))
            elif op==5:c+=2
            else:raise AssertionError(op)
        assert len(framejobs)==len(loaded[frame]),(frame,len(framejobs),len(loaded[frame]))
        for (h,ctx,raw),(lh,expected) in zip(framejobs,loaded[frame]):
            assert h==lh,(h,lh);jobs.append(dict(header=h,context=ctx,raw=raw,expected=expected))
    payload=OUT/'consumer-input.bin'
    with payload.open('xb') as f:
        f.write(struct.pack('<I',len(jobs)))
        for j in jobs:
            f.write(struct.pack('<4I',*j['header']))
            for k in [1,2,3]:f.write(struct.pack('<%dI'%len(j['context'][k]),*j['context'][k]))
            f.write(j['raw']);f.write(j['expected'])
    # Bind LOAD records to original F11 cost observations in call order.
    rows=list(csv.DictReader((CAMP/'phase10/raw-frontend-observer/capture-normal/raw-frontend.csv').open()))
    source=[r for r in rows if r['kind']=='vertex' and int(r['immutable']) and int(r['first'])<32 and int(r['count'])<=32-int(r['first'])]
    assert len(source)==len(jobs),(len(source),len(jobs))
    selected=[]
    for j,r in zip(jobs,source):
        h=j['header'];assert (h[0],h[2],h[3])==(int(r['frame']),int(r['count']),int(r['first']))
        d=j['expected'];clip=[d[36*i+32] for i in range(h[2])];ws=[struct.unpack_from('<I',d,36*i+12)[0] for i in range(h[2])]
        j['inside']=not any(clip) and all(0<x<0x46fffe00 for x in ws)
        j['attrs_supported']=not(j['context'][3][0]&0x40000) and (not(j['context'][3][0]&0x20000) or 1<=j['context'][2][21]<=3)
        j['cycles']=int(r['inclusive_cycles'])
        if 360<=h[0]<=720:selected.append(j)
    summary={}
    for key,group in [('all',selected),('all_inside',[j for j in selected if j['inside']]),('all_inside_attributes_supported',[j for j in selected if j['inside'] and j['attrs_supported']])]:
        summary[key]=dict(loads=len(group),vertices=sum(j['header'][2] for j in group),original_inclusive_cycles=sum(j['cycles'] for j in group),original_ms_per_frame=sum(j['cycles'] for j in group)/361/1e5)
    summary['destination_counts']=dict(Counter(j['header'][3] for j in selected))
    summary['load_count_distribution']=dict(Counter(j['header'][2] for j in selected))
    write(OUT/'whole-load-coverage.json',dict(passed_pairing=True,summary=summary,jobs=len(jobs),elf_sha256=sha(BASE/'app.elf'),raw_stream_sha256=sha(CAP/'raw-dry.bin'),loaded_capture_sha256=sha(CAP/'raw-loaded.bin'),reference_input_sha256=sha(payload),helper_sha256=sha(Path(__file__)),limits=['Input+actual F11 result pairing only; qsim reference is IEEE FMA and is not the physical os30 reduced-accuracy FMA.','Whole-LOAD gate excludes any outside/invalid-W vertex even when unused by triangles.','Fallback may consume completed hardware results; these LOAD cycle costs are not automatically acceleration gains.']))
    cpp=ROOT/'tools/experiments/os30_fps25_20261005/phase10_raw_consumer.cpp';exe=OUT/'consumer'
    subprocess.run(['g++','-O2','-std=c++17','-mfma','-ffp-contract=off',str(cpp),'-o',str(exe)],check=True)
    with (OUT/'consumer-result.json').open('x') as out,(OUT/'consumer.log').open('x') as err:
        p=subprocess.run([str(exe),str(payload)],stdout=out,stderr=err)
    write(OUT/'consumer-build.json',dict(returncode=p.returncode,source_sha256=sha(cpp),executable_sha256=sha(exe),input_sha256=sha(payload)))
    print(json.dumps(summary,indent=2));print((OUT/'consumer-result.json').read_text());print((OUT/'consumer.log').read_text())

if __name__=='__main__':main()
