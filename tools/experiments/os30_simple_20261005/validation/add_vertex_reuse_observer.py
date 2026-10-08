#!/usr/bin/env python3
"""Observe exact duplicate position bits in immutable static G_VTX batches."""
from common import *
import shutil,subprocess
parent=OUT/'qsim-observed';q=OUT/'qsim-vertex-reuse-observed';q.mkdir(exist_ok=False)
assert sha(parent/'qsim')=='509cfbb58a1e5544f269e41dea58d36db4eff703c3d4ffe26a08579ffb27ef0d'
for p in parent.iterdir():
 if p.suffix in ('.c','.h') or p.name=='Makefile':shutil.copy2(p,q/p.name)
def change(name,a,b):
 p=q/name;s=p.read_text();assert s.count(a)==1,(name,a);p.write_text(s.replace(a,b,1))
change('cpu.h','    /* architectural state */','''    /* Host-only observer; never consumed by guest or modeled timing. */
    uint32_t observe_pc;
    void (*observe)(struct qsim_cpu *);
    void *observe_context;
    /* architectural state */''')
macro_line='#define INSN() do { '+chr(92)+'\n'
change('cpu_exec.h',macro_line+'        size_t _i',macro_line+'        if (unlikely(e->pc == cpu->observe_pc && cpu->observe != NULL)) cpu->observe(cpu); '+chr(92)+'\n        size_t _i')
change('main.c','#include "lint.h"','''#include "lint.h"
typedef struct {
 FILE *out;const size_t *frame;uint32_t low,high;
} vertex_reuse_observer;
static void observe_vertex_reuse(qsim_cpu *cpu) {
 const vertex_reuse_observer *o=cpu->observe_context;
 const uint32_t count=cpu->x[10],dest=cpu->x[11],address=cpu->x[12];
 const unsigned eligible=count>0 && count<=32 && address>=o->low
   && address<=o->high && count<=(o->high-address)/24u;
 unsigned duplicates=0,attribute_seams=0;
 if(eligible) {
  const unsigned char *v=qsim_mem_ptr(cpu,address,count*24u);if(!v)abort();
  for(unsigned i=0;i<count;i++)for(unsigned j=0;j<i;j++) {
   if(!memcmp(v+i*24u,v+j*24u,12)) {
    duplicates++;attribute_seams+=memcmp(v+i*24u+14,v+j*24u+14,8)!=0;break;
   }
  }
 }
 fprintf(o->out,"%zu,%u,%u,%u,%u,%u,%u\\n",*o->frame,count,dest,address,eligible,duplicates,attribute_seams);
}
''')
change('main.c','    size_t cap_rows = 4096, nrows = 0;','''    size_t cap_rows = 4096, nrows = 0;
    const qsim_sym *vertex_fn=qsim_syms_lookup(&E.syms,"gfx_sp_vertex");
    const qsim_sym *vertex_low=qsim_syms_lookup(&E.syms,"_start");
    const qsim_sym *vertex_high=qsim_syms_lookup(&E.syms,"__EH_FRAME_BEGIN__");
    if(!vertex_fn || vertex_fn->addr!=0x104d1e40u || vertex_fn->size!=2800u ||
       !vertex_low || !vertex_high || vertex_low->addr>=vertex_high->addr)abort();
    snprintf(path,sizeof path,"%s/vertex-reuse.csv",out);
    vertex_reuse_observer vertex_obs={fopen(path,"w"),&nrows,vertex_low->addr,vertex_high->addr};
    if(!vertex_obs.out)abort();
    fprintf(vertex_obs.out,"frame,count,dest,address,eligible,duplicates,attribute_seams\\n");
    E.cpu->observe_pc=vertex_fn->addr;E.cpu->observe=observe_vertex_reuse;E.cpu->observe_context=&vertex_obs;
    snprintf(path,sizeof path,"%s/vertex-reuse-symbols.json",out);
    FILE *vertex_symbols=fopen(path,"w");if(!vertex_symbols)abort();
    fprintf(vertex_symbols,"{\\"entry\\":%u,\\"size\\":%u,\\"low\\":%u,\\"high\\":%u,\\"vertex_stride\\":24,\\"position_bytes\\":12,\\"attribute_bytes_offset\\":14,\\"attribute_bytes_count\\":8}\\n",vertex_fn->addr,vertex_fn->size,vertex_low->addr,vertex_high->addr);fclose(vertex_symbols);''')
change('main.c','    if(culling_csv)fclose(culling_csv);','    if(culling_csv)fclose(culling_csv);\n    fclose(vertex_obs.out);')
changed=[p.name for p in q.iterdir() if p.is_file() and sha(p)!=sha(parent/p.name)]
assert sorted(changed)==['cpu.h','cpu_exec.h','main.c']
with (OUT/'qsim-vertex-reuse-observed-build.log').open('w') as f:subprocess.run(['make','-j3'],cwd=q,stdout=f,stderr=subprocess.STDOUT,check=True)
meta={'script_sha256':sha(Path(__file__)),'parent_qsim_sha256':sha(parent/'qsim'),'exe_sha256':sha(q/'qsim'),'changed':changed,'source_sha256':{p.name:sha(p) for p in q.iterdir() if p.is_file()},'scope':'Host-only callback before baseline gfx_sp_vertex entry104d1e40. Reads ABI a0=count,a1=dest,a2=Vtxpointer. For batches1..32 wholly inside _start..__EH_FRAME_BEGIN__, compares all12 object-position bytes per24-byte Vtx. No guest/model mutation. Attribute seam count compares tc+color bytes14..21 only. Source region eligibility follows proposed release guard; linker/source immutability requires separate review.'}
(OUT/'qsim-vertex-reuse-observed-manifest.json').write_text(json.dumps(meta,indent=2)+'\n');print(q/'qsim')
