#!/usr/bin/env python3
"""Passive stack-lifetime/ordering checks of the actual stack-overlap ELF."""
import argparse,csv,json,os,re,shutil,subprocess
from pathlib import Path
from elftools.elf.elffile import ELFFile
from phase10_experiment import CAMP,ROOT,module,sha,write
H=Path(__file__).resolve().parent;P=CAMP/'phase10';OUT=P/'load-submit-stack-functional-v2';CAND=P/'load-submit-stack-v2';OLD=P/'lod5-recycle-functional-v1'
ELF_SHA='b3c1d09e21eb1c8275495391db6b8a32ee8770cbbf1635cde28865830fc54a32'
def rep(t,a,b):
 assert t.count(a)==1,(a,t.count(a));return t.replace(a,b)
def prepare():
 assert sha(CAND/'app.elf')==ELF_SHA
 OUT.mkdir(exist_ok=False);q=OUT/'functional-qsim';q.mkdir()
 for n in ('app.elf','gfx_pc.c'):shutil.copy2(CAND/n,OUT/n)
 for p in(OLD/'functional-qsim').iterdir():
  if p.suffix in('.c','.h')or p.name=='Makefile':shutil.copy2(p,q/p.name)
 subprocess.run(['cp','-a','--reflink=auto',str(OLD/'capture-baseline'),str(OUT/'capture-baseline')],check=True)
 with(CAND/'app.elf').open('rb')as f:syms={s.name:(int(s['st_value']),int(s['st_size']))for s in ELFFile(f).get_section_by_name('.symtab').iter_symbols()}
 names=['gfx_run_dl','gfx_cpu_indexed_run','gfx_cpu_submit_pending','gfx_sp_vertex','gfx_start_frame','gfx_run','gfx_end_frame']
 asm=subprocess.check_output(['llvm-objdump','-d','--mattr=+m,+a,+f,+c','--no-show-raw-insn','--disassemble-symbols='+','.join(names),str(CAND/'app.elf')],text=True);(OUT/'observer-sites.asm').write_text(asm)
 assert re.search(r'^104d1d00:\s+addi\s+sp, sp, -0x560$',asm,re.M)
 dispatch=[0x104d2f38,0x104d2f58];returns=[0x104d3afe,0x104d3b36];nested_return=0x104d25fe
 for pc in dispatch:assert re.search(r'^%x:\s+lw\s+s0, 0x0\(s1\)'%pc,asm,re.M)
 for pc in returns:assert re.search(r'^%x:\s+bne\s+s1, a0,'%pc,asm,re.M)
 assert re.search(r'^104d25fa:\s+jal\s+0x104cf5c0 <gfx_sp_vertex>',asm,re.M)
 defs='\n'.join('#define OBS_'+n.upper()+' 0x%xu'%syms[n][0]for n in names+['load_submit_count','load_submit_stats','load_submit_payload','load_submit_jobs','load_submit_alpha'])
 sites=dispatch+returns+[nested_return]+[syms[n][0]for n in names]
 predicate=' || '.join('e->pc==0x%xu'%pc for pc in sites)
 p=q/'cpu.h';t=p.read_text();t=rep(t,'    void *ctx;','    void *ctx;\n    void (*observe)(void *ctx,qsim_cpu *cpu,uint32_t pc);');t=rep(t,'    uint32_t x[33];','    uint32_t observed_min_sp,observed_index_entry,observed_index_depth;\n    unsigned observed_index_active;\n    uint32_t x[33];');p.write_text(t)
 p=q/'cpu_exec.h';t=p.read_text();t=rep(t,'goto *tbl[e->h]; } while (0)',f'''if(e->h){{ uint32_t osp=x[2]; if(osp&&(!cpu->observed_min_sp||osp<cpu->observed_min_sp))cpu->observed_min_sp=osp; if(cpu->observed_index_active&&osp<=cpu->observed_index_entry&&cpu->observed_index_entry-osp>cpu->observed_index_depth)cpu->observed_index_depth=cpu->observed_index_entry-osp; if(unlikely({predicate}))cpu->env.observe(cpu->env.ctx,cpu,e->pc); }} goto *tbl[e->h]; }} while (0)''');p.write_text(t)
 observer=defs+r'''
static struct {uint64_t dispatch,adjacent,indexed,returns,submissions,started,fallback,frames,vertex,zero;
 unsigned saved;uint32_t payload_pointer,jobs_pointer,cmd;unsigned char payload[896],jobs[256],alpha;} obs;
static unsigned obs_count(qenv *E){return rd32(E,OBS_LOAD_SUBMIT_COUNT);}
static int obs_pointers(qenv *E){return rd32(E,OBS_LOAD_SUBMIT_PAYLOAD)||rd32(E,OBS_LOAD_SUBMIT_JOBS);}
static void obs_empty(qenv *E){if(obs_count(E)||obs.saved||obs_pointers(E))fatal(E,"pending stack batch escaped ownership");}
static void obs_frame(qenv *E){
 obs_empty(E);if(E->cpu->observed_index_active)fatal(E,"indexed frame crosses presentation");
 uint32_t *s=(uint32_t*)gp(E,OBS_LOAD_SUBMIT_STATS,12);
 if(s[0]!=s[1]+s[2]||s[0]!=obs.submissions||s[1]!=obs.started||s[2]!=obs.fallback||obs.indexed!=obs.returns)fatal(E,"overlap accounting mismatch");
 const char *path=getenv("LOAD_SUBMIT_OBSERVER_PATH");if(!path)abort();static FILE*f;
 if(!f){f=fopen(path,"w");if(!f)abort();fprintf(f,"frame,queued,after_start,fallback,dispatch,adjacent,indexed,returns,vertex,zero,frames,min_sp,max_indexed_stack_bytes\n");}
 fprintf(f,"%llu,%u,%u,%u,%llu,%llu,%llu,%llu,%llu,%llu,%llu,%u,%u\n",(unsigned long long)E->flips,s[0],s[1],s[2],
 (unsigned long long)obs.dispatch,(unsigned long long)obs.adjacent,(unsigned long long)obs.indexed,(unsigned long long)obs.returns,
 (unsigned long long)obs.vertex,(unsigned long long)obs.zero,(unsigned long long)obs.frames,E->cpu->observed_min_sp,E->cpu->observed_index_depth);fflush(f);
}
static void obs_pc(void *ctx,qsim_cpu *cpu,uint32_t pc){
 qenv *E=ctx;unsigned n=obs_count(E);
 if(pc==OBS_GFX_CPU_INDEXED_RUN){obs_empty(E);if(cpu->observed_index_active)fatal(E,"nested indexed buffer reuse");cpu->observed_index_active=1;cpu->observed_index_entry=cpu->x[2];obs.cmd=0;obs.indexed++;return;}
 if(pc==0x104d3afeu||pc==0x104d3b36u){
  obs_empty(E);if(!cpu->observed_index_active||cpu->x[2]!=cpu->observed_index_entry)fatal(E,"indexed frame not restored");
  if(obs.cmd&&cpu->x[10]!=obs.cmd+8)fatal(E,"adjacent VTX not consumed exactly once");
  cpu->observed_index_active=0;obs.returns++;return;
 }
 if(pc==0x104d25feu){obs_empty(E);if(!cpu->observed_index_active)fatal(E,"nested VTX returned after stack release");return;}
 if(pc==OBS_GFX_SP_VERTEX){
  obs.vertex++;if(!cpu->x[10])obs.zero++;
  if(n){
   if(!cpu->observed_index_active||obs.saved||n>64||cpu->x[1]!=0x104d25feu)fatal(E,"pending batch lacks indexed owner");
   uint32_t cmd=cpu->x[27],word=rd32(E,cmd),count=(word>>12)&255,first=((word>>1)&127)-count;
   if(word>>24!=1||cpu->x[10]!=count||cpu->x[11]!=first||cpu->x[12]!=rd32(E,cmd+4))fatal(E,"nested VTX decode/adjacency differs");
   obs.cmd=cmd;obs.saved=n;obs.adjacent++;
   obs.payload_pointer=rd32(E,OBS_LOAD_SUBMIT_PAYLOAD);obs.jobs_pointer=rd32(E,OBS_LOAD_SUBMIT_JOBS);
   if(obs.payload_pointer<cpu->x[2]||obs.payload_pointer+sizeof obs.payload>cpu->observed_index_entry||
      obs.jobs_pointer<cpu->x[2]||obs.jobs_pointer+sizeof obs.jobs>cpu->observed_index_entry||
      !(obs.payload_pointer+sizeof obs.payload<=obs.jobs_pointer||obs.jobs_pointer+sizeof obs.jobs<=obs.payload_pointer))fatal(E,"prepared arrays outside/nonexclusive caller stack");
   memcpy(obs.payload,gp(E,obs.payload_pointer,sizeof obs.payload),sizeof obs.payload);
   memcpy(obs.jobs,gp(E,obs.jobs_pointer,sizeof obs.jobs),sizeof obs.jobs);obs.alpha=*(unsigned char*)gp(E,OBS_LOAD_SUBMIT_ALPHA,1);
  }return;
 }
 if(pc==OBS_GFX_CPU_SUBMIT_PENDING){
  if(!n||n!=obs.saved||!cpu->observed_index_active||rd32(E,OBS_LOAD_SUBMIT_PAYLOAD)!=obs.payload_pointer||rd32(E,OBS_LOAD_SUBMIT_JOBS)!=obs.jobs_pointer||
   memcmp(obs.payload,gp(E,obs.payload_pointer,sizeof obs.payload),sizeof obs.payload)||memcmp(obs.jobs,gp(E,obs.jobs_pointer,sizeof obs.jobs),sizeof obs.jobs)||obs.alpha!=*(unsigned char*)gp(E,OBS_LOAD_SUBMIT_ALPHA,1))fatal(E,"prepared stack batch lifetime violated");
  uint32_t*s=(uint32_t*)gp(E,OBS_LOAD_SUBMIT_STATS,12);obs.submissions++;
  if(s[1]==obs.started+1&&s[2]==obs.fallback){if(load_model.phase!=1)fatal(E,"after-START submit without LOAD");obs.started++;}
  else if(s[1]==obs.started&&s[2]==obs.fallback+1)obs.fallback++;
  else fatal(E,"submit accounting mismatch");obs.saved=0;return;
 }
 obs_empty(E);if(cpu->observed_index_active)fatal(E,"indexed frame escaped to outer dispatch");
 if(pc==0x104d2f38u||pc==0x104d2f58u)obs.dispatch++;
 if(pc==OBS_GFX_START_FRAME||pc==OBS_GFX_RUN||pc==OBS_GFX_END_FRAME)obs.frames++;
}
'''
 (q/'overlap_observer.h').write_text(observer)
 p=q/'env.c';t=p.read_text();t=rep(t,'#include "load_model.h"','#include "load_model.h"\n#include "overlap_observer.h"');t=rep(t,'static void lm_stats_frame(qenv *E);','static void lm_stats_frame(qenv *E);\nstatic void obs_frame(qenv *E);');t=rep(t,'    lm_stats_frame(E);','    obs_frame(E);\n    lm_stats_frame(E);');t=rep(t,'    cpu->env.ctx = E;','    cpu->env.ctx = E;\n    cpu->env.observe = obs_pc;');p.write_text(t)
 with(OUT/'build.log').open('x')as f:subprocess.run(['make','-j4'],cwd=q,stdout=f,stderr=subprocess.STDOUT,check=True)
 write(OUT/'prepared.json',dict(elf_sha256=ELF_SHA,parent_manifest_sha256=sha(OLD/'manifest.json'),source_sha256=sha(CAND/'gfx_pc.c'),simulator_sha256=sha(q/'qsim'),observer_sites=sites,indexed_static_frame_bytes=0x560,payload_bytes=896,jobs_bytes=256,helper_sha256=sha(Path(__file__)),sources={p.name:sha(p)for p in q.iterdir()if p.suffix in('.c','.h')or p.name=='Makefile'},scope='Passive actual-ELF execution/stack observer. No instructions replaced or timing charges changed. Synthetic LOAD model does not measure overlap or device FPS.'))
 shutil.copy2(Path(__file__),OUT/'recipe.py')
def action(name,mode):
 m=module('stack_overlap_capture',H/'phase10_load_cram_capture.py');m.OUT=OUT;m.CAND=CAND
 if name=='capture':
  os.environ['LOD5_RECYCLE_STATS_ADDR']=hex(m.symbols(CAND/'app.elf')['cram_vertex_recycle_stats'])
  os.environ['LOAD_SUBMIT_OBSERVER_PATH']=str(OUT/('observer-'+mode+'.csv'))
 getattr(m,name)(mode)
 if name=='analyze':
  rows=list(csv.DictReader((OUT/('observer-'+mode+'.csv')).open()));assert len(rows)==721
  for r in rows:assert int(r['queued'])==int(r['after_start'])+int(r['fallback'])==int(r['adjacent']) and r['indexed']==r['returns']
  last=rows[-1];write(OUT/('observer-result-'+mode+'.json'),dict(passed=True,frames=len(rows),last=last,payload_and_jobs_inside_owned_caller_frame=True,all_return_pointers_cleared=True,all_pending_zero_at_outer_dispatch_and_frames=True,all_indexed_frames_restored=True,nested_VTX_decode_and_single_consumption_exact=True,global_stack_top=0x13a80000,global_max_stack_bytes=0x13a80000-int(last['min_sp']),max_indexed_including_callees_bytes=int(last['max_indexed_stack_bytes']),limits='Dynamic high-water for the exercised workload and synthetic OS stack. No proof of unbounded level recursion; zero-VTX source reviewed but dynamic count reported explicitly.'))
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('action',choices=['prepare','capture','analyze']);p.add_argument('--mode',choices=['normal','faults','absent','timeout','fullpool'],default='normal');a=p.parse_args();prepare()if a.action=='prepare'else action(a.action,a.mode)
