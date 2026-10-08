#!/usr/bin/env python3
"""Read actual clipper arguments without changing guest instructions or inputs."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from common import *
import subprocess,shutil
out=OUT/'cpu-kernel';q=out/'qsim-inputs';q.mkdir(exist_ok=False)
for p in (OUT/'qsim-observed').iterdir():
 if p.suffix in ('.c','.h') or p.name=='Makefile':shutil.copy2(p,q/p.name)
def change(name,a,b):
 p=q/name;s=p.read_text();assert s.count(a)==1;(p).write_text(s.replace(a,b,1))
change('cpu.h','    /* architectural state */','''    uint32_t observe_pc;
    void (*observe)(struct qsim_cpu *);
    void *observe_context;
    /* architectural state */''')
change('cpu_exec.h','#define INSN() do { \\\n        size_t _i','#define INSN() do { \\\n        if (unlikely(e->pc == cpu->observe_pc && cpu->observe != NULL)) cpu->observe(cpu); \\\n        size_t _i')
change('main.c','#include "lint.h"','''#include "lint.h"
typedef struct { FILE *out; const size_t *frame; unsigned seen[5],saved[5]; } clip_observer;
static void observe_clip(qsim_cpu *cpu) {
    clip_observer *o=cpu->observe_context;
    unsigned wanted[]={360,400,500,600,720}, index=5;
    for(unsigned i=0;i<5;i++)if(*o->frame==wanted[i])index=i;
    if(index==5 || (o->seen[index]++%13)!=0 || o->saved[index]>=64)return;
    uint32_t head[2]={(uint32_t)*o->frame,cpu->x[13]};
    if(fwrite(head,4,2,o->out)!=2)abort();
    for(unsigned i=0;i<3;i++) {
      const void *p=qsim_mem_ptr(cpu,cpu->x[10+i],36);if(!p)abort();
      if(fwrite(p,36,1,o->out)!=1)abort();
    }
    o->saved[index]++;
}
''')
change('main.c','    size_t cap_rows = 4096, nrows = 0;','''    size_t cap_rows = 4096, nrows = 0;
    const qsim_sym *clip_fn=qsim_syms_lookup(&E.syms,"gfx_clip_triangle.constprop.0");
    if(!clip_fn)abort();
    snprintf(path,sizeof path,"%s/clip-inputs.bin",out);
    clip_observer observer={.out=fopen(path,"wb"),.frame=&nrows};if(!observer.out)abort();
    E.cpu->observe_pc=clip_fn->addr;E.cpu->observe=observe_clip;E.cpu->observe_context=&observer;
    fprintf(stderr,"CLIP INPUT OBSERVER pc=%08x size=%u\\n",clip_fn->addr,clip_fn->size);
''')
change('main.c','    if(culling_csv)fclose(culling_csv);','''    if(culling_csv)fclose(culling_csv);
    fclose(observer.out);
    for(unsigned i=0;i<5;i++)fprintf(stderr,"CLIP INPUTS %u seen=%u saved=%u\\n",i,observer.seen[i],observer.saved[i]);''')
with (out/'observer-build.log').open('w') as f:subprocess.run(['make','-j3'],cwd=q,stdout=f,stderr=subprocess.STDOUT,check=True)
(out/'observer-manifest.json').write_text(json.dumps({'source_sha256':{p.name:sha(p) for p in q.iterdir() if p.is_file()},'parent_sha256':sha(OUT/'qsim-observed/qsim'),'script_sha256':sha(Path(__file__)),'scope':'Observation only at real clipper entry. Five frames, every13th call, up to64 each; copies3x36 bytes only.'},indent=2)+'\n')
print(q/'qsim')
