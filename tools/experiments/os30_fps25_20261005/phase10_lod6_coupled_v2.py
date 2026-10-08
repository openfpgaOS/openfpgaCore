#!/usr/bin/env python3
"""Matched LOD6 lighting-overlap screen using real LOAD/GPU/SDRAM/full CRAM."""
import argparse,csv,json,os,shutil,subprocess,sys
from pathlib import Path
from phase10_experiment import CAMP,ROOT,module,sha,write
P=CAMP/'phase10';OLD=P/'lod5-real-coupled-v2';OUT=P/'lod6-coupled-v2';FUNC=P/'lod6-functional-v1';CAND=P/'lod6-driver-v1'
def prepare():
 OUT.mkdir(exist_ok=False)
 shutil.copytree(OLD/'model/frozen',OUT/'model/frozen')
 shutil.copytree(OLD/'qsim',OUT/'qsim',ignore=shutil.ignore_patterns('build','qsim'))
 shutil.copy2(P/'lod6-components-v1/frozen/phase10_load_alias.v',OUT/'model/frozen/common/phase10_load_alias.v')
 shutil.copy2(FUNC/'functional-qsim/load_model.h',OUT/'qsim/load_model.h')
 p=OUT/'model/frozen/coupled.cpp';t=p.read_text()
 def rep(a,b):
  nonlocal t
  assert t.count(a)==1,(a,t.count(a));t=t.replace(a,b)
 rep('static uint64_t gd_start_cycle;','static uint64_t gd_start_cycle,gd_actual_finish,gd_first_poll;\nstatic bool gd_seen_active;')
 rep('static void gd_begin_sample() {','static void gd_begin_sample() {\n    if(gd_operation) {\n        if(tb->rootp->tb_gpu__DOT__load_client__DOT__active)gd_seen_active=true;\n        else if(gd_seen_active&&!gd_actual_finish)gd_actual_finish=cycles;\n    }')
 rep('reason,words,count,lit\\n','reason,words,count,lit,hardware_finish,first_poll\\n')
 rep('    tb->gd_reg_addr=off/4;tb->eval();','    if(off==4&&gd_operation&&!gd_first_poll)gd_first_poll=cycles;\n    tb->gd_reg_addr=off/4;tb->eval();')
 rep('%u,0,0,0,0,%u,%u\\n','%u,0,0,0,0,%u,%u,%llu,%llu\\n')
 rep('value,source_count,source_light);','value,source_count,source_light,(unsigned long long)gd_actual_finish,(unsigned long long)gd_first_poll);')
 rep('        ++gd_loads;','        gd_actual_finish=gd_first_poll=0;gd_seen_active=false;\n        ++gd_loads;')
 p.write_text(t)

 for n in ('env.c','main.c'):
  p=OUT/'qsim'/n;p.write_text(p.read_text().replace('LOD5','LOD6').replace('lod5','lod6'))
 cmd=[x.replace(str(OLD),str(OUT))for x in json.loads((OLD/'build-command.json').read_text())]
 write(OUT/'build-command.json',cmd)
 write(OUT/'prepared.json',dict(scope=__doc__,parent_manifest_sha256=sha(OLD/'manifest.json'),elf_sha256=sha(CAND/'app.elf'),source_sha256={str(p.relative_to(OUT)):sha(p)for p in OUT.rglob('*')if p.is_file()},helper_sha256=sha(Path(__file__))))
def build():
 with(OUT/'build-qsim.log').open('x')as f:subprocess.run(['make','-j3',*[f'build/qsim/{n}.o'for n in'cpu fpu cache prof qelf lint gpu app env main'.split()]],cwd=OUT/'qsim',stdout=f,stderr=subprocess.STDOUT,check=True)
 cmd=json.loads((OUT/'build-command.json').read_text())
 with(OUT/'model/build.log').open('x')as f:subprocess.run(cmd,stdout=f,stderr=subprocess.STDOUT,check=True)
 write(OUT/'model/build.json',dict(command=cmd,simulator_sha256=sha(OUT/'model/obj/Vtb_gpu')))
def run():
 m=module('lod6_coupled_run',ROOT/'tools/experiments/os30_overlap_20261006/run.py');m.OUT=OUT
 os.environ['IDEAL_GPU_MEMORY']='0';os.environ['LOD6_MODEL_MODE']='normal'
 sys.argv=[str(Path(__file__)),'window-8','--elf',str(CAND/'app.elf')];m.main()
def analyze():
 a=OLD/'run-candidate';b=OUT/'run-window-8'
 for n in('scene.csv','geometry.csv','culling.csv',*[f'frame_{i:05}.bin'for i in range(360,368)]):assert(a/n).read_bytes()==(b/n).read_bytes(),n
 assert'FATAL'not in(b/'run.log').read_text()
 source=json.loads((OUT/'prepared.json').read_text())
 for n,h in source['source_sha256'].items():assert sha(OUT/n)==h,n
 inv=json.loads((b/'inputs.json').read_text());assert inv['success']
 for n,h in inv['results_sha256'].items():assert sha(b/n)==h,n
 baseline=json.loads((a/'coupled.json').read_text())['cpu_cycles']/800000;candidate=json.loads((b/'coupled.json').read_text())['cpu_cycles']/800000
 ar=list(csv.DictReader((a/'frames.csv').open()))[360:368];br=list(csv.DictReader((b/'frames.csv').open()))[360:368]
 fields=('est_cycles','icache_miss_sdram','dcache_miss','insns','mispredict');totals={f:{'lod5':sum(int(r[f])for r in ar),'lod6':sum(int(r[f])for r in br)}for f in fields}
 def profile(p):return{r['function']:r for r in csv.DictReader(p.open(),delimiter='\t')}
 p0=profile(b/'profile-0360.tsv');p1=profile(b/'profile.tsv');hot=[]
 for n,r in p1.items():
  c=int(r['self_cycles'])-int(p0.get(n,{}).get('self_cycles',0));calls=int(r['calls'])-int(p0.get(n,{}).get('calls',0));hot.append(dict(function=n,self_cycles=c,self_ms_per_frame=c/800000,calls=calls))
 hot.sort(key=lambda r:r['self_cycles'],reverse=True);assert sum(x['self_cycles']for x in hot)==round(candidate*800000)
 write(OUT/'hot-profile.json',dict(first=360,last=367,self_only=True,functions=hot))
 ops=list(csv.DictReader((b/'geometry-operations.csv').open()))
 result=dict(passed=True,lod5_ms=baseline,lod6_ms=candidate,delta_ms=candidate-baseline,time_change_percent=100*(candidate/baseline-1),throughput_change_percent=100*(baseline/candidate-1),all8_actual_color_depth_exact=True,all368_scene_geometry_culling_exact=True,frame_totals=totals,load_jobs=len(ops),load_vertices=sum(int(r['count'])for r in ops),load_start_to_observed_completion_ms_per_frame=sum(int(r['cycles'])for r in ops)/800000,hardware_lit_jobs=sum(int(r['lit'])for r in ops),qualified_for_deployment=False,limits=['Estimated retained CPU/cache timing; actual LOAD/GPU/SDRAM/CRAM RTL with synthetic background traffic. No physical FPS claim.','Hardware LOAD completion is observed on first CPU status poll after lighting. Trace duration includes overlapping CPU work and is not pure polling wait.','Warm steady-state frames360..367, actual real source client and instrumentation, no source misses or recycling.','Only LOD6 wrapper, client ELF and warmup functional model differ from LOD5.'])
 write(OUT/'result.json',result);print(json.dumps(result,indent=2))
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('action',choices=['prepare','build','run','analyze']);a=p.parse_args();globals()[a.action]()
