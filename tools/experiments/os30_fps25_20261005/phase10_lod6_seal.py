#!/usr/bin/env python3
"""Seal measured LOD6 lighting overlap and unchanged output, including limits."""
import csv,json
from pathlib import Path
import phase10_lod6_coupled_v2 as m
P=m.P;O=m.OUT;sha=m.sha;write=m.write
r=json.loads((O/'result.json').read_text());assert r['passed']
a=P/'lod5-real-coupled-v2/run-candidate';b=O/'run-window-8'
ops=list(csv.DictReader((b/'geometry-operations.csv').open()))
keys=('start','finish','hardware_finish','first_poll');same_edge=0
for x in ops:
 for k in keys:x[k]=int(x[k])
 if x['hardware_finish']==0:
  # The passive sampler runs before the edge; the immediately following status
  # observation detects DONE on this very edge. There is no intervening edge.
  x['hardware_finish']=x['finish'];same_edge+=1
 assert x['start']<x['hardware_finish']<=x['finish'] and x['start']<x['first_poll']<=x['finish']
metrics={k:sum(f(x)for x in ops)/800000 for k,f in {
 'actual_hardware_start_to_done_ms_per_frame':lambda x:x['hardware_finish']-x['start'],
 'start_to_first_poll_ms_per_frame':lambda x:x['first_poll']-x['start'],
 'hardware_overlap_before_first_poll_ms_per_frame':lambda x:min(x['first_poll'],x['hardware_finish'])-x['start'],
 'hardware_remaining_at_first_poll_ms_per_frame':lambda x:max(0,x['hardware_finish']-x['first_poll']),
 'poll_loop_elapsed_ms_per_frame':lambda x:x['finish']-x['first_poll'],
 'hardware_done_to_observation_ms_per_frame':lambda x:x['finish']-x['hardware_finish']}.items()}
metrics.update(jobs=len(ops),hardware_complete_before_first_poll=sum(x['hardware_finish']<=x['first_poll']for x in ops),completion_on_status_sample_edge=same_edge,scope='Exact RTL completion edges within hybrid model; pre-poll interval includes call/statistics overhead plus CPU lighting. No assumption that all pre-poll cycles are useful lighting.')
write(O/'overlap.json',metrics)
def profile(p):return{r['function']:r for r in csv.DictReader(p.open(),delimiter='\t')}
stages={}
for label,d in[('lod5',a),('lod6',b)]:
 p0=profile(d/'profile-0360.tsv');p1=profile(d/'profile.tsv');stages[label]={}
 for n,r0 in p1.items():
  if any(s in n for s in ('load_alias_light','load_alias_copy','load_alias_vertex','gfx_gpu_cram_vertex_source')):
   row={k:int(r0[k])-int(p0.get(n,{}).get(k,0))for k in ('calls','self_insns','self_cycles','dcache_miss','icache_miss','mispredict')};row['self_ms_per_frame']=row['self_cycles']/800000;stages[label][n]=row
write(O/'load-stage-profile.json',stages)
(O/'REPORT.txt').write_text('''LOD6 transform-only hardware with overlapping original CPU lighting.

Matched real-client warm frames360..367: LOD5 34.73734000ms -> LOD6
36.16691375ms, +1.42957375ms (+4.1154% frame time, -3.9527% throughput).
All eight actual RTL color/depth buffers and368 scene/geometry/culling
records match. This is a negative performance result, not a deployable win.

Overlap is real: hardware LOAD takes2.244850ms/frame, of which2.089350ms
occurs before firstpoll. Only0.155500ms hardware remains at firstpoll;
the complete polling interval is0.160710ms. 2231of2608 jobs finish before
firstpoll. CPU load_alias_light alone costs3.81246375ms/frame, exceeding
the available overlap. Its 11056 D-cache misses account for a large part
of total miss growth47482->62313 over8frames. Hardware geometry-lighting
removal saves area in the separately measured component, but makes the
CPU the limiting producer here. Native-cache/CPU timing remains modeled.

Lit+unlit copying costs1.70023375ms/frame versusLOD5 1.7714025ms; the
copy optimization is too small to offset CPU lighting. The raw source
and result format are unchanged24B/32B, preserving clean attribution.
Pool lookup includes all eight built-in counters. No source uploads occur
in this warmwindow; no coldLOD6 physical window or recycling is measured.

Passive RTL timestamps distinguish actualDONE from observedDONE. Nine
jobs finish on the exact status-sampling edge; their zero pre-edge marker
is normalized to that same observedcycle in overlap.json. No latency is
subtracted from the measured frame result. Complete source bindings and
profiles are preserved. Functional five-mode721-record/eight-stream proof
is separate and must not be interpreted as721 renderedframes.

No production files, Pocket deployment, CPU regeneration or Quartusfit.
''')
write(O/'manifest.json',dict(files={str(p.relative_to(O)):sha(p)for p in O.rglob('*')if p.is_file()and'obj'not in p.parts and'build'not in p.parts and p.name!='manifest.json'},helpers={Path(m.__file__).name:sha(Path(m.__file__)),Path(__file__).name:sha(Path(__file__))},functional_manifest_sha256=sha(P/'lod6-functional-v1/manifest.json'),hardware_sources_sha256=sha(P/'lod6-components-v1/sources.json'),qualified_for_deployment=False))
print(json.dumps(metrics,indent=2));print('manifest',sha(O/'manifest.json'))
