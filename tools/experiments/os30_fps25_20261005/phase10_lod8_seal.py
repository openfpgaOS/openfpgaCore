#!/usr/bin/env python3
"""Seal the rejected dense36 alias screen without changing any implementation."""
import csv,json
from pathlib import Path
from phase10_experiment import CAMP,ROOT,sha,write
P=CAMP/'phase10';OUT=P/'lod8-cow-coupled-v3';OLD=P/'cache-write-lod5-coupled-v2'
def profile(d):
 a={r['function']:r for r in csv.DictReader((d/'profile.tsv').open(),delimiter='\t')}
 b={r['function']:r for r in csv.DictReader((d/'profile-0360.tsv').open(),delimiter='\t')}
 return {n:{k:int(v[k])-int(b.get(n,{}).get(k,0))for k in('self_cycles','self_insns','dcache_miss','icache_miss','mispredict','calls')}for n,v in a.items()}
def main():
 prep=json.loads((OUT/'prepared.json').read_text())
 for n,h in prep['sources'].items():assert sha(OUT/n)==h,n
 results={}
 for w,start in [('warm',360),('cold',234)]:
  a=OLD/f'run-{w}';b=OUT/f'run-{w}';ims=[]
  for d in(a,b):
   inv=json.loads((d/'inputs.json').read_text());assert inv['success']and inv['start']==start and inv['frames']==8 and inv['traffic']==1
   for n,h in inv['results_sha256'].items():assert sha(d/n)==h,n
   assert 'FATAL'not in(d/'run.log').read_text()
  assert sha(b/'sm64.elf')==prep['app_sha256']
  for n in ('scene.csv','geometry.csv','culling.csv'):assert(a/n).read_bytes()==(b/n).read_bytes(),n
  for f in range(start,start+8):
   n=f'frame_{f:05}.bin';assert(a/n).read_bytes()==(b/n).read_bytes(),n;ims.append({'frame':f,'sha256':sha(b/n)})
  timing={}
  for label,d in [('baseline',a),('candidate',b)]:
   c=json.loads((d/'coupled.json').read_text());assert c['frames']==c['presented']==8 and not c['memory_errors']and not c['cram1_errors']and not c['ring_overflow']
   ops=list(csv.DictReader((d/'geometry-operations.csv').open()));g=json.loads((d/'geometry.json').read_text())
   timing[label]={'frame_ms':c['cpu_cycles']/800000,'load_status_elapsed_ms':sum(int(x['cycles'])for x in ops)/800000,
     'load_hardware_elapsed_ms':sum(int(x['hardware_finish'])-int(x['start'])for x in ops)/800000,'alias_fills_per_frame':g['alias_fills']/8,'alias_cycles_per_fill':g['alias_cycles']/g['alias_fills']}
  assert '288 words checked before timed work'in(b/'run.log').read_text()
  results[w]={'all8_actual_color_depth_exact':True,'all_histories_exact':True,'images':ims,'timing':timing,
    'frame_saving_ms':timing['baseline']['frame_ms']-timing['candidate']['frame_ms'],'warm_seed_sha256':sha(b/'warm-alias-snapshot.bin')}
 a=profile(OLD/'run-warm');b=profile(OUT/'run-warm');hot=[]
 for n in a.keys()|b.keys():
  x=a.get(n,{});y=b.get(n,{});dc=y.get('self_cycles',0)-x.get('self_cycles',0)
  hot.append(dict(function=n,baseline_ms=x.get('self_cycles',0)/800000,candidate_ms=y.get('self_cycles',0)/800000,delta_ms=dc/800000,
   instructions_delta_per_frame=(y.get('self_insns',0)-x.get('self_insns',0))/8,dcache_misses_delta_per_frame=(y.get('dcache_miss',0)-x.get('dcache_miss',0))/8))
 hot.sort(key=lambda r:abs(r['delta_ms']),reverse=True)
 write(OUT/'result.json',dict(passed=True,selected=False,rejection='Warm saving0.124449ms is below0.5ms gate, while component adds443 ALUT/104FF/1DSP.',results=results,hot_self=hot,limits=['Actual LOAD/alias/GPU/cache/SDRAM/16MiBCRAM RTL; CPU timing/cache/background traffic modeled,100MHz timebase, not Pocket FPS.','Warm functional-native state transferred into result BRAMs only before timing; all18 actual alias lines compared without warming CPU cache. No timed reseeding.','One selected eight-frame warm and cold window, not whole-game performance.'],deployment_ready=False))
 (OUT/'REPORT.txt').write_text('Rejected: persistent canonical36 alias with CPU pointer/COW ownership.\n\nWarm selectedLOD5 control34.95709500 ->34.83264625ms/frame:0.12444875ms saved (0.3560% lower time), below the0.5ms gate. Cold49.21498625 ->49.07220625ms:0.14278000ms saved. All16 actual color/depth buffers and scene/geometry/culling histories are byte-exact; memory,CRAM and ring checks pass. No fitter or deployment is authorized by this result.\n\nWarm costs explain the lost theoretical copy benefit:\n'+''.join(f"  {r['function']}: {r['baseline_ms']:.6f} ->{r['candidate_ms']:.6f}ms, delta{r['delta_ms']:+.6f}; instructions/frame{r['instructions_delta_per_frame']:+.3f}, Dmiss/frame{r['dcache_misses_delta_per_frame']:+.3f}.\n"for r in hot[:8])+'\nThese are exclusive self-profile modeled cycles, including attributed cache and MMIO stalls, not independently additive optimizations. Actual LOAD hardware duration is2.281288 ->2.282509ms/frame; alias linefills rise2100 ->2394/frame and stay19cycles/fill. The copy disappears, but ownership publication, deferred fog copies and later triangle reads absorb most of it. The independent hardware map adds443ALUT/104FF/1DSP with no extra result RAM; performance does not justify fitting it.\n\nScope: retained modeled CPU with actual LOAD/alias/GPU/cache and physical memory-controller RTL at100MHz. This is not measured Pocket FPS or a25FPS claim. The control uses prior bit/cycle-equivalent GPU/geometry sources; LOD8 binds the compact-v5 arithmetic/component proof. Existing CPU/GPU timing-model limits remain.\n\nWarm initialization is explicit: preserve the functional-native32-slot canonical snapshot and validity at the existing idle takeover, seed corresponding actual result BRAMs, and compare all288 words through18 real alias line reads before timing. CPU cache state is not warmed or invalidated by this validation, and no measured counters run during it. Initial snapshots are retained in each run; no timed result reseeding occurs.\n\nFunctional normal/fault/absent/timeout/fullpool evidence: ../lod8-cow-functional-v1/manifest.json. Hardware protocol/native proof: ../lod8-canonical-alias-v1/manifest.json. Source reviews: ../lod8-independent-source-review-v1/manifest.json and ../lod8-coupled-source-review-v1/manifest.json. Failed preparation/host-compilation attemptsv1/v2 remain separate and did not produce timing results. App and its inherited stale consume-before-START comment remain unchanged from the functional proof.\n')
 refs=[OLD/'manifest.json',P/'lod8-cow-functional-v1/manifest.json',P/'lod8-canonical-alias-v1/manifest.json',P/'lod8-independent-source-review-v1/manifest.json',P/'lod8-coupled-source-review-v1/manifest.json',Path(__file__)]
 files={str(p.relative_to(OUT)):sha(p)for p in OUT.rglob('*')if p.is_file()and'obj'not in p.parts and'build'not in p.parts and p.name!='manifest.json'}
 files['model/obj/Vtb_gpu']=sha(OUT/'model/obj/Vtb_gpu')
 write(OUT/'manifest.json',dict(passed=True,selected=False,deployment_ready=False,files=files,evidence_sha256={str(p.relative_to(ROOT)):sha(p)for p in refs}))
 print(sha(OUT/'manifest.json'))
if __name__=='__main__':main()
