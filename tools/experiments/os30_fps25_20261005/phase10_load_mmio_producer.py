#!/usr/bin/env python3
"""Measure exact LOAD-only register preparation beside unchanged F11 rendering.

Volatile shadow registers are ordinary cached memory. This measures CPU issue
and context work, not MMIO bus latency, DMA, completion or result readback.
"""
import difflib
import json
from pathlib import Path
from phase10_experiment import CAMP,sha,write
BASE=CAMP/'phase9/checks-state-f11'
OUT=CAMP/'phase10/load-mmio-producer-v2'
PREFIX=r'''
/* PRIVATE LOAD-ONLY REGISTER SHADOW. Original CPU rendering still executes.
 * These are cached-memory shadows, never live MMIO. Real MMIO latency,
 * ownership invalidation, DMA completion and result copies are excluded. */
#if defined(TARGET_OPENFPGA) && !GFX_GPU_GEOMETRY_WORKER_APP && !defined(OF_PC)
#define LOAD_DRY_ENABLED 1
volatile uint32_t load_mmio_shadow[0x140/4];
uint32_t load_dry_stats[16];
static uint32_t load_dry_result[32*9] __attribute__((aligned(64)));
static unsigned load_dry_dirty=3,load_dry_controls_valid;
static uint32_t load_dry_scale,load_dry_lit;
static __attribute__((noinline)) void load_dry_vertex(unsigned count,unsigned first,
                                                       const Vtx *vertices,float aspect,int eager_fog) {
    const uintptr_t p=(uintptr_t)vertices,end=(uintptr_t)__EH_FRAME_BEGIN__;
    const unsigned lit=(rsp.geometry_mode&G_LIGHTING)!=0;
    load_dry_stats[0]++;load_dry_stats[1]+=count;
    if(!count||first>=32||count>32-first||p<(uintptr_t)&_start||p>=end||count>(end-p)/sizeof(Vtx)) {
        load_dry_stats[2]++;return;
    }
    if(eager_fog || (lit&&((rsp.geometry_mode&G_TEXTURE_GEN)||rsp.current_num_lights!=2))) {
        load_dry_stats[3]++;return;
    }
    unsigned writes=4;
    if(load_dry_dirty&1) {
        const uint32_t *m=(const uint32_t*)rsp.MP_matrix;
        for(unsigned i=0;i<16;i++)load_mmio_shadow[0x100/4+i]=m[i];
        uint32_t a;memcpy(&a,&aspect,4);load_mmio_shadow[0x14/4]=a;
        load_dry_dirty&=~1u;writes+=17;load_dry_stats[6]++;
    }
    if(lit&&(load_dry_dirty&2)) {
        const uint32_t *c=(const uint32_t*)rsp.current_lights_coeffs;
        load_mmio_shadow[0x18/4]=c[0];load_mmio_shadow[0x1c/4]=c[1];load_mmio_shadow[0x20/4]=c[2];
        const uint8_t *a=rsp.current_lights[1].col,*d=rsp.current_lights[0].col;
        load_mmio_shadow[0x24/4]=a[0]|((uint32_t)a[1]<<8)|((uint32_t)a[2]<<16);
        load_mmio_shadow[0x28/4]=d[0]|((uint32_t)d[1]<<8)|((uint32_t)d[2]<<16);
        load_dry_dirty&=~2u;writes+=5;load_dry_stats[7]++;
    }
    uint32_t scale=rsp.texture_scaling_factor.s|((uint32_t)rsp.texture_scaling_factor.t<<16);
    if(!load_dry_controls_valid||scale!=load_dry_scale) {
        load_mmio_shadow[0x2c/4]=scale;load_dry_scale=scale;writes++;
    }
    if(!load_dry_controls_valid||lit!=load_dry_lit) {
        load_mmio_shadow[0x30/4]=lit;load_dry_lit=lit;writes++;
    }
    load_dry_controls_valid=1;
    load_mmio_shadow[0x0c/4]=p;
    load_mmio_shadow[0x10/4]=count|(first<<8);
    load_mmio_shadow[0x40/4]=(uintptr_t)load_dry_result;
    load_mmio_shadow[0x08/4]=1;
    load_dry_stats[4]++;load_dry_stats[5]+=count;load_dry_stats[8]+=writes;
}
#else
#define LOAD_DRY_ENABLED 0
#endif
'''
def main():
    build=json.loads((BASE/'build-normal.json').read_text())
    inputs={n:(BASE/n).read_text() for n in build['inputs']}
    for n,r in build['inputs'].items():assert sha(BASE/n)==r['sha256']
    original=s=inputs['gfx_pc.c']
    old='static __attribute__((noinline)) void gfx_sp_vertex('
    assert s.count(old)==1;s=s.replace(old,PREFIX+'\n'+old)
    old='''    if (n_vertices == 0)
        return;'''
    assert s.count(old)==1;s=s.replace(old,old+'''
#if LOAD_DRY_ENABLED
    if(rsp.matrix_changed)load_dry_dirty|=1;
    if((rsp.geometry_mode&G_LIGHTING)&&rsp.lights_changed)load_dry_dirty|=2;
#endif''')
    old='    const float aspect_scale = gfx_vertex_aspect_scale;'
    assert s.count(old)==1;s=s.replace(old,old+'''
#if LOAD_DRY_ENABLED
    load_dry_vertex(n_vertices,dest_index,vertices,aspect_scale,eager_fog);
#endif''')
    old='void gfx_start_frame(void) {'
    assert s.count(old)==1;s=s.replace(old,old+'''
#if LOAD_DRY_ENABLED
    load_dry_dirty=3;load_dry_controls_valid=0;
#endif''')
    inputs['gfx_pc.c']=s;OUT.mkdir(exist_ok=False)
    for n,c in inputs.items():(OUT/n).write_text(c)
    (OUT/'candidate.patch').write_text(''.join(difflib.unified_diff(original.splitlines(True),s.splitlines(True),fromfile='F11/gfx_pc.c',tofile='load-mmio-producer-v2/gfx_pc.c')))
    write(OUT/'source.json',dict(parent='F11',helper_sha256=sha(Path(__file__)),source_sha256={n:sha(OUT/n) for n in inputs},
       scope='Exact direct-MMIO register preparation/store count, but stores target ordinary cached shadow memory. Original CPU rendering remains enabled.',
       excluded=['real MMIO bus latency','cache invalidation/ownership','DMA and completion waits','result materialization'],
       fallback='mutable/out-of-range input, eager fog, texgen, light count other than one; runtime arithmetic unsupported is hardware responsibility',
       counters='0calls,1vertices,2sourcefallthrough,3attributefallthrough,4eligiblecalls,5eligiblevertices,6matrixupdates,7lightupdates,8registerstores',
       deployment=False))
    print(OUT)
if __name__=='__main__':main()
