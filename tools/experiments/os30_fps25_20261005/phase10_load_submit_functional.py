#!/usr/bin/env python3
"""Private passive ownership and exact-stream proof of adjacent LOAD submission."""
import argparse,csv,json,os,re,shutil,subprocess
from pathlib import Path
from elftools.elf.elffile import ELFFile
from phase10_experiment import CAMP,ROOT,module,sha,write
H=Path(__file__).resolve().parent;P=CAMP/'phase10'
OUT=P/'load-submit-functional-v1';CAND=P/'load-submit-overlap-v1';OLD=P/'lod5-recycle-functional-v1'
ELF_SHA='bbb1c31adce29de9f33be6df1cfd2c126714b8017e3d232b1cb9c77c12d9c405'
def rep(t,a,b):
 assert t.count(a)==1,(a,t.count(a));return t.replace(a,b)
def prepare():
 assert sha(CAND/'app.elf')==ELF_SHA
 OUT.mkdir(exist_ok=False);q=OUT/'functional-qsim';q.mkdir()
 for n in ('app.elf','gfx_pc.c'):shutil.copy2(CAND/n,OUT/n)
 for p in(OLD/'functional-qsim').iterdir():
  if p.suffix in('.c','.h')or p.name=='Makefile':shutil.copy2(p,q/p.name)
 # Reuse the immutable reference capture rather than re-running known F11.
 subprocess.run(['cp','-a','--reflink=auto',str(OLD/'capture-baseline'),str(OUT/'capture-baseline')],check=True)
 with(CAND/'app.elf').open('rb')as f:
  syms={s.name:(int(s['st_value']),int(s['st_size']))for s in ELFFile(f).get_section_by_name('.symtab').iter_symbols()}
 names=['gfx_run_dl','gfx_cpu_indexed_run','gfx_cpu_submit_pending','gfx_sp_vertex','gfx_start_frame','gfx_run','gfx_end_frame']
 asm=subprocess.check_output(['llvm-objdump','-d','--mattr=+m,+a,+f,+c','--no-show-raw-insn','--disassemble-symbols='+','.join(names),str(CAND/'app.elf')],text=True)
 (OUT/'observer-sites.asm').write_text(asm)
 # Both compiler-generated loop heads read command word0 from s1. Observe
 # before that unmodified instruction: this does not replace guest execution.
 dl=asm[asm.index('<gfx_run_dl>:'):];dl=dl.split('\n\n',1)[0]
 command_loads=[int(x,16)for x in re.findall(r'^([0-9a-f]+):\s+lw\s+s0, 0x0\(s1\)$',dl,re.M)]
 assert command_loads==[0x104d2e38,0x104d2e58,0x104d3a02,0x104d3a3a]
 dispatch=command_loads[:2] # The latter two reload TRI fallback arguments.
 assert dispatch==[0x104d2e38,0x104d2e58],dispatch
 defs='\n'.join('#define OBS_'+n.upper()+' 0x%xu'%syms[n][0]for n in names+['load_submit_count','load_submit_stats','load_submit_payload','load_submit_jobs','load_submit_alpha'])
 sites=dispatch+[syms[n][0]for n in names]
 predicate=' || '.join('e->pc==0x%xu'%x for x in sites)
 p=q/'cpu.h';t=p.read_text();t=rep(t,'    void *ctx;','    void *ctx;\n    void (*observe)(void *ctx, qsim_cpu *cpu, uint32_t pc);');p.write_text(t)
 p=q/'cpu_exec.h';t=p.read_text();t=rep(t,'goto *tbl[e->h]; } while (0)',f'if (unlikely(e->h && ({predicate}))) cpu->env.observe(cpu->env.ctx,cpu,e->pc); goto *tbl[e->h]; }} while (0)');p.write_text(t)
 payload_size=syms['load_submit_payload'][1];jobs_size=syms['load_submit_jobs'][1]
 observer=defs+f'''
static struct {{uint64_t dispatch,adjacent,indexed,submissions,started,fallback,frames,vertex,zero;
 unsigned saved;unsigned char payload[{payload_size}],jobs[{jobs_size}],alpha;}} obs;
static unsigned obs_count(qenv *E) {{return rd32(E,OBS_LOAD_SUBMIT_COUNT);}}
static void obs_frame(qenv *E) {{
 if(obs_count(E)||obs.saved)fatal(E,"pending batch crosses presentation boundary");
 uint32_t *s=(uint32_t*)gp(E,OBS_LOAD_SUBMIT_STATS,12);
 if(s[0]!=s[1]+s[2]||s[0]!=obs.submissions||s[1]!=obs.started||s[2]!=obs.fallback)
  fatal(E,"overlap accounting mismatch");
 const char *path=getenv("LOAD_SUBMIT_OBSERVER_PATH");if(!path)abort();static FILE *f;
 if(!f){{f=fopen(path,"w");if(!f)abort();fprintf(f,"frame,queued,after_start,fallback,dispatch,adjacent,indexed,vertex,zero,frames\\n");}}
 fprintf(f,"%llu,%u,%u,%u,%llu,%llu,%llu,%llu,%llu,%llu\\n",(unsigned long long)E->flips,s[0],s[1],s[2],
 (unsigned long long)obs.dispatch,(unsigned long long)obs.adjacent,(unsigned long long)obs.indexed,
 (unsigned long long)obs.vertex,(unsigned long long)obs.zero,(unsigned long long)obs.frames);fflush(f);
}}
static void obs_pc(void *ctx,qsim_cpu *cpu,uint32_t pc) {{
 qenv *E=ctx;unsigned n=obs_count(E);
 if(pc==0x{dispatch[0]:x}u||pc==0x{dispatch[1]:x}u){{
  obs.dispatch++;unsigned opcode=rd32(E,cpu->x[9])>>24;
  if(n){{if(opcode!=1||obs.saved||n>64)fatal(E,"pending batch crosses non-adjacent G_VTX");
   obs.saved=n;obs.adjacent++;memcpy(obs.payload,gp(E,OBS_LOAD_SUBMIT_PAYLOAD,sizeof obs.payload),sizeof obs.payload);
   memcpy(obs.jobs,gp(E,OBS_LOAD_SUBMIT_JOBS,sizeof obs.jobs),sizeof obs.jobs);obs.alpha=*(unsigned char*)gp(E,OBS_LOAD_SUBMIT_ALPHA,1);
  }}return;
 }}
 if(pc==OBS_GFX_CPU_SUBMIT_PENDING){{
  if(!n||n!=obs.saved||memcmp(obs.payload,gp(E,OBS_LOAD_SUBMIT_PAYLOAD,sizeof obs.payload),sizeof obs.payload)||
    memcmp(obs.jobs,gp(E,OBS_LOAD_SUBMIT_JOBS,sizeof obs.jobs),sizeof obs.jobs)||obs.alpha!=*(unsigned char*)gp(E,OBS_LOAD_SUBMIT_ALPHA,1))
   fatal(E,"prepared batch lifetime violated");
  uint32_t *s=(uint32_t*)gp(E,OBS_LOAD_SUBMIT_STATS,12);obs.submissions++;
  if(s[1]==obs.started+1&&s[2]==obs.fallback){{
   if(load_model.phase!=1)fatal(E,"after-START submission without active LOAD");obs.started++;
  }}else if(s[1]==obs.started&&s[2]==obs.fallback+1)obs.fallback++;
  else fatal(E,"submit reason/accounting mismatch");
  obs.saved=0;return;
 }}
 if(pc==OBS_GFX_SP_VERTEX){{obs.vertex++;if(!cpu->x[10])obs.zero++;return;}}
 if(n||obs.saved)fatal(E,"pending batch at buffer reuse or non-G_VTX/frame entry");
 if(pc==OBS_GFX_CPU_INDEXED_RUN)obs.indexed++;
 if(pc==OBS_GFX_START_FRAME||pc==OBS_GFX_RUN||pc==OBS_GFX_END_FRAME)obs.frames++;
}}
'''
 (q/'overlap_observer.h').write_text(observer)
 p=q/'env.c';t=p.read_text();t=rep(t,'#include "load_model.h"','#include "load_model.h"\n#include "overlap_observer.h"')
 t=rep(t,'static void lm_stats_frame(qenv *E);','static void lm_stats_frame(qenv *E);\nstatic void obs_frame(qenv *E);')
 t=rep(t,'    lm_stats_frame(E);','    obs_frame(E);\n    lm_stats_frame(E);')
 t=rep(t,'    cpu->env.ctx = E;','    cpu->env.ctx = E;\n    cpu->env.observe = obs_pc;');p.write_text(t)
 with(OUT/'build.log').open('x')as f:subprocess.run(['make','-j4'],cwd=q,stdout=f,stderr=subprocess.STDOUT,check=True)
 write(OUT/'prepared.json',dict(elf_sha256=ELF_SHA,parent_manifest_sha256=sha(OLD/'manifest.json'),source_sha256=sha(CAND/'gfx_pc.c'),simulator_sha256=sha(q/'qsim'),observer_sites=sites,dispatch_cmd_register='s1/x9',G_VTX_opcode=1,payload_bytes=payload_size,jobs_bytes=jobs_size,helper_sha256=sha(Path(__file__)),sources={p.name:sha(p)for p in q.iterdir()if p.suffix in('.c','.h')or p.name=='Makefile'},scope='Passive observer only, no guest instruction replacement or modeled cycle changes. Native FMA functional model, synthetic LOAD timing; no device performance claim.'))
 shutil.copy2(Path(__file__),OUT/'recipe.py')
def action(name,mode):
 m=module('load_submit_capture',H/'phase10_load_cram_capture.py');m.OUT=OUT;m.CAND=CAND
 if name=='capture':
  os.environ['LOD5_RECYCLE_STATS_ADDR']=hex(m.symbols(CAND/'app.elf')['cram_vertex_recycle_stats'])
  os.environ['LOAD_SUBMIT_OBSERVER_PATH']=str(OUT/('observer-'+mode+'.csv'))
 getattr(m,name)(mode)
 if name=='analyze':
  rows=list(csv.DictReader((OUT/('observer-'+mode+'.csv')).open()));assert len(rows)==721
  for row in rows:assert int(row['queued'])==int(row['after_start'])+int(row['fallback'])==int(row['adjacent'])
  write(OUT/('observer-result-'+mode+'.json'),dict(passed=True,frames=len(rows),last=rows[-1],all_boundaries_pending_zero=True,prepared_payload_and_jobs_unchanged_while_pending=True,no_non_VTX_dispatch_while_pending=True,all_indexed_reuse_pending_zero=True,submission_after_actual_model_START=True,limits='Zero-VTX direct path is source-reviewed; dynamic coverage is reported by zero count. Synthetic LOAD latency is not a performance measurement.'))
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('action',choices=['prepare','capture','analyze']);p.add_argument('--mode',choices=['normal','faults','absent','timeout','fullpool'],default='normal');a=p.parse_args();prepare()if a.action=='prepare'else action(a.action,a.mode)
