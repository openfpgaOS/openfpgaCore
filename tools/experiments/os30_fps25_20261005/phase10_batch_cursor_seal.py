#!/usr/bin/env python3
"""Seal bounded local-cursor screen; no production selection."""
import csv,json
from pathlib import Path
from phase10_gpu_critical import P,sha,save
APP=P/'batch-cursor-v1';OUT=P/'batch-cursor-coupled-v1';OLD=P/'gpu-critical-bound-v2/coupled';a=OLD/'run-baseline-8';b=OUT/'run-candidate'
for d in [a,b]:
 inv=json.loads((d/'inputs.json').read_text());assert inv['success'];assert 'FATAL'not in(d/'run.log').read_text()
 for n,h in inv['results_sha256'].items():assert sha(d/n)==h,n
assert sha(OLD/'model/obj/Vtb_gpu')==sha(OUT/'model/obj/Vtb_gpu')
for n,h in json.loads((APP/'source.json').read_text())['source_sha256'].items():assert sha(APP/n)==h,n
for n in ['scene.csv','geometry.csv','culling.csv']:assert(a/n).read_bytes()==(b/n).read_bytes(),n
renders=[]
for f in sorted(a.glob('frame_*.bin')):
 assert f.stat().st_size==307200 and f.read_bytes()==(b/f.name).read_bytes(),f.name
 renders.append(dict(frame=int(f.stem.split('_')[1]),sha256=sha(f),color_exact=True,depth_exact=True))
assert len(renders)==8
x=json.loads((a/'coupled.json').read_text());y=json.loads((b/'coupled.json').read_text())
assert y['frames']==y['presented']==8 and not y['cram1_errors']and not y['memory_errors']and not y['ring_overflow']
ac=list(csv.DictReader((a/'counters.csv').open()))[-1];bc=list(csv.DictReader((b/'counters.csv').open()))[-1]
count={k:dict(baseline=int(ac[k]),candidate=int(bc[k]))for k in ['all_gpu_busy','rdptr_polls','fence_polls','publications','uploaded_words']}
fun=json.loads((APP/'functional-result.json').read_text());assert fun['passed']
result=dict(passed=True,selected=False,qualified_for_deployment=False,baseline_ms=x['cpu_cycles']/800000,candidate_ms=y['cpu_cycles']/800000,saved_ms=(x['cpu_cycles']-y['cpu_cycles'])/800000,time_reduction_percent=100*(1-y['cpu_cycles']/x['cpu_cycles']),counts=count,actual_render_outputs=renders,all368_scene_geometry_culling_records_exact=True,all721_functional_records_exact=True,functional_stream_interleaving_changed_frames=fun['combined_interleaving_changed_frames'],cpu_only_cost=fun['cpu_only_cost'],scope=['Actual GPU/cache/SDRAM/CRAM RTL with estimated CPU/cache and synthetic memory traffic; fixed-step eight-frame window, not hardware FPS.','Functional capture validates 721 records and eight individual command/MMIO streams. Indexed GPU opcodes are not rendered in qsim; real buffer proof covers only the coupled eight frames360..367.','Publication grouping changes; frame200 combined command/MMIO interleaving is unqualified by this eight-frame screen.','Read-only source review found ownership coherent. Boundary/adversarial SDK tests and LOD5 transplant were deliberately not pursued because the gain is only0.291%. No fit, production change or deployment.'])
save(OUT/'result.json',result)
(OUT/'REPORT.txt').write_text('''Backend local packet cursor, private F11 screen.

The display-list/job producer is unchanged. Backend holds a local staging
cursor, checks conservative29-word capacity against both staging and known
ring space, and commits before every reserve/kick/return. Original per-slot
payload, command order and32-triangle kick cadence are retained. Unlike old
batch-reserve-v1, this adds no per-job producer prefix/popcount bookkeeping.

Native-FMA CPU-only gameplay mean:30.361046 ->30.288946ms (0.237% lower).
Matched busy CPU-only eight-frame mean:35.746666 ->35.689680ms (0.159% lower).
Actual coupled mean:37.827574 ->37.717378ms (0.291% lower,0.110196ms saved).
All eight real color/depth buffers and368 scene/geometry/culling records match.
GPU words183171 unchanged; polls86234->83909 and publications578->563.

The source audit passes ownership checks, but this is not selected or production
qualified. Functional frame200 changes command/MMIO interleaving; that frame is
outside the coupled raster window. We did not expand verification or transplant
the change toLOD5 for a marginal gain. Normal retained controls already compile
profiling scopes out; these results are not profiling-instrumentation removal.

CPU/cache timing is modeled, with real GPU/SDRAM/CRAM RTL and synthetic traffic.
This does not predict Pocket FPS or prove every game/scene/SDK build mode.
''')
inputs=[APP/'source.json',APP/'build-normal.json',APP/'app.elf',APP/'candidate.patch',APP/'functional-result.json',APP/'capture-baseline/inputs.json',APP/'capture-candidate/inputs.json',a/'inputs.json',b/'inputs.json',OLD/'inputs.json',OLD/'model/build.json']
inputs=[p for p in inputs if p.exists()]
review=P/'batch-cursor-review-v1/manifest.json'
if review.exists():inputs.append(review)
save(OUT/'manifest.json',dict(inputs={str(p.relative_to(P)):sha(p)for p in inputs},outputs={str(p.relative_to(OUT)):sha(p)for p in OUT.rglob('*')if p.is_file()and'obj'not in p.parts},helpers={n:sha(Path(__file__).parent/n)for n in ['phase10_batch_cursor.py','phase10_batch_cursor_capture.py','phase10_batch_cursor_coupled.py',Path(__file__).name]}))
print(json.dumps({k:result[k]for k in ['baseline_ms','candidate_ms','saved_ms','time_reduction_percent']},indent=2));print('manifest',sha(OUT/'manifest.json'))
