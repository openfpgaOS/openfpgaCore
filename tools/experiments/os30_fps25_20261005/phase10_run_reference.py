#!/usr/bin/env python3
"""Feed actual owned producer records to independent packet-generating worker."""
from collections import defaultdict,deque
import json
from pathlib import Path
import struct
import subprocess
from elftools.elf.elffile import ELFFile
from phase10_experiment import CAMP,ROOT,sha,write
BASE=CAMP/'phase10/run-producer-v1';OUT=CAMP/'phase10/run-consumer-v1';CAP=OUT/'capture-normal'
def main():
    with (BASE/'app.elf').open('rb') as f:
        e=ELFFile(f);section=e.get_section_by_name('.text');low=section['sh_addr'];data=section.data();high=low+len(data)
    def memory(a,n):
        assert low<=a<=high-n,(hex(a),n);return data[a-low:a-low+n]
    loaded=defaultdict(deque);runs=defaultdict(deque)
    b=(CAP/'raw-loaded.bin').read_bytes();o=0
    while o<len(b):
        h=struct.unpack_from('<4I',b,o);o+=16;d=b[o:o+h[2]*36];o+=len(d);loaded[h[0]].append((h,d))
    b=(CAP/'raw-run-ref.bin').read_bytes();o=0
    while o<len(b):
        h=struct.unpack_from('<6I',b,o);o+=24;w=struct.unpack_from('<%dI'%h[3],b,o);o+=h[3]*4;runs[h[0]].append((h,w))
    b=(CAP/'raw-dry.bin').read_bytes();o=0;events=0;laststats=None;max_words=0;gameplay_words=0
    binary=OUT/'worker-input.bin'
    with binary.open('xb+') as stream:
        stream.write(struct.pack('<I',0))
        def emit(op,p):
            nonlocal events
            stream.write(struct.pack('<%dI'%(2+len(p)),op,len(p),*p));events+=1
        while o<len(b):
            magic,frame,slot,n=struct.unpack_from('<4I',b,o);o+=16;stats=struct.unpack_from('<32I',b,o);o+=128
            assert magic==0x52415731 and n<=16384 and not stats[9];laststats=stats
            max_words=max(max_words,n)
            if 360<=frame<=720:gameplay_words+=n
            words=struct.unpack_from('<%dI'%n,b,o);o+=n*4;c=0;pending=None
            while c<n:
                header=words[c];c+=1;op=header>>24
                if op in [1,2,3]:
                    count=header&0xffffff;emit(op,words[c:c+count]);c+=count
                elif op==4:
                    count,first,address=header&255,header>>8&255,words[c];c+=1
                    h,expected=loaded[frame].popleft();assert h==(frame,address,count,first)
                    raw=memory(address,count*24);payload=[frame,address,count,first]
                    for v in range(count):payload.extend([*struct.unpack_from('<6I',raw,v*24),*struct.unpack_from('<9I',expected,v*36)])
                    emit(4,payload)
                elif op==5:
                    assert pending is None
                    address,cull,owned=words[c:c+3];c+=3;commands=[];triangles=0;cursor=address
                    while cursor<=high-8:
                        a,z=struct.unpack('<2I',memory(cursor,8));nc=1 if a>>24==5 else 2 if a>>24==6 else 0
                        if not nc or triangles+nc>64:break
                        commands.extend([a,z]);cursor+=8;triangles+=nc
                    h,expected=runs[frame].popleft();assert h[:2]==(frame,address),(h,frame,address)
                    pending=(h,expected);emit(5,[frame,address,cull,owned,*commands])
                elif op==6:
                    assert header&0xffffff==8 and pending is not None
                    emit(6,words[c:c+8]);c+=8
                elif op==7:
                    assert pending is not None and header&0xffffff==1
                    address=words[c];c+=1;h,expected=pending;assert address==h[1]
                    emit(7,[address,h[2],h[3],h[4],h[5],*expected]);pending=None
                else:raise AssertionError(op)
            assert pending is None and not loaded[frame] and not runs[frame]
        stream.seek(0);stream.write(struct.pack('<I',events))
    assert not any(loaded.values()) and not any(runs.values())
    proof={};baseline=BASE/'capture-normal'
    for p in baseline.rglob('*'):
        if p.is_file() and (p.name in ('frames.csv','scene.csv','geometry.csv','culling.csv') or p.suffix in ('.vec','.tsv')):
            rel=p.relative_to(baseline);assert p.read_bytes()==(CAP/rel).read_bytes(),rel;proof[str(rel)]=sha(p)
    cpp=ROOT/'tools/experiments/os30_fps25_20261005/phase10_run_reference.cpp';frozen=OUT/cpp.name;frozen.write_bytes(cpp.read_bytes());exe=OUT/'worker'
    subprocess.run(['g++','-O2','-std=c++17','-mfma','-ffp-contract=off',str(frozen),'-o',str(exe)],check=True)
    with (OUT/'worker-result.json').open('x') as out,(OUT/'worker.log').open('x') as log:p=subprocess.run([str(exe),str(binary)],stdout=out,stderr=log)
    write(OUT/'worker-manifest.json',dict(returncode=p.returncode,source_sha256=sha(frozen),elf_sha256=sha(BASE/'app.elf'),executable_sha256=sha(exe),binary_sha256=sha(binary),events=events,gameplay_descriptor_bytes_per_frame=gameplay_words*4/361,max_frame_words=max_words,passive_capture_exact=proof,observer_sha256=sha(OUT/'observer.json'),helper_sha256=sha(Path(__file__)),scope='Actual descriptor and LOAD context consumption; original CPU backend packets are comparison outputs only. qsim IEEE FMA; native FMA validated separately in raw-load RTL. No live transport/GPU execution or physical FPS claim.'))
    print((OUT/'worker-result.json').read_text());print((OUT/'worker.log').read_text());raise SystemExit(p.returncode)
if __name__=='__main__':main()
