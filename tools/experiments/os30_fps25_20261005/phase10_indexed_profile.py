#!/usr/bin/env python3
"""Passive per-instruction cost attribution in unchanged LOD5 indexed renderer."""
import argparse,json,os,shutil,subprocess,sys
from pathlib import Path
from phase10_experiment import CAMP,ROOT,module,sha,write
P=CAMP/'phase10';OLD=P/'cache-stage-lod5-coupled-v2';OUT=P/'lod5-indexed-profile-v1'
FIELDS='cyc pen_icache pen_prefetch pen_dcache pen_sb pen_uncached pen_cbo pen_misaligned pen_mispredict pen_btb_taken pen_dep pen_div pen_fdiv pen_fsqrt pen_f2i'.split()
DEPS='alu load mul div csr f2i fcmp fadd fmul fma fdiv fmisc fload i2f'.split()
def prepare():
 OUT.mkdir(exist_ok=False);shutil.copytree(OLD/'model/frozen',OUT/'model/frozen');shutil.copytree(OLD/'qsim',OUT/'qsim',ignore=shutil.ignore_patterns('build','qsim'))
 p=OUT/'qsim/coupled.h';p.write_text(p.read_text()+'\nvoid coupled_indexed_sample(unsigned pc);\n')
 p=OUT/'qsim/cpu_exec.h';s=p.read_text();a='#define INSN() do { \\\n';assert s.count(a)==1;s=s.replace(a,a+'        coupled_indexed_sample(e->pc); \\\n');p.write_text(s)
 p=OUT/'model/frozen/coupled.cpp';s=p.read_text();a='static uint64_t gd_reads,gd_writes,gd_arbursts,gd_rbeats,gd_arwait,gd_loads;';assert s.count(a)==1
 code='''
static const uint32_t INDEXED_BASE=0x104d1c00u,INDEXED_END=0x104d2d10u;
static const unsigned INDEXED_N=(INDEXED_END-INDEXED_BASE)/2,INDEXED_FIELDS=29;
static uint32_t indexed_last;
static uint64_t indexed_before[INDEXED_FIELDS],indexed_values[INDEXED_N][INDEXED_FIELDS],indexed_count[INDEXED_N];
static void indexed_model(uint64_t *v) {
'''+''.join(f' v[{i}]=env->cpu->model->{n};\n'for i,n in enumerate(FIELDS))+''.join(f' v[{15+i}]=env->cpu->model->pen_dep_by[{i}];\n'for i in range(14))+'''}
extern "C" void coupled_indexed_sample(unsigned pc) {
 if(!active)return;
 if(indexed_last) {
  uint64_t now[INDEXED_FIELDS];indexed_model(now);unsigned i=(indexed_last-INDEXED_BASE)/2;
  for(unsigned k=0;k<INDEXED_FIELDS;k++)indexed_values[i][k]+=now[k]-indexed_before[k];
  indexed_count[i]++;
 }
 if(pc>=INDEXED_BASE&&pc<INDEXED_END) {indexed_last=pc;indexed_model(indexed_before);}
 else indexed_last=0;
}
static void indexed_report() {
 coupled_indexed_sample(0);
 FILE *f=fopen((folder+"/indexed-pc.csv").c_str(),"w");assert(f);
 fprintf(f,"pc,count,'''+','.join(FIELDS+['dep_'+n for n in DEPS])+'''\\n");
 for(unsigned i=0;i<INDEXED_N;i++)if(indexed_count[i]) {
  fprintf(f,"%08x,%llu",INDEXED_BASE+i*2,(unsigned long long)indexed_count[i]);
  for(unsigned k=0;k<INDEXED_FIELDS;k++)fprintf(f,",%llu",(unsigned long long)indexed_values[i][k]);
  fprintf(f,"\\n");
 }fclose(f);
}
'''
 s=s.replace(a,a+code);a='    coupled_sync();snapshot("cpu_stop",last_submitted);';assert s.count(a)==1;s=s.replace(a,a+'\n    indexed_report();');p.write_text(s)
 cmd=[x.replace(str(OLD),str(OUT))for x in json.loads((OLD/'build-command.json').read_text())];write(OUT/'build-command.json',cmd)
 with(OUT/'indexed.asm').open('w')as f:subprocess.run(['llvm-objdump','-d','--mattr=+m,+a,+f,+c','--no-show-raw-insn','--disassemble-symbols=gfx_cpu_indexed_run',str(P/'load-cram-driver-v1/app.elf')],stdout=f,check=True)
 write(OUT/'prepared.json',dict(parent_manifest_sha256=sha(OLD/'manifest.json'),scope=__doc__,elf_sha256=sha(P/'load-cram-driver-v1/app.elf'),sources={str(p.relative_to(OUT)):sha(p)for p in OUT.rglob('*')if p.is_file()},helper_sha256=sha(Path(__file__))))
def action(name):
 m=module('indexed_profile_runner',Path(__file__).parent/'phase10_cache_stage_coupled_v2.py');m.OUT=OUT
 if name=='run':m.run('warm')
 else:getattr(m,name)()
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('action',choices=['prepare','build','run']);a=p.parse_args();prepare()if a.action=='prepare'else action(a.action)
