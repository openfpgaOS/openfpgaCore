#!/usr/bin/env python3
"""Bind and compare bounded LOD4 coupled results; does not predict hardware FPS."""
import csv,json,hashlib
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
P=ROOT/'docs/measurements/os30-fps25-20261005/phase10'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def profile(d):
 old={r['function']:int(r['self_cycles']) for r in csv.DictReader((d/'profile-0360.tsv').open(),delimiter='\t')}
 return {r['function']:(int(r['self_cycles'])-old.get(r['function'],0))/800000
         for r in csv.DictReader((d/'profile.tsv').open(),delimiter='\t')}
def main(label):
 out=P/label;a=out/'run-baseline-8';b=out/'run-candidate-8';build=json.loads((out/'model/build.json').read_text())
 assert sha(out/'model/obj/Vtb_gpu')==build['simulator_sha256']
 for d in (a,b):
  invocation=json.loads((d/'inputs.json').read_text());assert invocation['success'] and invocation['frames']==8
  assert invocation['build_sha256']==sha(out/'model/build.json')
  assert 'FATAL' not in (d/'run.log').read_text()
  for name,digest in invocation['results_sha256'].items():assert sha(d/name)==digest,name
 outputs=[]
 for f in sorted(a.glob('frame_*.bin')):
  g=b/f.name;x=f.read_bytes();y=g.read_bytes();assert len(x)==len(y)==307200 and x==y
  outputs.append(dict(frame=int(f.stem.split('_')[1]),baseline_sha256=sha(f),candidate_sha256=sha(g),color_exact=True,depth_exact=True))
 assert len(outputs)==8
 scene={n:sha(a/n)==sha(b/n) for n in ('scene.csv','geometry.csv','culling.csv')};assert all(scene.values())
 status={n:json.loads((d/'coupled.json').read_text()) for n,d in [('baseline',a),('candidate',b)]}
 for v in status.values():assert v['frames']==v['presented']==8 and not v['cram1_errors'] and not v['ring_overflow']
 operations=list(csv.DictReader((b/'geometry-operations.csv').open()))
 assert len(operations)==2608 and all(int(r['operation'])==1 and int(r['status'])&6==6 and not int(r['status'])&0xe9 for r in operations)
 original=status['baseline']['cpu_cycles']/800000;candidate=status['candidate']['cpu_cycles']/800000
 profiles=dict(baseline=profile(a),candidate=profile(b))
 result=dict(passed=True,private_only=True,frames=8,first_frame=360,clock_hz=100000000,
  modeled_cpu_ms_per_frame=dict(baseline=original,candidate=candidate),
  modeled_time_reduction_pct=100*(1-candidate/original),modeled_throughput_increase_pct=100*(original/candidate-1),
  actual_load_wait_ms_per_frame=sum(int(r['cycles']) for r in operations)/800000,
  actual_alias=json.loads((b/'geometry.json').read_text()),actual_render_outputs=outputs,
  all368_scene_geometry_culling_exact=scene,profiles_ms_per_frame=profiles,
  inputs=dict(build_sha256=sha(out/'model/build.json'),baseline_elf_sha256=sha(a/'sm64.elf'),candidate_elf_sha256=sha(b/'sm64.elf'),
   native_functional_proof_sha256=sha(P/'load-alias-functional-v2/final-manifest.json'),
   rtl_component_proof_sha256=sha(P/'load-bram-alias-v3/sources.json'),
   native_handoff_sha256=sha(P/'bram-alias-native-v1/final-manifest.json')),
  limits=['CPU instruction/cache timing is estimated, with alias-specific CBO calibration against actual retainedCPU firmware. This is not native full-CPU execution or hardware FPS.',
  'Actual RTL LOAD, alias handshakes, GPU, SDRAM, CRAM1; non-alias CPU memory accesses remain estimated and synthetic background bus traffic, not traced CPU requests.',
  'Warmup advertises no device and uses CPU fallback; discovery resets at the idle frame360 boundary. No pending LOAD state is fabricated.',
  'Eight consecutive fixed-step Bob-omb Battlefield frames, not broad-world coverage. Only these eight framebuffers are actually rendered in this comparison.',
  'Physical timing closure and ALM fit are separate gates. No production source or deployment changed.'])
 with (out/'result.json').open('x') as f:json.dump(result,f,indent=2);f.write('\n')
 print(label,original,candidate,result['actual_load_wait_ms_per_frame'])
if __name__=='__main__':
 for label in ('load-alias-coupled-v1','load-alias-coupled-priority1-v1'):main(label)
