#!/usr/bin/env python3
"""Prove disjoint SCOPY and optional clipping replacement without changing defaults."""
import argparse,collections,json,random,sys
from pathlib import Path
HERE=Path(__file__).resolve().parent
sys.path[:0]=[str(HERE.parent/'client'),str(HERE.parent/'engine')]
from isa import Program
from emulator import run
from mesh_transform import OUT,read,transform_vertex,clip_polygon,clip_code,build_clip,word,signed
from mesh_program import build
from check_mesh_program import bank_for,expected_stream

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--random',type=int,default=1000)
    ap.add_argument('--out-name',default='scopy');a=ap.parse_args()
    dest=HERE/'results'/a.out_name;dest.mkdir(exist_ok=False)
    rng=random.Random(0x53c0);counts=collections.Counter()
    for n in range(1,129):
        for source in range(256):
            target=(source+n+rng.randrange(257-2*n))&255
            scratch=[rng.getrandbits(32) for _ in range(256)]
            ra,rb=rng.sample(range(256),2);scratch[ra]=source;scratch[rb]=target
            expected=scratch[:]
            for i in range(n):expected[(target+i)&255]=scratch[(source+i)&255]
            p=Program().emit('SCOPY',n-1,ra,rb).emit('HALT').finish()
            result=run(p,scratch=scratch)
            assert result['scratch']==expected and result['cycles']==n+5,(n,source,target,result['cycles'])
            counts['opcode_cases']+=1
    for source,target,n in [(0,0,1),(0,1,2),(255,0,2),(0,128,129),(0,0,256)]:
        scratch=[0]*256;scratch[200]=source;scratch[201]=target
        try:run(Program().emit('SCOPY',n-1,200,201).finish(),scratch=scratch)
        except ValueError:counts['overlap_rejections']+=1
        else:raise AssertionError('overlap accepted')
    clip=[build_clip(fused_copy=f).finish() for f in (False,True)]
    def check(vertices,label):
        scratch=[rng.getrandbits(32) for _ in range(256)]
        scratch[48]=clip_code(vertices[0])|clip_code(vertices[1])|clip_code(vertices[2])
        scratch[64:85]=[word(x) for v in vertices for x in v]
        bank=[rng.getrandbits(32) for _ in range(256)]
        expected=clip_polygon(vertices)
        pair=[run(code,bank=bank,scratch=scratch) for code in clip]
        for r in pair:
            assert not r['fault'] and r['scratch'][51]==0,label
            assert r['scratch'][:48]==scratch[:48] and r['bank']==bank,label
            assert r['scratch'][49]==len(expected),label
            assert r['scratch'][64:64+7*len(expected)]==[word(x) for v in expected for x in v],label
        assert pair[1]['cycles']<=pair[0]['cycles'],label
        counts['clip_cases']+=1
        counts['clip_scalar_cycles']+=pair[0]['cycles'];counts['clip_scopy_cycles']+=pair[1]['cycles']
    jobs=[j for path in sorted((OUT/'run-360-8').glob('mesh-*.bin')) for j in read(path)[1]]
    programs=[build(fused_copy=f,unroll_project=False,outblock=False,fused_planes=False,unroll_attributes=False,fused_setup=False,honor_guardband=False).finish() for f in (False,True)]
    for number,job in enumerate(jobs):
        vertices=[transform_vertex(job['vertices'][4*i:4*i+4],job['state']) for i in range(job['header'][3])]
        for index in job['indices']:check([vertices[(index>>s)&31] for s in (0,5,10)],('scene',number,index))
        bank,state=bank_for(job);scratch=[0]*256;scratch[62],scratch[63]=job['header'][3:5]
        expected,_,draws=expected_stream(job,state)
        pair=[run(code,bank=bank,scratch=scratch,compact=True) for code in programs]
        for r in pair:assert not r['fault'] and r['output']==expected,number
        assert pair[0]['bank']==pair[1]['bank'],number
        counts['mesh_cases']+=1;counts['mesh_draws']+=draws
        counts['mesh_scalar_cycles']+=pair[0]['cycles'];counts['mesh_scopy_cycles']+=pair[1]['cycles']
    for i in range(a.random):
        vertices=[[rng.randrange(-100000,100001) for _ in range(6)]+[rng.getrandbits(32)] for _ in range(3)]
        for v in vertices:v[3]=rng.randrange(-1000,100001);v[4:6]=[signed(rng.getrandbits(32)),signed(rng.getrandbits(32))]
        check(vertices,('random',i))
    for direction in (-1,1):
        limit=1<<29
        check([[direction*limit,0,0,limit//2,-2**31,2**31-1,0xff000000],
               [0,-limit//3,0,limit//2,2**31-1,-2**31,0x00ffffff],
               [0,limit//3,0,limit//2,-2**31,2**31-1,0x80402010]],('boundary',direction))
    result=dict(passed=True,counts=dict(counts),program_words=list(map(len,programs)),clip_words=list(map(len,clip)),
                scope=__doc__,limitation='Scene materials are synthetic; real captured material/runtime stream is checked separately. No overlap semantics are promised.')
    (dest/'result.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))

if __name__=='__main__':main()
