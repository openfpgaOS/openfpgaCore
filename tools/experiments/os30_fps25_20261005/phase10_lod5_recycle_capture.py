#!/usr/bin/env python3
"""Native-FMA functional proof of bounded LOD5 source-pool recycling."""
import argparse,json,shutil,subprocess,sys
from pathlib import Path
from phase10_experiment import CAMP,module,sha,write
H=Path(__file__).resolve().parent;P=CAMP/'phase10';OUT=P/'lod5-recycle-functional-v1';CAND=P/'lod5-recycle-driver-v1';OLD=P/'load-cram-functional-v1'
def prepare():
 OUT.mkdir(exist_ok=False);q=OUT/'functional-qsim';q.mkdir()
 for n in ('app.elf','gfx_pc.c'):shutil.copy2(CAND/n,OUT/n)
 for p in(OLD/'functional-qsim').iterdir():
  if p.suffix in('.c','.h')or p.name=='Makefile':shutil.copy2(p,q/p.name)
 p=q/'load_model.h';t=p.read_text()
 a='    uint32_t *stats=(uint32_t *)gp(E,(uint32_t)strtoul(a,NULL,0),32);'
 assert t.count(a)==1
 t=t.replace(a,'''    if(!strcmp(load_model.mode?load_model.mode:"normal","fullpool") &&
       (E->flips==234 || E->flips==235)) {
        const char *top=getenv("LOD5_POOL_TOP_ADDR");if(!top)abort();
        if(load_model.phase==1)fatal(E,"recycle injection while source active");
        wr32(E,(uint32_t)strtoul(top,NULL,0),0x01000000u);
    }
'''+a)
 a='    for(unsigned i=0;i<8;i++)fprintf(f,",%u",stats[i]);fputc(\'\\n\',f);fflush(f);';assert t.count(a)==1
 t=t.replace(a,'''    for(unsigned i=0;i<8;i++)fprintf(f,",%u",stats[i]);
    const char *recycle=getenv("LOD5_RECYCLE_STATS_ADDR");if(!recycle)abort();
    uint32_t *rs=(uint32_t *)gp(E,(uint32_t)strtoul(recycle,NULL,0),8);
    fprintf(f,",%u,%u\\n",rs[0],rs[1]);fflush(f);''')
 t=t.replace('probes,unavailable\\n','probes,unavailable,recycles,throttled\\n');p.write_text(t)
 with(OUT/'build.log').open('x')as f:subprocess.run(['make','-j4'],cwd=q,stdout=f,stderr=subprocess.STDOUT,check=True)
 write(OUT/'prepared.json',dict(elf_sha256=sha(OUT/'app.elf'),parent_manifest_sha256=sha(OLD/'manifest.json'),sources={p.name:sha(p)for p in q.iterdir()if p.suffix in('.c','.h')or p.name=='Makefile'},simulator_sha256=sha(q/'qsim'),helper_sha256=sha(Path(__file__)),limits='Functional source ownership/data and native-FMA proof only; synthetic worker completion/upload timing. Fullpool mode forces initialcapacity and capacity at frame234/235 to exercise generations and throttling.'))
def action(name,mode):
 import os
 m=module('lod5_recycle_capture',H/'phase10_load_cram_capture.py');m.OUT=OUT;m.CAND=CAND
 if name=='capture':os.environ['LOD5_RECYCLE_STATS_ADDR']=hex(m.symbols(CAND/'app.elf')['cram_vertex_recycle_stats'])
 getattr(m,name)(mode)
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('action',choices=['prepare','capture','analyze']);p.add_argument('--mode',choices=['baseline','normal','faults','absent','timeout','fullpool'],default='normal');a=p.parse_args();prepare()if a.action=='prepare'else action(a.action,a.mode)
