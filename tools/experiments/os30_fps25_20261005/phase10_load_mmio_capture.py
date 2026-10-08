#!/usr/bin/env python3
"""Passive per-LOAD size, eligibility and register-write observer."""
from pathlib import Path
import subprocess
import sys
from elftools.elf.elffile import ELFFile
from phase10_experiment import CAMP,ROOT,module,sha,write
OUT=CAMP/'phase10/load-mmio-producer-v2'
SOURCE=CAMP/'phase5/qsim'
C=r'''
typedef struct {
 FILE *jobs;const size_t *frame;uint32_t stats,count,first,source,eager,eligible,writes;
} load_mmio_observer;
static uint32_t lmword(qsim_cpu *cpu,uint32_t address){uint32_t x;memcpy(&x,qsim_mem_ptr(cpu,address,4),4);return x;}
static void load_mmio_check(qsim_cpu *cpu,uint32_t pc){
 load_mmio_observer *o=cpu->load_mmio_observer;
 if(pc==cpu->load_mmio_return){
  cpu->load_mmio_return=0;
  uint32_t h[7]={*o->frame,o->count,o->first,lmword(cpu,o->stats+16)-o->eligible,
      lmword(cpu,o->stats+32)-o->writes,o->source,o->eager};
  fwrite(h,sizeof h,1,o->jobs);return;
 }
 if(cpu->load_mmio_return)abort();cpu->load_mmio_return=cpu->x[1];
 o->count=cpu->x[10];o->first=cpu->x[11];o->source=cpu->x[12];o->eager=cpu->x[13];
 o->eligible=lmword(cpu,o->stats+16);o->writes=lmword(cpu,o->stats+32);
}
'''
def main():
    q=OUT/'observer-qsim';q.mkdir(exist_ok=False)
    for p in SOURCE.iterdir():
        if p.suffix in ('.c','.h') or p.name=='Makefile':(q/p.name).write_bytes(p.read_bytes())
    def change(name,old,new):
        p=q/name;s=p.read_text();assert s.count(old)==1,(name,old);p.write_text(s.replace(old,new))
    change('cpu.h','    /* architectural state */','''    uint32_t load_mmio_vertex,load_mmio_return;
    void (*load_mmio_check)(struct qsim_cpu *,uint32_t);
    void *load_mmio_observer;
    /* architectural state */''')
    anchor='#define INSN() do { '+chr(92)+'\n'
    change('cpu_exec.h',anchor,anchor+'        if(unlikely(e->pc==cpu->load_mmio_vertex||e->pc==cpu->load_mmio_return))cpu->load_mmio_check(cpu,e->pc); '+chr(92)+'\n')
    change('main.c','#include "lint.h"','#include "lint.h"\n'+C)
    with (OUT/'app.elf').open('rb') as f:
        e=ELFFile(f);symbols={s.name:s['st_value'] for s in e.get_section_by_name('.symtab').iter_symbols()}
    setup=f'''    load_mmio_observer load_observer={{0}};
    load_observer.frame=&nrows;load_observer.stats={symbols['load_dry_stats']}u;
    snprintf(path,sizeof path,"%s/load-mmio-jobs.bin",out);load_observer.jobs=fopen(path,"wb");if(!load_observer.jobs)abort();
    E.cpu->load_mmio_vertex={symbols['load_dry_vertex']}u;
    E.cpu->load_mmio_check=load_mmio_check;E.cpu->load_mmio_observer=&load_observer;
'''
    anchor='    size_t cap_rows = 4096, nrows = 0;';change('main.c',anchor,anchor+'\n'+setup)
    change('main.c','    if(culling_csv)fclose(culling_csv);','    if(culling_csv)fclose(culling_csv);\n    fclose(load_observer.jobs);')
    with (OUT/'observer-build.log').open('x') as log:subprocess.run(['make','-j4'],cwd=q,stdout=log,stderr=subprocess.STDOUT,check=True)
    write(OUT/'observer.json',dict(elf_sha256=sha(OUT/'app.elf'),helper_sha256=sha(Path(__file__)),simulator_sha256=sha(q/'qsim'),source_sha256={p.name:sha(p) for p in q.iterdir() if p.suffix in ('.c','.h') or p.name=='Makefile'},passive=True))
    m=module('load_capture',ROOT/'tools/experiments/sm64_batch_cull_20261005/validation/capture.py');m.OUT=OUT
    sys.argv=[str(Path(__file__)),'normal','--elf',str(OUT/'app.elf'),'--exe',str(q/'qsim'),'--geometry','0','--audio'];m.main()
if __name__=='__main__':main()
