#!/usr/bin/env python3
"""Private conservative experiment: omit provably dead, wholly clipped static runs."""
from pathlib import Path
import json,shutil,difflib
from phase10_experiment import OUT,PARENT,INPUTS
HELPER=r'''
#if defined(TARGET_OPENFPGA) && !GFX_GPU_GEOMETRY_WORKER_APP && !defined(OF_PC) && defined(F3DEX_GBI_2)
struct CpuClosedRun {
    const Gfx *command;
    Gfx *next;
    float box[6];
};
static struct CpuClosedRun cpu_closed_runs[512];
static float cpu_closed_planes[6][4],cpu_closed_errors[6][4];
static unsigned cpu_closed_cached_serial;
static float cpu_closed_cached_aspect;
static int cpu_closed_readonly(const void *ptr,unsigned bytes) {
    uintptr_t a=(uintptr_t)ptr,e=(uintptr_t)__EH_FRAME_BEGIN__;
    return a>=(uintptr_t)&_start && a<e && bytes<=e-a;
}
static __attribute__((noinline)) Gfx *cpu_closed_skip(Gfx *cmd) {
    /* The first load after any matrix change follows the full CPU path. */
    if(rsp.matrix_changed || !cpu_closed_readonly(cmd,2*sizeof(Gfx)))return NULL;
    uintptr_t h=(uintptr_t)cmd;
    unsigned index=((h>>3)^(h>>12))&511u;
    struct CpuClosedRun *entry=&cpu_closed_runs[index];
    for(unsigned n=0;n<4;n++,index=(index+1)&511u) {
        entry=&cpu_closed_runs[index];
        if(!entry->command || entry->command==cmd)break;
    }
    if(entry->command!=cmd) {
        entry->command=cmd;entry->next=NULL;
        unsigned count=(cmd->words.w0>>12)&255u;
        unsigned first=((cmd->words.w0>>1)&127u)-count;
        const Vtx *vertices=seg_addr(cmd->words.w1);
        if(count<8 || count>32 || first>=32 || count>32-first ||
            !cpu_closed_readonly(vertices,count*sizeof(Vtx)))return NULL;
        Gfx *next=cmd+1;
        for(unsigned scan=0;;scan++,next++) {
            if(scan>=128 || !cpu_closed_readonly(next,sizeof(Gfx)))return NULL;
            unsigned opcode=next->words.w0>>24;
            if(opcode==G_VTX) {
                unsigned nc=(next->words.w0>>12)&255u;
                unsigned nf=((next->words.w0>>1)&127u)-nc;
                if(nf>first || nf+nc<first+count || nc>32 || nf>=32 || nc>32-nf)return NULL;
                break;
            }
            unsigned slots[6];
            unsigned triangles=gfx_geometry_decode(next,slots);
            if(!triangles)return NULL;
            for(unsigned j=0;j<3*triangles;j++)
                if(slots[j]<first || slots[j]>=first+count)return NULL;
        }
        for(unsigned axis=0;axis<3;axis++) {
            float lo=vertices[0].v.ob[axis],hi=lo;
            for(unsigned j=1;j<count;j++) {
                float v=vertices[j].v.ob[axis];
                if(v<lo)lo=v;if(v>hi)hi=v;
            }
            /* Broad finite domain; unsupported inputs retain ordinary math. */
            if(!(lo>=-100000000.f && hi<=100000000.f))return NULL;
            entry->box[axis]=lo;entry->box[axis+3]=hi;
        }
        entry->next=next;
    }
    if(!entry->next)return NULL;
    float aspect=gfx_vertex_aspect_scale;
    if(cpu_closed_cached_serial!=cpu_closed_mvp_serial || cpu_closed_cached_aspect!=aspect) {
        const float (*m)[4]=rsp.MP_matrix;
        for(unsigned plane=0;plane<6;plane++) {
            unsigned axis=plane/2;
            float scale=axis==0?aspect:1.f;
            if(plane&1)scale=-scale;
            for(unsigned k=0;k<4;k++) {
                float a=m[k][axis]*scale,b=m[k][3];
                cpu_closed_planes[plane][k]=a+b;
                cpu_closed_errors[plane][k]=fabsf(a)+fabsf(b);
            }
        }
        cpu_closed_cached_serial=cpu_closed_mvp_serial;
        cpu_closed_cached_aspect=aspect;
    }
    const float *box=entry->box;
    float mx=fmaxf(fabsf(box[0]),fabsf(box[3]));
    float my=fmaxf(fabsf(box[1]),fabsf(box[4]));
    float mz=fmaxf(fabsf(box[2]),fabsf(box[5]));
    for(unsigned plane=0;plane<6;plane++) {
        const float *c=cpu_closed_planes[plane],*e=cpu_closed_errors[plane];
        float x=c[0]>=0?box[3]:box[0];
        float y=c[1]>=0?box[4]:box[1];
        float z=c[2]>=0?box[5]:box[2];
        float upper=x*c[0]+y*c[1]+z*c[2]+c[3];
        float mag=mx*e[0]+my*e[1]+mz*e[2]+e[3];
        /* Screening threshold only: a numerical proof and actual-A oracle
         * are required before deployment. Wide slack absorbs ordinary
         * contraction/rounding changes; boundary boxes always execute CPU. */
        if(mag<1e30f && upper < -(mag*(1.f/1024.f)+1.f))return entry->next;
    }
    return NULL;
}
#endif
'''
def main():
 out=OUT/'closed-cull-v1';out.mkdir(exist_ok=False)
 for name in INPUTS:shutil.copy2(PARENT/name,out/name)
 original=(out/'gfx_pc.c').read_text()
 s=original.replace('static uint32_t vertex_state_epoch=1,lighting_epoch=1;','static uint32_t vertex_state_epoch=1,lighting_epoch=1;\nstatic unsigned cpu_closed_mvp_serial=1;',1)
 anchor='        rsp.matrix_changed = false;'
 assert s.count(anchor)==1
 s=s.replace(anchor,anchor+'\n#ifdef TARGET_OPENFPGA\n        ++cpu_closed_mvp_serial;\n#endif',1)
 anchor='#define C0(pos, width)'
 assert s.count(anchor)==1
 s=s.replace(anchor,HELPER+'\n'+anchor,1)
 anchor='''            case G_VTX:
#ifdef F3DEX_GBI_2'''
 replace='''            case G_VTX:
#if defined(TARGET_OPENFPGA) && !GFX_GPU_GEOMETRY_WORKER_APP && !defined(OF_PC) && defined(F3DEX_GBI_2)
                { Gfx *next=cpu_closed_skip(cmd); if(next) { cmd=next-1;break; } }
#endif
#ifdef F3DEX_GBI_2'''
 assert s.count(anchor)==1;s=s.replace(anchor,replace,1)
 (out/'gfx_pc.c').write_text(s)
 (out/'change.patch').write_text(''.join(difflib.unified_diff(original.splitlines(True),s.splitlines(True))))
 shutil.copy2(__file__,out/'prepare.py')
 (out/'intent.json').write_text(json.dumps({'private':True,'candidate':out.name,'rendering_qualified':False,'required':'Conservative bound proof plus actual CPU oracle, lifetime tests, command/image replay; initially CPU cost screen only','scope':'Static G_VTX + contiguous triangles whose slots are entirely overwritten by following G_VTX. No deferred state, context copies or per-vertex confirmation.'},indent=2)+'\n')
 print(out)
if __name__=='__main__':main()
