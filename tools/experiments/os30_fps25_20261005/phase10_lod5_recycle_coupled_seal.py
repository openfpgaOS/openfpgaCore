#!/usr/bin/env python3
"""Seal unchanged-model recycler app paired warm/cold qualification."""
import csv,json
from pathlib import Path
import phase10_lod5_recycle_coupled as m

def main():
 O=m.OUT;sha=m.sha;write=m.write
 prep=json.loads((O/'prepared.json').read_text())
 for n,h in prep['source_sha256'].items():assert sha(O/n)==h,n
 assert sha(O/'model/obj/Vtb_gpu')==sha(m.OLD/'model/obj/Vtb_gpu')==prep['simulator_sha256']
 for p,h in m.PROOFS.items():assert sha(p)==h,p
 for p in (m.OLD/'model/frozen').rglob('*'):
  if p.is_file():assert sha(p)==sha(O/'model/frozen'/p.relative_to(m.OLD/'model/frozen')),p
 results={}
 for window,first in [('warm',360),('cold',234)]:
  a=m.OLD/f'run-{window}';b=O/f'run-{window}'
  for d,elfsha in [(a,prep['baseline_elf_sha256']),(b,prep['elf_sha256'])]:
   inv=json.loads((d/'inputs.json').read_text());assert inv['success'];assert 'FATAL'not in(d/'run.log').read_text()
   assert inv['start']==first and inv['frames']==8 and inv['traffic']==1
   for n,h in inv['results_sha256'].items():assert sha(d/n)==h,(d,n)
   assert sha(d/'sm64.elf')==elfsha
  histories={}
  for n in ('scene.csv','geometry.csv','culling.csv'):
   assert(a/n).read_bytes()==(b/n).read_bytes(),(window,n)
   histories[n]=dict(sha256=sha(a/n),records=len(list(csv.DictReader((a/n).open()))))
  renders=[]
  for i in range(first,first+8):
   n=f'frame_{i:05}.bin';assert(a/n).read_bytes()==(b/n).read_bytes(),n
   renders.append(dict(frame=i,sha256=sha(a/n),bytes=(a/n).stat().st_size))
  timing={};jobs={}
  for name,d in [('original',a),('recycler',b)]:
   c=json.loads((d/'coupled.json').read_text())
   assert c['presented']==c['frames']==8 and not c['memory_errors']and not c['cram1_errors']and not c['ring_overflow']
   rows=list(csv.DictReader((d/'geometry-operations.csv').open()));jobs[name]=rows
   cnt=list(csv.DictReader((d/'counters.csv').open()))[-1]
   timing[name]=dict(frame_ms=c['cpu_cycles']/800000,
    hardware_load_ms=sum(int(r['hardware_finish'])-int(r['start'])for r in rows)/800000,
    observed_load_ms=sum(int(r['cycles'])for r in rows)/800000,load_jobs=len(rows),
    loaded_vertices=sum(int(r['count'])for r in rows),texture_upload_words=c['texture_uploads'],
    gpu_command_words=int(cnt['uploaded_words']),gpu_busy_ms=int(cnt['all_gpu_busy'])/800000,
    rdptr_polls=int(cnt['rdptr_polls']),publications=int(cnt['publications']))
  keys=('frame','operation','status','count','lit')
  assert [[r[k]for k in keys]for r in jobs['original']]==[[r[k]for k in keys]for r in jobs['recycler']]
  assert timing['original']['gpu_command_words']==timing['recycler']['gpu_command_words']
  assert timing['original']['texture_upload_words']==timing['recycler']['texture_upload_words']
  x=timing['original']['frame_ms'];y=timing['recycler']['frame_ms']
  results[window]=dict(actual_color_depth_exact=True,all_histories_exact=True,histories=histories,
   renders=renders,load_sequence_count_lighting_status_exact=True,timing=timing,
   delta_frame_ms=y-x,frame_time_change_percent=(y/x-1)*100)
 layout=json.loads((O/'elf-layout.json').read_text())
 stats=list(csv.DictReader((m.FUNCTIONAL/'pool-stats-normal.csv').open()))
 assert all(int(r['recycles'])==0 for r in stats)
 preserved={mode:sha(m.FUNCTIONAL/f'result-{mode}.json')for mode in ('normal','faults','absent','timeout','fullpool')}
 write(O/'result.json',dict(passed=True,app_qualification_only=True,model_rtl_changed=False,
  same_simulator_binary=True,results=results,elf_layout=layout,
  preserved_functional_result_sha256=preserved,normal_functional_recycles=0,
  limits=['Actual GPU/cache/LOAD/SDRAM/full16MiB CRAM RTL, estimated CPU/cache timing and synthetic memory traffic. Not Pocket FPS.',
   'Hardware remains the frozen cache-v2 model using unified-v1 LOAD and span-v3 GPU. Compact-v3 hardware requires its separately sealed bit/cycle equivalence bridges and fit.',
   'These normal warm/cold windows exercise no capacity recycle. Existing five-mode functional capture and independent full-arena/wrap/ASan tests preserve recycling correctness evidence.',
   'No source, renderer option, SD card or production artifact changed. Reviewed recycler ELF is used unchanged.']))
 report='Reviewed LOD5 source-pool recycler, matched actual warm/cold raster qualification.\n\n'
 for w,r in results.items():
  a=r['timing']['original'];b=r['timing']['recycler']
  report+=f"{w}: {a['frame_ms']:.8f} -> {b['frame_ms']:.8f} ms/frame; +{r['delta_frame_ms']:.8f} ms ({r['frame_time_change_percent']:+.4f}% frame time). All8 actual color/depth buffers and complete scene/geometry/cull histories match.\n"
 report+='''
The exact same simulator executable is reused. Renderer options, workload,
GPU/cache/LOAD/SDRAM/16MiB CRAM sources and parameters remain unchanged.
CPU/cache timings are modeled; these numbers are not a hardware FPS result.

ELF layout: +128 text bytes, +12 data bytes, +8 BSS bytes. LOAD/copy/indexed
entrypoints and RSP/source-table addresses remain fixed. Projection and pool
lookup helpers move64 bytes; direct/generic triangle backends move128 bytes.
Warm incremental profile totals equal the159319-cycle eight-frame increase:
+95206 I-cache penalty cycles, +40296 mispredict cycles and +43080 issued
instructions, offset by other decreases. Thus the0.199149ms/frame change
includes modeled layout/cache effects, not solely the new frame counter.

Unchanged reviewed source-pool policy: on capacity miss, first complete pool
recycle is immediate; subsequent recycles require120 rendered frames with
unsigned wrap-safe age. Hits remain available during cooldown; misses fall
back; bounded probe exhaustion does not recycle. The synchronous LOAD/upload
and copied-result ownership proof is preserved in the prior review manifest.
Normal721-frame functional capture performs no recycle. The prior forced
capacity capture reaches three generations, alongside normal/fault/absent/
drained-timeout exact stream proofs and independent actual-C full256KiB,
cooldown, multigeneration, wrap and collision tests. Those sealed files were
not overwritten. This paired run adds16 actual raster-buffer comparisons;
it does not turn the721-frame command-stream proof into721 raster renders.

No software tuning, source changes, core build, device deployment or SD write.
Any compact-v3 hardware claim depends separately on its equivalence bridges,
physical resource fit and timing evidence.
'''
 with (O/'REPORT.txt').open('x')as f:f.write(report)
 files={str(p.relative_to(O)):sha(p)for p in O.rglob('*')if p.is_file()and'obj'not in p.parts and'build'not in p.parts}
 files['model/obj/Vtb_gpu']=sha(O/'model/obj/Vtb_gpu')
 write(O/'manifest.json',dict(passed=True,deployment_ready=False,files=files,
  helpers={Path(m.__file__).name:sha(Path(m.__file__)),Path(__file__).name:sha(Path(__file__))},
  preserved_proofs={str(p.relative_to(m.ROOT)):h for p,h in m.PROOFS.items()}))
 print(json.dumps({w:{k:r[k]for k in ['delta_frame_ms','frame_time_change_percent']}for w,r in results.items()},indent=2))
 print('manifest',sha(O/'manifest.json'))

if __name__=='__main__':main()
