#!/usr/bin/env python3
"""Private F11 LOD4 adapter: read-only raw BRAM view, canonical CPU vertices."""
from pathlib import Path
import difflib
from phase10_experiment import CAMP, parent_inputs, sha, write

OUT = CAMP / 'phase10/load-alias-driver-v1'
BASE = CAMP / 'phase9/checks-state-f11'

DRIVER = r'''
/* Private LOD4. The fixed view occupies the existing reserved SDRAM guard.
 * It is read-only, cacheable, and never supplied to GPU/audio/DMA consumers.
 * A successful LOAD replaces view slots0..count-1; CPU destination remains
 * the normal36B LoadedVertex array, preserving fog and fallback stores. */
#if defined(TARGET_OPENFPGA) && !GFX_GPU_GEOMETRY_WORKER_APP && !defined(OF_PC)
#define LOAD_ALIAS_ENABLED 1
struct LoadAliasRegisters {
    const volatile uint32_t id, status;
    volatile uint32_t control, source, shape, aspect, coefficient[3];
    volatile uint32_t ambient, diffuse, scales, lighting;
    uint32_t reserved[51];
    volatile uint32_t matrix[16];
};
_Static_assert(__builtin_offsetof(struct LoadAliasRegisters,matrix)==0x100,"LOD4 matrix ABI");
_Static_assert(sizeof(struct LoadedVertex)==36,"LOD4 canonical vertex size");
_Static_assert(__builtin_offsetof(struct LoadedVertex,u)==20,"LOD4 UV offset");
_Static_assert(__builtin_offsetof(struct LoadedVertex,color)==28,"LOD4 color offset");
_Static_assert(__builtin_offsetof(struct LoadedVertex,clip_rej)==32,"LOD4 clip offset");
#define LOAD_ALIAS_REGS ((struct LoadAliasRegisters *)0x4b000000u)
#define LOAD_ALIAS_ID 0x4c4f4404u
#define LOAD_ALIAS_VIEW ((const uint32_t *)0x13f80000u)
/* calls, launched, successful, vertices, ineligible, absent/disabled,
 * unsupported, read errors, reserved, timeout-drained, other error, writes. */
uint32_t load_driver_stats[16];
static unsigned load_alias_present,load_alias_dirty=3,load_alias_controls_valid;
static uint32_t load_alias_scales,load_alias_lighting;
static inline uint32_t load_alias_bits(float f) {
    uint32_t u;memcpy(&u,&f,4);return u;
}
static __attribute__((noinline)) void load_alias_copy(uint32_t *restrict d,
                                                     const uint32_t *restrict s,
                                                     unsigned count) {
    for(unsigned i=0;i<count;i++,s+=8,d+=9) {
        d[0]=s[0];d[1]=s[1];d[2]=s[2];d[3]=s[3];d[4]=s[4];
        uint32_t uv=s[5];float u=(float)(int16_t)uv,v=(float)(int16_t)(uv>>16);
        memcpy(d+5,&u,4);memcpy(d+6,&v,4);d[7]=s[6];d[8]=s[7];
    }
}
static inline void load_alias_discard(unsigned count) {
    __asm__ volatile("fence rw,rw" ::: "memory");
    for(uintptr_t p=(uintptr_t)LOAD_ALIAS_VIEW,
                  end=p+count*32u;p<end;p+=64u)
        __asm__ volatile(".insn i 0x0f,2,x0,%0,0" :: "r"(p) : "memory");
    __asm__ volatile("fence rw,rw" ::: "memory");
}
static __attribute__((noinline)) int load_alias_vertex(unsigned count,unsigned first,
                                                      const Vtx *vertices,float aspect,
                                                      int eager_fog) {
    load_driver_stats[0]++;
    const uintptr_t p=(uintptr_t)vertices,end=(uintptr_t)__EH_FRAME_BEGIN__;
    const unsigned lit=(rsp.geometry_mode&G_LIGHTING)!=0;
    unsigned frm;__asm__ volatile("csrr %0,frm" : "=r"(frm));
    if(!count||first>=32||count>32-first||(p&3)||p<(uintptr_t)&_start||p>=end||
       count>(end-p)/sizeof(Vtx)||eager_fog||frm||
       (rsp.geometry_mode&G_TEXTURE_GEN)||(lit&&rsp.current_num_lights!=2)) {
        load_driver_stats[4]++;return 0;
    }
    if(!load_alias_present) {
        load_alias_present=LOAD_ALIAS_REGS->id==LOAD_ALIAS_ID?1:2;
        if(load_alias_present==1) {
            /* A previous app may have left a job running. Read-only input
             * DMA must drain before any new context is installed. */
            while(LOAD_ALIAS_REGS->status&1u)__asm__ volatile("" ::: "memory");
            load_alias_dirty=3;load_alias_controls_valid=0;
        }
    }
    if(load_alias_present!=1) {load_driver_stats[5]++;return 0;}
    if(LOAD_ALIAS_REGS->status&1u) {
        while(LOAD_ALIAS_REGS->status&1u)__asm__ volatile("" ::: "memory");
        load_alias_present=2;load_driver_stats[10]++;return 0;
    }
    unsigned writes=3;
    if(load_alias_dirty&1) {
        for(unsigned i=0;i<16;i++)
            LOAD_ALIAS_REGS->matrix[i]=load_alias_bits(rsp.MP_matrix[i/4][i%4]);
        LOAD_ALIAS_REGS->aspect=load_alias_bits(aspect);load_alias_dirty&=~1u;writes+=17;
    }
    if(lit&&(load_alias_dirty&2)) {
        for(unsigned i=0;i<3;i++)
            LOAD_ALIAS_REGS->coefficient[i]=load_alias_bits(rsp.current_lights_coeffs[0][i]);
        const uint8_t *a=rsp.current_lights[1].col,*d=rsp.current_lights[0].col;
        LOAD_ALIAS_REGS->ambient=a[0]|((uint32_t)a[1]<<8)|((uint32_t)a[2]<<16);
        LOAD_ALIAS_REGS->diffuse=d[0]|((uint32_t)d[1]<<8)|((uint32_t)d[2]<<16);
        load_alias_dirty&=~2u;writes+=5;
    }
    uint32_t scales=rsp.texture_scaling_factor.s|((uint32_t)rsp.texture_scaling_factor.t<<16);
    if(!load_alias_controls_valid||scales!=load_alias_scales) {
        LOAD_ALIAS_REGS->scales=scales;load_alias_scales=scales;writes++;
    }
    if(!load_alias_controls_valid||lit!=load_alias_lighting) {
        LOAD_ALIAS_REGS->lighting=lit;load_alias_lighting=lit;writes++;
    }
    load_alias_controls_valid=1;
    LOAD_ALIAS_REGS->source=p;LOAD_ALIAS_REGS->shape=count;
    __asm__ volatile("fence rw,rw" ::: "memory");
    LOAD_ALIAS_REGS->control=1;
    load_driver_stats[1]++;load_driver_stats[11]+=writes;
    unsigned remaining=4096,status;
    do {status=LOAD_ALIAS_REGS->status;}while((status&1u)&&--remaining);
    const unsigned timed_out=(status&1u)!=0;
    if(timed_out) {
        /* No local reset may abandon an accepted AXI read. Drain first,
         * disable offload, then use the unchanged CPU fallback. */
        do {status=LOAD_ALIAS_REGS->status;}while(status&1u);
        load_alias_present=2;load_driver_stats[9]++;
    }
    if(status&8u)load_driver_stats[6]++;
    if(status&0x60u)load_driver_stats[7]++;
    if(!timed_out&&(status&6u)==6u&&!(status&0xe8u)) {
        /* The alias is permanently CPU-read-only: no pre-invalidate and
         * no dirty writeback ownership. Invalidate after the entire LOAD,
         * then consume all results before allowing the next START. */
        load_alias_discard(count);
        load_alias_copy((uint32_t *)&rsp.loaded_vertices[first],LOAD_ALIAS_VIEW,count);
        load_driver_stats[2]++;load_driver_stats[3]+=count;return 1;
    }
    load_driver_stats[10]++;return 0;
}
#else
#define LOAD_ALIAS_ENABLED 0
#endif
'''

def main():
    parent = parent_inputs()
    OUT.mkdir(exist_ok=False)
    for name in parent['inputs']:
        (OUT/name).write_bytes((BASE/name).read_bytes())
    p = OUT/'gfx_pc.c'; original = p.read_text(); source = original
    anchor = 'static __attribute__((noinline)) void gfx_sp_vertex('
    assert source.count(anchor) == 1
    source = source.replace(anchor, DRIVER+'\n'+anchor)
    anchor = '    if (n_vertices == 0)\n        return;'
    assert source.count(anchor) == 1
    source = source.replace(anchor, anchor+'''
#if LOAD_ALIAS_ENABLED
    if(rsp.matrix_changed)load_alias_dirty|=1;
    if((rsp.geometry_mode&G_LIGHTING)&&rsp.lights_changed)load_alias_dirty|=2;
#endif''')
    anchor = '    const float aspect_scale = gfx_vertex_aspect_scale;'
    assert source.count(anchor) == 1
    source = source.replace(anchor, anchor+'''
#if LOAD_ALIAS_ENABLED
    if(load_alias_vertex(n_vertices,dest_index,vertices,aspect_scale,eager_fog)) {
#ifdef TARGET_POCKET
        prof_vtx += PROF_TMR() - _pv;
#endif
        return;
    }
#endif''')
    anchor = 'void gfx_start_frame(void) {'
    assert source.count(anchor) == 1
    source = source.replace(anchor, anchor+'''
#if LOAD_ALIAS_ENABLED
    load_alias_dirty=3;load_alias_controls_valid=0;
#endif''')
    p.write_text(source)
    (OUT/'candidate.patch').write_text(''.join(difflib.unified_diff(
        original.splitlines(True),source.splitlines(True),fromfile='F11/gfx_pc.c',
        tofile='load-alias-driver-v1/gfx_pc.c')))
    write(OUT/'source.json',dict(parent_elf_sha256=parent['elf_sha256'],
        recipe_sha256=sha(Path(__file__)),private_only=True,production_changed=False,
        source_sha256={n:sha(OUT/n) for n in parent['inputs']},
        id='0x4c4f4404',alias='0x13f80000',alias_bytes=1024,
        contract='Read-only BRAM view, hardware dest0, one post-invalidate then copy to normal36B CPU vertices. No geometry outputs remain pending at return.',
        qualified_for_deployment=False))
    print(OUT)

if __name__ == '__main__':
    main()
