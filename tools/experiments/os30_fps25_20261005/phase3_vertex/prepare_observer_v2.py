#!/usr/bin/env python3
import hashlib,json,shutil,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[4]
OUT=ROOT/'docs/measurements/os30-fps25-20261005/phase3/vertex-cpu'
OLD=ROOT/'docs/measurements/os30-simple-20261005/validation/qsim-observed'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def main():
 assert sha(OLD/'qsim')=='509cfbb58a1e5544f269e41dea58d36db4eff703c3d4ffe26a08579ffb27ef0d'
 q=OUT/'qsim-v2';q.mkdir(exist_ok=False)
 for p in OLD.iterdir():
  if p.suffix in ('.c','.h') or p.name=='Makefile':shutil.copy2(p,q/p.name)
 def change(n,a,b):
  p=q/n;s=p.read_text();assert s.count(a)==1,(n,a);p.write_text(s.replace(a,b,1))
 change('cpu.h','    /* architectural state */','    uint32_t observe_pc;\n    void (*observe)(struct qsim_cpu *);\n    void *observe_context;\n    /* architectural state */')
 line='#define INSN() do { '+chr(92)+'\n'
 change('cpu_exec.h',line+'        size_t _i',line+'        if (unlikely(e->pc == cpu->observe_pc && cpu->observe != NULL)) cpu->observe(cpu); '+chr(92)+'\n        size_t _i')
 change('main.c','#include "lint.h"','#include "lint.h"\n'+Path(__file__).with_name('observer_v2.c').read_text())
 change('main.c','    size_t cap_rows = 4096, nrows = 0;',r'''    size_t cap_rows = 4096, nrows = 0;
    const qsim_sym *vertex_fn=qsim_syms_lookup(&E.syms,"gfx_sp_vertex");
    const qsim_sym *vertex_rsp=qsim_syms_lookup(&E.syms,"rsp");
    const qsim_sym *vertex_memo=qsim_syms_lookup(&E.syms,"lighting_memo");
    if(!vertex_fn || vertex_fn->addr!=0x104d0180u || vertex_fn->size!=2656 ||
       !vertex_rsp || vertex_rsp->addr!=0x1115d908u || vertex_rsp->size!=3388 ||
       !vertex_memo || vertex_memo->addr!=0x1115cd08u || vertex_memo->size!=3072)abort();
    snprintf(path,sizeof path,"%s/vertex-state.bin",out);
    vertex_observer vertex_obs={.out=fopen(path,"wb"),.frame=&nrows,.entry=vertex_fn->addr};if(!vertex_obs.out)abort();
    snprintf(path,sizeof path,"%s/vertex-state.csv",out);vertex_obs.csv=fopen(path,"w");if(!vertex_obs.csv)abort();
    fprintf(vertex_obs.csv,"frame,ordinal,count,dest,address,geometry_mode,matrix_word,lighting_epoch,fog_pending,rdp_word,insns,est_cycles,dependency,fdiv,icache,dcache,branch\n");
    E.cpu->observe_pc=vertex_fn->addr;E.cpu->observe=observe_vertex;E.cpu->observe_context=&vertex_obs;''')
 change('main.c','    if(culling_csv)fclose(culling_csv);','''    if(culling_csv)fclose(culling_csv);
    if(vertex_obs.active)abort();fclose(vertex_obs.out);fclose(vertex_obs.csv);
    for(unsigned i=0;i<5;i++)fprintf(stderr,"VERTEX_SAMPLES %u seen=%u saved=%u\\n",i,vertex_obs.seen[i],vertex_obs.saved[i]);
    fprintf(stderr,"VERTEX_GAMEPLAY calls=%u vertices=%u\\n",vertex_obs.all_calls,vertex_obs.all_vertices);''')
 with (OUT/'observer-v2-build.log').open('w') as f:subprocess.run(['make','-j3'],cwd=q,stdout=f,stderr=subprocess.STDOUT,check=True)
 record={'parent_qsim_sha256':sha(OLD/'qsim'),'exe_sha256':sha(q/'qsim'),'script_sha256':sha(Path(__file__)),'observer_sha256':sha(Path(__file__).with_name('observer_v2.c')),'source_sha256':{p.name:sha(p) for p in q.iterdir() if p.suffix in ('.c','.h') or p.name=='Makefile'},'scope':'Host-only pre/post snapshots of full F5 vertex function, every7th G_VTX at frames360/400/500/600/720, up to40each. Full RSP/RDP/lighting memo + dynamic fog/epoch/viewport globals + exact input vertex bytes, original addresses/GP/SP and modeled call costs. No guest/model mutation; noninterference still required.'}
 (OUT/'observer-v2-manifest.json').write_text(json.dumps(record,indent=2)+'\n');print(q/'qsim')
if __name__=='__main__':main()
