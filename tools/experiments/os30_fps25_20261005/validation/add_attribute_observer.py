#!/usr/bin/env python3
"""Host-only entry/return snapshots for exact static G_VTX attribute reuse."""
from common import *
import shutil
import subprocess

parent = PREVIOUS / 'qsim-observed'
dest = OUT / 'qsim-attributes-observed'
elf = ROOT / 'docs/measurements/os30-simple-20261005/simple-v4/app.elf'
assert sha(parent / 'qsim') == '509cfbb58a1e5544f269e41dea58d36db4eff703c3d4ffe26a08579ffb27ef0d'
assert sha(elf) == 'e11905966c8d5c3c8a8d086e3269e5c97a64042eb77c21c1e15d9964b68770a4'
dest.mkdir(exist_ok=False)
for p in parent.iterdir():
    if p.suffix in ('.c', '.h') or p.name == 'Makefile':
        shutil.copy2(p, dest / p.name)


def change(name, old, new):
    p = dest / name
    s = p.read_text()
    assert s.count(old) == 1, (name, s.count(old), old[:60])
    p.write_text(s.replace(old, new, 1))


change('cpu.h', '    /* architectural state */', '''    /* Host observation only; no guest data or model timing mutation. */
    uint32_t observe_pc;
    void (*observe)(struct qsim_cpu *);
    void *observe_context;
    /* architectural state */''')
line = '#define INSN() do { ' + chr(92) + '\n'
change('cpu_exec.h', line + '        size_t _i', line + '        if (unlikely(e->pc == cpu->observe_pc && cpu->observe != NULL)) cpu->observe(cpu); ' + chr(92) + '\n        size_t _i')
change('main.c', '#include "lint.h"', r'''#include "lint.h"
typedef struct {
 FILE *out; const size_t *frame;
 uint32_t entry,low,high,rsp,epoch;
 uint32_t active,count,dest,address,eligible,return_pc;
} attribute_observer;
static uint32_t attribute_u32(const unsigned char *p) {
 uint32_t v;memcpy(&v,p,4);return v;
}
static void attribute_hex(FILE *out,const unsigned char *p,unsigned count) {
 for(unsigned i=0;i<count;i++)fprintf(out,"%02x",p[i]);
}
static void observe_attributes(qsim_cpu *cpu) {
 attribute_observer *o=cpu->observe_context;
 if(!o->active) {
  o->count=cpu->x[10];o->dest=cpu->x[11];o->address=cpu->x[12];o->return_pc=cpu->x[1];
  o->eligible=o->count>0 && o->count<=32 && o->dest<32 && o->count<=32-o->dest
      && o->address>=o->low && o->address<o->high && o->count<=(o->high-o->address)/24u;
  o->active=1;cpu->observe_pc=o->return_pc;return;
 }
 const unsigned char *rsp=qsim_mem_ptr(cpu,o->rsp,3388);if(!rsp)abort();
 const unsigned char *epoch=qsim_mem_ptr(cpu,o->epoch,4);if(!epoch)abort();
 const uint32_t mode=attribute_u32(rsp+928),scale=attribute_u32(rsp+936);
 const unsigned lights=rsp[924];
 if(lights>3)abort();
 fprintf(o->out,"%zu,%u,%u,%u,%u,%u,%u,%u,%u,",*o->frame,o->count,o->dest,o->address,
     o->eligible,mode,scale,lights,attribute_u32(epoch));
 /* Canonical light key omits padding, copied colors, unused light slots,
  * mode bits unrelated to attributes, and the changing modelview epoch. */
 if(mode&0x20000u) {
  for(unsigned i=0;i<lights;i++)attribute_hex(o->out,rsp+837+12*i,3);
  for(unsigned i=0;i+1<lights;i++)attribute_hex(o->out,rsp+876+12*i,12);
 }
 fputc(',',o->out);
 if(o->eligible)for(unsigned i=0;i<o->count;i++) {
  const unsigned char *v=rsp+940+36*(o->dest+i);
  attribute_hex(o->out,v+20,8);attribute_hex(o->out,v+28,3);
 }
 fputc('\n',o->out);
 o->active=0;cpu->observe_pc=o->entry;
}
''')
change('main.c', '    size_t cap_rows = 4096, nrows = 0;', r'''    size_t cap_rows = 4096, nrows = 0;
    const qsim_sym *attr_fn=qsim_syms_lookup(&E.syms,"gfx_sp_vertex");
    const qsim_sym *attr_rsp=qsim_syms_lookup(&E.syms,"rsp");
    const qsim_sym *attr_epoch=qsim_syms_lookup(&E.syms,"lighting_epoch");
    const qsim_sym *attr_low=qsim_syms_lookup(&E.syms,"_start");
    const qsim_sym *attr_high=qsim_syms_lookup(&E.syms,"__EH_FRAME_BEGIN__");
    if(!attr_fn || attr_fn->addr!=0x104cf000u || attr_fn->size!=2384u ||
       !attr_rsp || attr_rsp->size!=3388u || !attr_epoch || !attr_low || !attr_high)abort();
    snprintf(path,sizeof path,"%s/attributes.csv",out);
    attribute_observer attribute_obs={.out=fopen(path,"w"),.frame=&nrows,
        .entry=attr_fn->addr,.low=attr_low->addr,.high=attr_high->addr,
        .rsp=attr_rsp->addr,.epoch=attr_epoch->addr};
    if(!attribute_obs.out)abort();
    fprintf(attribute_obs.out,"frame,count,dest,address,eligible,mode,scale,lights,epoch,light_key,attributes\n");
    E.cpu->observe_pc=attribute_obs.entry;E.cpu->observe=observe_attributes;E.cpu->observe_context=&attribute_obs;
    snprintf(path,sizeof path,"%s/attributes-symbols.json",out);
    FILE *attribute_symbols=fopen(path,"w");if(!attribute_symbols)abort();
    fprintf(attribute_symbols,"{\"entry\":%u,\"size\":%u,\"rsp\":%u,\"rsp_size\":%u,\"epoch\":%u,\"low\":%u,\"high\":%u,\"lights_offset\":837,\"coefficients_offset\":876,\"num_lights_offset\":924,\"mode_offset\":928,\"scale_offset\":936,\"vertices_offset\":940}\n",
        attr_fn->addr,attr_fn->size,attr_rsp->addr,attr_rsp->size,attr_epoch->addr,attr_low->addr,attr_high->addr);
    fclose(attribute_symbols);''')
change('main.c', '    if(culling_csv)fclose(culling_csv);', '    if(culling_csv)fclose(culling_csv);\n    if(attribute_obs.active)abort();\n    fclose(attribute_obs.out);')
with (OUT / 'qsim-attributes-observed-build.log').open('w') as log:
    subprocess.run(['make', '-j3'], cwd=dest, stdout=log, stderr=subprocess.STDOUT, check=True)
record = {
    'qualified': False,
    'script_sha256': sha(Path(__file__)), 'parent_qsim_sha256': sha(parent / 'qsim'),
    'baseline_elf_sha256': sha(elf), 'exe_sha256': sha(dest / 'qsim'),
    'source_sha256': {p.name: sha(p) for p in dest.iterdir() if p.is_file()},
    'scope': 'Host-only G_VTX entry/return observation. Reads exact materialized UV/RGB and canonical current light state; never changes guest state, cache model or clock.',
    'layout': 'Extracted RSP/LoadedVertex/Light_t layout: total3388, LoadedVertex36, Light_t12. Source offsetof proof required alongside symbol guards.',
    'required': 'All original guest traces/profile/vector hashes identical to retained simple-v4 normal capture before trusting reuse statistics.',
}
(OUT / 'qsim-attributes-observed-manifest.json').write_text(json.dumps(record, indent=2) + '\n')
print(dest / 'qsim')
