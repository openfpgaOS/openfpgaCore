#!/usr/bin/env python3
"""Compare captured mesh bytes and index order against original immutable ELF data."""
from pathlib import Path
import argparse,json,struct
from analyze import OUT,read
p=argparse.ArgumentParser();p.add_argument('run');a=p.parse_args();dest=OUT/a.run
elfpath=dest/'app.elf'
if not elfpath.exists():elfpath=OUT/'shadow-v1.elf'
if not elfpath.exists():elfpath=OUT.parent/'.obj/sm64-perfdiag/app.elf'
elf=elfpath.read_bytes()
assert elf[:7]==b'\x7fELF\x01\x01\x01'
phoff=struct.unpack_from('<I',elf,28)[0];phsize,phnum=struct.unpack_from('<HH',elf,42)
segments=[struct.unpack_from('<8I',elf,phoff+i*phsize) for i in range(phnum)]
def load(addr,n):
    for kind,offset,base,_,size,_,_,_ in segments:
        if kind==1 and base<=addr and addr+n<=base+size:return elf[offset+addr-base:offset+addr-base+n]
    raise AssertionError((hex(addr),n))
vertices=triangles=jobs=0
for file in sorted(dest.glob('mesh-*.bin')):
    _,records=read(file)
    for j in records:
        h=j['header'];v=j['vertices'];indices=j['indices'];jobs+=1
        raw=load(h[5],h[3]*24)
        for i in range(h[3]):
            x,y,z=struct.unpack_from('<3f',raw,i*24)
            st=struct.unpack_from('<I',raw,i*24+14)[0]
            color=struct.unpack_from('<I',raw,i*24+18)[0]
            expected=[(int(x)&65535)|((int(y)&65535)<<16),int(z)&65535,st,color]
            assert expected==v[i*4:i*4+4],(h,i,expected,v[i*4:i*4+4])
            vertices+=1
        source=[];cmd=h[6]
        while len(source)<h[4]:
            w0,w1=struct.unpack('<2I',load(cmd,8));cmd+=8
            opcode=w0>>24;assert opcode in (5,6)
            words=[w0,w1] if opcode==6 else [w0]
            for word in words:source.append(tuple(((word>>s)&255)//2 for s in (16,8,0)))
        assert len(source)==h[4]
        first=source[0][0]-(indices[0]&31)
        assert 0<=first<32 and first+h[3]<=32
        for original,index in zip(source,indices):
            assert original==tuple(((index>>s)&31)+first for s in (0,5,10))
            triangles+=1
result=dict(jobs=jobs,vertices=vertices,triangles=triangles,success=True,
            checks='Every normalized vertex and provoking/index order matches immutable source ELF; whole TRI2 opcodes preserved.')
(dest/'source-validation.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))
