#!/usr/bin/env python3
"""Private dense canonical alias / COW client, over the sealed LOD5 recycler."""
import difflib
from pathlib import Path
from phase10_experiment import CAMP,INPUTS,sha,write
BASE=CAMP/'phase10/lod5-recycle-driver-v1'
OUT=CAMP/'phase10/lod8-cow-driver-v1'
def rep(t,a,b):
    assert t.count(a)==1,(a,t.count(a));return t.replace(a,b)
def main():
    OUT.mkdir(exist_ok=False)
    for n in INPUTS:(OUT/n).write_bytes((BASE/n).read_bytes())
    p=OUT/'gfx_pc.c';old=p.read_text();t=old
    table=',\n    '.join('&rsp.loaded_vertices[%u]'%i for i in range(32))
    helper='''
/* Private LOD8: alias records are always read-only. Each logical slot selects
 * either its persistent canonical BRAM record or the existing writable RAM.
 * Slots32..63 and rectangle scratch64..67 always use ordinary RAM. */
#if defined(TARGET_OPENFPGA) && !GFX_GPU_GEOMETRY_WORKER_APP && !defined(OF_PC)
#define GFX_CANONICAL_ALIAS8 1
#define CANONICAL_ALIAS_BASE ((uintptr_t)0x13f80000u)
static const struct LoadedVertex *loaded_vertex_source[32] = {
    TABLE
};
static uint32_t loaded_vertex_alias_owned;
static inline __attribute__((always_inline)) const struct LoadedVertex *gfx_vertex_read(unsigned slot) {
    return slot<32?loaded_vertex_source[slot]:&rsp.loaded_vertices[slot];
}
static inline uint32_t gfx_vertex_slot_mask(unsigned first,unsigned count) {
    return count>=32-first?0xffffffffu<<first:((1u<<count)-1u)<<first;
}
static inline void gfx_vertex_use_ram(unsigned first,unsigned count) {
    if(first>=32)return;
    if(count>32-first)count=32-first;
    uint32_t mask=gfx_vertex_slot_mask(first,count);
    if(!(loaded_vertex_alias_owned&mask))return;
    loaded_vertex_alias_owned&=~mask;
    for(unsigned i=first;i<first+count;i++)loaded_vertex_source[i]=&rsp.loaded_vertices[i];
}
static inline struct LoadedVertex *gfx_vertex_write(unsigned slot) {
    struct LoadedVertex *d=&rsp.loaded_vertices[slot];
    const uint32_t bit=1u<<slot;
    if(loaded_vertex_alias_owned&bit) {
        *d=*loaded_vertex_source[slot];
        loaded_vertex_source[slot]=d;
        loaded_vertex_alias_owned&=~bit;
    }
    return d;
}
#else
static inline const struct LoadedVertex *gfx_vertex_read(unsigned slot) { return &rsp.loaded_vertices[slot]; }
static inline struct LoadedVertex *gfx_vertex_write(unsigned slot) { return &rsp.loaded_vertices[slot]; }
static inline void gfx_vertex_use_ram(unsigned first,unsigned count) { (void)first;(void)count; }
#endif
'''.replace('TABLE',table)
    t=rep(t,'} rsp;\n#ifdef TARGET_OPENFPGA','} rsp;\n'+helper+'\n#ifdef TARGET_OPENFPGA')
    t=rep(t,'struct LoadedVertex *d = &rsp.loaded_vertices[slot];','struct LoadedVertex *d = gfx_vertex_write(slot);')
    t=t.replace('LOD5','LOD8').replace('0x4c4f4405u','0x4c4f4408u')
    t=rep(t,' * A successful LOAD replaces view slots0..count-1; CPU destination remains\n * the normal36B LoadedVertex array, preserving fog and fallback stores. */',
        ' * Successful LOADs replace only their destination slots. Software publishes\n * those read-only pointers after rounded invalidation; fog uses RAM COW. */')
    start=t.index('static __attribute__((noinline)) void load_alias_copy(')
    end=t.index('static __attribute__((noinline)) int load_alias_vertex(',start)
    t=t[:start]+'''static inline void load_alias_discard(unsigned first,unsigned count) {
    __asm__ volatile("fence rw,rw" ::: "memory");
    uintptr_t begin=(uintptr_t)LOAD_ALIAS_VIEW+first*sizeof(struct LoadedVertex);
    uintptr_t end=begin+count*sizeof(struct LoadedVertex);
    for(uintptr_t p=begin&~(uintptr_t)63u;p<end;p+=64u)
        __asm__ volatile(".insn i 0x0f,2,x0,%0,0" :: "r"(p) : "memory");
    __asm__ volatile("fence rw,rw" ::: "memory");
}
'''+t[end:]
    t=rep(t,'LOAD_ALIAS_REGS->shape=count;','LOAD_ALIAS_REGS->shape=(first<<8)|count;')
    t=rep(t,'''        /* The cacheable alias is read-only: every line is clean CPU
         * ownership. Invalidate after the entire LOAD,
         * then consume all results before allowing the next START. */''',
        '''        /* Invalidate changed slots, including clean shared-line neighbors.
         * Hardware preserves the other slots. No alias pointer is published
         * before completion and no CPU mutation may target this view. */''') if '        /* The cacheable alias is read-only: every line is clean CPU\n         * ownership. Invalidate after the entire LOAD,\n         * then consume all results before allowing the next START. */' in t else t
    t=rep(t,'        load_alias_discard(count);\n        load_alias_copy((uint32_t *)&rsp.loaded_vertices[first],LOAD_ALIAS_VIEW,count);',
        '''        load_alias_discard(first,count);
        for(unsigned i=first;i<first+count;i++)
            loaded_vertex_source[i]=(const struct LoadedVertex *)LOAD_ALIAS_VIEW+i;
        loaded_vertex_alias_owned|=gfx_vertex_slot_mask(first,count);''')
    # Every fallback fully overwrites RAM; it need not copy stale alias bytes.
    t=rep(t,'#if defined(TARGET_OPENFPGA) && GFX_GPU_GEOMETRY_WORKER_APP\n    const int geometry_result=',
        '    gfx_vertex_use_ram(dest_index,n_vertices);\n#if defined(TARGET_OPENFPGA) && GFX_GPU_GEOMETRY_WORKER_APP\n    const int geometry_result=')
    for names in [('vtx1_idx','vtx2_idx','vtx3_idx'),('a','b','c')]:
        for k,s in enumerate(names,1):
            t=rep(t,f'    struct LoadedVertex *v{k} = &rsp.loaded_vertices[{s}];',
                    f'    const struct LoadedVertex *v{k} = gfx_vertex_read({s});')
    t=rep(t,'static inline bool gfx_clip_triangle(struct LoadedVertex *v1, struct LoadedVertex *v2, struct LoadedVertex *v3,',
        'static inline bool gfx_clip_triangle(const struct LoadedVertex *v1, const struct LoadedVertex *v2, const struct LoadedVertex *v3,')
    for k in range(3):
        t=rep(t,f'const struct LoadedVertex *v{k+1} = &rsp.loaded_vertices[s[{k}]];',
              f'const struct LoadedVertex *v{k+1} = gfx_vertex_read(s[{k}]);')
    t=rep(t,'const struct LoadedVertex *v = &rsp.loaded_vertices[slot[0]];',
        'const struct LoadedVertex *v = gfx_vertex_read(slot[0]);')
    t=rep(t,'''reject[t] = gfx_cpu_batch_reject(&rsp.loaded_vertices[s[0]],
                                       &rsp.loaded_vertices[s[1]],
                                       &rsp.loaded_vertices[s[2]]);''',
        '''reject[t] = gfx_cpu_batch_reject(gfx_vertex_read(s[0]),
                                       gfx_vertex_read(s[1]),
                                       gfx_vertex_read(s[2]));''')
    # This spelling occurs once in the active indexed preparation loop.
    t=rep(t,'            const struct LoadedVertex *v = &rsp.loaded_vertices[i];',
        '            const struct LoadedVertex *v = gfx_vertex_read(i);')
    # Both CPU RAM and dense alias use the same36B slot index mapping.
    t=rep(t,'static int gfx_vc_try_push(','''static inline uintptr_t gfx_vc_slot_offset(const struct LoadedVertex *v) {
    uintptr_t offset=(uintptr_t)v-(uintptr_t)rsp.loaded_vertices;
#ifdef GFX_CANONICAL_ALIAS8
    if(offset>=32*sizeof(struct LoadedVertex)) {
        uintptr_t alias=(uintptr_t)v-CANONICAL_ALIAS_BASE;
        if(alias<32*sizeof(struct LoadedVertex))offset=alias;
    }
#endif
    return offset;
}

static int gfx_vc_try_push(''')
    t=rep(t,'const uintptr_t offset = (uintptr_t)v_arr[i] - (uintptr_t)rsp.loaded_vertices;',
        'const uintptr_t offset = gfx_vc_slot_offset(v_arr[i]);')
    t=rep(t,'uintptr_t offset=(uintptr_t)v_arr[i]-(uintptr_t)rsp.loaded_vertices;',
        'uintptr_t offset=gfx_vc_slot_offset(v_arr[i]);')
    p.write_text(t)
    p=OUT/'gfx_gpu.c';oldgpu=p.read_text();gpu=oldgpu.replace('LOD5','LOD8').replace('0x4c4f4405u','0x4c4f4408u')
    gpu=rep(gpu,'BRAM result. GPU packets contain values, never source-pool pointers.',
        'BRAM result ownership. Persistent results do not retain CRAM pointers.\n     * GPU packets contain values, never source-pool pointers.')
    p.write_text(gpu)
    (OUT/'candidate.patch').write_text(''.join(''.join(difflib.unified_diff(a.splitlines(True),b.splitlines(True),fromfile='LOD5-recycle/'+n,tofile='LOD8-COW/'+n))for n,a,b in [('gfx_pc.c',old,t),('gfx_gpu.c',oldgpu,gpu)]))
    write(OUT/'source.json',dict(parent_elf_sha256=sha(BASE/'app.elf'),sources={n:sha(OUT/n)for n in INPUTS},helper_sha256=sha(Path(__file__)),abi='ID0x4c4f4408; shape=(dest<<8)|count; persistent dense36 canonical read-only alias0x13f80000; raw24 source and recycler unchanged',qualified_for_deployment=False))
    print(OUT)
if __name__=='__main__':main()
