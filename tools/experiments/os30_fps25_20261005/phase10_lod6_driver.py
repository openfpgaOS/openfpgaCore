#!/usr/bin/env python3
"""Private LOD6 transform plus overlapped original memoized CPU lighting."""
from pathlib import Path
import difflib
from phase10_experiment import CAMP,INPUTS,sha,write
BASE=CAMP/'phase10/load-cram-driver-v1';OUT=CAMP/'phase10/lod6-driver-v1'
def rep(t,a,b):
 assert t.count(a)==1,(a,t.count(a));return t.replace(a,b)
LIGHT=r'''
/* Same memo key/epoch, arithmetic and clamp as the original native LOAD.
 * Only one directional light is eligible here. START already owns immutable
 * CRAM input while the CPU computes these independent ordinary-memory colors. */
static __attribute__((noinline)) void load_alias_light(unsigned count,unsigned first,
                                                      const Vtx *vertices) {
    const float c0=rsp.current_lights_coeffs[0][0],c1=rsp.current_lights_coeffs[0][1],c2=rsp.current_lights_coeffs[0][2];
    const int ar=rsp.current_lights[1].col[0],ag=rsp.current_lights[1].col[1],ab=rsp.current_lights[1].col[2];
    const float lr=rsp.current_lights[0].col[0],lg=rsp.current_lights[0].col[1],lb=rsp.current_lights[0].col[2];
    for(unsigned i=0;i<count;i++) {
        const Vtx_t *v=&vertices[i].v;const Vtx_tn *vn=&vertices[i].n;
        struct LoadedVertex *d=&rsp.loaded_vertices[first+i];
        const uint32_t normal=gfx_lighting_normal_key(v->cn);
        struct LightingMemo *memo=&lighting_memo[(normal^(normal>>11))&255u];
        if(memo->epoch==lighting_epoch && memo->normal==normal) {
            d->color=memo->color;
        } else {
            int r=ar,g=ag,b=ab;
            float intensity=0;
            intensity+=vn->n[0]*c0;
            intensity+=vn->n[1]*c1;
            intensity+=vn->n[2]*c2;
            intensity/=127.0f;
            if(intensity>0.0f) {r+=intensity*lr;g+=intensity*lg;b+=intensity*lb;}
            d->color.r=r<0?0:r>255?255:r;
            d->color.g=g<0?0:g>255?255:g;
            d->color.b=b<0?0:b>255?255:b;
            memo->epoch=lighting_epoch;memo->normal=normal;
            memo->color.r=d->color.r;memo->color.g=d->color.g;memo->color.b=d->color.b;
        }
        d->color.a=v->cn[3];
    }
}
static __attribute__((noinline)) void load_alias_copy_lit(uint32_t *restrict d,
                                                        const uint32_t *restrict s,
                                                        unsigned count) {
    for(unsigned i=0;i<count;i++,s+=8,d+=9) {
        d[0]=s[0];d[1]=s[1];d[2]=s[2];d[3]=s[3];d[4]=s[4];
        uint32_t uv=s[5];float u=(float)(int16_t)uv,v=(float)(int16_t)(uv>>16);
        memcpy(d+5,&u,4);memcpy(d+6,&v,4);d[8]=s[7];
    }
}
'''
def main():
 OUT.mkdir(exist_ok=False);diff=[]
 for n in INPUTS:(OUT/n).write_bytes((BASE/n).read_bytes())
 p=OUT/'gfx_gpu.c';a=p.read_text();b=a.replace('Private LOD5','Private LOD6').replace('0x4c4f4405u','0x4c4f4406u');p.write_text(b);diff.append(('gfx_gpu.c',a,b))
 p=OUT/'gfx_pc.c';a=p.read_text();b=a.replace('LOD5','LOD6').replace('0x4c4f4405u','0x4c4f4406u')
 b=rep(b,'_Static_assert(sizeof(struct LoadedVertex)==36,','_Static_assert(sizeof(Vtx)==24,"LOD6 raw source stride");\n_Static_assert(sizeof(struct LoadedVertex)==36,')
 b=rep(b,'static inline void load_alias_discard(unsigned count) {',LIGHT+'\nstatic inline void load_alias_discard(unsigned count) {')
 first=b.index('    if(lit&&(load_alias_dirty&2)) {');last=b.index('    uint32_t scales=',first)
 b=b[:first]+b[last:]
 b=rep(b,'    if(!load_alias_controls_valid||lit!=load_alias_lighting) {\n        LOAD_ALIAS_REGS->lighting=lit;load_alias_lighting=lit;writes++;\n    }','''    if(!load_alias_controls_valid) {
        LOAD_ALIAS_REGS->lighting=0;writes++;
    }''')
 b=rep(b,'static uint32_t load_alias_scales,load_alias_lighting;','static uint32_t load_alias_scales;')
 b=rep(b,'    LOAD_ALIAS_REGS->control=1;','    LOAD_ALIAS_REGS->control=1;\n    if(lit)load_alias_light(count,first,vertices);')
 b=rep(b,'        load_alias_copy((uint32_t *)&rsp.loaded_vertices[first],LOAD_ALIAS_VIEW,count);','''        if(lit)load_alias_copy_lit((uint32_t *)&rsp.loaded_vertices[first],LOAD_ALIAS_VIEW,count);
        else load_alias_copy((uint32_t *)&rsp.loaded_vertices[first],LOAD_ALIAS_VIEW,count);''')
 p.write_text(b);diff.append(('gfx_pc.c',a,b))
 (OUT/'candidate.patch').write_text(''.join(''.join(difflib.unified_diff(a.splitlines(True),b.splitlines(True),fromfile='LOD5/'+n,tofile='LOD6/'+n))for n,a,b in diff))
 write(OUT/'source.json',dict(parent_elf_sha256=sha(BASE/'app.elf'),inputs={n:sha(OUT/n)for n in INPUTS},helper_sha256=sha(Path(__file__)),id='0x4c4f4406',source_pool='Unchanged no-recycle LOD5pool',lighting='Same original one-light memoizedCPU expression afterSTART/beforepoll, with sourcealpha; successlitcopy preservescolor.',qualified_for_deployment=False));print(OUT)
if __name__=='__main__':main()
