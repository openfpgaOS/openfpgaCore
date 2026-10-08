#!/usr/bin/env python3
"""Passive F11 raw-geometry coverage/traffic observer, no guest cost discount."""
import argparse
import csv
import json
from pathlib import Path
import shutil
import subprocess
import sys
from elftools.elf.elffile import ELFFile
from phase10_experiment import CAMP,ROOT,module,sha,write

OUT=CAMP/'phase10/raw-frontend-observer'
BASE=CAMP/'phase9/checks-state-f11'
SOURCE=CAMP/'phase5/qsim'

C=r'''
typedef struct {
 FILE *out; const size_t *frame;
 uint32_t low, high, rsp, aspect, kind, address, count, first, immutable;
 uint32_t source[64], owned[64], generation[64], next_generation;
 uint64_t start;
 unsigned char matrix[68], light[88], controls[12];
 unsigned have_context;
} raw_frontend_observer;
static uint32_t raw_u32(const unsigned char *p) {uint32_t x; memcpy(&x,p,4);return x;}
static void raw_observe(qsim_cpu *cpu,uint32_t pc) {
 raw_frontend_observer *o=cpu->raw_observer;
 if(pc==cpu->raw_return) {
  cpu->raw_return=0;
  uint64_t cycles=cpu->model->cyc-o->start;
  if(o->kind==1) {
   const unsigned char *rsp=qsim_mem_ptr(cpu,o->rsp,940);
   const unsigned char *aspect=qsim_mem_ptr(cpu,o->aspect,4);
   if(!rsp||!aspect)abort();
   unsigned char matrix[68],light[88],controls[12];
   memcpy(matrix,rsp+708,64);memcpy(matrix+64,aspect,4);
   memset(light,0,sizeof light);memcpy(light,rsp+837,36);
   memcpy(light+36,rsp+876,48);light[84]=rsp[924];
   memcpy(controls,rsp+928,12);
   unsigned mc=!o->have_context||memcmp(matrix,o->matrix,sizeof matrix);
   unsigned lc=!o->have_context||memcmp(light,o->light,sizeof light);
   unsigned cc=!o->have_context||memcmp(controls,o->controls,sizeof controls);
   memcpy(o->matrix,matrix,sizeof matrix);memcpy(o->light,light,sizeof light);
   memcpy(o->controls,controls,sizeof controls);o->have_context=1;
   fprintf(o->out,"%zu,vertex,%u,%u,%u,%u,0,0,0,%u,0,%llu,%u,%u,%u,%u\n",*o->frame,o->address,o->count,o->first,o->immutable,o->count*24u,(unsigned long long)cycles,mc,lc,cc,raw_u32(rsp+928));
  } else {
   uint32_t end=cpu->x[10];
   if(end<o->address||(end-o->address)%8||(end-o->address)>512)abort();
   unsigned commands=(end-o->address)/8,triangles=0,all_owned=1,mixed=0,valid=1;
   uint32_t first_generation=0;
   for(unsigned c=0;c<commands;c++) {
    const unsigned char *p=qsim_mem_ptr(cpu,o->address+c*8,8);if(!p)abort();
    uint32_t w0=raw_u32(p),w1=raw_u32(p+4),op=w0>>24;
    unsigned n=op==5?1:op==6?2:0;if(!n)abort();triangles+=n;
    uint32_t words[2]={w0,w1};
    for(unsigned t=0;t<n;t++)for(unsigned j=0;j<3;j++) {
     unsigned slot=(words[t]>>(16-8*j)&255u)/2;
     if(slot>=64){valid=0;all_owned=0;continue;}
     all_owned&=o->owned[slot];
     if(!first_generation)first_generation=o->generation[slot];
     else if(first_generation!=o->generation[slot])mixed=1;
    }
   }
   unsigned immutable=o->address>=o->low&&end<=o->high;
   fprintf(o->out,"%zu,run,%u,%u,0,%u,%u,%u,%u,0,%u,%llu,0,0,0,%u\n",*o->frame,o->address,commands,immutable,triangles,all_owned&&valid,mixed,commands*8,(unsigned long long)cycles,raw_u32(qsim_mem_ptr(cpu,o->rsp+928,4)));
  }
  return;
 }
 if(cpu->raw_return)abort();
 o->start=cpu->model->cyc;cpu->raw_return=cpu->x[1];
 if(pc==cpu->raw_vertex) {
  o->kind=1;o->count=cpu->x[10];o->first=cpu->x[11];o->address=cpu->x[12];
  o->immutable=o->address>=o->low&&o->address<o->high&&o->count<=(o->high-o->address)/24;
  uint32_t generation=++o->next_generation;
  for(unsigned i=0;i<o->count&&o->first+i<64;i++) {
   o->source[o->first+i]=o->address+24*i;o->owned[o->first+i]=o->immutable;
   o->generation[o->first+i]=generation;
  }
 } else {o->kind=2;o->address=cpu->x[10];}
}
'''

def prepare():
    OUT.mkdir(exist_ok=False);q=OUT/'qsim';q.mkdir()
    for p in SOURCE.iterdir():
        if p.suffix in ('.c','.h') or p.name=='Makefile':shutil.copy2(p,q/p.name)
    def change(name,old,new):
        p=q/name;s=p.read_text();assert s.count(old)==1,(name,old);p.write_text(s.replace(old,new))
    change('cpu.h','    /* architectural state */','''    uint32_t raw_vertex, raw_run, raw_return;
    void (*raw_observe)(struct qsim_cpu *, uint32_t);
    void *raw_observer;
    /* architectural state */''')
    old='#define INSN() do { '+chr(92)+'\n'
    new=old+'        if (unlikely(e->pc == cpu->raw_vertex || e->pc == cpu->raw_run || e->pc == cpu->raw_return)) cpu->raw_observe(cpu,e->pc); '+chr(92)+'\n'
    change('cpu_exec.h',old,new)
    change('main.c','#include "lint.h"','#include "lint.h"\n'+C)
    with (BASE/'app.elf').open('rb') as f:
        e=ELFFile(f);symbols={s.name:s for s in e.get_section_by_name('.symtab').iter_symbols()};text=e.get_section_by_name('.text')
        state={name:dict(address=symbols[name]['st_value'],size=symbols[name]['st_size']) for name in ['gfx_sp_vertex','gfx_cpu_indexed_run','rsp','gfx_vertex_aspect_scale']}
        lo,hi=text['sh_addr'],text['sh_addr']+text['sh_size']
    assert state['rsp']['size']==3388
    setup=f'''    raw_frontend_observer raw_obs={{0}};
    raw_obs.frame=&nrows;raw_obs.low={lo}u;raw_obs.high={hi}u;
    raw_obs.rsp={state['rsp']['address']}u;raw_obs.aspect={state['gfx_vertex_aspect_scale']['address']}u;
    snprintf(path,sizeof path,"%s/raw-frontend.csv",out);
    raw_obs.out=fopen(path,"w");if(!raw_obs.out)abort();
    fprintf(raw_obs.out,"frame,kind,address,count,first,immutable,triangles,all_vertices_immutable,mixed_loads,vertex_bytes,index_bytes,inclusive_cycles,matrix_changed,light_changed,controls_changed,geometry_mode\\n");
    E.cpu->raw_vertex={state['gfx_sp_vertex']['address']}u;E.cpu->raw_run={state['gfx_cpu_indexed_run']['address']}u;
    E.cpu->raw_observe=raw_observe;E.cpu->raw_observer=&raw_obs;
'''
    anchor='    size_t cap_rows = 4096, nrows = 0;';change('main.c',anchor,anchor+'\n'+setup)
    change('main.c','    if(culling_csv)fclose(culling_csv);','    if(culling_csv)fclose(culling_csv);\n    fclose(raw_obs.out);')
    with (OUT/'build.log').open('w') as f:subprocess.run(['make','-j4'],cwd=q,stdout=f,stderr=subprocess.STDOUT,check=True)
    write(OUT/'observer.json',dict(elf_sha256=sha(BASE/'app.elf'),symbols=state,readonly_range=[lo,hi],scope='Passive F11 call-entry/return observer. Original guest instructions and modeled timing unchanged. RSP offsets follow exact3388-byte F11 layout.',helper_sha256=sha(Path(__file__)),simulator_sha256=sha(q/'qsim'),source_sha256={p.name:sha(p) for p in q.iterdir() if p.suffix in ('.c','.h') or p.name=='Makefile'}))

def capture():
    m=module('raw_capture',ROOT/'tools/experiments/sm64_batch_cull_20261005/validation/capture.py');m.OUT=OUT
    sys.argv=[str(Path(__file__)),'normal','--elf',str(BASE/'app.elf'),'--exe',str(OUT/'qsim/qsim'),'--geometry','0','--audio'];m.main()

def analyze():
    baseline=BASE/'capture-normal';current=OUT/'capture-normal';proof={}
    for p in baseline.rglob('*'):
        if not p.is_file():continue
        if p.name in ('frames.csv','scene.csv','geometry.csv','culling.csv') or p.suffix in ('.vec','.tsv'):
            rel=p.relative_to(baseline);assert p.read_bytes()==(current/rel).read_bytes(),rel;proof[str(rel)]=sha(p)
    rows=list(csv.DictReader((current/'raw-frontend.csv').open()))
    for r in rows:
        for k in r:
            if k!='kind':r[k]=int(r[k])
    def summary(rs):
        v=[r for r in rs if r['kind']=='vertex'];t=[r for r in rs if r['kind']=='run']
        vi=[r for r in v if r['immutable']];ti=[r for r in t if r['immutable'] and r['all_vertices_immutable']]
        return dict(vertex_loads=len(v),vertices=sum(r['count'] for r in v),immutable_loads=len(vi),immutable_vertices=sum(r['count'] for r in vi),raw_vertex_bytes=sum(r['vertex_bytes'] for r in vi),run_calls=len(t),triangles=sum(r['triangles'] for r in t),immutable_runs=len(ti),immutable_run_triangles=sum(r['triangles'] for r in ti),raw_index_bytes=sum(r['index_bytes'] for r in ti),mixed_load_runs=sum(r['mixed_loads'] for r in ti),matrix_changes=sum(r['matrix_changed'] for r in v),light_changes=sum(r['light_changed'] for r in v),controls_changes=sum(r['controls_changed'] for r in v),vertex_inclusive_cycles=sum(r['inclusive_cycles'] for r in v),immutable_vertex_inclusive_cycles=sum(r['inclusive_cycles'] for r in vi),run_inclusive_cycles=sum(r['inclusive_cycles'] for r in t),immutable_run_inclusive_cycles=sum(r['inclusive_cycles'] for r in ti),unique_vertex_descriptors=len({(r['address'],r['count'],r['first']) for r in vi}),unique_run_descriptors=len({(r['address'],r['count']) for r in ti}))
    selected=[r for r in rows if 360<=r['frame']<=720]
    res=dict(passed_noninterference=True,evidence_sha256=proof,observation_sha256=sha(current/'raw-frontend.csv'),observer_sha256=sha(OUT/'observer.json'),helper_sha256=sha(Path(__file__)),gameplay=summary(selected),windows=[dict(first=lo,last=hi-1,**summary([r for r in rows if lo<=r['frame']<hi])) for lo,hi in [(360,480),(480,600),(600,721)]],limits=['Coverage and byte counts only: no implemented accelerator, client cost or speedup claim.','Vertex and run cycles are inclusive and must not be added to self cycles of called functions.','Raw floatVtx DMA would require immutable-source ownership/cache publication and a transport path; modeled CPU memory requests remain approximate.','Mixed loads require retaining per-slot vertex-load contexts. Unsupported material/clip cases not filtered by this observer.','Matrix/light/control event counts compare complete groups at successive actual G_VTX returns; transient unused state changes are naturally omitted.'])
    write(OUT/'results.json',res);print(json.dumps(res['gameplay'],indent=2))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('action',choices=['prepare','capture','analyze']);a=p.parse_args();globals()[a.action]()
