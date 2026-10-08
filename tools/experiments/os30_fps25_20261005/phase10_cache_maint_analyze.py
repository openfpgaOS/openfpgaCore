#!/usr/bin/env python3
"""Report private passive cache counts and paired GPU busy-drain experiment."""
import hashlib,json,re,csv
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3];P=ROOT/'docs/measurements/os30-fps25-20261005/phase10'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def counters(d):
 t=(d/'run.log').read_text();assert 'FATAL' not in t
 rows=re.findall(r'CACHE_OBS cause (\d+) starts (\d+) cycles (\d+) aw (\d+) words (\d+)',t);assert len(rows)==8
 r=dict(causes={x[0]:dict(zip(['starts','cycles','aw','words'],map(int,x[1:]))) for x in rows})
 x=re.search(r'CACHE_CPU status_reads (\d+) idle_with_lines (\d+)',t);r.update(status_reads=int(x[1]),status_idle_lines=int(x[2]))
 x=re.search(r'CACHE_OBS tail_not_drained (\d+) repeated_gaps (\d+) max_flushes_per_gap (\d+)',t);r.update(zip(['tail_not_drained','repeated_gaps','max_flushes_per_gap'],map(int,x.groups())))
 x=re.search(r'CACHE_OBS requests (\d+) barrier_cycles (\d+) texture_cycles (\d+) hits (\d+) misses (\d+) writebacks (\d+)',t);r.update(zip(['auto_requests','barrier_request_cycles','texture_request_cycles','hits','misses','writebacks'],map(int,x.groups())))
 r['idle_gap_log2_histogram']=[dict(log2_lower=int(a),count=int(b),had_lines=int(c)) for a,b,c in re.findall(r'CACHE_OBS gap_bin (\d+) count (\d+) had_lines (\d+)',t)]
 r['maintenance_ms_per_frame']=sum(x['cycles'] for x in r['causes'].values())/800000
 return r

def main():
 base=P/'gpu-cache-maint-observe-v2';candidate=P/'gpu-idle-drain-coupled-v1';a=base/'run-baseline-8';b=candidate/'run-candidate-8';prior=P/'load-alias-coupled-v1/run-baseline-8'
 for folder in (base,candidate):
  build=json.loads((folder/'model/build.json').read_text());assert build['simulator_sha256']==sha(folder/'model/obj/Vtb_gpu')
 outputs=[]
 for f in sorted(a.glob('frame_*.bin')):
  assert f.read_bytes()==(b/f.name).read_bytes()==(prior/f.name).read_bytes()
  outputs.append(dict(frame=int(f.stem.split('_')[1]),baseline_sha256=sha(f),candidate_sha256=sha(b/f.name)))
 assert len(outputs)==8
 for name in ('coupled.json','events.csv','counters.csv','publishes.csv'):
  assert (a/name).read_bytes()==(prior/name).read_bytes(),name
 assert (a/'publishes.csv').read_bytes()==(b/'publishes.csv').read_bytes()
 x=json.loads((a/'coupled.json').read_text());y=json.loads((b/'coupled.json').read_text());assert x['cpu_cycles']==y['cpu_cycles']
 difference=[]
 for f in sorted((base/'model/frozen').rglob('*')):
  if f.is_file() and sha(f)!=sha(candidate/'model/frozen'/f.relative_to(base/'model/frozen')):difference.append(str(f.relative_to(base/'model/frozen')))
 assert difference==['common/gpu_core.v'],difference
 for f in (base/'qsim').iterdir():
  if f.suffix in ('.c','.h') or f.name=='Makefile':assert sha(f)==sha(candidate/'qsim'/f.name)
 r=dict(passed=True,private_only=True,frames=8,first_frame=360,baseline=counters(a),candidate=counters(b),
  actual_frame_color_depth_exact=True,outputs=outputs,modeled_cpu_ms_per_frame=x['cpu_cycles']/800000,modeled_cpu_cycles_identical=True,
  baseline_passive_observer_no_behavior_change=True,only_selected_hdl_difference=difference,
  selected_candidate_sha256=sha(candidate/'model/frozen/common/gpu_core.v'),
  gpu_candidate_proof_sha256=sha(P/'gpu-idle-drain-v1/prepared.json'),
  limits=['Estimated CPU, actual GPU/cache/SDRAM/CRAM1. No Pocket FPS claim, area/timing fit or deployment.',
  'Cause bits at maintenance entry:1 automatic idle,2 explicit barrier,4 texture flush. Request-held cycles and actual maintenance episodes are separate.',
  'Maintenance cycles use cache.maintenance, including final hold; they are not necessarily on the CPU critical path.',
  'Initial observe-v1 explicit-barrier duration was invalid because its observer waited for a pulse that some barriers do not generate; v2 corrects only the observer.'])
 for folder in (base,candidate):
  with (folder/'result.json').open('x') as f:json.dump(r,f,indent=2);f.write('\n')
 print(json.dumps({k:r[k] for k in ('modeled_cpu_ms_per_frame','modeled_cpu_cycles_identical','baseline','candidate')},indent=2))
if __name__=='__main__':main()
