#!/usr/bin/env python3
"""Passive direct/simple versus generic fallback clip-projection bound."""
import argparse,json,shutil,subprocess
from pathlib import Path
from phase10_experiment import CAMP,module,sha,write
from phase10_indexed_profile import FIELDS,DEPS
P=CAMP/'phase10';OLD=P/'cache-stage-lod5-coupled-v2';OUT=P/'lod5-projection-profile-v1'
def prepare():
 OUT.mkdir(exist_ok=False);shutil.copytree(OLD/'model/frozen',OUT/'model/frozen');shutil.copytree(OLD/'qsim',OUT/'qsim',ignore=shutil.ignore_patterns('build','qsim'))
 p=OUT/'qsim/coupled.h';p.write_text(p.read_text()+'\nvoid coupled_projection_sample(unsigned pc);\n')
 p=OUT/'qsim/cpu_exec.h';s=p.read_text();a='#define INSN() do { \\\n';assert s.count(a)==1;s=s.replace(a,a+'        coupled_projection_sample(e->pc); \\\n');p.write_text(s)
 p=OUT/'model/frozen/coupled.cpp';s=p.read_text();a='static uint64_t gd_reads,gd_writes,gd_arbursts,gd_rbeats,gd_arwait,gd_loads;';assert s.count(a)==1
 code='''
static const uint32_t PROJECT_BASE=0x104d5300u,PROJECT_END=0x104d5440u;
static const unsigned PROJECT_FIELDS=29;
static bool project_previous;
static unsigned project_caller;
static uint64_t project_before[PROJECT_FIELDS],project_values[2][PROJECT_FIELDS],project_count[2],project_calls[2];
static void project_model(uint64_t *v) {
'''+''.join(f' v[{i}]=env->cpu->model->{n};\n'for i,n in enumerate(FIELDS))+''.join(f' v[{15+i}]=env->cpu->model->pen_dep_by[{i}];\n'for i in range(14))+'''}
extern "C" void coupled_projection_sample(unsigned pc) {
 if(!active)return;
 if(project_previous) {
  uint64_t now[PROJECT_FIELDS];project_model(now);
  for(unsigned k=0;k<PROJECT_FIELDS;k++)project_values[project_caller][k]+=now[k]-project_before[k];
  project_count[project_caller]++;
 }
 project_previous=pc>=PROJECT_BASE&&pc<PROJECT_END;
 if(project_previous) {
  if(pc==PROJECT_BASE) {
   uint32_t ra=env->cpu->x[1];
   if(ra>=0x104dd4c0u&&ra<0x104de4aau)project_caller=0;
   else {assert(ra>=0x104de500u&&ra<0x104e010eu);project_caller=1;}
   project_calls[project_caller]++;
  }
  project_model(project_before);
 }
}
static void project_report() {
 coupled_projection_sample(0);
 FILE *f=fopen((folder+"/projection-cost.csv").c_str(),"w");assert(f);
 fprintf(f,"caller,calls,count,'''+','.join(FIELDS+['dep_'+n for n in DEPS])+'''\\n");
 for(unsigned i=0;i<2;i++) {
  fprintf(f,"%s,%llu,%llu",i?"generic":"simple",(unsigned long long)project_calls[i],(unsigned long long)project_count[i]);
  for(unsigned k=0;k<PROJECT_FIELDS;k++)fprintf(f,",%llu",(unsigned long long)project_values[i][k]);
  fprintf(f,"\\n");
 }fclose(f);
}
'''
 s=s.replace(a,a+code);a='    coupled_sync();snapshot("cpu_stop",last_submitted);';assert s.count(a)==1;s=s.replace(a,a+'\n    project_report();');p.write_text(s)
 cmd=[x.replace(str(OLD),str(OUT))for x in json.loads((OLD/'build-command.json').read_text())];write(OUT/'build-command.json',cmd)
 with(OUT/'projection.asm').open('w')as f:subprocess.run(['llvm-objdump','-d','--mattr=+m,+a,+f,+c','--no-show-raw-insn','--disassemble-symbols=gpu_project_clip',str(P/'load-cram-driver-v1/app.elf')],stdout=f,check=True)
 write(OUT/'prepared.json',dict(parent_manifest_sha256=sha(OLD/'manifest.json'),scope=__doc__,elf_sha256=sha(P/'load-cram-driver-v1/app.elf'),sources={str(p.relative_to(OUT)):sha(p)for p in OUT.rglob('*')if p.is_file()},helper_sha256=sha(Path(__file__))))
def action(name):
 m=module('projection_profile_runner',Path(__file__).parent/'phase10_cache_stage_coupled_v2.py');m.OUT=OUT
 if name=='run':m.run('warm')
 else:getattr(m,name)()
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('action',choices=['prepare','build','run']);a=p.parse_args();prepare()if a.action=='prepare'else action(a.action)
