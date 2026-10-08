#!/usr/bin/env python3
"""One bounded rescue: lazy numerical guards and one run-level ring copy."""
from pathlib import Path
import json,difflib
from phase10_experiment import CAMP,sha,write
BASE=CAMP/'phase10/raw-slot-ring-v1';OUT=CAMP/'phase10/raw-slot-ring-v2'
def main():
 inputs={n:(BASE/n).read_text() for n in json.loads((BASE/'build-normal.json').read_text())['inputs']};old=inputs.copy();s=inputs['gfx_pc.c']
 s=s.replace('static uint32_t raw_slot_good,raw_slot_stale=0xffffffffu;','static uint32_t raw_slot_checked,raw_slot_good,raw_slot_stale=0xffffffffu;')
 s=s.replace('w>=0x00800000u','w>=0x37800000u')
 s=s.replace('(!x||x>=0x3b800000u) && (!y||y>=0x3b800000u) &&\n        !(rsp.geometry_mode&G_TEXTURE_GEN);','(!x||x>=0x3b800000u) && (!y||y>=0x3b800000u) &&\n        x<0x46fffe00u && y<0x46fffe00u;')
 anchor='static __attribute__((noinline)) void gfx_sp_vertex('
 s=s.replace(anchor,'''static __attribute__((noinline)) void raw_slot_check(uint32_t pending) {
    raw_slot_checked|=pending;
    while(pending) {unsigned slot=__builtin_ctz(pending);uint32_t bit=1u<<slot;pending&=pending-1u;
        if(raw_slot_eligible(&rsp.loaded_vertices[slot]))raw_slot_good|=bit;
    }
}

'''+anchor)
 s=s.replace('raw_slot_stale|=overwritten;raw_slot_good&=~overwritten;','raw_slot_stale|=overwritten;raw_slot_good&=~overwritten;\n    raw_slot_checked&=~overwritten;\n    if(rsp.geometry_mode&G_TEXTURE_GEN)raw_slot_checked|=overwritten;')
 s=s.replace('\n        if(dest_index<32 && raw_slot_eligible(d))raw_slot_good|=1u<<dest_index;','')
 s=s.replace('uint32_t jobs[64],context[5],stale=raw_slot_stale;','uint32_t jobs[64],context[5],used_total=0;')
 s=s.replace('        if(!good||(raw_slot_good&used)!=used)break;','        if(!good)break;\n        if(used&~raw_slot_checked)raw_slot_check(used&~raw_slot_checked);\n        if((raw_slot_good&used)!=used)break;')
 oldrun='''        for(unsigned t=0;t<n;t++) {
            unsigned *s=&slot[t*3],dirty=0;
            for(unsigned k=0;k<3;k++) {
                uint32_t bit=1u<<s[k];if(stale&bit){dirty|=1u<<k;stale&=~bit;}
            }
            jobs[count++]=s[0]|(s[1]<<5)|(s[2]<<10)|(dirty<<15);
        }'''
 newrun='''        used_total|=used;
        for(unsigned t=0;t<n;t++) {
            unsigned *s=&slot[t*3];jobs[count++]=s[0]|(s[1]<<5)|(s[2]<<10);
        }''';assert oldrun in s;s=s.replace(oldrun,newrun)
 s=s.replace('jobs,count,alpha,context,(rsp.geometry_mode&G_CULL_BOTH)>>9);','jobs,count,alpha,context,(rsp.geometry_mode&G_CULL_BOTH)>>9,used_total&raw_slot_stale);')
 s=s.replace('raw_slot_stale=stale;','raw_slot_stale&=~used_total;')
 s=s.replace('raw_slot_stale=0xffffffffu;raw_slot_good=0;','raw_slot_stale=0xffffffffu;raw_slot_good=0;raw_slot_checked=0;')
 inputs['gfx_pc.c']=s
 inputs['gfx_gpu.h']=inputs['gfx_gpu.h'].replace('const uint32_t[5],unsigned);','const uint32_t[5],unsigned,uint32_t);')
 s=inputs['gfx_gpu.c'];s=s.replace('const uint32_t context[5],unsigned cull) {','const uint32_t context[5],unsigned cull,uint32_t dirty) {')
 start=s.index('    for(unsigned t=0;t<count;t++) {',s.index('void gfx_gpu_raw_slot_batch'));end=s.index('\n}\n\nvoid gfx_gpu_vtx_cache_batch',start)
 s=s[:start]+'''    const unsigned words=7u*(unsigned)__builtin_popcount(dirty)+2u*count;
    _gpu_stream_reserve_words(words);uint32_t*p=_gpu_ring_claim();
    while(dirty) {
        unsigned slot=__builtin_ctz(dirty);dirty&=dirty-1u;
        const struct gfx_raw_slot*v=&vertices[slot];
        *p++=0x60000006u|(slot<<13);
        uint32_t x,y,w,inv;memcpy(&x,&v->x,4);memcpy(&y,&v->y,4);memcpy(&w,&v->w,4);memcpy(&inv,&v->w_inv,4);
        *p++=x;*p++=y;*p++=w;*p++=inv;
        *p++=(uint16_t)(int16_t)v->u|((uint32_t)(uint16_t)(int16_t)v->v<<16);
        *p++=v->rgba;g_vc_loads++;
    }
    for(unsigned t=0;t<count;t++){*p++=0x62000001u;*p++=jobs[t]|(cull<<15);}
    _gpu_ring_commit(words);g_vc_words+=words;g_vc_tris_cached+=count;
    g_vc_kick+=count;if(g_vc_kick>=32u){g_vc_kick=0;of_gpu_kick();}
'''+s[end:];inputs['gfx_gpu.c']=s
 OUT.mkdir(exist_ok=False)
 for n,c in inputs.items():(OUT/n).write_text(c)
 (OUT/'candidate.patch').write_text(''.join(''.join(difflib.unified_diff(old[n].splitlines(True),inputs[n].splitlines(True),fromfile='raw-slot-v1/'+n,tofile='raw-slot-v2/'+n)) for n in inputs if inputs[n]!=old[n]))
 write(OUT/'source.json',dict(parent='raw-slot-ring-v1',source_sha256={n:sha(OUT/n) for n in inputs},helper_sha256=sha(Path(__file__)),scope=__doc__))
if __name__=='__main__':main()
