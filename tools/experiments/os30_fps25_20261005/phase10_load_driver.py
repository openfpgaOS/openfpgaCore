#!/usr/bin/env python3
"""Build inputs for private LOD3 direct-cache-line SM64 driver validation."""
from pathlib import Path
import difflib
import json
from phase10_experiment import CAMP, sha, write

PARENT = CAMP/'phase10/load-layout64-v1'
OUT = CAMP/'phase10/load-driver-v1'
DRIVER = r'''
/* Private LOD3 experiment: an exclusive cache line per output vertex. */
#if defined(TARGET_OPENFPGA) && !GFX_GPU_GEOMETRY_WORKER_APP && !defined(OF_PC)
#define LOAD_ACCEL_ENABLED 1
struct LoadAcceleratorRegisters {
    const volatile uint32_t id, status;
    volatile uint32_t control, source, shape, aspect, coefficient[3];
    volatile uint32_t ambient, diffuse, scales, lighting;
    const volatile uint32_t cycles, compute_cycles, valid_slots;
    volatile uint32_t output;
    uint32_t reserved[47];
    volatile uint32_t matrix[16];
};
_Static_assert(__builtin_offsetof(struct LoadAcceleratorRegisters,matrix)==0x100,"LOD3 matrix ABI");
_Static_assert(__builtin_offsetof(struct LoadAcceleratorRegisters,output)==0x40,"LOD3 output ABI");
_Static_assert(__builtin_offsetof(struct RSP,loaded_vertices)%64==0,"LOD3 output alignment");
#define LOAD_REGS ((struct LoadAcceleratorRegisters *)0x4b000000u)
#define LOAD_ID 0x4c4f4403u
/* 0 calls,1 launched,2 successful,3 vertices,4 eligibility fallback,
 * 5 no device/disabled,6 unsupported,7 read error,8 write error,
 * 9 timeout-then-drained,10 other error,11 register writes. */
uint32_t load_driver_stats[16];
static unsigned load_driver_present,load_driver_dirty=3,load_driver_controls_valid;
static uint32_t load_driver_scales,load_driver_lighting;
static inline uint32_t load_float_bits(float f) {uint32_t u;memcpy(&u,&f,4);return u;}
static inline void load_discard_slots(struct LoadedVertex *first,unsigned count) {
    __asm__ volatile("fence rw,rw" ::: "memory");
    for(unsigned i=0;i<count;i++) {
        __asm__ volatile(".insn i 0x0f,2,x0,%0,0" :: "r"(first+i) : "memory");
    }
    __asm__ volatile("fence rw,rw" ::: "memory");
}
static __attribute__((noinline)) int load_driver_vertex(unsigned count,unsigned first,
                                                       const Vtx *vertices,float aspect,int eager_fog) {
    load_driver_stats[0]++;
    const uintptr_t p=(uintptr_t)vertices,end=(uintptr_t)__EH_FRAME_BEGIN__;
    const unsigned lit=(rsp.geometry_mode&G_LIGHTING)!=0;
    unsigned frm;__asm__ volatile("csrr %0,frm" : "=r"(frm));
    if(!count||first>=32||count>32-first||(p&3)||p<(uintptr_t)&_start||p>=end||
       count>(end-p)/sizeof(Vtx)||eager_fog||frm||
       (rsp.geometry_mode&G_TEXTURE_GEN)||(lit&&rsp.current_num_lights!=2)) {
        load_driver_stats[4]++;return 0;
    }
    if(!load_driver_present)load_driver_present=LOAD_REGS->id==LOAD_ID?1:2;
    if(load_driver_present!=1) {load_driver_stats[5]++;return 0;}
    /* This driver owns the device. An unexpected active job may already own
     * output memory: drain it before disabling offload and returning. */
    if(LOAD_REGS->status&1u) {
        while(LOAD_REGS->status&1u) __asm__ volatile("" ::: "memory");
        load_driver_present=2;load_driver_stats[10]++;return 0;
    }
    unsigned writes=4;
    if(load_driver_dirty&1) {
        for(unsigned i=0;i<16;i++)LOAD_REGS->matrix[i]=load_float_bits(rsp.MP_matrix[i/4][i%4]);
        LOAD_REGS->aspect=load_float_bits(aspect);load_driver_dirty&=~1u;writes+=17;
    }
    if(lit&&(load_driver_dirty&2)) {
        for(unsigned i=0;i<3;i++)LOAD_REGS->coefficient[i]=load_float_bits(rsp.current_lights_coeffs[0][i]);
        const uint8_t *a=rsp.current_lights[1].col,*d=rsp.current_lights[0].col;
        LOAD_REGS->ambient=a[0]|((uint32_t)a[1]<<8)|((uint32_t)a[2]<<16);
        LOAD_REGS->diffuse=d[0]|((uint32_t)d[1]<<8)|((uint32_t)d[2]<<16);
        load_driver_dirty&=~2u;writes+=5;
    }
    uint32_t scales=rsp.texture_scaling_factor.s|((uint32_t)rsp.texture_scaling_factor.t<<16);
    if(!load_driver_controls_valid||scales!=load_driver_scales) {
        LOAD_REGS->scales=scales;load_driver_scales=scales;writes++;
    }
    if(!load_driver_controls_valid||lit!=load_driver_lighting) {
        LOAD_REGS->lighting=lit;load_driver_lighting=lit;writes++;
    }
    load_driver_controls_valid=1;
    struct LoadedVertex *output=&rsp.loaded_vertices[first];
    /* Discard old CPU/fog stores only in the slots this job replaces. No CPU
     * access to these cache lines is permitted until every DMA B has retired. */
    load_discard_slots(output,count);
    LOAD_REGS->source=p;LOAD_REGS->shape=count|(first<<8);
    LOAD_REGS->output=(uintptr_t)output;
    __asm__ volatile("fence rw,rw" ::: "memory");
    LOAD_REGS->control=1;
    load_driver_stats[1]++;load_driver_stats[11]+=writes;
    unsigned remaining=4096,status;
    do {status=LOAD_REGS->status;} while((status&1u)&&--remaining);
    const unsigned timed_out=(status&1u)!=0;
    if(timed_out) {
        /* Accepted AXI writes cannot be safely cancelled. This recovery wait
         * is deliberately unbounded: a missing bus response must never allow
         * CPU fallback to race a late DMA write into owned output slots. */
        do {status=LOAD_REGS->status;} while(status&1u);
        load_driver_present=2;load_driver_stats[9]++;
    }
    load_discard_slots(output,count);
    if(status&8u)load_driver_stats[6]++;
    if(status&0x60u)load_driver_stats[7]++;
    if(status&0x600u)load_driver_stats[8]++;
    if(!timed_out&&(status&6u)==6u&&!(status&0x6e8u)) {
        load_driver_stats[2]++;load_driver_stats[3]+=count;return 1;
    }
    /* Unsupported input produces no DMA output. A write response error may
     * leave a partial result, but active is now zero and every slot is cold;
     * the retained CPU loop safely rewrites all meaningful fields. */
    load_driver_stats[10]++;return 0;
}
#else
#define LOAD_ACCEL_ENABLED 0
#endif
'''

def main():
    record=json.loads((PARENT/'build-normal.json').read_text())
    inputs={n:(PARENT/n).read_text() for n in record['inputs']}
    for n,v in record['inputs'].items(): assert sha(PARENT/n)==v['sha256']
    original=s=inputs['gfx_pc.c']
    anchor='static __attribute__((noinline)) void gfx_sp_vertex('
    assert s.count(anchor)==1;s=s.replace(anchor,DRIVER+'\n'+anchor)
    anchor='''    if (n_vertices == 0)
        return;'''
    assert s.count(anchor)==1;s=s.replace(anchor,anchor+'''
#if LOAD_ACCEL_ENABLED
    if(rsp.matrix_changed)load_driver_dirty|=1;
    if((rsp.geometry_mode&G_LIGHTING)&&rsp.lights_changed)load_driver_dirty|=2;
#endif''')
    anchor='    const float aspect_scale = gfx_vertex_aspect_scale;'
    assert s.count(anchor)==1;s=s.replace(anchor,anchor+'''
#if LOAD_ACCEL_ENABLED
    if(load_driver_vertex(n_vertices,dest_index,vertices,aspect_scale,eager_fog)) {
#ifdef TARGET_POCKET
        prof_vtx += PROF_TMR() - _pv;
#endif
        return;
    }
#endif''')
    anchor='void gfx_start_frame(void) {'
    assert s.count(anchor)==1;s=s.replace(anchor,anchor+'''
#if LOAD_ACCEL_ENABLED
    load_driver_dirty=3;load_driver_controls_valid=0;
#endif''')
    inputs['gfx_pc.c']=s;OUT.mkdir(exist_ok=False)
    for n,c in inputs.items():(OUT/n).write_text(c)
    (OUT/'candidate.patch').write_text(''.join(difflib.unified_diff(original.splitlines(True),s.splitlines(True),fromfile='layout64/gfx_pc.c',tofile='load-driver-v1/gfx_pc.c')))
    write(OUT/'source.json',dict(parent='phase10/load-layout64-v1',helper_sha256=sha(Path(__file__)),source_sha256={n:sha(OUT/n) for n in inputs},feature_id='0x4c4f4403',stride=64,production_changed=False,qualified_for_deployment=False))
    print(OUT)
if __name__=='__main__':main()
