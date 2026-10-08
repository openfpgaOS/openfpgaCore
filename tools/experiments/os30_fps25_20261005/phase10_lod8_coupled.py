#!/usr/bin/env python3
"""Actual persistent LOD8 dense alias with unchanged selected GPU/cache workload."""
import argparse,json,os,shutil,subprocess,sys
from pathlib import Path
from phase10_experiment import CAMP,ROOT,module,sha,write
P=CAMP/'phase10';OLD=P/'cache-write-lod5-coupled-v2';OUT=P/'lod8-cow-coupled-v3'
COMP=P/'lod8-canonical-alias-v1';FUNC=P/'lod8-cow-functional-v1';APP=P/'lod8-cow-driver-v1/app.elf'
def rep(t,a,b,n=1):
 assert t.count(a)==n,(a,t.count(a));return t.replace(a,b)
SEED=r'''
static uint32_t alias_expected,alias_invalidated,source_first;
extern "C" void lod8_snapshot(void *,uint32_t *,uint32_t *);
extern "C" void coupled_alias_access(uint32_t off,unsigned size,unsigned store) {
 assert(active && !store && off+size<=0x03f80480u);
 assert(!tb->rootp->tb_gpu__DOT__load_client__DOT__active);
 unsigned first=(off-0x03f80000u)/36,last=(off+size-1-0x03f80000u)/36;
 uint32_t valid=tb->rootp->tb_gpu__DOT__view_valid_slots;
 assert((valid&(1u<<first)) && (valid&(1u<<last)));
 if(first<source_first+source_count && last>=source_first) {
  uint32_t lines=(1u<<((off-0x03f80000u)/64))|(1u<<((off+size-1-0x03f80000u)/64));
  assert(!(lines&alias_expected&~alias_invalidated));
 }
}
extern "C" void coupled_alias_cbo(unsigned op,uint32_t addr) {
 if(op || addr-0x13f80000u>=2048)return;
 assert(!tb->rootp->tb_gpu__DOT__load_client__DOT__active && !(addr&63));
 alias_invalidated|=1u<<((addr-0x13f80000u)/64);
}
static void seed_alias_state() {
 assert(initializing && !active);
 uint32_t words[288],valid;lod8_snapshot(env,words,&valid);
 auto r=tb->rootp;
 for(unsigned s=0;s<32;++s) {
  for(unsigned row=0;row<4;++row)
   r->tb_gpu__DOT__load_client__DOT__load__DOT__compute__DOT__acc[row*32+s]=words[s*9+row];
  r->tb_gpu__DOT__load_client__DOT__load__DOT__compute__DOT__g_context_bram__DOT__context_memory[32+s]=words[s*9+4];
  float u,v;memcpy(&u,words+s*9+5,4);memcpy(&v,words+s*9+6,4);
  assert(u>=-32768&&u<=32767&&v>=-32768&&v<=32767);
  assert(float(int16_t(u))==u&&float(int16_t(v))==v);
  r->tb_gpu__DOT__load_client__DOT__load__DOT__attributes[s]=words[s*9+7];
  r->tb_gpu__DOT__load_client__DOT__load__DOT__attributes[32+s]=uint16_t(int16_t(u))|(uint32_t(uint16_t(int16_t(v)))<<16);
  r->tb_gpu__DOT__load_client__DOT__load__DOT__attributes[64+s]=words[s*9+8];
 }
 r->tb_gpu__DOT__view_valid_slots=valid;
 uint32_t owned=guest_word("loaded_vertex_alias_owned");assert(!(owned&~valid));
 FILE *f=fopen((folder+"/warm-alias-snapshot.bin").c_str(),"wb");assert(f);
 fwrite(&valid,4,1,f);fwrite(&owned,4,1,f);fwrite(words,4,288,f);fclose(f);
 // Validate actual response formatting and ownership without modifying the
 // CPU's cache model or warming its cache. No measured counters run here.
 for(unsigned line=0;line<18;++line) {
  tb->alias_araddr=0x13f80000u+line*64;tb->alias_arlen=15;
  tb->alias_arvalid=1;tb->alias_rready=1;unsigned beat=0;bool accepted=false;
  for(unsigned timeout=0;timeout<10000&&beat<16;++timeout) {
   tb->clk=0;tb->eval();bool ar=tb->alias_arvalid&&tb->alias_arready;
   bool rd=tb->alias_rvalid&&tb->alias_rready;uint32_t data=tb->alias_rdata;
   if(rd) {
    unsigned wi=line*16+beat;uint32_t want=(valid&(1u<<(wi/9)))?words[wi]:0;
    assert(accepted&&tb->alias_rresp==0&&bool(tb->alias_rlast)==(beat==15)&&data==want);
   }
   tick();if(ar){accepted=true;tb->alias_arvalid=0;}if(rd)++beat;
  }
  assert(accepted&&beat==16);tb->alias_rready=0;
 }
 assert(!r->tb_gpu__DOT__alias_port__DOT__active);
 fprintf(stderr,"LOD8 warm alias seeded mask=%08x owned=%08x,288 words checked before timed work\n",valid,owned);
}
'''
def prepare():
 assert sha(OLD/'manifest.json')=='c46755893729024837ec5958017e2b8a4f5ee80b0c10387c6146a202388415b3'
 assert sha(COMP/'manifest.json')=='0a1f6189977309b794a36e0627ab0635efcb5177233132cf63fbafede5cfe8b4'
 assert sha(FUNC/'manifest.json')=='41aa4343b48081280c91b9ca928d1a65ca5a8c9f6a70c64ca5bef103119547e5'
 assert sha(APP)==sha(FUNC/'app.elf')
 OUT.mkdir(exist_ok=False)
 shutil.copytree(OLD/'model/frozen',OUT/'model/frozen')
 shutil.copytree(OLD/'qsim',OUT/'qsim',ignore=shutil.ignore_patterns('build','qsim'))
 common=OUT/'model/frozen/common'
 for name,h in json.loads((COMP/'sources.json').read_text()).items():
  if name.endswith('.v') and name!='tb_load_alias.v':
   assert sha(COMP/'frozen'/name)==h
   target=common/name
   if name=='phase10_cram1_read_arbiter.v':target=common/'geometry_cram1_read_arbiter.v'
   assert target.exists(),name;shutil.copyfile(COMP/'frozen'/name,target)
 p=OUT/'model/frozen/tb_gpu.v';t=p.read_text()
 t=rep(t,'wire view_active,view_block,view_valid;\nwire [5:0] view_count;','wire view_active,view_block;\nwire [31:0] view_valid_slots;')
 t=rep(t,'.view_valid(view_valid),.view_count(view_count)', '.view_valid_slots(view_valid_slots)',2);p.write_text(t)
 q=OUT/'qsim';p=q/'load_model.h';t=(FUNC/'functional-qsim/load_model.h').read_text()
 t=rep(t,'    load_model_state *m=&load_model;\n    if(store)', '    if(coupled_active()){coupled_alias_access(off,size,store);return;}\n    load_model_state *m=&load_model;\n    if(store)')
 t+='''\nvoid lod8_snapshot(void *ctx,uint32_t *words,uint32_t *valid) {
    qenv *E=ctx;if(load_model.phase==1){fatal(E,"LOD8 snapshot while active");abort();}
 memcpy(words,gp(E,0x13f80000u,1152),1152);*valid=load_model.valid_slots;
}\n''';p.write_text(t)
 p=q/'cpu.h';p.write_text(p.read_text()+'\nvoid lod8_alias_access(void *ctx,uint32_t offset,unsigned size,unsigned store);\n')
 p=q/'cpu_exec.h';t=p.read_text();new=(FUNC/'functional-qsim/cpu_exec.h').read_text().splitlines()
 for prefix in ['LINT_CA','LINT_UC_RD','LINT_UC_WR']:
  oldlines=[x for x in t.splitlines()if x.startswith('#define '+prefix+'(')]
  newlines=[x for x in new if x.startswith('#define '+prefix+'(')]
  assert len(oldlines)==len(newlines)==2
  for a,b in zip(oldlines,newlines):t=rep(t,a,b)
 p.write_text(t)
 p=q/'coupled.h';t=p.read_text();t=rep(t,'int coupled_active(void);','int coupled_active(void);\nvoid coupled_alias_access(uint32_t,unsigned,unsigned);\nvoid coupled_alias_cbo(unsigned,uint32_t);');p.write_text(t)
 p=q/'env.c';t=p.read_text();t=rep(t,'if(!coupled_active())lm_cbo(ctx,cpu,op,addr);','if(!coupled_active())lm_cbo(ctx,cpu,op,addr);else coupled_alias_cbo(op,addr);');p.write_text(t)
 p=q/'cache.c';t=p.read_text();t=rep(t,'(addr&~1023u)==0x13f80000u','(addr&~2047u)==0x13f80000u',2);p.write_text(t)
 p=OUT/'model/frozen/coupled.cpp';t=p.read_text();t=rep(t,'extern "C" void coupled_start(',SEED+'\nextern "C" void coupled_start(')
 t=rep(t,'    cpu_origin=E->cpu->model->cyc;', '    seed_alias_state();\n    cpu_origin=E->cpu->model->cyc;')
 t=rep(t,'(addr&~1023u)==0x13f80000u','(addr&~2047u)==0x13f80000u')
 t=rep(t,'if(off==16)source_count=value&63u;','if(off==16){source_count=value&63u;source_first=(value>>8)&31u;}')
 t=rep(t,'        gd_operation=0;','''        if((value&6u)==6u&&!(value&0xe8u)) {
            unsigned lo=source_first*36/64,hi=((source_first+source_count)*36+63)/64;
            alias_expected=((1u<<hi)-1u)^((1u<<lo)-1u);alias_invalidated=0;
        } else alias_expected=alias_invalidated=0;
        gd_operation=0;''')
 t=t.replace('    // and cache entries exist. CPU slots remain materialized after each LOAD.','    // and cache entries exist. Persistent output BRAMs were seeded and checked above.')
 p.write_text(t)
 cmd=[x.replace(str(OLD),str(OUT))for x in json.loads((OLD/'build-command.json').read_text())]
 write(OUT/'build-command.json',cmd)
 write(OUT/'prepared.json',dict(scope=__doc__,app_sha256=sha(APP),parent_sha256=sha(OLD/'manifest.json'),component_sha256=sha(COMP/'manifest.json'),functional_sha256=sha(FUNC/'manifest.json'),recipe_sha256=sha(Path(__file__)),sources={str(p.relative_to(OUT)):sha(p)for p in OUT.rglob('*')if p.is_file()},warm_handoff='Seed canonical output snapshot into actual BRAMs and validity at existing idle pre-timing handoff; all18 lines read/compared through actual alias, without CPU-cache warming or timed counter advance. No timed reseeding.',deployment_ready=False))
def build():
 with(OUT/'build-qsim.log').open('x')as f:subprocess.run(['make','-j3',*[f'build/qsim/{n}.o'for n in 'cpu fpu cache prof qelf lint gpu app env main'.split()]],cwd=OUT/'qsim',stdout=f,stderr=subprocess.STDOUT,check=True)
 with(OUT/'model/build.log').open('x')as f:subprocess.run(json.loads((OUT/'build-command.json').read_text()),stdout=f,stderr=subprocess.STDOUT,check=True)
 write(OUT/'model/build.json',dict(simulator_sha256=sha(OUT/'model/obj/Vtb_gpu')))
def run(window):
 m=module('lod8_runner',ROOT/'tools/experiments/os30_overlap_20261006/run.py');m.OUT=OUT
 os.environ['IDEAL_GPU_MEMORY']='0';os.environ['LOD5_MODEL_MODE']='normal'
 sys.argv=[str(Path(__file__)),window,'--elf',str(APP),'--start','360'if window=='warm'else'234','--frames','8'];m.main()
 assert 'FATAL'not in(OUT/f'run-{window}/run.log').read_text()
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('action',choices=['prepare','build','run']);p.add_argument('--window',choices=['warm','cold'],default='warm');a=p.parse_args();run(a.window)if a.action=='run'else globals()[a.action]()
