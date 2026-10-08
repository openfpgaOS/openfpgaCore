#!/usr/bin/env python3
"""Private full G_VTX memo, keyed by exact inputs and rendering state."""
from pathlib import Path
import json, shutil, hashlib, difflib
from phase10_experiment import OUT, PARENT, INPUTS
HELPER=r'''
#if defined(TARGET_OPENFPGA) && !GFX_GPU_GEOMETRY_WORKER_APP && !defined(OF_DBG_FACE) && !defined(OF_PC)
#define GFX_BATCH_MEMO 1
/* Every byte of the source batch and all RSP state read by vertex processing
 * are compared. Mutable vertices, camera changes and changed lights miss.
 * Pending fog bookkeeping runs before lookup; cached alpha is the original
 * post-G_VTX value, before any later deferred fog materialization. */
#define CPU_BATCH_STATE_BYTES (offsetof(struct RSP, loaded_vertices) - offsetof(struct RSP, MP_matrix))
struct CpuBatchMemo {
    const Vtx *source;
    unsigned count, fog_mode, rounding;
    float aspect;
    unsigned char state[CPU_BATCH_STATE_BYTES];
    Vtx input[32];
    struct LoadedVertex output[32];
};
static struct CpuBatchMemo cpu_batch_memo[128];
static unsigned cpu_batch_rounding(void) {
#if defined(__riscv)
    unsigned value;
    __asm__ volatile ("frrm %0" : "=r"(value));
    return value;
#else
    return 0;
#endif
}
static __attribute__((noinline)) struct CpuBatchMemo *cpu_batch_lookup(
        const Vtx *source, unsigned count, unsigned first, float aspect,
        unsigned fog_mode, bool *hit) {
    *hit = false;
    if (count < 8 || first >= 32 || count > 32-first) return NULL;
    uintptr_t key=(uintptr_t)source;
    struct CpuBatchMemo *entry=&cpu_batch_memo[((key>>4)^(key>>11))&127u];
    unsigned rounding=cpu_batch_rounding();
    if (entry->source == source && entry->count == count &&
        entry->fog_mode == fog_mode && entry->rounding == rounding &&
        memcmp(&entry->aspect,&aspect,sizeof(aspect))==0 &&
        memcmp(entry->state,rsp.MP_matrix,CPU_BATCH_STATE_BYTES)==0 &&
        memcmp(entry->input,source,count*sizeof(Vtx))==0) {
        memcpy(&rsp.loaded_vertices[first],entry->output,count*sizeof(entry->output[0]));
        *hit=true;
        return entry;
    }
    entry->source=source;entry->count=count;entry->fog_mode=fog_mode;
    entry->aspect=aspect;entry->rounding=rounding;
    memcpy(entry->state,rsp.MP_matrix,CPU_BATCH_STATE_BYTES);
    memcpy(entry->input,source,count*sizeof(Vtx));
    return entry;
}
#else
#define GFX_BATCH_MEMO 0
#endif
'''
LOOKUP='''#if GFX_BATCH_MEMO
    const unsigned batch_first=dest_index;
    bool batch_hit;
    struct CpuBatchMemo *batch_entry=cpu_batch_lookup(vertices,n_vertices,dest_index,
        aspect_scale,(unsigned)configEnableFog | ((unsigned)eager_fog<<1),&batch_hit);
    if (batch_hit) {
#ifdef TARGET_POCKET
        prof_vtx += PROF_TMR() - _pv;
#endif
        return;
    }
#endif
'''
SAVE='''#if GFX_BATCH_MEMO
    if(batch_entry) memcpy(batch_entry->output,&rsp.loaded_vertices[batch_first],
                           n_vertices*sizeof(batch_entry->output[0]));
#endif
'''
def main():
 out=OUT/'batch-memo-v1';out.mkdir(exist_ok=False)
 for name in INPUTS:shutil.copy2(PARENT/name,out/name)
 original=(out/'gfx_pc.c').read_text()
 anchor='static __attribute__((noinline)) void gfx_sp_vertex('
 assert original.count(anchor)==1
 result=original.replace(anchor,HELPER+'\n'+anchor,1)
 anchor='    SM64_PERF_COUNT(SM64_PERF_CPU,n_vertices);\n'
 assert result.count(anchor)==1
 result=result.replace(anchor,anchor+LOOKUP,1)
 anchor='''    }
    }
#ifdef TARGET_POCKET
    prof_vtx += PROF_TMR() - _pv;
#endif
}

static inline struct ColorCombiner'''
 assert result.count(anchor)==1
 result=result.replace(anchor,'    }\n    }\n'+SAVE+anchor[len('    }\n    }\n'):],1)
 (out/'gfx_pc.c').write_text(result)
 (out/'change.patch').write_text(''.join(difflib.unified_diff(original.splitlines(True),result.splitlines(True))))
 shutil.copy2(__file__,out/'prepare.py')
 (out/'intent.json').write_text(json.dumps({'candidate':out.name,'private':True,'claim':'No performance claim pending paired captures','scope':'Cross-frame and cross-command exact batch reuse; source contents, full relevant RSP state, aspect, fog mode and rounding checked. Exceptions flags are not replayed.'},indent=2)+'\n')
 print(out)
if __name__=='__main__':main()
