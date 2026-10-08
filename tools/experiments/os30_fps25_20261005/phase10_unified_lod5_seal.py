#!/usr/bin/env python3
"""Seal unchanged-client unified scheduler warm/cold full-system comparison."""
import csv,collections,json
from pathlib import Path
import phase10_unified_lod5_coupled as m
P=m.P;O=m.OUT;sha=m.sha;write=m.write
for label in ('baseline','unified'):
 d=O/label;prep=json.loads((d/'prepared.json').read_text())
 for n,h in prep['source_sha256'].items():assert sha(d/n)==h,n
 assert sha(d/'model/obj/Vtb_gpu')==json.loads((d/'model/build.json').read_text())['simulator_sha256']
for p in(O/'baseline/model/frozen').rglob('*'):
 if p.is_file()and p.name!='geometry_transform_light.v':assert sha(p)==sha(O/'unified/model/frozen'/p.relative_to(O/'baseline/model/frozen'))
for p in(O/'baseline/qsim').iterdir():
 if p.suffix in('.c','.h')or p.name=='Makefile':assert sha(p)==sha(O/'unified/qsim'/p.name)
results={}
for window,first,oldname in [('warm',360,'candidate'),('cold',234,'candidate-cold')]:
 a=O/f'baseline/run-{window}';b=O/f'unified/run-{window}';old=m.OLD/f'run-{oldname}'
 for d in (a,b):
  inv=json.loads((d/'inputs.json').read_text());assert inv['success'];assert'FATAL'not in(d/'run.log').read_text()
  for n,h in inv['results_sha256'].items():assert sha(d/n)==h,n
 for n in ('coupled.json','scene.csv','geometry.csv','culling.csv','frames.csv',*[f'frame_{i:05}.bin'for i in range(first,first+8)]):assert(old/n).read_bytes()==(a/n).read_bytes(),('baseline_changed',n)
 for n in ('scene.csv','geometry.csv','culling.csv',*[f'frame_{i:05}.bin'for i in range(first,first+8)]):assert(a/n).read_bytes()==(b/n).read_bytes(),n
 x=list(csv.DictReader((a/'geometry-operations.csv').open()));y=list(csv.DictReader((b/'geometry-operations.csv').open()));assert len(x)==len(y)
 hist=collections.Counter()
 for l,r in zip(x,y):
  for k in ('frame','count','lit','status'):assert l[k]==r[k],k
  hist[(int(r['hardware_finish'])-int(r['start']))-(int(l['hardware_finish'])-int(l['start']))]+=1
 timing={}
 for label,d,rows in [('baseline',a,x),('unified',b,y)]:
  timing[label]=dict(frame_ms=json.loads((d/'coupled.json').read_text())['cpu_cycles']/800000,hardware_load_ms_per_frame=sum(int(r['hardware_finish'])-int(r['start'])for r in rows)/800000,observed_load_ms_per_frame=sum(int(r['cycles'])for r in rows)/800000,source_ar_wait_cycles=json.loads((d/'geometry.json').read_text())['ar_wait'],source_response_cycles=json.loads((d/'cram-occupancy.json').read_text())['gd_response_cycles'])
 results[window]=dict(first=first,last=first+7,all8_actual_color_depth_exact=True,all_prior_scene_geometry_culling_exact=True,baseline_cycle_and_output_identical_to_retained_lod5=True,timing=timing,frame_delta_ms=timing['unified']['frame_ms']-timing['baseline']['frame_ms'],load_jobs=len(x),hardware_delta_cycles_histogram=dict(sorted(hist.items())),per_job_context_count_light_status_exact=True)
write(O/'result.json',dict(passed=True,results=results,limits=['RetainedCPU/cache time modeled, hardwareLOAD/GPU/SDRAM/CRAM actualRTL with synthetic backgroundtraffic. No physicalFPS or nativewholeCPU timing claim.','Actual hardwarecompletion recorded independently from statuspoll observation. Perjobdifferences beyond2..3 reflect changed source arbitration phase; independent component fixture proves intrinsic2..3cycle overhead.','LOD5app, sourceupload policy, sourcebytes, full16MiB memory, GPUspanv3 and allotherRTL unchanged. No recycling inthiscomparison.','Wholeframe timing is effectively flat; no performanceimprovement claimed. Resourcegain is separate map/fit evidence.'],qualified_for_deployment=False))
(O/'REPORT.txt').write_text('''Unified transform+hardware lighting scheduler, unchanged realLOD5 client.

Warm360..367:34.73734000->34.73315625ms (-0.00418375ms).
Cold234..241:48.96838500->48.96784875ms (-0.00053625ms).
All16 actualRTL color/depth buffers and respective368/242 scene/geometry/
culling records match. The instrumented oldcontrol is cycle/output identical
to the sealed originalLOD5. Performance is effectively unchanged.

Warmhardware LOAD durations2.27177750->2.28201125ms/frame; observedstatuswait
2.30347750->2.30851750ms.2266of2608jobs addexactly2or3hardwarecycles;
remainingjobs changeCRAM arbitrationphase. Source responsecycles unchanged,
sourceARwait increases1475cycles over8frames. Independentnative/component
proof establishes intrinsic2..3cycle overhead across128count/light cases.
Coldhardware2.367555->2.37813875ms; observed2.400800->2.405735ms.

Only geometry_transform_light.v differs between models. Same application,
lookup/counters, physical16MiB CRAM, uploads, SDRAM, GPUspanv3 andcachemodel.
This is a hardware-area opportunity with retainedperformance, nota newFPS
claim. CPU andbackgroundtraffic remainmodeled. No deployment or FPGAfit.
''')
write(O/'manifest.json',dict(files={str(p.relative_to(O)):sha(p)for p in O.rglob('*')if p.is_file()and'obj'not in p.parts and'build'not in p.parts and p.name!='manifest.json'},helpers={Path(m.__file__).name:sha(Path(m.__file__)),Path(__file__).name:sha(Path(__file__))},component_manifest_sha256='774fe5a9659d7420287d410946cec930f5e079d0d6d0730b2666d13a1be4e958',qualified_for_deployment=False))
print('manifest',sha(O/'manifest.json'))
