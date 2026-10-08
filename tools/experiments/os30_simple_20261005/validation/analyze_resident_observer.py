#!/usr/bin/env python3
"""Require unchanged guest execution before interpreting the host histogram."""
from pathlib import Path
import hashlib,json,csv,collections
ROOT=Path(__file__).resolve().parents[4]
C=ROOT/'docs/measurements/os30-simple-20261005';O=C/'validation'
B=ROOT/'docs/measurements/sm64-batch-cull-20261005/validation/capture-normal-reference-geo0'
N=O/'capture-resident-observed-normal'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
assert sha(N/'sm64.elf')==sha(B/'sm64.elf')=='0350acc15976134120b3ba4cb48de4ee5f9077f62732143251840d68293ebc6f'
proof={}
for p in B.rglob('*'):
 if not p.is_file():continue
 rel=p.relative_to(B)
 if p.name in ['frames.csv','scene.csv','geometry.csv','culling.csv'] or p.suffix=='.tsv' or p.suffix=='.vec':
  assert (N/rel).read_bytes()==p.read_bytes(),str(rel)
  proof[str(rel)]=sha(p)
assert len(proof)>20
rows=list(csv.DictReader((N/'resident-calls.csv').open()))
sel=[r for r in rows if 360<=int(r['frame'])<=720]
def summary(rs):
 masks=collections.Counter(int(r['dirty_mask']) for r in rs); clean=[r for r in rs if int(r['dirty_mask'])==0]
 return {'calls':len(rs),'dirty_masks':dict(sorted(masks.items())),'dirty0_calls':len(clean),'dirty0_percent':100*len(clean)/len(rs) if rs else 0,'vertex_uploads':sum(n*((m&1)+((m>>1)&1)+((m>>2)&1)) for m,n in masks.items()),'alpha_changed_calls':sum(r['alpha']!=r['current_alpha'] for r in rs),'dirty0_alpha_changed_calls':sum(r['alpha']!=r['current_alpha'] for r in clean),'viewport_bits_differ_calls':sum(int(r['viewport_bits_differ']) for r in rs),'dirty0_viewport_bits_differ_calls':sum(int(r['viewport_bits_differ']) for r in clean),'alpha_values':dict(sorted(collections.Counter(int(r['alpha']) for r in rs).items()))}
# Profiles snapshot at360/720 bracket exactly calls stamped360..719.
ps={r['function']:r for r in csv.DictReader((B/'profile-0360.tsv').open(),delimiter='\t')}
pe={r['function']:r for r in csv.DictReader((B/'profile-0720.tsv').open(),delimiter='\t')}
expected=int(pe['gfx_gpu_vtx_cache_tri']['calls'])-int(ps['gfx_gpu_vtx_cache_tri']['calls'])
actual=sum(360<=int(r['frame'])<720 for r in rows)
assert actual==expected,(actual,expected)
res={'passed_noninterference':True,'elf_sha256':sha(N/'sm64.elf'),'observer_manifest_sha256':sha(O/'qsim-resident-observed-manifest.json'),'observation_sha256':sha(N/'resident-calls.csv'),'script_sha256':sha(Path(__file__)),'unchanged_guest_artifacts':proof,'profile_count_crosscheck':{'window':'360..719','profile_function_calls':expected,'observed_calls':actual},'gameplay':{'first_frame':360,'last_frame':720,**summary(sel)},'windows':[{'first_frame':f,'last_frame':min(720,f+59),**summary([r for r in sel if f<=int(r['frame'])<min(721,f+60)])} for f in range(360,721,60)],'scope':'Host reads at actual writer entry before execution, a2 dirty_mask. Every selected profile/trace/vector identical to retained baseline. This measures eligible emitted cache triangles only, not culled/fallback triangles. Alpha comparison is exact byte; viewport raw-bit diagnostic conservatively includes signed-zero differences.'}
(O/'resident-observer-results.json').write_text(json.dumps(res,indent=2)+'\n')
print(json.dumps({k:res[k] for k in ['passed_noninterference','profile_count_crosscheck','gameplay']},indent=2))
