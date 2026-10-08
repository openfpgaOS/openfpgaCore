#!/usr/bin/env python3
"""Modeled cache-capacity screen on real LOD5 client; no CPU regeneration/fit."""
import argparse,csv,json,os,shutil,subprocess,sys
from pathlib import Path
from phase10_experiment import CAMP,ROOT,module,sha,write
P=CAMP/'phase10';OLD=P/'lod5-real-coupled-v2';OUT=P/'lod5-cache-capacity-v1'
CHOICES={'ic16-dc128':('ic_sets = 256','ic_sets = 128'),'ic32-dc64':('dc_sets = 1024','dc_sets = 512')}
def prepare(label):
 d=OUT/label;d.mkdir(parents=True,exist_ok=False)
 shutil.copytree(OLD/'model/frozen',d/'model/frozen');shutil.copytree(OLD/'qsim',d/'qsim',ignore=shutil.ignore_patterns('build','qsim'))
 p=d/'qsim/cache.c';t=p.read_text();a,b=CHOICES[label];assert t.count('p->'+a+';')==1;t=t.replace('p->'+a+';','p->'+b+';');p.write_text(t)
 cmd=[x.replace(str(OLD),str(d))for x in json.loads((OLD/'build-command.json').read_text())];write(d/'build-command.json',cmd)
 write(d/'prepared.json',dict(scope=__doc__,label=label,parent_manifest_sha256=sha(OLD/'manifest.json'),change=[a,b],source_sha256={str(p.relative_to(d)):sha(p)for p in d.rglob('*')if p.is_file()},helper_sha256=sha(Path(__file__))))
def build(label):
 d=OUT/label
 with(d/'build-qsim.log').open('x')as f:subprocess.run(['make','-j3',*[f'build/qsim/{n}.o'for n in'cpu fpu cache prof qelf lint gpu app env main'.split()]],cwd=d/'qsim',stdout=f,stderr=subprocess.STDOUT,check=True)
 cmd=json.loads((d/'build-command.json').read_text())
 with(d/'model/build.log').open('x')as f:subprocess.run(cmd,stdout=f,stderr=subprocess.STDOUT,check=True)
 write(d/'model/build.json',dict(command=cmd,simulator_sha256=sha(d/'model/obj/Vtb_gpu')))
def run(label):
 m=module('lod5_capacity_run',ROOT/'tools/experiments/os30_overlap_20261006/run.py');m.OUT=OUT/label
 os.environ['IDEAL_GPU_MEMORY']='0';os.environ['LOD5_MODEL_MODE']='normal'
 sys.argv=[str(Path(__file__)),'window-8','--elf',str(P/'load-cram-driver-v1/app.elf')];m.main()
def analyze(label):
 d=OUT/label;a=OLD/'run-candidate';b=d/'run-window-8'
 for p in(OLD/'model/frozen').rglob('*'):
  if p.is_file():assert sha(p)==sha(d/'model/frozen'/p.relative_to(OLD/'model/frozen'))
 for p in(OLD/'qsim').iterdir():
  if(p.suffix in('.c','.h')or p.name=='Makefile')and p.name!='cache.c':assert sha(p)==sha(d/'qsim'/p.name)
 for name in ['scene.csv','geometry.csv','culling.csv',*[f'frame_{i:05}.bin'for i in range(360,368)]]:assert(a/name).read_bytes()==(b/name).read_bytes(),name
 assert'FATAL'not in(b/'run.log').read_text()
 source=json.loads((d/'prepared.json').read_text())
 for n,h in source['source_sha256'].items():assert sha(d/n)==h,n
 invocation=json.loads((b/'inputs.json').read_text());assert invocation['success']
 for n,h in invocation['results_sha256'].items():assert sha(b/n)==h,n
 baseline=json.loads((a/'coupled.json').read_text())['cpu_cycles']/800000;candidate=json.loads((b/'coupled.json').read_text())['cpu_cycles']/800000
 ar=list(csv.DictReader((a/'frames.csv').open()))[360:368];br=list(csv.DictReader((b/'frames.csv').open()))[360:368]
 fields=('est_cycles','icache_miss_sdram','dcache_miss','insns','mispredict');totals={f:{'baseline':sum(int(r[f])for r in ar),'candidate':sum(int(r[f])for r in br)}for f in fields}
 result=dict(passed=True,private_only=True,label=label,baseline_ic32_dc128_ms=baseline,candidate_ms=candidate,extra_ms=candidate-baseline,time_increase_percent=100*(candidate/baseline-1),throughput_change_percent=100*(baseline/candidate-1),all8_actual_color_depth_exact=True,all368_scene_geometry_culling_exact=True,frame_totals=totals,limits=['Only qsim estimatedcachecapacity changes. No CPU RTLgeneration, placement, timing, ALM or actualM10K count claim.','Same realLOD5 sourceclient, actualLOAD/GPU/SDRAM/CRAM and fixed360..367 window. Warmlookup costs included, no source misses in this window.','Synthetic CPUtraffic/cachemodel remains; no physicalPocketFPS or broadworldcache guarantee.'])
 write(d/'result.json',result);write(d/'manifest.json',dict(files={str(p.relative_to(d)):sha(p)for p in d.rglob('*')if p.is_file()and'obj'not in p.parts and'build'not in p.parts},helper_sha256=sha(Path(__file__))))
 print(json.dumps(result,indent=2))
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('action',choices=['prepare','build','run','analyze']);p.add_argument('label',choices=CHOICES);a=p.parse_args();globals()[a.action](a.label)
