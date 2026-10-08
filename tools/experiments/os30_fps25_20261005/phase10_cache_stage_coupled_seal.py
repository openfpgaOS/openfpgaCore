#!/usr/bin/env python3
"""Bind registered-cache lookup to matched unchanged LOD5 coupled frames."""
import csv,json,re
from pathlib import Path
import phase10_cache_stage_coupled as m
P=m.P;O=m.OUT;OLD=m.OLD;sha=m.sha;write=m.write
component=m.COMP/'manifest.json'
assert sha(component)=='780ee94fe56925552cfda7cedd3b953787ebc1982efd1532cc842542b4d9e70b'
prep=json.loads((O/'prepared.json').read_text())
for n,h in prep['source_sha256'].items():assert sha(O/n)==h,n
assert sha(O/'model/obj/Vtb_gpu')==json.loads((O/'model/build.json').read_text())['simulator_sha256']
changed=[]
for p in(OLD/'model/frozen').rglob('*'):
 if p.is_file()and sha(p)!=sha(O/'model/frozen'/p.relative_to(OLD/'model/frozen')):changed.append(str(p.relative_to(OLD/'model/frozen')))
assert sorted(changed)==['common/gpu_color_depth_cache.sv','tb_gpu.v'],changed
for p in(OLD/'qsim').iterdir():
 if p.suffix in('.c','.h')or p.name=='Makefile':assert sha(p)==sha(O/'qsim'/p.name),p.name
results={}
for window,first in [('warm',360),('cold',234)]:
 a=OLD/f'run-{window}';b=O/f'run-{window}'
 for d in (a,b):
  inv=json.loads((d/'inputs.json').read_text());assert inv['success'];assert'FATAL'not in(d/'run.log').read_text()
  for n,h in inv['results_sha256'].items():assert sha(d/n)==h,n
  assert inv['sha256'][str(d/'sm64.elf')]==prep['elf_sha256']
 for n in ('scene.csv','geometry.csv','culling.csv'):assert(a/n).read_bytes()==(b/n).read_bytes(),n
 renders=[]
 for i in range(first,first+8):
  n=f'frame_{i:05}.bin';assert(a/n).read_bytes()==(b/n).read_bytes(),n;renders.append(dict(frame=i,sha256=sha(a/n),color_exact=True,depth_exact=True))
 t={}
 for label,d in [('baseline',a),('registered_read',b)]:
  coupled=json.loads((d/'coupled.json').read_text());assert coupled['presented']==coupled['frames']==8 and not coupled['memory_errors']and not coupled['cram1_errors']and not coupled['ring_overflow']
  rows=list(csv.DictReader((d/'geometry-operations.csv').open()));c=list(csv.DictReader((d/'counters.csv').open()))[-1]
  ars=int(re.search(r'BN mem zfills \d+ rd_ar (\d+)',(d/'run.log').read_text()).group(1))
  t[label]=dict(frame_ms=coupled['cpu_cycles']/800000,hardware_load_ms=sum(int(r['hardware_finish'])-int(r['start'])for r in rows)/800000,observed_load_ms=sum(int(r['cycles'])for r in rows)/800000,cache_side_AR_accepted=ars,gpu_busy_ms=int(c['all_gpu_busy'])/800000,rdptr_polls=int(c['rdptr_polls']),publications=int(c['publications']))
 results[window]=dict(all8_actual_color_depth_exact=True,all_prior_scene_geometry_culling_exact=True,renders=renders,timing=t,frame_delta_ms=t['registered_read']['frame_ms']-t['baseline']['frame_ms'],frame_change_percent=100*(t['registered_read']['frame_ms']/t['baseline']['frame_ms']-1))
write(O/'result.json',dict(passed=True,results=results,cache_source_sha256=prep['cache_source_sha256'],parameter=prep['parameter'],qualified_for_deployment=False,limits=['Actual GPU/cache/LOAD/SDRAM/CRAM RTL; CPU/cache timings and background memory traffic are modeled. Fixed game step, not Pocket FPS.','Only cache module and REGISTER_READ_LOOKUP=1 instance parameter differ. Original LOD5 app, source-pool policy, unified-v1 geometry module and span-v3 GPU remain unchanged.','The additional state is for timing closure, not claimed performance optimization. Independent protocol proof is bound separately; FPGA fit/timing remains required.','Unified-v2 matrix/inverse RAM-fold equivalence and any newer GPU ownership implementation require their separate evidence bridges; this experiment uses unified-v1 and span-v3.']))
report='Registered render-cache read lookup, unchanged real LOD5 client.\n\n'
for window,r in results.items():
 a=r['timing']['baseline'];b=r['timing']['registered_read'];report+=f"{window}: {a['frame_ms']:.8f} -> {b['frame_ms']:.8f} ms/frame ({r['frame_change_percent']:+.4f}%). All eight actual color/depth buffers and prior scene/geometry/culling records match.\n"
report+='\nExact original app and worker/GPU model retained. Cache c876134ee6f008ef768b27bf68fb886522746f3e9847178f862b8c2adfe8b0fa enables REGISTER_READ_LOOKUP=1; AW/W unchanged. The stage targets FPGA timing closure. CPU timing remains modeled; no physical FPS, fit, or deployment claim.\n'
(O/'REPORT.txt').write_text(report)
write(O/'manifest.json',dict(files={str(p.relative_to(O)):sha(p)for p in O.rglob('*')if p.is_file()and'obj'not in p.parts and'build'not in p.parts and p.name!='manifest.json'},helpers={Path(m.__file__).name:sha(Path(m.__file__)),Path(__file__).name:sha(Path(__file__))},component_prepared_sha256=sha(m.COMP/'prepared.json'),component_protocol_manifest_sha256=sha(component),qualified_for_deployment=False))
print(json.dumps({k:{n:v[n]for n in ['frame_delta_ms','frame_change_percent']}for k,v in results.items()},indent=2));print('manifest',sha(O/'manifest.json'))
