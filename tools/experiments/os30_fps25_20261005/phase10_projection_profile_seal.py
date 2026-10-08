#!/usr/bin/env python3
"""Seal an unchanged-ELF projection/transport feasibility bound."""
import csv,json
from pathlib import Path
import phase10_projection_profile as m
O=m.OUT;P=m.P;sha=m.sha;write=m.write;a=m.OLD/'run-warm';b=O/'run-warm'
for n in ('coupled.json','geometry-operations.csv','scene.csv','geometry.csv','culling.csv','frames.csv',*[f'frame_{i:05}.bin'for i in range(360,368)]):assert(a/n).read_bytes()==(b/n).read_bytes(),n
for n,h in json.loads((O/'prepared.json').read_text())['sources'].items():assert sha(O/n)==h,n
inv=json.loads((b/'inputs.json').read_text());assert inv['success']and'FATAL'not in(b/'run.log').read_text()
for n,h in inv['results_sha256'].items():assert sha(b/n)==h,n
rows={r['caller']:{k:int(v)for k,v in r.items()if k!='caller'}for r in csv.DictReader((b/'projection-cost.csv').open())}
for r in rows.values():
 assert r['cyc']==r['count']+sum(v for k,v in r.items()if k.startswith('pen_'))
 r['self_ms_per_frame']=r['cyc']/800000;r['base_ms_per_frame']=r['count']/800000
old={r['function']:r for r in csv.DictReader((b/'profile-0360.tsv').open(),delimiter='\t')};new={r['function']:r for r in csv.DictReader((b/'profile.tsv').open(),delimiter='\t')};name='gpu_project_clip'
assert sum(r['cyc']for r in rows.values())==int(new[name]['self_cycles'])-int(old[name]['self_cycles'])
assert sum(r['calls']for r in rows.values())==int(new[name]['calls'])-int(old[name]['calls'])
s=rows['simple'];assert s['calls']%3==0;tri=s['calls']/24;extra=14*tri;upload_min=extra*7/100000;staging_min=extra/100000;quant_min=s['calls']/8*9/100000
old_review=P.parents[1]/'os30-simple-20261005/independent-review/transient-clipped-slots-review.json'
result=dict(passed=True,selected_for_implementation=False,passive_cycle_and_actual_render_identical=True,callers=rows,simple_triangles_per_frame=tri,legacy_words_per_triangle=18,explicit_zi_cached_words_per_triangle=32,extra_words_per_frame=extra,current_words_per_frame=183171/8,extra_upload_minimum_ms=upload_min,extra_staging_store_minimum_ms=staging_min,retained_three_multiply_three_convert_minimum_ms=quant_min,projection_only_optimistic_remaining_ms=s['self_ms_per_frame']-upload_min-staging_min-quant_min,limits=['This is a projection-only budget, not an implemented candidate or whole-frame performance prediction. Caller setup/local-array changes outside the measured projection helper are not included.','Extra upload floor uses retained CPU-ring implementation: one staging load, one MMIOstore and five modeled IO penaltycycles per extra word. Actual branch/cache/reservation/ownership costs can be higher.','Three clip-coordinate multiply+FCVT pairs must remain host work for existing integerprojector: six issuecycles plus three modeledFCVTextra cycles/vertex minimum, excluding dependencies/constantloads/packing.','Added GPU projection, cache-write/read, temporary-slot invalidation and future resident reload costs are not discounted from the optimistic remaining budget.','Prior transient-slot idea was unimplemented/source-rejected for different interpolationzi; explicitzi is distinct and resolves that specific obstacle only.','No new app, command format, RTL, fit, hardware candidate or deployment.'])
write(O/'result.json',result)
(O/'REPORT.txt').write_text('''Simple-material fallback projection bound, actual fixed frames360..367.

Passive caller-tagged accounting leaves all cycles, records and eight actual
color/depth buffers unchanged. Simple direct records call gpu_project_clip
5196 times (216.5triangles/frame):0.8180775ms self. Generic fallback2427calls:
0.3846375ms. All7623calls/962172cycles reconcile to the existing functionprofile.

Three0x56loads extended by one explicit interpolationzi word plus0x54 draw
would be32words/triangle versus18for existing0x4E. Additional3031words/frame
are13.24% of current22896.375words. Existing CPU-ring upload needs at least
0.21217ms additional loads/MMIO stores; extra staging stores0.03031ms. Six
retained clip-coordinate FMUL/FCVT instructions plusFCVT penalties pervertex
need at least0.058455ms. Projection-only optimistic remainder is0.5171425ms,
before additionalGPUprojection/cachework, pointer/reservecost and slotreloads.
Caller setup/local-array savings are outside this budget, so it is not a strict
upper bound on an entirely redesigned producer. No actual candidate is claimed.

The old transient-slot rejection did not test explicitzi; it rejected current
GPU-derivedzi changing texture interpolation. Explicitzi is a distinct idea,
but its32-word form leaves little netheadroom. Parent selected no hardware
prototype now. A compact whole-triangle command could reduce transport, but
requires separate design/proof and was not implemented or measured here.

ModeledCPU/cache/IObounds, actual unchangedGPU/LOAD/memoryRTL, fixed game step;
no physicalFPS claim. No production change or deployment.
''')
write(O/'manifest.json',dict(files={str(p.relative_to(O)):sha(p)for p in O.rglob('*')if p.is_file()and'obj'not in p.parts and'build'not in p.parts and p.name!='manifest.json'},helpers={Path(m.__file__).name:sha(Path(m.__file__)),Path(__file__).name:sha(Path(__file__))},old_transient_review_sha256=sha(old_review),qualified_for_deployment=False));print('manifest',sha(O/'manifest.json'))
