#!/usr/bin/env python3
"""Validate producer fixtures and export exact 256-word DMA bank images."""
from pathlib import Path
import argparse,json,struct,collections
ROOT=Path(__file__).resolve().parents[4]
OUT=ROOT/'docs/measurements/os30-mesh-20261004/SM64/mesh-shadow'
STATS='vertex_loads mutable_vertices invalid_vertices cache_capacity matrix_range unsupported_attributes run_attempts no_vertex_snapshot mixed_or_mutable_runs queue_overflow captured_jobs captured_triangles vertex_decodes run_decodes vertex_cache_hits run_cache_hits'.split()
def signed(v,b=32):return (v+(1<<(b-1)))%(1<<b)-(1<<(b-1))
def read(path):
    data=path.read_bytes();magic,count,*stats=struct.unpack_from('<18I',data);assert magic==0x3148534d
    cursor=72;jobs=[]
    for _ in range(count):
        h=struct.unpack_from('<72I',data,cursor);cursor+=288
        raw=list(struct.unpack_from(f'<{h[3]*4}I',data,cursor));cursor+=h[3]*16
        indices=list(struct.unpack_from(f'<{h[4]}I',data,cursor));cursor+=h[4]*4
        assert 1<=h[3]<=32 and 1<=h[4]<=64
        assert all(max(x&31,(x>>5)&31,(x>>10)&31)<h[3] and x<32768 for x in indices)
        jobs.append(dict(header=list(h[:8]),state=list(h[8:]),vertices=raw,indices=indices))
    assert cursor==len(data)
    return stats,jobs
def analyze(dest):
    frames=[];alljobs=[];types=collections.Counter();counts=collections.Counter();first=None;last=None
    immutable={}
    for p in sorted(dest.glob('mesh-*.bin')):
        stats,jobs=read(p);first=first or stats;last=stats
        for j in jobs:
            h=j['header'];s=j['state'];v=j['vertices'];flags=[]
            for i in range(h[3]):
                pos=[signed(v[i*4],16),signed(v[i*4]>>16,16),signed(v[i*4+1],16)]
                q=[signed(signed(sum(signed(s[row*4+k])*pos[k] for k in range(3))+
                          (signed(s[row*4+3])<<16),48)>>16) for row in range(4)]
                x,y,z,w=q
                flags.append((int(x>w)<<0)|(int(x<-w)<<1)|(int(y>w)<<2)|
                             (int(y<-w)<<3)|(int(z>w)<<4)|(int(z<-w)<<5))
            for t in j['indices']:
                a,b,c=(flags[(t>>shift)&31] for shift in (0,5,10))
                types['trivial_reject' if a&b&c else 'clipping_candidate' if a|b|c else 'inside']+=1
            counts['jobs']+=1;counts['vertices']+=h[3];counts['triangles']+=h[4]
            if h[3]<8:counts['tiny_vertex_jobs']+=1
            if h[4]<=2:counts['one_or_two_triangle_jobs']+=1
            for key,content in ((('v',h[1]),v),(('i',h[2]),j['indices'])):
                value=tuple(content)
                assert key not in immutable or immutable[key]==value,'cache address changed while referenced'
                immutable[key]=value
            bank=[0]*256;bank[:len(v)]=v;bank[128:192]=s;bank[192:192+h[4]]=j['indices']
            j['bank_index']=len(alljobs);alljobs.append(j)
        frames.append(dict(frame=int(p.stem.split('-')[1]),jobs=len(jobs),stats=dict(zip(STATS,stats))))
    assert frames
    with (dest/'banks.bin').open('wb') as f:
        for j in alljobs:
            bank=[0]*256;v=j['vertices'];bank[:len(v)]=v;bank[128:192]=j['state'];bank[192:192+len(j['indices'])]=j['indices']
            f.write(struct.pack('<256I',*bank))
    result=dict(scope='Shadow producer fixtures, original rendering preserved. Classification uses fixed-point clip predicates, not a clipping or rendering correctness claim.',
                counts=dict(counts),triangles=dict(types),frames=frames,
                warm_window_delta=dict(zip(STATS,[(b-a)&0xffffffff for a,b in zip(first,last)])),
                jobs=alljobs)
    (dest/'jobs.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k not in ('jobs','frames')},indent=2))
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('run');a=p.parse_args();analyze(OUT/a.run)
