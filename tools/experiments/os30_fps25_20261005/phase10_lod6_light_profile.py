#!/usr/bin/env python3
"""Passive unchanged-ELF lighting function/memo/cycle accounting."""
import argparse,json,shutil,subprocess,sys,os
from pathlib import Path
from phase10_experiment import CAMP,ROOT,module,sha,write
P=CAMP/'phase10';OLD=P/'lod6-coupled-v2';OUT=P/'lod6-light-profile-v1'
FIELDS='cyc pen_icache pen_prefetch pen_dcache pen_sb pen_uncached pen_cbo pen_misaligned pen_mispredict pen_btb_taken pen_dep pen_div pen_fdiv pen_fsqrt pen_f2i'.split()
def prepare():
 OUT.mkdir(exist_ok=False);shutil.copytree(OLD/'model/frozen',OUT/'model/frozen');shutil.copytree(OLD/'qsim',OUT/'qsim',ignore=shutil.ignore_patterns('build','qsim'))
 p=OUT/'qsim/coupled.h';p.write_text(p.read_text()+'\nvoid coupled_light_enter(void);\nvoid coupled_light_exit(void);\n')
 p=OUT/'qsim/cpu_exec.h';t=p.read_text();a='        if ((tpc) == coupled_render_pc) coupled_render_enter(); \\\n';assert t.count(a)==1
 t=t.replace(a,a+'        if ((tpc) == 0x104ce680u) coupled_light_enter(); \\\n        if ((isr) && e->rs1 == 1 && e->pc >= 0x104ce680u && e->pc <= 0x104ce858u) coupled_light_exit(); \\\n');p.write_text(t)
 p=OUT/'model/frozen/coupled.cpp';t=p.read_text();a='static uint64_t gd_reads,gd_writes,gd_arbursts,gd_rbeats,gd_arwait,gd_loads;';assert t.count(a)==1
 code='''
static const unsigned LIGHT_N=15;
static uint64_t light_before[LIGHT_N],light_total[LIGHT_N],light_calls;
static uint64_t light_pc_before[237][5];
static bool in_light;
static void light_values(uint64_t *v) {
'''+''.join(f'    v[{i}]=env->cpu->model->{n};\n'for i,n in enumerate(FIELDS))+'''}
extern "C" void coupled_light_enter(void) {
 if(!active)return;assert(!in_light);in_light=true;light_values(light_before);light_calls++;
}
extern "C" void coupled_light_exit(void) {
 if(!active)return;assert(in_light);in_light=false;uint64_t v[LIGHT_N];light_values(v);
 for(unsigned i=0;i<LIGHT_N;i++)light_total[i]+=v[i]-light_before[i];
}
static void light_pc_snapshot(bool finish) {
 const auto *pr=env->cpu->prof;
 FILE *f=finish?fopen((folder+"/lighting-pc.csv").c_str(),"w"):nullptr;
 if(f)fprintf(f,"pc,count,penalty,ic_miss,dc_miss,mispredict\\n");
 for(unsigned j=0;j<237;j++) {
  uint32_t pc=0x104ce680u+j*2;
  size_t i=(env->cpu->sd_dc-env->cpu->dc_base)+((pc-env->cpu->sdram_base)>>1);
  uint64_t v[5]={pr->cnt[i],pr->pen[i],pr->ev_ic[i],pr->ev_dc[i],pr->ev_bp[i]};
  if(f){fprintf(f,"%08x",pc);for(unsigned k=0;k<5;k++)fprintf(f,",%llu",(unsigned long long)(v[k]-light_pc_before[j][k]));fprintf(f,"\\n");}
  else for(unsigned k=0;k<5;k++)light_pc_before[j][k]=v[k];
 }
 if(f)fclose(f);
}
'''
 t=t.replace(a,a+code)
 t=t.replace('#include "cache.h"','#include "cache.h"\n#include "prof.h"')
 a='    initializing=false;active=true;snapshot("start",0);';assert t.count(a)==1;t=t.replace(a,'    light_pc_snapshot(false);\n'+a)
 a='    coupled_sync();snapshot("cpu_stop",last_submitted);';assert t.count(a)==1
 code='''
    assert(!in_light);light_pc_snapshot(true);
    FILE *light=fopen((folder+"/lighting-counters.json").c_str(),"w");assert(light);
    fprintf(light,"{\\"calls\\":%llu",(unsigned long long)light_calls);
'''+''.join(f'    fprintf(light,",\\"{n}\\":%llu",(unsigned long long)light_total[{i}]);\n'for i,n in enumerate(FIELDS))+'''    fprintf(light,"}\\n");fclose(light);
'''
 t=t.replace(a,a+code);p.write_text(t)
 cmd=[x.replace(str(OLD),str(OUT))for x in json.loads((OLD/'build-command.json').read_text())];write(OUT/'build-command.json',cmd)
 write(OUT/'prepared.json',dict(parent_manifest_sha256=sha(OLD/'manifest.json'),scope=__doc__,sources={str(p.relative_to(OUT)):sha(p)for p in OUT.rglob('*')if p.is_file()},helper_sha256=sha(Path(__file__))))
def action(name):
 m=module('lod6_probe_runner',Path(__file__).parent/'phase10_lod6_coupled_v2.py');m.OUT=OUT;getattr(m,name)()
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('action',choices=['prepare','build','run']);a=p.parse_args();prepare()if a.action=='prepare'else action(a.action)
