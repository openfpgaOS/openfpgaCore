#!/usr/bin/env python3
"""Private CPU-native LOAD, seven-word raw GPU slot/cull/format producer."""
from pathlib import Path
import json,difflib
from phase10_experiment import CAMP,sha,write
BASE=CAMP/'phase9/checks-state-f11';OUT=CAMP/'phase10/raw-slot-ring-v1'
STATE=r'''
/* Private in-ring float frontend. Native CPU LOAD owns original values. */
static uint32_t raw_slot_good,raw_slot_stale=0xffffffffu;
static inline uint32_t raw_float_bits(float f) {uint32_t u;memcpy(&u,&f,4);return u;}
static inline unsigned raw_slot_eligible(const struct LoadedVertex *v) {
    uint32_t x=raw_float_bits(v->x)&0x7fffffffu,y=raw_float_bits(v->y)&0x7fffffffu;
    uint32_t w=raw_float_bits(v->w),inv=raw_float_bits(v->w_inv);
    unsigned e=(inv>>23)&255u;
    return !v->clip_rej && w>=0x00800000u && w<0x46fffe00u &&
        !(inv>>31) && e>=112u && e<=143u &&
        (!x||x>=0x3b800000u) && (!y||y>=0x3b800000u) &&
        !(rsp.geometry_mode&G_TEXTURE_GEN);
}
'''
RUN=r'''
/* No per-triangle winding or fixed-point/materialized payload calculation.
 * Whole TRI2 goes to the original CPU path when a cheap input guard fails. */
static __attribute__((noinline)) Gfx *gfx_raw_slot_run(Gfx *cmd) {
    uint32_t jobs[64],context[5],stale=raw_slot_stale;
    unsigned count=0;uint8_t alpha=255;int material=0;
    unsigned frm;__asm__ volatile("csrr %0,frm" : "=r"(frm));
    if(frm)return gfx_cpu_indexed_run_original(cmd);
    for(;;) {
        unsigned slot[6];unsigned n=gfx_geometry_decode(cmd,slot);
        if(!n||count+n>64)break;
        uint32_t used=0;unsigned good=1;
        for(unsigned t=0;t<n;t++) {
            unsigned *s=&slot[t*3];
            if((s[0]|s[1]|s[2])>=32){good=0;break;}
            used|=(1u<<s[0])|(1u<<s[1])|(1u<<s[2]);
            uint32_t a=raw_float_bits(rsp.loaded_vertices[s[0]].w);
            uint32_t b=raw_float_bits(rsp.loaded_vertices[s[1]].w);
            uint32_t c=raw_float_bits(rsp.loaded_vertices[s[2]].w);
            if(a==b&&a==c){good=0;break;}
        }
        if(!good||(raw_slot_good&used)!=used)break;
        if(!material) {
            bool fog,use_alpha,texture;
            struct ColorCombiner *comb=gfx_prepare_triangle_material(&fog,&use_alpha,&texture);
            unsigned rgb=triangle_vc_rgb_input<0?255u:comb->shader_input_mapping[0][triangle_vc_rgb_input];
            unsigned asrc=triangle_vc_alpha_input<0?8u:comb->shader_input_mapping[1][triangle_vc_alpha_input];
            if(!triangle_vc_elig||rgb==CC_LOD||asrc==CC_LOD||(asrc==CC_SHADE&&!fog))break;
            if(asrc!=8&&asrc!=CC_SHADE){struct RGBA tmp;const struct LoadedVertex*v=&rsp.loaded_vertices[slot[0]];alpha=gfx_vc_resolve(asrc,v,v,&tmp)->a;}
            unsigned textured=triangle_vc_elig&GFX_VC_TEXTURED;
            unsigned linear=textured&&(rdp.other_mode_h&(3u<<G_MDSFT_TEXTFILT))!=G_TF_POINT;
            gfx_vc_texture_state(comb,textured,linear?0.5f:0.0f);
            context[0]=(textured?1u:0u)|(rgb<<8);
            memcpy(&context[1],&rdp.prim_color,4);memcpy(&context[2],&rdp.env_color,4);
            context[3]=rdp.texture_tile.uls|((uint32_t)rdp.texture_tile.ult<<16);context[4]=linear;
            material=1;
        }
        for(unsigned t=0;t<n;t++) {
            unsigned *s=&slot[t*3],dirty=0;
            for(unsigned k=0;k<3;k++) {
                uint32_t bit=1u<<s[k];if(stale&bit){dirty|=1u<<k;stale&=~bit;}
            }
            jobs[count++]=s[0]|(s[1]<<5)|(s[2]<<10)|(dirty<<15);
        }
        cmd++;
    }
    if(count) {
        if(buf_vbo_len)gfx_flush();
        gfx_gpu_raw_slot_batch((const struct gfx_raw_slot *)rsp.loaded_vertices,jobs,count,alpha,context,(rsp.geometry_mode&G_CULL_BOTH)>>9);
        raw_slot_stale=stale;
        /* Raw culling may skip projection entirely. Every subsequent legacy
         * draw must re-upload its needed fixed-cache slots. */
        vc_slot_stale=0xffffffffu;
        return cmd;
    }
    return gfx_cpu_indexed_run_original(cmd);
}
'''
BACK=r'''
/* Experimental seven-word input stream; CPU retains exact native LOAD. */
static uint32_t raw_context_last[5];
static unsigned raw_context_valid;
void gfx_gpu_raw_slot_batch(const struct gfx_raw_slot *vertices,const uint32_t *jobs,unsigned count,uint8_t alpha,const uint32_t context[5],unsigned cull) {
PREFIX
    unsigned changed=!raw_context_valid;
    for(unsigned i=0;i<5;i++)changed|=raw_context_last[i]!=context[i];
    if(changed) {
        _gpu_stream_reserve_words(6);uint32_t*p=_gpu_ring_claim();*p++=0x61000005u;
        for(unsigned i=0;i<5;i++){*p++=context[i];raw_context_last[i]=context[i];}
        _gpu_ring_commit(6);raw_context_valid=1;g_vc_words+=6;
    }
    for(unsigned t=0;t<count;t++) {
        uint32_t job=jobs[t];unsigned dirty=(job>>15)&7u;
        unsigned words=7*((dirty&1)+((dirty>>1)&1)+((dirty>>2)&1))+2;
        _gpu_stream_reserve_words(words);uint32_t*p=_gpu_ring_claim();
        for(unsigned k=0;k<3;k++)if(dirty&(1u<<k)) {
            unsigned slot=(job>>(5*k))&31u;const struct gfx_raw_slot*v=&vertices[slot];
            *p++=0x60000006u|(slot<<13);
            uint32_t x,y,w,inv;memcpy(&x,&v->x,4);memcpy(&y,&v->y,4);memcpy(&w,&v->w,4);memcpy(&inv,&v->w_inv,4);
            *p++=x;*p++=y;*p++=w;*p++=inv;
            /* Cost is intentionally included: original UV fields are floats
             * that contain exact signed16 integers for this guarded subset. */
            *p++=(uint16_t)(int16_t)v->u|((uint32_t)(uint16_t)(int16_t)v->v<<16);
            *p++=v->rgba;g_vc_loads++;
        }
        *p++=0x62000001u;*p++=(job&32767u)|(cull<<15);
        _gpu_ring_commit(words);g_vc_words+=words;g_vc_tris_cached++;
        if(++g_vc_kick>=32u){g_vc_kick=0;of_gpu_kick();}
    }
}
'''
def main():
 inputs={n:(BASE/n).read_text() for n in json.loads((BASE/'build-normal.json').read_text())['inputs']};old=inputs.copy();s=inputs['gfx_pc.c']
 anchor='static __attribute__((noinline)) void gfx_sp_vertex(';assert s.count(anchor)==1;s=s.replace(anchor,STATE+'\n'+anchor)
 anchor='    gfx_cpu_flush_fog(~overwritten);';assert s.count(anchor)==1;s=s.replace(anchor,'    raw_slot_stale|=overwritten;raw_slot_good&=~overwritten;\n'+anchor)
 anchor='''        d->w_inv = RSPV_RCP(w);''';assert s.count(anchor)==1;s=s.replace(anchor,anchor+'''
        if(dest_index<32 && raw_slot_eligible(d))raw_slot_good|=1u<<dest_index;''')
 s=s.replace('gfx_cpu_indexed_run(', 'gfx_cpu_indexed_run_original(')
 anchor='#define C0(pos, width)';assert s.count(anchor)==1;s=s.replace(anchor,RUN+'\n'+anchor)
 s=s.replace('Gfx *next = gfx_cpu_indexed_run_original(cmd);','Gfx *next = gfx_raw_slot_run(cmd);')
 anchor='void gfx_start_frame(void) {';assert s.count(anchor)==1;s=s.replace(anchor,anchor+'\n    raw_slot_stale=0xffffffffu;raw_slot_good=0;')
 inputs['gfx_pc.c']=s
 h=inputs['gfx_gpu.h'];anchor='struct gfx_vc_vtx {';assert h.count(anchor)==1
 h=h.replace(anchor,'''struct gfx_raw_slot {float x,y,z,w,w_inv,u,v;uint32_t rgba;uint8_t clip,pad[3];};
void gfx_gpu_raw_slot_batch(const struct gfx_raw_slot *,const uint32_t *,unsigned,uint8_t,const uint32_t[5],unsigned);

'''+anchor);inputs['gfx_gpu.h']=h
 s=inputs['gfx_gpu.c'];start=s.index('void gfx_gpu_vtx_cache_batch(');body=s.index('    SM64_PERF_SCOPE',start);end=s.index('    /* No state mutation',body);prefix=s[body:end]
 s=s[:start]+BACK.replace('PREFIX',prefix)+'\n'+s[start:]
 inputs['gfx_gpu.c']=s
 OUT.mkdir(exist_ok=False)
 for n,c in inputs.items():(OUT/n).write_text(c)
 (OUT/'candidate.patch').write_text(''.join(''.join(difflib.unified_diff(old[n].splitlines(True),inputs[n].splitlines(True),fromfile='F11/'+n,tofile='raw-slot/'+n)) for n in inputs if inputs[n]!=old[n]))
 write(OUT/'source.json',{'parent':'checks-state-f11','source_sha256':{n:sha(OUT/n) for n in inputs},'helper_sha256':sha(Path(__file__)),'scope':'Private raw-slot CPU producer cost and exact-stream experiment. New opcodes require unqualified hardware, never deploy.'})
 print(OUT)
if __name__=='__main__':main()
