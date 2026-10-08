#!/usr/bin/env python3
"""Whole producer ISA versus independent transform/clip/packet reference.

Real SM64 meshes/matrices are paired with explicit synthetic materials. This
validates the processor and ordering; it is not a reconstructed SM64 framebuffer
or a claim that diagnostic shadow selectors are renderable material packets.
"""
from pathlib import Path
import argparse,collections,json,random,struct,sys
from mesh_program import build
from mesh_transform import OUT,read,transform_vertex,clip_polygon,word,signed
from emulator import run
from reference import Vertex,prepared_packet

def bank_for(job,variant=-1):
    # Materials are explicit test inputs. They are not inferred from the old
    # renderer's selector snapshot. C/D/alpha/cull variants exercise ordering.
    source=job['state'];state=[0]*64;state[:24]=source[:24]
    if variant<0:
        cull=(1 if source[24]&0x200 else 0)|(2 if source[24]&0x400 else 0)
        variant=0
    else:cull=variant%4
    flags=3|(cull<<8)
    if variant&4:flags|=4
    if variant&8:flags|=1<<16
    if variant&16:flags|=1<<17
    if variant&32:flags|=1<<18
    vx,vy=source[28]&65535,source[28]>>16
    width,height=source[29]&65535,source[29]>>16
    hw,hh=width//2,height//2
    clipx=(320<<16);clipy=(240<<16)
    state[24:41]=[0x10000,640,2,0x100000,32,0x001f001f,
                  0x80001000|((flags&4)<<28),0,31<<16,0,31<<16,
                  0x20000,640,2,clipx,clipy,255]
    state[41:45]=[vx+hw,(240-vy-height+hh)*16,hw,hh*16]
    state[45:52]=[clipx,clipy,flags,word(-12345),23456,0x1234,0x5abc]
    raw=job['vertices'];indices=job['indices'];bank=[0]*256
    bank[:len(raw)]=raw;bank[128:192]=state
    for i in range(0,len(indices),2):bank[192+i//2]=indices[i]|((indices[i+1] if i+1<len(indices) else 0)<<16)
    return bank,state

def projected(v,state):
    depth=(1<<38)//max(256,v[3]);zi=(depth&0xffffffff)>>6
    rx=signed((v[0]*zi)>>16);ry=signed((v[1]*zi)>>16)
    x=min(32767,max(-32768,(signed(state[41])<<4)+((signed(state[43])*rx)>>12)))
    y=min(32767,max(-32768,signed(state[42])-((signed(state[44])*ry)>>16)))
    rgba=v[6]
    rgb=((rgba&0xf8)<<8)|((rgba&0xfc00)>>5)|((rgba>>19)&31)
    flags=state[47]
    d=rgb if flags&(1<<17) else state[50]
    c=state[51] if flags&(1<<16) else rgb
    return Vertex(x,y,signed(v[4]+signed(state[48])),signed(v[5]+signed(state[49])),zi,depth&0xffffffff,c,d)

def expected_stream(job,state):
    vertices=[transform_vertex(job['vertices'][i*4:i*4+4],state) for i in range(job['header'][3])]
    material=state[24:41];output=[0x4a000011,*material];draws=0
    flags=state[47]
    for t in job['indices']:
        poly=clip_polygon([vertices[(t>>shift)&31] for shift in (0,5,10)],
                          plane_mask=3 if flags&(1<<19) else 63)
        for fan in range(1,len(poly)-1):
            v=[projected(poly[i],state) for i in (0,fan,fan+1)]
            a,b,c=v
            cross=(a.x-b.x)*(c.y-b.y)-(a.y-b.y)*(c.x-b.x)
            cull=(flags>>8)&3
            if cull==3 or (cull==1 and cross>=0) or (cull==2 and cross<=0):continue
            if flags&(1<<18):
                alpha=poly[0][6]>>24
                if alpha!=material[16]:material=material[:];material[16]=alpha;output += [0x4a000011,*material]
            bounds=tuple(signed(state[k]>>shift,16) for k in (38,39) for shift in (0,16))
            packet=prepared_packet(v,bounds,subpixel=bool(flags&1),normalize=bool(flags&2),combine=bool(flags&4))
            output+=packet;draws+=bool(packet)
    return output,vertices,draws

def main():
    p=argparse.ArgumentParser();p.add_argument('--limit',type=int,default=0);p.add_argument('--variants',type=int,default=0)
    p.add_argument('--label',default='whole-program')
    a=p.parse_args();program=build().finish();dest=OUT/a.label;dest.mkdir(exist_ok=True)
    jobs=[j for path in sorted((OUT/'run-360-8').glob('mesh-*.bin')) for j in read(path)[1]]
    if a.limit:jobs=jobs[:a.limit]
    counts=collections.Counter();cases=[]
    for n,job in enumerate(jobs):
        for variant in (range(a.variants) if a.variants else [-1]):
            bank,state=bank_for(job,variant);scratch=[0]*256;scratch[62],scratch[63]=job['header'][3:5]
            expected,vertices,draws=expected_stream(job,state)
            result=run(program,bank=bank,scratch=scratch,compact=True)
            if result['fault'] or result['scratch'][63] or result['output']!=expected:
                failure=dict(job=n,variant=variant,input=bank,initial_scratch=scratch,expected=expected,result=result)
                (dest/'failure.json').write_text(json.dumps(failure,indent=2)+'\n')
                mismatch=next(((i,hex(x),hex(y)) for i,(x,y) in enumerate(zip(expected,result['output'])) if x!=y),None)
                raise AssertionError((n,variant,len(expected),len(result['output']),mismatch,result['fault'],result['scratch'][63]))
            assert result['bank'][:7*len(vertices)]==[word(x) for v in vertices for x in v],('vertices',n,variant)
            counts['jobs']+=1;counts['vertices']+=len(vertices);counts['triangles']+=len(job['indices'])
            counts['draw_packets']+=draws;counts['cycles']+=result['cycles'];counts['words']+=len(expected)
            cases.append(dict(job=n,variant=variant,cycles=result['cycles'],draws=draws,words=len(expected)))
    report=dict(scope=__doc__,success=True,program_words=len(program),counts=dict(counts),cases=cases,
                limitation='Instruction cycles are validated separately against RTL; this ISA run excludes DMA and raster backpressure.')
    (dest/'result.json').write_text(json.dumps(report,indent=2)+'\n')
    (dest/'program.hex').write_text(''.join(f'{w:08x}\n' for w in program))
    (dest/'program.json').write_text(json.dumps(dict(words=len(program),labels=build().labels),indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k!='cases'},indent=2))
if __name__=='__main__':main()
