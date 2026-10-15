#!/usr/bin/env python3
from pathlib import Path
import hashlib,json
R=Path(__file__).resolve().parent;P=R.parent
def sha(p):
 with p.open('rb')as f:return hashlib.file_digest(f,'sha256').hexdigest()
def get(p):return json.loads(p.read_text())
base=get(P/'baseline-source.json')['sha256']
for n,h in base.items():assert sha(P/'baseline'/n)==h,n
world=get(P/'world/evidence/source-build-v1.json');alias=get(P/'alias/evidence/build-v2.json')
source={}
for label,folder,overrides,elf,h in [('baseline','baseline',{},R/'baseline-obj/app.elf',get(R/'baseline-build-result.json')['sha256']),('world-v1','world',world['files'],Path(world['elf']),world['elf_sha256']),('alias-v2','alias',alias['source_sha256']|alias['test_sha256'],Path(alias['elf']),alias['elf_sha256'])]:
 pins=base|overrides
 for n,d in pins.items():assert sha(P/folder/n)==d,(label,n)
 assert sha(elf)==h
 assert sha(R/'elf'/(label+'.elf'))==h
 source[label]=dict(passed=True,files=len(pins),elf_sha256=h,overrides=overrides)
for n,h in get(R/'prepared.json')['pins'].items():assert sha(R/n)==h,n
for n,h in get(R/'raster/prepared.json')['pins'].items():assert sha(R/'raster'/n)==h,n
rows=[]
for candidate in('world-v1','alias-v2'):
 for x in get(R/'comparisons'/(candidate+'-vs-baseline.json')):
  w=x['workload'];a=get(R/'runs/baseline'/w/'full/summary.json')['gpu_sync'];b=get(R/'runs'/candidate/w/'full/summary.json')['gpu_sync']
  rows.append(dict(candidate=candidate,workload=w,mode='worker',baseline_cycles=x['baseline_cycles_per_frame'],candidate_cycles=x['candidate_cycles_per_frame'],change_percent=x['change_percent'],original_strict_passed=x['passed'],all_commands_exact=x['all_commands_order_and_frame_assignments_including_tail_exact'],cpu_frames_exact=x['complete_RGB565_all_frames_exact'],sound_calls_exact=x['sound_service_counts_exact'],baseline_publications=a['publishes'],candidate_publications=b['publishes'],baseline_readptr_observations=a['observations']['rdptr_read'],candidate_readptr_observations=b['observations']['rdptr_read'],baseline_words=a['published_words'],candidate_words=b['published_words']))
  assert x['all_commands_order_and_frame_assignments_including_tail_exact'] and x['complete_RGB565_all_frames_exact'] and x['sound_service_counts_exact']
 assert get(R/'comparisons'/(candidate+'-vs-baseline-batch-semantics.json'))['passed']
 assert get(R/'raster'/('baseline-vs-'+candidate+'.json'))['passed']
 for mode in('cpu','inline'):
  x=get(R/'comparisons'/(candidate+'-'+mode+'-vs-baseline-'+mode+'.json'))[0];assert x['passed']
  rows.append(dict(candidate=candidate,workload='main',mode=mode,baseline_cycles=x['baseline_cycles_per_frame'],candidate_cycles=x['candidate_cycles_per_frame'],change_percent=x['change_percent'],original_strict_passed=True))
receipt_paths=[R/'prepared.json',R/'raster/prepared.json',R/'baseline-command.json',R/'baseline-build-result.json',R/'baseline-tests.json',P/'baseline-source.json',P/'world/evidence/source-build-v1.json',P/'alias/evidence/build-v2.json']
receipt_paths+=sorted((R/'comparisons').glob('*.json'))
receipt_paths+=sorted((R/'raster').glob('baseline-vs-*.json'))
for label in('baseline','world-v1','alias-v2'):
 receipt_paths += [R/'raster/captures'/label/'cpu-crosscheck.json',R/'raster/captures'/label/'command.json',R/'raster/captures'/label/'result.json',R/'raster/replays'/label/'result.json']
for label in('baseline','world-v1','alias-v2','baseline-cpu','baseline-inline','world-v1-cpu','world-v1-inline','alias-v2-cpu','alias-v2-inline'):
 for w in('main','sound','live'):
  for n in('result.json','full/summary.json','full-command.json','startup-command.json'):
   p=R/'runs'/label/w/n
   if p.exists():receipt_paths.append(p)
result=dict(passed=True,outcome='REJECT_BOTH_NO_MEANINGFUL_GAIN',source_pins=source,measurements=rows,receipt_sha256={str(p.relative_to(P)):sha(p)for p in receipt_paths},recipe_sha256={p.name:sha(p)for p in(R/'prepare.py',R/'bench.py',R/'raster.py',R/'batch_semantics.py',Path(__file__))},diagnosis='SDK already caches known ring free space and stages large batches. These candidates amortize per-call admission bookkeeping but leave all GPU payload words and triangles intact. Alias actual publication counts stay exact; world publication counts change by only +32/+16/-9 over whole workloads.',strict_gate_note='World worker original strict bench is failed only because gpu_barriers observation counts differ in 105/40/43 frames. Original receipts retained. Separate bounded semantic gate proves identical fence commands/observations, all other frame fields, full command/framebuffer/sound outputs, zero hazards/overflow/queued work and full selected-RTL color/depth samples. All alias strict receipts and all CPU-only/inline compatibility strict receipts pass.',limits=['Modeled scalar OS25-derived CPU latencies with OS30 caches, not native Vexii or Pocket FPS.','CPU model runs demo360/sound120/live120 frames, excludes first20 from timing; exact checks include all frames and trailing commands.','Full320x240 color/depth RTL samples20/80/180/300 use selected existing Q2 worker RTL and maximal-rate producer with no CPU/audio/scanout contention.','Functional CPU framebuffers independently checked; CPU HUD snapshots removed from RTL GPU replay.','Same compiled control built from frozen current source with GCC15.2; candidates same compiler. No physical fit, full seed sweep or deployment.'],deployed=False,physical_fit=False)
(R/'final-result.json').write_text(json.dumps(result,indent=2)+'\n')
lines=['Quake2 cached packet/admission batching validation, 2026-10-12','', 'REJECT BOTH: neither candidate provides a meaningful modeled CPU improvement.','', 'Candidate  Mode    Workload   Baseline cycles/frame  Candidate cycles/frame  Change']
for x in rows:lines.append(f"{x['candidate']:10} {x['mode']:7} {x['workload']:7} {x['baseline_cycles']:20,.2f} {x['candidate_cycles']:21,.2f} {x['change_percent']:+.3f}%")
lines += ['',result['diagnosis'],'',result['strict_gate_note'],'','Both candidates: all command bytes/order/frame assignments and complete CPU RGB565 frames equal; all four selected-RTL sample color/depth outputs byte-exact.','',*result['limits'],'','See final-result.json for source, binary and receipt pins.']
(R/'REPORT.txt').write_text('\n'.join(lines)+'\n');print('final receipts',len(receipt_paths))
