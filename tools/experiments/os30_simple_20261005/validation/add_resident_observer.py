#!/usr/bin/env python3
"""Read-only host entry histogram; no guest state or timing-model mutation."""
from pathlib import Path
import json,hashlib,shutil,subprocess
ROOT=Path(__file__).resolve().parents[4]
OUT=ROOT/'docs/measurements/os30-simple-20261005/validation'
parent=OUT/'qsim-observed';q=OUT/'qsim-resident-observed'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
assert sha(parent/'qsim')=='509cfbb58a1e5544f269e41dea58d36db4eff703c3d4ffe26a08579ffb27ef0d'
q.mkdir(exist_ok=False)
for p in parent.iterdir():
 if p.suffix in ('.c','.h') or p.name=='Makefile':shutil.copy2(p,q/p.name)
def change(name,a,b):
 p=q/name;s=p.read_text();assert s.count(a)==1,(name,a);p.write_text(s.replace(a,b,1))
change('cpu.h','    /* architectural state */','''    /* Host-only observation: never consumed by guest or modeled timing. */
    uint32_t observe_pc;
    void (*observe)(struct qsim_cpu *);
    void *observe_context;
    /* architectural state */''')
change('cpu_exec.h','#define INSN() do { \\\n        size_t _i','#define INSN() do { \\\n        if (unlikely(e->pc == cpu->observe_pc && cpu->observe != NULL)) cpu->observe(cpu); \\\n        size_t _i')
change('main.c','#include "lint.h"','''#include "lint.h"

typedef struct {
    FILE *out;
    const size_t *frame;
    uint32_t alpha_addr, vp_valid_addr, vp_addr, cx_addr, cy_addr, hw_addr, hh_addr;
} resident_observer;
static uint32_t resident_word(qsim_cpu *cpu,uint32_t addr) {
    uint32_t v;const void *p=qsim_mem_ptr(cpu,addr,4);if(!p)abort();memcpy(&v,p,4);return v;
}
static void observe_resident(qsim_cpu *cpu) {
    const resident_observer *o=cpu->observe_context;
    const unsigned char *v=qsim_mem_ptr(cpu,cpu->x[10],36);
    const unsigned char *a=qsim_mem_ptr(cpu,o->alpha_addr,1);
    const unsigned char *s=qsim_mem_ptr(cpu,cpu->x[11],3);
    if(!v || !a || !s)abort();
    unsigned vp=!resident_word(cpu,o->vp_valid_addr)
        || resident_word(cpu,o->vp_addr)!=resident_word(cpu,o->cx_addr)
        || resident_word(cpu,o->vp_addr+4)!=resident_word(cpu,o->cy_addr)
        || resident_word(cpu,o->vp_addr+8)!=resident_word(cpu,o->hw_addr)
        || resident_word(cpu,o->vp_addr+12)!=resident_word(cpu,o->hh_addr);
    fprintf(o->out,"%zu,%u,%u,%u,%u,%u,%u,%u\\n",*o->frame,cpu->x[12],
        (unsigned)v[27],(unsigned)*a,vp,(unsigned)s[0],(unsigned)s[1],(unsigned)s[2]);
}
''')
change('main.c','    size_t cap_rows = 4096, nrows = 0;','''    size_t cap_rows = 4096, nrows = 0;
    const qsim_sym *resident_fn=qsim_syms_lookup(&E.syms,"gfx_gpu_vtx_cache_tri");
    const char *resident_names[]={"g_surf_alpha","g_vc_vp_valid","g_vc_vp","g_cx","g_cy","g_hw","g_hh"};
    uint32_t resident_addr[7];
    for(unsigned i=0;i<7;i++) {
        const qsim_sym *s=qsim_syms_lookup(&E.syms,resident_names[i]);if(!s)abort();resident_addr[i]=s->addr;
    }
    if(!resident_fn || resident_fn->addr!=0x104e0b00u || resident_fn->size!=0x1edau)abort();
    snprintf(path,sizeof path,"%s/resident-calls.csv",out);
    resident_observer resident={fopen(path,"w"),&nrows,resident_addr[0],resident_addr[1],resident_addr[2],resident_addr[3],resident_addr[4],resident_addr[5],resident_addr[6]};
    if(!resident.out)abort();
    fprintf(resident.out,"frame,dirty_mask,alpha,current_alpha,viewport_bits_differ,slot0,slot1,slot2\\n");
    E.cpu->observe_pc=resident_fn->addr;E.cpu->observe=observe_resident;E.cpu->observe_context=&resident;
    snprintf(path,sizeof path,"%s/resident-symbols.json",out);
    FILE *resident_symbols=fopen(path,"w");if(!resident_symbols)abort();
    fprintf(resident_symbols,"{\\"entry\\":%u,\\"size\\":%u,\\"alpha_offset\\":27,\\"vtx_size\\":36,\\"symbols\\":{",resident_fn->addr,resident_fn->size);
    for(unsigned i=0;i<7;i++)fprintf(resident_symbols,"%s\\"%s\\":%u",i?",":"",resident_names[i],resident_addr[i]);
    fprintf(resident_symbols,"}}\\n");fclose(resident_symbols);''')
change('main.c','    if(culling_csv)fclose(culling_csv);','    if(culling_csv)fclose(culling_csv);\n    fclose(resident.out);')
changed=[p.name for p in q.iterdir() if p.is_file() and sha(p)!=sha(parent/p.name)]
assert sorted(changed)==['cpu.h','cpu_exec.h','main.c']
with (OUT/'qsim-resident-observed-build.log').open('w') as f:subprocess.run(['make','-j3'],cwd=q,stdout=f,stderr=subprocess.STDOUT,check=True)
meta={'script_sha256':sha(Path(__file__)),'parent_qsim_sha256':sha(parent/'qsim'),'exe_sha256':sha(q/'qsim'),'changed':changed,'source_sha256':{p.name:sha(p) for p in q.iterdir() if p.is_file()},'scope':'Observer before actual instruction at baseline normal writer entry104e0b00 reads ABI a2 dirty_mask,a0 vertex0 alpha byte27,a1 slots and backend globals only. No hooks replacing guest instructions; no guest/model writes. Viewport comparison is raw-bit diagnostic, not claimed identical for signed zero/NaN. Frame is same host nrows used by existing route observer.'}
(OUT/'qsim-resident-observed-manifest.json').write_text(json.dumps(meta,indent=2)+'\n');print(q/'qsim')
