#!/usr/bin/env python3
"""Emit immutable firmware assets from qualified native LOD5 fixture rows."""
import hashlib,json,struct
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
P=ROOT/'docs/measurements/os30-fps25-20261005/phase10'
SOURCE=P/'unified-load-components-v2/frozen/native.bin'
OUT=P/'lod5-live-native-v1/firmware'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()

def f16(word):
    signed=word if word<32768 else word-65536
    return struct.unpack('<I',struct.pack('<f',float(signed)))[0]

def main():
    data=SOURCE.read_bytes();words=list(struct.unpack('<%dI'%(len(data)//4),data));pos=1
    assert words[0]==20
    native=[]
    for i in range(20):
        h=words[pos:pos+27];pos+=27
        rows=[words[pos+15*k:pos+15*(k+1)] for k in range(h[0])];pos+=15*h[0]
        assert 1<=h[0]<=32 and len(rows[-1])==15
        native.append((h,rows,i))
    assert pos==len(words)
    jobs=list(native)
    for n in range(1,33):
        h,rows,i=native[(n-1)%len(native)];h=h.copy();h[0]=n
        jobs.append((h,[rows[k%len(rows)] for k in range(n)],i))
    OUT.mkdir(parents=True,exist_ok=True)
    dest=OUT/'vectors.h';assert not dest.exists()
    lines=['/* Generated private firmware assets. Do not write original application addresses. */',
           '#ifndef LOD5_NATIVE_VECTORS_H','#define LOD5_NATIVE_VECTORS_H','#include <stdint.h>',
           '#define LOD5_NATIVE_CAPTURED_JOBS 20u',f'#define LOD5_NATIVE_VECTOR_COUNT {len(jobs)}u',
           '/* h: count, CRAM byteoffset, aspect, matrix16, coeffXYZ, ambient, diffuse, lit, Uscale, Vscale. */',
           '/* raw is dedicated immutable ELF rodata; upload its count*24 bytes to h[1]. */',
           '/* expected/mask: count rows of9 words, native LoadedVertex x,y,z,w,invw,u,v,RGBA,clip. */',
           'typedef struct { uint32_t h[27]; const uint32_t *raw, *expected, *mask; uint32_t native_index; } Lod5NativeVector;']
    arrays=[];pool=0xfc0000;metadata=[]
    def arr(name,values,align=False):
        lines.append('static const uint32_t '+name+'[]'+(' __attribute__((section(".native_source"), aligned(64)))' if align else '')+' = {')
        for k in range(0,len(values),9):lines.append('  '+','.join('0x%08xu'%v for v in values[k:k+9])+',')
        lines.append('};')
    for index,(header,rows,source_index) in enumerate(jobs):
        h=header.copy();h[1]=pool;pool+=(len(rows)*24+63)&~63
        raw=[];expected=[];masks=[];qualified=0
        for row in rows:
            raw+=row[:6];q=row[6:14];uv=q[5]
            expected+=q[:5]+[f16(uv&65535),f16(uv>>16),q[6],q[7]]
            masks+=[0xffffffff]*7+[0xffffffff if row[14] else 0xff000000,0xff]
            qualified+=bool(row[14])
        name=f'lod5_vector_{index:02d}'
        arr(name+'_raw',raw,True);arr(name+'_expected',expected);arr(name+'_mask',masks)
        arrays.append('  {{'+','.join('0x%08xu'%v for v in h)+'},'+name+'_raw,'+name+'_expected,'+name+'_mask,'+str(source_index)+'u},')
        metadata.append(dict(index=index,count=len(rows),native_context=source_index,cram_offset=h[1],qualified_rgb_vertices=qualified,captured_original=index<20,raw_sha256=hashlib.sha256(struct.pack('<%dI'%len(raw),*raw)).hexdigest()))
    assert pool<=0x1000000
    lines+=['static const Lod5NativeVector lod5_native_vectors[LOD5_NATIVE_VECTOR_COUNT] = {']+arrays+['};','#endif','']
    dest.write_text('\n'.join(lines))
    info=dict(source=str(SOURCE.relative_to(ROOT)),source_sha256=sha(SOURCE),header_sha256=sha(dest),jobs=metadata,
        original_native_jobs=20,original_native_vertices=sum(len(r) for h,r,i in native),
        repeated_jobs=32,repeated_vertices=528,total_vertices=sum(len(r) for h,r,i in jobs),
        upload_pool_start=0xfc0000,upload_pool_end=pool,
        interpretation='Expected geometry/reciprocal/clip/UV from native-qualified fixture; RGB masked where original fixture lacked native lighting qualification. Repeated rows are not additional native captures.',
        raw_section='.native_source', linker_window_required='Verified unused immutable asset window inside0x10400000..0x10d3bc80; fixture owner must choose and prove it', original_application_memory_mutated=False,rtl_changed=False)
    (OUT/'vectors.json').write_text(json.dumps(info,indent=2)+'\n')
    (OUT/'vectors-recipe.py').write_bytes(Path(__file__).read_bytes())
    print(json.dumps({k:v for k,v in info.items() if k!='jobs'},indent=2))

if __name__=='__main__':main()
