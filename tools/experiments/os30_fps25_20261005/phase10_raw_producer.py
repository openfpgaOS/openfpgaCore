#!/usr/bin/env python3
"""Real dry producer work beside unchanged F11 rendering, never offload."""
import difflib
import json
from pathlib import Path
from phase10_experiment import CAMP,sha,write
BASE=CAMP/'phase9/checks-state-f11';OUT=CAMP/'phase10/raw-producer-v1'
PREFIX=r'''
/* PRIVATE DRY PRODUCER. These owned records are never sent to hardware.
 * Geometry/material rendering below remains the original F11 implementation.
 * The dry queue retires only after legacy finish_render returns; a real
 * transport must replace that acknowledgement with its combined geometry,
 * raster and DMA completion fence. Immutable source buffers never recycle. */
#if defined(TARGET_OPENFPGA) && !GFX_GPU_GEOMETRY_WORKER_APP && !defined(OF_PC)
#define RAW_DRY_ENABLED 1
enum { RAW_DRY_WORDS=8192, RAW_DRY_FRAMES=3 };
uint32_t raw_dry_streams[RAW_DRY_FRAMES][RAW_DRY_WORDS];
uint32_t raw_dry_stats[32];
static uint32_t raw_dry_busy[RAW_DRY_FRAMES],raw_dry_slot,raw_dry_words;
static uint32_t raw_dry_next_frame,raw_dry_owned;
static const Gfx *raw_dry_next;
static uint32_t raw_dry_matrix[17],raw_dry_light[22],raw_dry_controls[3];
static unsigned raw_dry_context_valid;
static uint32_t *raw_dry_reserve(unsigned n) {
    if(n>RAW_DRY_WORDS-raw_dry_words){raw_dry_stats[9]++;return NULL;}
    uint32_t *w=raw_dry_streams[raw_dry_slot]+raw_dry_words;
    raw_dry_words+=n;raw_dry_stats[8]+=n;return w;
}
static void raw_dry_begin(void) {
    raw_dry_slot=raw_dry_next_frame++%RAW_DRY_FRAMES;
    if(raw_dry_busy[raw_dry_slot])__builtin_trap();
    raw_dry_busy[raw_dry_slot]=1;raw_dry_words=0;raw_dry_owned=0;
    raw_dry_next=NULL;raw_dry_context_valid=0;
    raw_dry_stats[10]++;raw_dry_stats[11]=raw_dry_slot;
}
static void raw_dry_finish(void) {
    if(!raw_dry_busy[raw_dry_slot])__builtin_trap();
    raw_dry_busy[raw_dry_slot]=0;raw_dry_stats[12]++;
}
static void raw_dry_context(unsigned op,uint32_t *previous,
                            const uint32_t *current,unsigned n) {
    const unsigned mask=1u<<op;
    if((raw_dry_context_valid&mask)&&!memcmp(previous,current,n*4))return;
    uint32_t *w=raw_dry_reserve(n+1);
    if(!w)return;
    *w++=(op<<24)|n;memcpy(w,current,n*4);memcpy(previous,current,n*4);
    raw_dry_context_valid|=mask;raw_dry_stats[op]++;
}
static __attribute__((noinline)) void raw_dry_vertex(unsigned count,unsigned first,const Vtx *vertices,float aspect) {
    const uintptr_t p=(uintptr_t)vertices,end=(uintptr_t)__EH_FRAME_BEGIN__;
    raw_dry_next=NULL;
    const uint32_t mask=first>=32?0:count>=32-first?~0u<<first:((1u<<count)-1u)<<first;
    raw_dry_owned&=~mask;
    if(!count||first>=32||count>32-first||p<(uintptr_t)&_start||p>=end||count>(end-p)/sizeof(Vtx)){
        raw_dry_stats[6]++;return;
    }
    uint32_t matrix[17],light[22],controls[3];
    memcpy(matrix,rsp.MP_matrix,64);memcpy(matrix+16,&aspect,4);
    memcpy(light,rsp.current_lights,36);
    memcpy(light+9,rsp.current_lights_coeffs,24);
    memcpy(light+15,rsp.current_lookat_coeffs,24);
    light[21]=rsp.current_num_lights;
    controls[0]=rsp.geometry_mode;
    if(!configEnableFog)controls[0]&=~G_FOG;
    controls[1]=(uint16_t)rsp.fog_mul|((uint32_t)(uint16_t)rsp.fog_offset<<16);
    controls[2]=rsp.texture_scaling_factor.s|((uint32_t)rsp.texture_scaling_factor.t<<16);
    raw_dry_context(1,raw_dry_matrix,matrix,17);
    raw_dry_context(2,raw_dry_light,light,22);
    raw_dry_context(3,raw_dry_controls,controls,3);
    uint32_t *w=raw_dry_reserve(2);
    if(!w)return;
    w[0]=0x04000000u|(first<<8)|count;w[1]=p;
    raw_dry_owned|=mask;raw_dry_stats[4]++;raw_dry_stats[13]+=count;
}
#else
#define RAW_DRY_ENABLED 0
#endif
'''
RUN=r'''
#if RAW_DRY_ENABLED
struct RawDryRun {uintptr_t address;uint16_t commands,triangles;uint32_t used;};
static struct RawDryRun raw_dry_runs[2048];
static __attribute__((noinline)) void raw_dry_run(const Gfx *cmd) {
    if(raw_dry_next&&(uintptr_t)cmd<(uintptr_t)raw_dry_next)return;
    const uintptr_t p=(uintptr_t)cmd,end=(uintptr_t)__EH_FRAME_BEGIN__;
    if(p<(uintptr_t)&_start||p>=end||sizeof(Gfx)>end-p){raw_dry_stats[7]++;return;}
    unsigned h=((uint32_t)(p>>3)*2654435761u)>>21;
    struct RawDryRun *e=NULL;
    for(unsigned i=0;i<2048;i++,h=(h+1)&2047u){
        e=&raw_dry_runs[h];
        if(e->address==p)break;
        if(e->address)continue;
        e->address=p;raw_dry_stats[14]++;
        const Gfx *c=cmd;
        while((uintptr_t)c<end&&sizeof(Gfx)<=end-(uintptr_t)c){
            unsigned slot[6],n=gfx_geometry_decode(c,slot);
            if(!n||e->triangles+n>64)break;
            unsigned mask=0;
            for(unsigned k=0;k<3*n;k++){
                if(slot[k]>=32){raw_dry_stats[15]++;return;}
                mask|=1u<<slot[k];
            }
            e->used|=mask;e->commands++;e->triangles+=n;c++;
        }
        break;
    }
    if(!e||e->address!=p||!e->triangles||(e->used&raw_dry_owned)!=e->used){raw_dry_stats[7]++;return;}
    uint32_t *w=raw_dry_reserve(3);if(!w)return;
    w[0]=0x05000000u|e->commands;w[1]=p;w[2]=e->triangles;
    raw_dry_next=cmd+e->commands;raw_dry_stats[5]++;raw_dry_stats[16]+=e->triangles;
}
#endif
'''

def main():
    build=json.loads((BASE/'build-normal.json').read_text());inputs={n:(BASE/n).read_text() for n in build['inputs']}
    for n,record in build['inputs'].items():assert sha(BASE/n)==record['sha256']
    s=original=inputs['gfx_pc.c']
    old='static __attribute__((noinline)) void gfx_sp_vertex('
    assert s.count(old)==1;s=s.replace(old,PREFIX+'\n'+old)
    old='    const float aspect_scale = gfx_vertex_aspect_scale;'
    assert s.count(old)==1;s=s.replace(old,old+'\n#if RAW_DRY_ENABLED\n    raw_dry_vertex(n_vertices,dest_index,vertices,aspect_scale);\n#endif')
    old='static void gfx_geometry_submit(const struct ColorCombiner *comb,int elig,int rgb_in,'
    assert s.count(old)==1;s=s.replace(old,RUN+'\n'+old)
    old='static __attribute__((noinline)) Gfx *gfx_cpu_indexed_run(Gfx *cmd) {'
    assert s.count(old)==1;s=s.replace(old,old+'\n#if RAW_DRY_ENABLED\n    raw_dry_run(cmd);\n#endif')
    old='        uint32_t opcode = cmd->words.w0 >> 24;'
    assert s.count(old)==1;s=s.replace(old,old+'\n#if RAW_DRY_ENABLED\n        if(opcode!=G_TRI1&&opcode!=G_TRI2)raw_dry_next=NULL;\n#endif')
    old='void gfx_start_frame(void) {'
    assert s.count(old)==1;s=s.replace(old,old+'\n#if RAW_DRY_ENABLED\n    raw_dry_begin();\n#endif')
    old='''        gfx_wapi->swap_buffers_end();
    }
#ifdef TARGET_POCKET'''
    assert s.count(old)==1;s=s.replace(old,'''        gfx_wapi->swap_buffers_end();
    }
#if RAW_DRY_ENABLED
    raw_dry_finish();
#endif
#ifdef TARGET_POCKET''')
    inputs['gfx_pc.c']=s;OUT.mkdir(exist_ok=False)
    for n,c in inputs.items():(OUT/n).write_text(c)
    (OUT/'candidate.patch').write_text(''.join(difflib.unified_diff(original.splitlines(True),s.splitlines(True),fromfile='F11/gfx_pc.c',tofile='raw-producer-v1/gfx_pc.c')))
    write(OUT/'source.json',dict(parent='phase9/checks-state-f11',source_sha256={n:sha(OUT/n) for n in inputs},recipe_sha256=sha(Path(__file__)),qualified=False,dry_producer_only=True,original_rendering_still_runs=True,abi={'1':'17wordfloatMVP+aspect','2':'22wordlightfields','3':'3wordvertexcontrols','4':'2wordheader(count,dest)+immutable24byteVtxpointer','5':'3wordheader(commandcount)+immutableGfxpointer+trianglecount'},limits=['No hardwaretransport or accelerator. No offloadspeedupclaim.','Materialresolution,transportcachemaintenance andcombinedcompletionfencestillrequireimplementationandmeasurement.','Drybuffersowneduntilexistinglegacyfinish_render; futurereusemustwaitoncombinedgeometryandrastercompletion.']))
    print(OUT)

if __name__=='__main__':main()
