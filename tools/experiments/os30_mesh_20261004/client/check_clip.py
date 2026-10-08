#!/usr/bin/env python3
from pathlib import Path
import argparse,collections,json,random,struct
from mesh_transform import OUT,read,transform_vertex,clip_polygon,clip_code,build_clip,run,word,signed
p=argparse.ArgumentParser();p.add_argument('--random',type=int,default=1000);p.add_argument('--limit',type=int,default=0)
a=p.parse_args();dest=OUT/'run-360-8'
program=build_clip().finish();rng=random.Random(0x30c11);counts=collections.Counter()
def check(vertices,label):
    scratch=[0]*256
    preserved=[rng.getrandbits(32) for _ in range(48)]
    scratch[:48]=preserved
    scratch[48]=clip_code(vertices[0])|clip_code(vertices[1])|clip_code(vertices[2])
    scratch[64:85]=[word(x) for v in vertices for x in v]
    expected=clip_polygon(vertices)
    bank=[rng.getrandbits(32) for _ in range(256)]
    result=run(program,bank=bank,scratch=scratch)
    assert not result['fault'] and result['scratch'][51]==0,(label,result)
    assert result['scratch'][:48]==preserved,label
    assert result['bank']==bank,label
    assert result['scratch'][49]==len(expected),(label,vertices,expected,result['scratch'][49])
    assert result['scratch'][64:64+7*len(expected)]==[word(x) for v in expected for x in v],label
    counts['triangles']+=1;counts['cycles']+=result['cycles'];counts['output_vertices']+=len(expected)
    if scratch[48]:counts['clipping_triangles']+=1
    group='random' if label[0]=='random' else 'boundary' if label[0]=='boundary' else (
        'inside' if not scratch[48] else 'outside' if clip_code(vertices[0])&clip_code(vertices[1])&clip_code(vertices[2]) else 'crossing')
    counts[group+'_triangles']+=1;counts[group+'_cycles']+=result['cycles']
for file in sorted(dest.glob('mesh-*.bin')):
    _,jobs=read(file)
    for job in jobs:
        vertices=[transform_vertex(job['vertices'][i*4:i*4+4],job['state']) for i in range(job['header'][3])]
        for index in job['indices']:
            check([vertices[(index>>s)&31] for s in (0,5,10)],('scene',file.name,index))
        if a.limit and counts['triangles']>=a.limit:break
    if a.limit and counts['triangles']>=a.limit:break
for i in range(a.random):
    vertices=[[rng.randrange(-100000,100001) for _ in range(6)]+[rng.getrandbits(32)] for _ in range(3)]
    for v in vertices:
        v[3]=rng.randrange(-1000,100001)
        v[4:6]=[signed(rng.getrandbits(32)),signed(rng.getrandbits(32))]
    check(vertices,('random',i))
# Coordinate extrema and full33-bit signed UV differences exercise the same
# multiword correction needed for exact edge presteps.
for direction in (-1,1):
    limit=1<<29
    check([[direction*limit,0,0,limit//2,-2**31,2**31-1,0xff000000],
           [0,-limit//3,0,limit//2,2**31-1,-2**31,0x00ffffff],
           [0,limit//3,0,limit//2,-2**31,2**31-1,0x80402010]],('boundary',direction))
scratch=[0]*256;scratch[48]=16;scratch[64]=(1<<29)+1
bad=run(program,scratch=scratch)
assert bad['scratch'][49]==0 and bad['scratch'][51]==1
result=dict(instructions=len(program),counts=dict(counts),success=True,
            scope='ISA microcode matches fixed-point Sutherland-Hodgman reference, preserves index/material scratch and bank, no float-byte-exact claim.')
(dest/'clip.json').write_text(json.dumps(result,indent=2)+'\n')
(dest/'clip.hex').write_text(''.join(f'{w:08x}\n' for w in program))
print(json.dumps(result,indent=2))
