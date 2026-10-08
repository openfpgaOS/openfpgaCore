#!/usr/bin/env python3
"""Independent source/evidence gate for one compact-v2 resource fit; no tools run."""
import hashlib,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
P=ROOT/'docs/measurements/os30-fps25-20261005/phase10'
OUT=P/'compact-lod5-core-v2';BASE=P/'compact-lod5-core-v1'
GPU='src/fpga/common/gpu_core.v';CACHE='src/fpga/common/gpu_color_depth_cache.sv';TOP='src/fpga/targets/pocket/core_top.v'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
evidence={};checks={}
def read(p):
 evidence[str(p.relative_to(ROOT))]=sha(p)
 return json.loads(p.read_text())
def manifest(p):
 j=read(p)
 if 'evidence_sha256' in j:
  files={ROOT/n:h for n,h in j['evidence_sha256'].items()}
 elif 'files' in j:
  files={p.parent/n:h for n,h in j['files'].items()}
  files.update({ROOT/'tools/experiments/os30_fps25_20261005'/n:h for n,h in j.get('helpers',{}).items()})
 else:files={p.parent/n:h for n,h in j.items()}
 for f,h in files.items():assert sha(f)==h,f
 checks[str(p.relative_to(ROOT))]=len(files)
 return j
def main():
 old=read(BASE/'prepared.json');new=read(OUT/'prepared.json')
 for parent,prep in [(BASE,old),(OUT,new)]:
  for n,h in prep['source_sha256'].items():assert sha(parent/'frozen'/n)==h,n
 assert old['source_sha256'].keys()==new['source_sha256'].keys()
 changed=[n for n,h in new['source_sha256'].items() if old['source_sha256'][n]!=h]
 assert sorted(changed)==sorted([GPU,CACHE,TOP])
 assert new['source_sha256'][GPU]==sha(P/'gpu-queue-pack-v8/gpu_core.v')
 assert new['source_sha256'][CACHE]==sha(P/'cache-read-stage-v2/gpu_color_depth_cache.sv')
 t=(BASE/'frozen'/TOP).read_text();needle='    .ADDR_W(26), .CACHE_ALL(1), .WAYS(2), .SET_BITS(6), .WORD_BITS(4)'
 assert t.count(needle)==1
 assert (OUT/'frozen'/TOP).read_text()==t.replace(needle,needle+',\n    .REGISTER_READ_LOOKUP(1), .REGISTER_FILL_HAZARD(1)')
 for n,h in new['evidence_sha256'].items():assert sha(ROOT/n)==h,n
 manifests=[BASE/'final-manifest.json',BASE/'independent-review.json',
  P/'gpu-queue-review-v8/manifest.json',P/'gpu-queue-map-v8/manifest.json',
  P/'cache-read-stage-v1/manifest.json',P/'cache-read-stage-v2/manifest.json',
  P/'cache-read-stage-source-review-v2/manifest.json',P/'cache-read-stage-core-v2/manifest.json',
  P/'cache-stage-lod5-coupled-v1/manifest.json',P/'cache-stage-lod5-coupled-v2/manifest.json']
 for m in manifests:manifest(m)
 coupled=read(P/'cache-stage-lod5-coupled-v2/result.json')
 assert coupled['passed'] and coupled['cache_source_sha256']==new['source_sha256'][CACHE]
 assert coupled['parameter']=={'REGISTER_READ_LOOKUP':1,'REGISTER_FILL_HAZARD':1}
 for mode,start in [('warm',360),('cold',234)]:
  report=coupled['results'][mode]
  assert report['all8_actual_color_depth_exact'] and report['all_prior_scene_geometry_culling_exact']
  for frame in range(start,start+8):
   a=P/f'cache-stage-lod5-coupled-v1/run-{mode}/frame_{frame:05}.bin'
   b=P/f'cache-stage-lod5-coupled-v2/run-{mode}/frame_{frame:05}.bin'
   assert a.read_bytes()==b.read_bytes(),(mode,frame)
 for f in [Path(__file__),ROOT/'tools/experiments/os30_fps25_20261005/phase10_compact_cache_fit.py',
           ROOT/'tools/experiments/os30_fps25_20261005/phase10_compact_lod5_fit.py',ROOT/'tools/sweep_target_matrix.py']:
  evidence[str(f.relative_to(ROOT))]=sha(f)
 review=dict(passed_for_resource_fit=True,deployment_ready=False,prepared_sha256=sha(OUT/'prepared.json'),
  only_changed_files=changed,verified_manifest_entries=checks,
  review_scope='One savedseed2 AREA resource/timing fit only. No app/production/SD change; no automatic publishing.',
  checks=[
   'Exact three-file source composition: only GPU queue storage, render-cache stages and selected instance parameters. All CPU, geometry, QSF/SDC and clocks byte-identical to compactv1.',
   'GPUv8 has exact output/cycles versusv6 in12native+4boundary runs and independent2.3million queue records, byte strobes and stalledAW/W checks. Packing cannot create a mixed-port collision: writes require notfull, reads requirenonempty, and pointers equal onlyempty/full.',
   'Cache READ_LOOKUP and FILL_HAZARD stages pass independent source/lifetime review and all12 protocol runs including poisoned live addresses, cross-line posted hazards, tracker full/wrap, simultaneousAW/B, backpressure and reset.',
   'Warm/cold coupled cache runs retain all16 raw frame buffers exactly. Total latency cost versus original cache is0.056065mswarm/0.0508mscold in modeled frame time, not a performance optimization or PocketFPS.',
   'Coupled model still uses originalCPU timing, unifiedv1 geometry and spanv3. Prior native queueCPU tests, unifiedv2 bit/cycle proof and GPUv6/v8 bit/cycle proof are the separate bridges. This is not complete whole-core native-game validation.',
   'GPUmap removes twoMLAB instances and31GPUALUT but wholemap rises45ALUT from remapping; cachemap reduces38wholeALUT. Do not add these to promise fittedALM/LAB/Fmax.',
   'Runner checks review/prepared/evidence before copying privatefitdir; one inventory row, one seedoffset0, savedseed2. sweep.prepare rewrites effectiveSEED2; report must confirm finaleffective2.',
   'No automatic deployment, source promotion, clock lowering or timing-exception edits. Fit/timing remains unqualified until results.'],
  evidence_sha256=evidence)
 with (OUT/'independent-review.json').open('x') as f:json.dump(review,f,indent=2);f.write('\n')
 print(sha(OUT/'independent-review.json'))
if __name__=='__main__':main()
