#!/usr/bin/env python3
"""Seal negative one-pass indexed preparation screen."""
import csv,json
from pathlib import Path
from phase10_experiment import CAMP,sha,write
P=CAMP/'phase10';O=P/'indexed-direct-v2';a=P/'batch-cursor-v1/capture-baseline';b=O/'capture-candidate'
for n,h in json.loads((O/'source.json').read_text())['source_sha256'].items():assert sha(O/n)==h,n
f=json.loads((O/'functional-result.json').read_text());assert f['passed']
def profile(d,n):return {r['function']:r for r in csv.DictReader((d/n).open(),delimiter='\t')}
r={}
for label,d,names in [('baseline',a,['gfx_cpu_indexed_run','gfx_gpu_vtx_cache_batch']),('direct',b,['gfx_cpu_indexed_run','gfx_gpu_vtx_cache_stream_begin','gfx_gpu_vtx_cache_stream_space','gfx_gpu_vtx_cache_stream_commit'])]:
 old=profile(d,'profile-0360.tsv');new=profile(d,'profile-0720.tsv');rows={}
 for n in names:
  rows[n]={k:int(new[n][k])-int(old.get(n,{}).get(k,0))for k in ['self_insns','self_cycles','icache_miss','dcache_miss','mispredict','calls']}
  rows[n]['self_ms_per_frame']=rows[n]['self_cycles']/36000000
  rows[n]['base_ms_per_frame']=rows[n]['self_insns']/36000000
 r[label]=rows
write(O/'decision.json',dict(selected=False,qualified_for_deployment=False,reason='CPU saving0.108ms gameplay/0.0816ms busywindow is below0.3ms gate; no coupled/LOD5 expansion.',function_profiles_360_to719=r,cpu_only_cost=f['cpu_only_cost'],whole721_records_exact=True,eight_individual_command_MMIO_streams_exact=True,combined_stream_changed_frames=f['combined_interleaving_changed_frames'],limits=['Native-FMA qsim timing model only; indexed GPU commands not rendered by functional model. No actual raster/coupled qualification for this variant.','Publication timing changes. Whole TRI2 guard, original per-corner dirty mask, duplicate rounded payload and fallback-prefix order retained in source.','Baseline capture reused same frozen simulator/native arithmetic and unchangedF11 ELF. Prototype input files, appELF and capture remain frozen.','Preparation-only v1 had malformed preprocessor separator, was never built or measured, and remains preserved.']))
(O/'REPORT.txt').write_text('''One-pass indexed preparation, private negative screen.

The producer validates each whole TRI command, lazily prepares material, then
quantizes dirty slots directly into SDK-owned packet storage. Jobs[64] and
payload[32] are removed. Duplicate dirty corners copy earlier already-rounded
payload words within the same reserved triangle. Original32-triangle kick
cadence and pre-fallback commit are retained. SDK ownership remains in backend.

Fresh native-FMA functional capture passes721 scene/geometry/culling records
and eight individual ordered command/MMIO streams. Publication boundaries
change; frame200 combined interleaving differs. Indexed GPU operations are not
rendered in that simulator, so this is not a pixel-equivalence claim.

Gameplay CPU30.361046->30.253210ms,0.107836ms saved(0.355%).
Busy eight-frame CPU35.746666->35.665066ms,0.081600ms saved(0.228%).
Gameplay instructions594163809->593171956(-0.167%), instruction-cache misses
1170868->967850, branch misses8110862->8231503. Much of the small modeled
saving is instruction-cache behavior, not a large reduction of required work.

The gain is below the0.3ms screen gate. No coupled qualification, LOD5
transplant, additional boundary-test suite, production edit, fit or deployment.
''')
inputs=[O/'source.json',O/'candidate.patch',O/'build-normal.json',O/'app.elf',O/'functional-result.json',a/'inputs.json',b/'inputs.json']
write(O/'manifest.json',dict(inputs={str(p.relative_to(P)):sha(p)for p in inputs},outputs={p.name:sha(p)for p in [O/'decision.json',O/'REPORT.txt']},helpers={n:sha(Path(__file__).parent/n)for n in ['phase10_indexed_direct.py','phase10_indexed_direct_capture.py',Path(__file__).name]}));print(json.dumps(r,indent=2));print('manifest',sha(O/'manifest.json'))
