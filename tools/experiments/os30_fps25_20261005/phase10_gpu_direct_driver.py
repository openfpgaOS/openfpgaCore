#!/usr/bin/env python3
"""Private synchronous rawLOAD/PRECHECK/COMMIT application adapter."""
import difflib,json
from pathlib import Path
from phase10_experiment import CAMP,sha,write
import phase10_load_driver as prior
HERE=Path(__file__).resolve().parent;PARENT=CAMP/'phase9/checks-state-f11';OUT=CAMP/'phase10/gpu-direct-driver-v3'

def make_load():
 s=prior.DRIVER
 a=s.index('struct LoadAcceleratorRegisters');b=s.index('/* 0 calls',a)
 s=s[:a]+(HERE/'phase10_gpu_direct_abi.h').read_text()+'''\n#define LOAD_REGS ((struct GdRegisters *)GD_BASE)
#define LOAD_ID GD_ID
static uint32_t gd_cpu_stale;
'''+s[b:]
 a=s.index('static inline void load_discard_slots(');b=s.index('static __attribute__((noinline)) int load_driver_vertex',a);s=s[:a]+s[b:]
 a=s.index('    struct LoadedVertex *output=');b=s.index('    LOAD_REGS->source=p;',a);s=s[:a]+s[b:]
 s=s.replace('    LOAD_REGS->output=(uintptr_t)output;\n','')
 s=s.replace('    load_discard_slots(output,count);\n','')
 s=s.replace('    unsigned writes=4;','    unsigned writes=3;')
 s=s.replace('if(!load_driver_present)load_driver_present=LOAD_REGS->id==LOAD_ID?1:2;','''if(!load_driver_present) {
        load_driver_present=LOAD_REGS->id==LOAD_ID?1:2;
        if(load_driver_present==1) {
            while(LOAD_REGS->status&1u)__asm__ volatile("" ::: "memory");
            LOAD_REGS->control=GD_CANCEL_PRECHECK;
            LOAD_REGS->invalidate=0xffffffffu;
            load_driver_dirty=3;load_driver_controls_valid=0;
        }
    }''')
 s=s.replace('load_driver_stats[2]++;load_driver_stats[3]+=count;return 1;','load_driver_stats[2]++;load_driver_stats[3]+=count;gd_cpu_stale|=(count==32?~0u:((1u<<count)-1))<<first;return 1;')
 s=s.replace('LOAD_REGS->coefficient','LOAD_REGS->coefficient')
 s=s.replace('/* Private LOD3 experiment: an exclusive cache line per output vertex. */','/* Private GDR1: raw results remain in device BRAM; CPU materializes only fallback slots. */')
 s=s.replace('    /* Unsupported input produces no DMA output.', '    /* Unsupported input produces no GPU output. Original-context CPU fallback follows.\n     * Unsupported input produces no DMA output.')
 s=s.replace('static unsigned load_driver_present,','static unsigned load_driver_present,')
 pos=s.index('\n#else\n#define LOAD_ACCEL_ENABLED 0')
 funcs=r'''
static void gd_overwrite(uint32_t mask) {
    gd_cpu_stale&=~mask;
    if(load_driver_present==1)LOAD_REGS->invalidate=mask;
}
static __attribute__((noinline)) void gd_materialize(uint32_t mask) {
    uint32_t needed=mask&gd_cpu_stale;
    for(unsigned slot=0;needed;slot++,needed>>=1) {
        if(!(needed&1u))continue;
        struct LoadedVertex *v=&rsp.loaded_vertices[slot];
        uint32_t *p=(uint32_t *)v;
        for(unsigned row=0;row<5;row++)p[row]=LOAD_REGS->raw[row][slot];
        uint32_t uv=LOAD_REGS->raw[5][slot];
        v->u=(float)(int16_t)uv;v->v=(float)(int16_t)(uv>>16);
        p[7]=LOAD_REGS->raw[6][slot];v->clip_rej=LOAD_REGS->raw[7][slot]&255;
        load_driver_stats[12]++;
    }
    gd_cpu_stale&=~mask;
}
static unsigned gd_wait(void) {
    unsigned status;
    do {status=LOAD_REGS->status;}while(status&1u);
    return status;
}
'''
 s=s[:pos]+funcs+s[pos:]
 return s
RUN=r'''
/* This wrapper uses completed private raw slots; no normal-path CPU vertex
 * loads, transform/cull arithmetic, quantization or per-triangle packet stores. */
static __attribute__((noinline)) Gfx *gfx_gpu_direct_run(Gfx *cmd) {
    const uintptr_t p=(uintptr_t)cmd,end=(uintptr_t)__EH_FRAME_BEGIN__;
    unsigned frm;__asm__ volatile("csrr %0,frm" : "=r"(frm));
    if(frm || load_driver_present!=1 || p<(uintptr_t)&_start || p>=end || end-p<512 || (p&7)) {
        gd_materialize(0xffffffffu);return gfx_cpu_indexed_run_original(cmd);
    }
    LOAD_REGS->command_source=p;
    LOAD_REGS->run_config=((rsp.geometry_mode&G_CULL_BOTH)>>9)|(64u<<8);
    LOAD_REGS->control=GD_START_PRECHECK;
    unsigned status=gd_wait();
    if((status&6)!=6 || status&0xe8) {
        LOAD_REGS->control=GD_CANCEL_PRECHECK;
        gd_materialize(0xffffffffu);return gfx_cpu_indexed_run_original(cmd);
    }
    unsigned accepted=LOAD_REGS->accepted_commands,live=LOAD_REGS->live_triangles;
    unsigned reason=LOAD_REGS->reason,counts=LOAD_REGS->reject_counts;
    if(live) {
        bool use_fog,use_alpha,use_texture;
        struct ColorCombiner *comb=gfx_prepare_triangle_material(&use_fog,&use_alpha,&use_texture);
        int rgb_in=triangle_vc_rgb_input,alpha_in=triangle_vc_alpha_input;
        unsigned rgb=rgb_in<0?255u:comb->shader_input_mapping[0][rgb_in];
        unsigned asrc=alpha_in<0?8u:comb->shader_input_mapping[1][alpha_in];
        if(!triangle_vc_elig || rgb==CC_LOD || asrc==CC_LOD || (asrc==CC_SHADE&&!use_fog)) {
            LOAD_REGS->control=GD_CANCEL_PRECHECK;
            gd_materialize(0xffffffffu);return gfx_cpu_indexed_run_original(cmd);
        }
        uint8_t alpha=255;
        if(asrc!=8 && asrc!=CC_SHADE) {
            struct RGBA tmp;const struct LoadedVertex *v=&rsp.loaded_vertices[0];
            alpha=gfx_vc_resolve(asrc,v,v,&tmp)->a;
        }
        const int textured=triangle_vc_elig&GFX_VC_TEXTURED;
        const unsigned linear=textured&&(rdp.other_mode_h&(3U<<G_MDSFT_TEXTFILT))!=G_TF_POINT;
        gfx_vc_texture_state(comb,textured,linear?0.5f:0.0f);
        if(buf_vbo_len)gfx_flush();
        gfx_gpu_direct_begin(alpha);
        LOAD_REGS->draw_flags=(textured?1u:0)|(rgb<<8);
        LOAD_REGS->initial_stale=vc_slot_stale;
        uint32_t prim,env;memcpy(&prim,&rdp.prim_color,4);memcpy(&env,&rdp.env_color,4);
        LOAD_REGS->prim_rgb=prim;LOAD_REGS->env_rgb=env;
        LOAD_REGS->tile=rdp.texture_tile.uls|((uint32_t)rdp.texture_tile.ult<<16);
        LOAD_REGS->linear_filter=linear;
        LOAD_REGS->control=GD_START_COMMIT;
        status=gd_wait();
        /* Whole-COMMIT publication is atomic; a failed commit rewinds every
         * unpublished word, so the original CPU run can safely replace it. */
        if((status&6)!=6 || status&0xe8) {
            gfx_gpu_direct_end(0,0,0);
            LOAD_REGS->control=GD_CANCEL_PRECHECK;
            gd_materialize(0xffffffffu);return gfx_cpu_indexed_run_original(cmd);
        }
        vc_slot_stale=LOAD_REGS->final_stale;
        gfx_gpu_direct_end(LOAD_REGS->emitted_words,LOAD_REGS->emitted_vertices,live);
        load_driver_stats[13]+=live;
    } else LOAD_REGS->control=GD_CANCEL_PRECHECK;
#ifdef TARGET_POCKET
    tri_diag.total+=(counts>>16)&255;
    tri_diag.clip_reject+=counts&255;
    tri_diag.cull_reject+=(counts>>8)&255;
    tri_diag.direct+=live;
#endif
    cmd+=accepted;
    if(reason) {
        unsigned slot[6];unsigned n=gfx_geometry_decode(cmd,slot);uint32_t mask=0;
        for(unsigned i=0;i<3*n;i++)if(slot[i]<32)mask|=1u<<slot[i];
        gd_materialize(mask);
        for(unsigned t=0;t<n;t++) {
            unsigned *s=&slot[t*3];
            unsigned rejected=gfx_cpu_batch_reject(&rsp.loaded_vertices[s[0]],&rsp.loaded_vertices[s[1]],&rsp.loaded_vertices[s[2]]);
            gfx_cpu_push_checked(s[0],s[1],s[2],rejected);
        }
        if(n)++cmd;
        load_driver_stats[14]++;
    }
    return cmd;
}
'''

def main():
 inputs={n:(PARENT/n).read_text() for n in json.loads((PARENT/'build-normal.json').read_text())['inputs']}; originals=inputs.copy();s=inputs['gfx_pc.c']
 s=s.replace('static uint32_t cpu_fog_pending;','static void gd_materialize(uint32_t mask);\nstatic void gd_overwrite(uint32_t mask);\nstatic uint32_t cpu_fog_pending;')
 s=s.replace('    cpu_fog_pending &= ~needed;','    gd_materialize(needed);\n    cpu_fog_pending &= ~needed;')
 anchor='static __attribute__((noinline)) void gfx_sp_vertex(';assert s.count(anchor)==1;s=s.replace(anchor,make_load()+'\n'+anchor)
 anchor='''    if (n_vertices == 0)
        return;''';assert s.count(anchor)==1;s=s.replace(anchor,anchor+'''
#if LOAD_ACCEL_ENABLED
    if(rsp.matrix_changed)load_driver_dirty|=1;
    if((rsp.geometry_mode&G_LIGHTING)&&rsp.lights_changed)load_driver_dirty|=2;
#endif''')
 anchor='    gfx_cpu_flush_fog(~overwritten);';assert s.count(anchor)==1;s=s.replace(anchor,'    gd_overwrite(overwritten);\n'+anchor)
 anchor='    const float aspect_scale = gfx_vertex_aspect_scale;';assert s.count(anchor)==1;s=s.replace(anchor,anchor+'''
#if LOAD_ACCEL_ENABLED
    if(load_driver_vertex(n_vertices,dest_index,vertices,aspect_scale,eager_fog)) {
#ifdef TARGET_POCKET
        prof_vtx += PROF_TMR() - _pv;
#endif
        return;
    }
#endif''')
 anchor='static void gfx_sp_tri1(uint8_t vtx1_idx, uint8_t vtx2_idx, uint8_t vtx3_idx) {';assert s.count(anchor)==1;s=s.replace(anchor,anchor+'''
#if LOAD_ACCEL_ENABLED
    uint32_t need=0;
    if(vtx1_idx<32)need|=1u<<vtx1_idx;
    if(vtx2_idx<32)need|=1u<<vtx2_idx;
    if(vtx3_idx<32)need|=1u<<vtx3_idx;
    gd_materialize(need);
#endif''')
 anchor='static __attribute__((noinline)) Gfx *gfx_cpu_indexed_run(Gfx *cmd) {';assert s.count(anchor)==1;s=s.replace(anchor,anchor.replace('gfx_cpu_indexed_run','gfx_cpu_indexed_run_original'))
 anchor='\n#define C0(pos, width)';assert s.count(anchor)==1;s=s.replace(anchor,'\n'+RUN+anchor)
 s=s.replace('Gfx *next = gfx_cpu_indexed_run(cmd);','Gfx *next = gfx_gpu_direct_run(cmd);')
 anchor='void gfx_start_frame(void) {';s=s.replace(anchor,anchor+'''
#if LOAD_ACCEL_ENABLED
    load_driver_dirty=3;load_driver_controls_valid=0;
#endif''')
 inputs['gfx_pc.c']=s
 s=inputs['gfx_gpu.c'];start=s.index('void gfx_gpu_vtx_cache_batch(');body=s.index('    SM64_PERF_SCOPE(SM64_PERF_SEND);',start);end=s.index('    /* No state mutation occurs',body)
 begin='void gfx_gpu_direct_begin(uint8_t alpha) {\n'+s[body:end]+'''    of_gpu_kick_now();
}
void gfx_gpu_direct_end(unsigned words,unsigned vertices,unsigned triangles) {
#ifndef OF_PC
    _gpu_wrptr=GPU_RING_WRPTR;
    _gpu_known_rdptr=GPU_RING_RDPTR;
#endif
    g_vc_words+=words;g_vc_loads+=vertices;g_vc_tris_cached+=triangles;g_vc_kick=0;
}
'''
 s=s[:start]+begin+s[start:];inputs['gfx_gpu.c']=s
 inputs['gfx_gpu.h']+='\nvoid gfx_gpu_direct_begin(uint8_t alpha);\nvoid gfx_gpu_direct_end(unsigned words,unsigned vertices,unsigned triangles);\n'
 OUT.mkdir(exist_ok=False)
 for n,t in inputs.items():(OUT/n).write_text(t)
 (OUT/'candidate.patch').write_text(''.join(''.join(difflib.unified_diff(originals[n].splitlines(True),inputs[n].splitlines(True),fromfile='F11/'+n,tofile='gpu-direct-driver-v1/'+n)) for n in inputs))
 write(OUT/'source.json',dict(parent='F11',helper_sha256=sha(Path(__file__)),abi_sha256=sha(HERE/'phase10_gpu_direct_abi.h'),source_sha256={n:sha(OUT/n) for n in inputs},private=True,functional_model_required=True,deployment=False))
 print(OUT)
if __name__=='__main__':main()
