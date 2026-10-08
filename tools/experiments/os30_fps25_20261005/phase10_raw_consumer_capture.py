#!/usr/bin/env python3
"""Capture F11 LoadedVertex values alongside real dry producer descriptors."""
from pathlib import Path
import json
import shutil
import subprocess
import sys
from elftools.elf.elffile import ELFFile
from phase10_experiment import CAMP,ROOT,module,sha,write

OUT=CAMP/'phase10/raw-consumer-capture'
BASE=CAMP/'phase10/raw-producer-v2'
SOURCE=CAMP/'phase10/raw-producer-observer/qsim'
C=r'''
typedef struct {FILE *out;const size_t *frame;uint32_t rsp,lo,hi,count,first,source;} dry_check_observer;
static void dry_check(qsim_cpu *cpu,uint32_t pc) {
 dry_check_observer *o=cpu->dry_check_observer;
 if(pc==cpu->dry_check_return) {
  cpu->dry_check_return=0;
  if(o->count&&o->first<32&&o->count<=32-o->first&&o->source>=o->lo&&o->source<o->hi&&o->count<=(o->hi-o->source)/24) {
   uint32_t h[4]={*o->frame,o->source,o->count,o->first};
   fwrite(h,sizeof h,1,o->out);
   fwrite(qsim_mem_ptr(cpu,o->rsp+940+36*o->first,36*o->count),36,o->count,o->out);
  }
 } else {
  if(cpu->dry_check_return)abort();
  o->count=cpu->x[10];o->first=cpu->x[11];o->source=cpu->x[12];
  cpu->dry_check_return=cpu->x[1];
 }
}
'''

def main():
    OUT.mkdir(exist_ok=False);q=OUT/'qsim';q.mkdir()
    for p in SOURCE.iterdir():
        if p.suffix in ('.c','.h') or p.name=='Makefile':shutil.copy2(p,q/p.name)
    def change(name,old,new):
        p=q/name;s=p.read_text();assert s.count(old)==1,(name,old);p.write_text(s.replace(old,new))
    change('cpu.h','    /* architectural state */','''    uint32_t dry_check_vertex, dry_check_return;
    void (*dry_check)(struct qsim_cpu *, uint32_t);
    void *dry_check_observer;
    /* architectural state */''')
    old='#define INSN() do { '+chr(92)+'\n'
    change('cpu_exec.h',old,old+'        if(unlikely(e->pc==cpu->dry_check_vertex || e->pc==cpu->dry_check_return))cpu->dry_check(cpu,e->pc); '+chr(92)+'\n')
    change('main.c','#include "lint.h"','#include "lint.h"\n'+C)
    with (BASE/'app.elf').open('rb') as f:
        e=ELFFile(f);symbols={s.name:s['st_value'] for s in e.get_section_by_name('.symtab').iter_symbols()}
    setup=f'''    dry_check_observer dry_observer={{0}};
    dry_observer.frame=&nrows;dry_observer.rsp={symbols['rsp']}u;
    dry_observer.lo={symbols['_start']}u;dry_observer.hi={symbols['__EH_FRAME_BEGIN__']}u;
    snprintf(path,sizeof path,"%s/raw-loaded.bin",out);dry_observer.out=fopen(path,"wb");if(!dry_observer.out)abort();
    E.cpu->dry_check_vertex={symbols['gfx_sp_vertex']}u;E.cpu->dry_check=dry_check;E.cpu->dry_check_observer=&dry_observer;
'''
    anchor='    size_t cap_rows = 4096, nrows = 0;';change('main.c',anchor,anchor+'\n'+setup)
    change('main.c','    if(culling_csv)fclose(culling_csv);','    if(culling_csv)fclose(culling_csv);\n    fclose(dry_observer.out);')
    with (OUT/'build.log').open('w') as f:subprocess.run(['make','-j4'],cwd=q,stdout=f,stderr=subprocess.STDOUT,check=True)
    write(OUT/'observer.json',dict(elf_sha256=sha(BASE/'app.elf'),helper_sha256=sha(Path(__file__)),simulator_sha256=sha(q/'qsim'),source_sha256={p.name:sha(p) for p in q.iterdir() if p.suffix in ('.c','.h') or p.name=='Makefile'}))
    m=module('raw_consumer_capture',ROOT/'tools/experiments/sm64_batch_cull_20261005/validation/capture.py');m.OUT=OUT
    sys.argv=[str(Path(__file__)),'normal','--elf',str(BASE/'app.elf'),'--exe',str(OUT/'qsim/qsim'),'--geometry','0','--audio'];m.main()

if __name__=='__main__':main()
