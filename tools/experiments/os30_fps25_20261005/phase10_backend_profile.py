#!/usr/bin/env python3
"""Passive per-instruction cost and prepared-slot reuse attribution in selected LOAD-overlap backend."""
import argparse,json,os,shutil,subprocess,sys
from pathlib import Path
from phase10_experiment import CAMP,ROOT,module,sha,write
P=CAMP/'phase10';OLD=P/'load-submit-coupled-v1';OUT=P/'selected-backend-profile-v2'
FIELDS='cyc pen_icache pen_prefetch pen_dcache pen_sb pen_uncached pen_cbo pen_misaligned pen_mispredict pen_btb_taken pen_dep pen_div pen_fdiv pen_fsqrt pen_f2i'.split()
DEPS='alu load mul div csr f2i fcmp fadd fmul fma fdiv fmisc fload i2f'.split()
def prepare():
 OUT.mkdir(exist_ok=False);shutil.copytree(OLD/'model/frozen',OUT/'model/frozen');shutil.copytree(OLD/'qsim',OUT/'qsim',ignore=shutil.ignore_patterns('build','qsim'))
 p=OUT/'qsim/coupled.h';p.write_text(p.read_text()+'\nvoid coupled_indexed_sample(unsigned pc);\n')
 p=OUT/'qsim/cpu_exec.h';s=p.read_text();a='#define INSN() do { \\\n';assert s.count(a)==1;s=s.replace(a,a+'        coupled_indexed_sample(e->pc); \\\n');p.write_text(s)
 p=OUT/'model/frozen/coupled.cpp';s=p.read_text();a='static uint64_t gd_reads,gd_writes,gd_arbursts,gd_rbeats,gd_arwait,gd_loads;';assert s.count(a)==1
 code='''
static const uint32_t INDEXED_BASE=0x104dc680u,INDEXED_END=0x104dd5b0u;
static const unsigned INDEXED_N=(INDEXED_END-INDEXED_BASE)/2,INDEXED_FIELDS=29;
static uint32_t indexed_last;
static uint32_t generation[32],prepared_generation[32],prior_prepared[32][7],prepared_valid;
static uint32_t pending_vtx_return,pending_vtx_count,pending_vtx_dest;
static uint64_t backend_batches,backend_triangles,backend_uploads,duplicate_uploads,unique_preparations;
static uint64_t same_generation,same_uv,same_rgb,same_all,batch_hist[65],dirty_hist[8];
static uint64_t indexed_before[INDEXED_FIELDS],indexed_values[INDEXED_N][INDEXED_FIELDS],indexed_count[INDEXED_N];
static void indexed_model(uint64_t *v) {
'''+''.join(f' v[{i}]=env->cpu->model->{n};\n'for i,n in enumerate(FIELDS))+''.join(f' v[{15+i}]=env->cpu->model->pen_dep_by[{i}];\n'for i in range(14))+'''}
extern "C" void coupled_indexed_sample(unsigned pc) {
 if(!active)return;
 if(pc==0x104d4500u) {prepared_valid=0; for(unsigned i=0;i<32;i++)generation[i]++;}
 if(pc==0x104cf580u) {pending_vtx_return=env->cpu->x[1];pending_vtx_count=env->cpu->x[10];pending_vtx_dest=env->cpu->x[11];}
 if(pending_vtx_return&&pc==pending_vtx_return) {
  for(unsigned i=pending_vtx_dest;i<32&&i<pending_vtx_dest+pending_vtx_count;i++)generation[i]++;
  pending_vtx_return=0;
 }
 if(pc==INDEXED_BASE) {
  unsigned n=env->cpu->x[12];assert(n<=64);backend_batches++;batch_hist[n]++;
  uint32_t vertices=env->cpu->x[10],jobs=env->cpu->x[11],seen=0;
  for(unsigned t=0;t<n;t++) {
   uint32_t j;memcpy(&j,memory()+(jobs-env->cpu->sdram_base)+4*t,4);unsigned dirty=(j>>15)&7;dirty_hist[dirty]++;backend_triangles++;
   for(unsigned k=0;k<3;k++)if(dirty&(1u<<k)) {
    unsigned slot=(j>>(5*k))&31;backend_uploads++;
    if(seen&(1u<<slot)){duplicate_uploads++;continue;}seen|=1u<<slot;unique_preparations++;
    uint32_t data[7];memcpy(data,memory()+(vertices-env->cpu->sdram_base)+28*slot,28);
    if((prepared_valid&(1u<<slot))&&prepared_generation[slot]==generation[slot]) {
     same_generation++;bool geom=true;for(unsigned a: {0u,1u,2u,6u})geom&=data[a]==prior_prepared[slot][a];
     if(!geom){fprintf(stderr,"PREP GEOMETRY CHANGED slot%u\\n",slot);abort();}
     if(data[3]==prior_prepared[slot][3]&&data[4]==prior_prepared[slot][4])same_uv++;
     if(data[5]==prior_prepared[slot][5])same_rgb++;
     if(!memcmp(data,prior_prepared[slot],28))same_all++;
    }
    memcpy(prior_prepared[slot],data,28);prepared_generation[slot]=generation[slot];prepared_valid|=1u<<slot;
   }
  }
 }
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
 FILE *f=fopen((folder+"/backend-pc.csv").c_str(),"w");assert(f);
 fprintf(f,"pc,count,'''+','.join(FIELDS+['dep_'+n for n in DEPS])+'''\\n");
 for(unsigned i=0;i<INDEXED_N;i++)if(indexed_count[i]) {
  fprintf(f,"%08x,%llu",INDEXED_BASE+i*2,(unsigned long long)indexed_count[i]);
  for(unsigned k=0;k<INDEXED_FIELDS;k++)fprintf(f,",%llu",(unsigned long long)indexed_values[i][k]);
  fprintf(f,"\\n");
 }fclose(f);
 f=fopen((folder+"/backend-reuse.csv").c_str(),"w");assert(f);
 fprintf(f,"batches,triangles,uploads,duplicate_uploads,preparations,same_generation,same_uv,same_rgb,same_all\\n%llu,%llu,%llu,%llu,%llu,%llu,%llu,%llu,%llu\\n",(unsigned long long)backend_batches,(unsigned long long)backend_triangles,(unsigned long long)backend_uploads,(unsigned long long)duplicate_uploads,(unsigned long long)unique_preparations,(unsigned long long)same_generation,(unsigned long long)same_uv,(unsigned long long)same_rgb,(unsigned long long)same_all);fclose(f);
 f=fopen((folder+"/backend-hist.csv").c_str(),"w");assert(f);fprintf(f,"kind,value,count\\n");for(unsigned i=0;i<65;i++)fprintf(f,"batch,%u,%llu\\n",i,(unsigned long long)batch_hist[i]);for(unsigned i=0;i<8;i++)fprintf(f,"dirty,%u,%llu\\n",i,(unsigned long long)dirty_hist[i]);fclose(f);
}
'''
 s=s.replace(a,a+code);a='    coupled_sync();snapshot("cpu_stop",last_submitted);';assert s.count(a)==1;s=s.replace(a,a+'\n    indexed_report();');p.write_text(s)
 cmd=[x.replace(str(P/'cache-write-lod5-coupled-v2'),str(OUT)).replace(str(OLD),str(OUT))for x in json.loads((OLD/'model/build.json').read_text())['command']];write(OUT/'build-command.json',cmd)
 with(OUT/'indexed.asm').open('w')as f:subprocess.run(['llvm-objdump','-d','--mattr=+m,+a,+f,+c','--no-show-raw-insn','--disassemble-symbols=gfx_gpu_vtx_cache_batch',str(P/'load-submit-overlap-v1/app.elf')],stdout=f,check=True)
 write(OUT/'prepared.json',dict(parent_manifest_sha256=sha(OLD/'manifest.json'),scope=__doc__,elf_sha256=sha(P/'load-submit-overlap-v1/app.elf'),sources={str(p.relative_to(OUT)):sha(p)for p in OUT.rglob('*')if p.is_file()},helper_sha256=sha(Path(__file__))))
def action(name):
 m=module('indexed_profile_runner',Path(__file__).parent/'phase10_cache_stage_coupled_v2.py');m.OUT=OUT
 if name=='run':
  r=module('backend_passive_runner',ROOT/'tools/experiments/os30_overlap_20261006/run.py');r.OUT=OUT
  os.environ['IDEAL_GPU_MEMORY']='0';os.environ['LOD5_MODEL_MODE']='normal'
  sys.argv=[str(Path(__file__)),'warm','--elf',str(P/'load-submit-overlap-v1/app.elf'),'--start','360','--frames','8'];r.main()
 else:getattr(m,name)()
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('action',choices=['prepare','build','run']);a=p.parse_args();prepare()if a.action=='prepare'else action(a.action)
