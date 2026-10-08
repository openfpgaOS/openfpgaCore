#!/usr/bin/env python3
"""Private LOD5 bounded source-pool recycling; hardware ABI remains unchanged."""
import difflib
from pathlib import Path
from phase10_experiment import CAMP,INPUTS,sha,write
BASE=CAMP/'phase10/load-cram-driver-v1';OUT=CAMP/'phase10/lod5-recycle-driver-v1'
def rep(t,a,b):
 assert t.count(a)==1,(a,t.count(a));return t.replace(a,b)
def main():
 OUT.mkdir(exist_ok=False)
 for n in INPUTS:(OUT/n).write_bytes((BASE/n).read_bytes())
 p=OUT/'gfx_gpu.c';old=p.read_text();t=old
 t=rep(t,'/* Private LOD5 source pool. Immutable ELF Vtx data only; never recycled within\n * this application. Texture exhaustion can change the texture route without\n * destroying these reserved bytes. A miss uploads actual cached source words. */','''/* Private LOD5 source pool. Immutable ELF Vtx data only. Capacity misses may
 * recycle the complete idle pool, at most once per120 rendered frames after
 * the first recycle. Texture-route fallback preserves this reserved arena. */''')
 t=rep(t,'static unsigned g_cram_vertex_enabled;','''static unsigned g_cram_vertex_enabled;
static uint32_t g_cram_vertex_frame,g_cram_vertex_last_recycle;
static unsigned g_cram_vertex_recycled;
/* Recycles and throttled capacity misses; cold-path counters only. */
uint32_t cram_vertex_recycle_stats[2];
static __attribute__((cold,noinline)) int gpu_cram_vertex_recycle(void) {
    if(g_cram_vertex_recycled &&
       (uint32_t)(g_cram_vertex_frame-g_cram_vertex_last_recycle)<120u) {
        cram_vertex_recycle_stats[1]++;return 0;
    }
    /* The synchronous caller has drained LOAD and copied or abandoned its
     * BRAM result. GPU packets contain values, never source-pool pointers.
     * No renderer/interrupt concurrently uses this CPU-only descriptor table;
     * clear every old key before any later upload can reuse its storage. */
    memset(g_cram_vertex_source,0,sizeof(g_cram_vertex_source));
    g_cram_vertex_top=CRAM_VERTEX_BEGIN;
    g_cram_vertex_last_recycle=g_cram_vertex_frame;
    g_cram_vertex_recycled=1;cram_vertex_recycle_stats[0]++;
    return 1;
}''')
 t=rep(t,'    for(unsigned probe=0;probe<CRAM_VERTEX_PROBES;probe++) {','retry_after_recycle:\n    for(unsigned probe=0;probe<CRAM_VERTEX_PROBES;probe++) {')
 t=rep(t,'                cram_vertex_stats[4]++;return 0;','''                if(gpu_cram_vertex_recycle())goto retry_after_recycle;
                cram_vertex_stats[4]++;return 0;''')
 t=rep(t,'    if (++g_tex_epoch == 0) {','''    /* Natural unsigned wrap: unlike g_tex_epoch, zero is a valid serial.
     * Dropped frames do not enter this function and do not age the cooldown. */
    ++g_cram_vertex_frame;
    if (++g_tex_epoch == 0) {''')
 p.write_text(t)
 p=OUT/'gfx_pc.c';a=p.read_text();b=rep(a,'_Static_assert(sizeof(struct LoadedVertex)==36,','_Static_assert(sizeof(Vtx)==24,"LOD5 raw source stride");\n_Static_assert(sizeof(struct LoadedVertex)==36,');p.write_text(b)
 (OUT/'candidate.patch').write_text(''.join(''.join(difflib.unified_diff(x.splitlines(True),y.splitlines(True),fromfile='LOD5/'+n,tofile='LOD5-recycle/'+n))for n,x,y in[('gfx_gpu.c',old,t),('gfx_pc.c',a,b)]))
 write(OUT/'source.json',dict(parent_elf_sha256=sha(BASE/'app.elf'),source_sha256={n:sha(OUT/n)for n in INPUTS},recipe_sha256=sha(Path(__file__)),abi='Unchanged LOD5 0x4c4f4405, raw24B source, 32B result alias',recycle='First capacity recycle immediate; subsequent capacity recycle after120 rendered gpu_start_frame calls using modulo-2^32 subtraction. Hits bypass cooldown; probe exhaustion never recycles.',ownership='Synchronous single renderer; prior LOAD drained and results copied/abandoned before helper. No hardware source pointer escapes into GPU packets.',qualified_for_deployment=False));print(OUT)
if __name__=='__main__':main()
