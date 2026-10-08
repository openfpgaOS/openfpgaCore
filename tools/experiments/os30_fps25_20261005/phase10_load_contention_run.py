#!/usr/bin/env python3
"""Run one independently verified native LOAD set alongside a real GPU frame."""
import pathlib,subprocess,concurrent.futures,json,re,hashlib,sys
ROOT=pathlib.Path(__file__).resolve().parents[3];P=ROOT/'docs/measurements/os30-fps25-20261005/phase10'
p=P/(sys.argv[1] if len(sys.argv)>1 else 'load-memory-contention-v3');v=P/'vcache-narrow-v1/replays/frame_00360.vec'; jobs=[]
for threshold in [12,4]:
 for traffic in range(3):
  for mode in ['quiet','baseline','overlap']:
   prefix=f'priority{threshold}-{mode}-{traffic}'; binary=p/('obj' if threshold==12 else 'obj-priority4')/'Vtb_gpu'
   args=['stdbuf','-oL',str(binary),f'+memory_traffic={traffic}',f'+load_mode={dict(quiet=2,baseline=0,overlap=1)[mode]}',f'+load_start={0 if mode=='quiet' else 150000}','+fast_tex=1','+tagged_tex=1',str(p/'quiet.vec') if mode=='quiet' else str(v),str(p/f'{prefix}.bin')]
   jobs.append((mode,traffic,threshold,prefix,args))
def run(j):
 mode,traffic,threshold,prefix,args=j
 with (p/f'{prefix}.log').open('w') as o,(p/f'{prefix}.stderr').open('w') as e:r=subprocess.run(args,stdout=o,stderr=e)
 txt=(p/f'{prefix}.log').read_text();stats={k:int(v) for k,v in re.findall(r'\b(\w+)=(\d+)', '\n'.join(l for l in txt.splitlines() if l.startswith(('LOAD ','RESULT '))))}
 jobstats=[{k:int(v) for k,v in re.findall(r'\b(\w+)=(\d+)',line)} for line in txt.splitlines() if line.startswith('LOAD_JOB ')]
 return dict(mode=mode,traffic=traffic,threshold=threshold,returncode=r.returncode,command=args,stats=stats,jobstats=jobstats)
with concurrent.futures.ThreadPoolExecutor(max_workers=3) as e:results=list(e.map(run,jobs))
(p/'runs.json').write_text(json.dumps(results,indent=2)+'\n')
for x in results:
 s=x['stats']; print(x['threshold'],x['traffic'],x['mode'],x['returncode'],{k:s.get(k) for k in ['cycles','job_cycles_sum','job_cycles_max','load_read_queue_p95','load_write_queue_p95']})
comparisons=[]
for threshold in [12,4]:
 for traffic in range(3):
  a=(p/f'priority{threshold}-baseline-{traffic}.bin').read_bytes();b=(p/f'priority{threshold}-overlap-{traffic}.bin').read_bytes()
  base=next(x for x in results if (x['mode'],x['traffic'],x['threshold'])==('baseline',traffic,threshold));run=next(x for x in results if (x['mode'],x['traffic'],x['threshold'])==('overlap',traffic,threshold));quiet=next(x for x in results if (x['mode'],x['traffic'],x['threshold'])==('quiet',traffic,threshold))
  s=run['stats'];q=quiet['stats'];c=dict(threshold=threshold,traffic=traffic,color_depth_fence_normalized_hash_exact=a==b,output_bytes=len(a),gpu_baseline_cycles=base['stats']['cycles'],gpu_overlap_cycles=s['cycles'],load_idle_cycles=q['job_cycles_sum'],load_busy_cycles=s['job_cycles_sum'],load_busy_withconfig=s['job_with_config_sum'],vertices=s['vertices'],native_independent_rgb=s['independent_rgb'],read_ar_wait_p95=s['load_read_queue_p95'],write_aw_wait_p95=s['load_write_queue_p95'],gpu_busy_during_job_percent=sum(j['gpu_busy'] for j in run['jobstats'])/s['job_with_config_sum']*100)
  c['gpu_increase_cycles']=c['gpu_overlap_cycles']-c['gpu_baseline_cycles'];c['gpu_increase_percent']=100*c['gpu_increase_cycles']/c['gpu_baseline_cycles'];c['busy_cycles_per_vertex']=c['load_busy_cycles']/c['vertices'];comparisons.append(c)
  print(c)
(p/'result.json').write_text(json.dumps({'passed':all(x['returncode']==0 for x in results) and all(c['color_depth_fence_normalized_hash_exact'] for c in comparisons),'scope':'Injected20nativejobs230vertices alongside frame360 at150kcycles; directregister driver, excludes CPU MMIO/cache/fallback overhead; no applicationFPS claim. EntireSDRAMhash normalizes private source/result storage only, GPUaccess there is an assertion failure.','comparisons':comparisons,'binary_sha256':{k:hashlib.sha256((p/k/'Vtb_gpu').read_bytes()).hexdigest() for k in ['obj','obj-priority4']}},indent=2)+'\n')
