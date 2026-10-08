#!/usr/bin/env python3
"""Private LOD5 real CRAM source pool over the original LOD4 client."""
from pathlib import Path
import difflib
from phase10_experiment import CAMP, INPUTS, sha, write
OUT=CAMP/'phase10/load-cram-driver-v1'
BASE=CAMP/'phase10/load-alias-driver-v1'
def rep(t,a,b):
    assert t.count(a)==1,(a,t.count(a));return t.replace(a,b)
POOL=r'''
/* Private LOD5 source pool. Immutable ELF Vtx data only; never recycled within
 * this application. Texture exhaustion can change the texture route without
 * destroying these reserved bytes. A miss uploads actual cached source words. */
#define CRAM_VERTEX_BEGIN 0x00fc0000u
#define CRAM_VERTEX_END   0x01000000u
#define CRAM_VERTEX_ENTRIES 1024u
#define CRAM_VERTEX_PROBES 16u
struct CramVertexSource { uintptr_t source; uint32_t offset_count; };
static struct CramVertexSource g_cram_vertex_source[CRAM_VERTEX_ENTRIES];
static uint32_t g_cram_vertex_top=CRAM_VERTEX_BEGIN;
static unsigned g_cram_vertex_enabled;
/* calls,hits,uploads,uploaded words,capacity failures,probe failures,probes,
 * unavailable. These include the real client work in every measured build. */
uint32_t cram_vertex_stats[8];
int gfx_gpu_cram_vertex_source(const void *source,unsigned count,uint32_t *offset) {
    cram_vertex_stats[0]++;
    if(!g_cram_vertex_enabled||!count||count>32||((uintptr_t)source&3)) {
        cram_vertex_stats[7]++;return 0;
    }
    const uintptr_t p=(uintptr_t)source;
    const unsigned h=((p>>4)^(p>>14)^(count*33u))&(CRAM_VERTEX_ENTRIES-1);
    for(unsigned probe=0;probe<CRAM_VERTEX_PROBES;probe++) {
        struct CramVertexSource *e=&g_cram_vertex_source[(h+probe)&(CRAM_VERTEX_ENTRIES-1)];
        cram_vertex_stats[6]++;
        if(e->source==p&&(e->offset_count>>24)==count) {
            *offset=e->offset_count&0xffffffu;cram_vertex_stats[1]++;return 1;
        }
        if(!e->source) {
            const unsigned bytes=count*24u,allocation=(bytes+63u)&~63u;
            if(allocation>CRAM_VERTEX_END-g_cram_vertex_top) {
                cram_vertex_stats[4]++;return 0;
            }
            /* Caller has drained any prior LOAD. Upload helper drains the final
             * word before publication of this immutable descriptor or START. */
            _of_gpu_fast_tex_upload(g_cram_vertex_top,(const uint32_t *)source,count*6u);
            e->offset_count=g_cram_vertex_top|(count<<24);
            e->source=p;
            *offset=g_cram_vertex_top;g_cram_vertex_top+=allocation;
            cram_vertex_stats[2]++;cram_vertex_stats[3]+=count*6u;return 1;
        }
    }
    cram_vertex_stats[5]++;return 0;
}
'''
def main():
    OUT.mkdir(exist_ok=False)
    for n in INPUTS:(OUT/n).write_bytes((BASE/n).read_bytes())
    diffs=[]
    p=OUT/'gfx_gpu.c';old=p.read_text();t=rep(old,'static int g_fast_tex_active;','static int g_fast_tex_active;\n'+POOL)
    t=rep(t,'    g_fast_tex_size = caps->tex_fast_size & ~15u;', '''    g_fast_tex_size = caps->tex_fast_size & ~15u;
    /* Reserve the source arena before even the permanent white texture.
     * A prior application LOAD survives CPU reset, so drain it before any
     * upload can overwrite reserved CRAM. Do not reset the per-app cache. */
    if(caps->tex_fast_size==CRAM_VERTEX_END &&
       *(volatile uint32_t *)0x4b000000u==0x4c4f4405u) {
        while(*(volatile uint32_t *)0x4b000004u&1u) { }
        g_fast_tex_size=CRAM_VERTEX_BEGIN;
        g_cram_vertex_enabled=1;
    }''')
    p.write_text(t);diffs.append(('gfx_gpu.c',old,t))
    p=OUT/'gfx_gpu.h';old=p.read_text();t=rep(old,'int gfx_gpu_geometry_enabled(void);','''/* Private immutable cached ELF Vtx source to LOD5 CRAM byte offset.
 * Caller must drain LOAD before a miss can upload; zero returns CPU fallback. */
int gfx_gpu_cram_vertex_source(const void *source,unsigned count,uint32_t *offset);
int gfx_gpu_geometry_enabled(void);''');p.write_text(t);diffs.append(('gfx_gpu.h',old,t))
    p=OUT/'gfx_pc.c';old=p.read_text();t=old.replace('LOD4','LOD5').replace('0x4c4f4404u','0x4c4f4405u')
    t=rep(t,'    unsigned writes=3;','''    uint32_t source_offset;
    if(!gfx_gpu_cram_vertex_source(vertices,count,&source_offset)) {
        load_driver_stats[8]++;return 0;
    }
    unsigned writes=3;''')
    t=rep(t,'LOAD_ALIAS_REGS->source=p;LOAD_ALIAS_REGS->shape=count;','LOAD_ALIAS_REGS->source=source_offset;LOAD_ALIAS_REGS->shape=count;')
    t=rep(t,' * unsupported, read errors, reserved, timeout-drained, other error, writes. */',' * unsupported, read errors, source-pool fallback, timeout-drained, other error, writes. */')
    p.write_text(t);diffs.append(('gfx_pc.c',old,t))
    (OUT/'candidate.patch').write_text(''.join(''.join(difflib.unified_diff(a.splitlines(True),b.splitlines(True),fromfile='LOD4/'+n,tofile='LOD5/'+n))for n,a,b in diffs))
    write(OUT/'source.json',dict(private_only=True,parent_elf_sha256=sha(BASE/'app.elf'),recipe_sha256=sha(Path(__file__)),source_sha256={n:sha(OUT/n)for n in INPUTS},id='0x4c4f4405',pool_begin='0x00fc0000',pool_bytes=262144,table_entries=1024,max_probes=16,pool_reset='Never within application lifetime',source='Immutable cached ELF Vtx bytes, real polled upload before LOAD',qualified_for_deployment=False))
    print(OUT)
if __name__=='__main__':main()
