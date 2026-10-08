#!/usr/bin/env python3
"""Passive reference for actual producer records and original F11 GPU batches."""
from pathlib import Path
import subprocess
import sys
from elftools.elf.elffile import ELFFile
from phase10_experiment import CAMP,ROOT,module,sha,write
from phase10_raw_producer_observer import C as FRAME_C
BASE=CAMP/'phase10/run-producer-v1';OUT=CAMP/'phase10/run-consumer-v1';SOURCE=CAMP/'phase5/qsim'
C=r'''
typedef struct {
 FILE *loaded,*runs;const size_t *frame;uint32_t rsp,lo,hi,count,first,source,kind;
 uint32_t words[64*29],nwords,triangles,alpha;
} run_check_observer;
static uint32_t rcword(qsim_cpu *cpu,uint32_t address){uint32_t x;memcpy(&x,qsim_mem_ptr(cpu,address,4),4);return x;}
static void run_check(qsim_cpu *cpu,uint32_t pc){
 run_check_observer *o=cpu->run_check_observer;
 if(pc==cpu->run_check_batch){
  if(o->kind!=2||o->nwords||cpu->x[12]>64)abort();
  o->triangles=cpu->x[12];o->alpha=cpu->x[13];
  for(unsigned t=0;t<o->triangles;t++){
   uint32_t job=rcword(cpu,cpu->x[11]+4*t),dirty=(job>>15)&7;
   for(unsigned k=0;k<3;k++)if(dirty&(1u<<k)){
    unsigned slot=(job>>(5*k))&31;o->words[o->nwords++]=0x56000008u;o->words[o->nwords++]=slot;
    for(unsigned f=0;f<7;f++)o->words[o->nwords++]=rcword(cpu,cpu->x[10]+28*slot+4*f);
   }
   o->words[o->nwords++]=0x54000001u;o->words[o->nwords++]=job&32767;
  }
  return;
 }
 if(pc==cpu->run_check_return){
  cpu->run_check_return=0;
  if(o->kind==1){
   if(o->count&&o->first<32&&o->count<=32-o->first&&o->source>=o->lo&&o->source<o->hi&&o->count<=(o->hi-o->source)/24){
    uint32_t h[4]={*o->frame,o->source,o->count,o->first};fwrite(h,sizeof h,1,o->loaded);
    fwrite(qsim_mem_ptr(cpu,o->rsp+940+36*o->first,36*o->count),36,o->count,o->loaded);
   }
  }else if(o->source>=o->lo&&o->source<o->hi&&8<=o->hi-o->source){
   uint32_t h[6]={*o->frame,o->source,cpu->x[10],o->nwords,o->triangles,o->alpha};
   fwrite(h,sizeof h,1,o->runs);fwrite(o->words,4,o->nwords,o->runs);
  }
  o->kind=0;return;
 }
 if(cpu->run_check_return)abort();cpu->run_check_return=cpu->x[1];
 if(pc==cpu->run_check_vertex){o->kind=1;o->count=cpu->x[10];o->first=cpu->x[11];o->source=cpu->x[12];}
 else{o->kind=2;o->source=cpu->x[10];o->nwords=o->triangles=0;o->alpha=255;}
}
'''
def main():
    OUT.mkdir(exist_ok=False);q=OUT/'qsim';q.mkdir()
    for p in SOURCE.iterdir():
        if p.suffix in ('.c','.h') or p.name=='Makefile':(q/p.name).write_bytes(p.read_bytes())
    def change(name,old,new):
        p=q/name;s=p.read_text();assert s.count(old)==1,(name,old);p.write_text(s.replace(old,new))
    change('cpu.h','    /* architectural state */','''    uint32_t run_check_vertex,run_check_run,run_check_batch,run_check_return;
    void (*run_check)(struct qsim_cpu *,uint32_t);
    void *run_check_observer;
    /* architectural state */''')
    anchor='#define INSN() do { '+chr(92)+'\n'
    change('cpu_exec.h',anchor,anchor+'        if(unlikely(e->pc==cpu->run_check_vertex||e->pc==cpu->run_check_run||e->pc==cpu->run_check_batch||e->pc==cpu->run_check_return))cpu->run_check(cpu,e->pc); '+chr(92)+'\n')
    change('main.c','#include "lint.h"','#include "lint.h"\n'+C)
    with (BASE/'app.elf').open('rb') as f:
        e=ELFFile(f);symbols={s.name:s['st_value'] for s in e.get_section_by_name('.symtab').iter_symbols()}
    setup=f'''    run_check_observer run_observer={{0}};
    run_observer.frame=&nrows;run_observer.rsp={symbols['rsp']}u;run_observer.lo={symbols['_start']}u;run_observer.hi={symbols['__EH_FRAME_BEGIN__']}u;
    snprintf(path,sizeof path,"%s/raw-loaded.bin",out);run_observer.loaded=fopen(path,"wb");if(!run_observer.loaded)abort();
    snprintf(path,sizeof path,"%s/raw-run-ref.bin",out);run_observer.runs=fopen(path,"wb");if(!run_observer.runs)abort();
    E.cpu->run_check_vertex={symbols['gfx_sp_vertex']}u;E.cpu->run_check_run={symbols['gfx_cpu_indexed_run']}u;E.cpu->run_check_batch={symbols['gfx_gpu_vtx_cache_batch']}u;
    E.cpu->run_check=run_check;E.cpu->run_check_observer=&run_observer;
'''
    anchor='    size_t cap_rows = 4096, nrows = 0;';change('main.c',anchor,anchor+'\n'+setup)
    frame=FRAME_C.replace('words>8192','words>rb->size/12').replace('data[11]*8192*4','data[11]*rb->size/3')
    change('main.c','    FILE *culling_csv=NULL;','    FILE *culling_csv=NULL;\n    FILE *raw_dry_csv=NULL,*raw_dry_bin=NULL;')
    change('main.c','                const qsim_sym *diagnostic=',frame+'\n                const qsim_sym *diagnostic=')
    change('main.c','    if(culling_csv)fclose(culling_csv);','''    if(culling_csv)fclose(culling_csv);
    fclose(run_observer.loaded);fclose(run_observer.runs);
    if(raw_dry_csv)fclose(raw_dry_csv);if(raw_dry_bin)fclose(raw_dry_bin);''')
    with (OUT/'build.log').open('x') as log:subprocess.run(['make','-j4'],cwd=q,stdout=log,stderr=subprocess.STDOUT,check=True)
    write(OUT/'observer.json',dict(elf_sha256=sha(BASE/'app.elf'),helper_sha256=sha(Path(__file__)),simulator_sha256=sha(q/'qsim'),source_sha256={p.name:sha(p) for p in q.iterdir() if p.suffix in ('.c','.h') or p.name=='Makefile'}))
    m=module('run_capture',ROOT/'tools/experiments/sm64_batch_cull_20261005/validation/capture.py');m.OUT=OUT
    sys.argv=[str(Path(__file__)),'normal','--elf',str(BASE/'app.elf'),'--exe',str(q/'qsim'),'--geometry','0','--audio'];m.main()
if __name__=='__main__':main()
