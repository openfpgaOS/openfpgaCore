#!/usr/bin/env python3
"""Bind exact native/protocol evidence for the separate-genvar GPU32 map retry."""
import json,pathlib
import phase10_rvcpipe_gpu32_storage_map_v2 as m
R,P,O,sha,put=m.R,m.P,m.O,m.sha,m.put
m.checked();W=P/'native-renderer-window-v1'
proofs={
 P/'native-cache-storage-check-v1/manifest.json':'2b8b222d884b5d0b78d96b66dc14d8855668a94252e0049fe0ecddd24bd6261e',
 P/'render-cache-storage-v3/manifest.json':'60f291321202527a298f366d54a6b467dea5f9c4cedc5740a00756fdcf07c707',
 W/'run-interval-render32-storage-v3/manifest.json':'084c62d8890732e6392a47d88fb156096316040cfd0979892ce55618a865a35a',
}
review=P/'render-cache-storage-source-review-v3/manifest.json';assert sha(review).startswith('2320ea0b');proofs[review]=sha(review)
checked=0
for p,h in proofs.items():
 assert sha(p)==h,p
 d=json.loads(p.read_text());assert d.get('passed',True)
 for n,v in d.get('files',{}).items():assert sha(p.parent/n)==v,(p,n);checked+=1
 for field in ['evidence_sha256','inputs_sha256']:
  for n,v in d.get(field,{}).items():assert sha(R/n)==v,(p,n);checked+=1
pair=json.loads((P/'native-cache-storage-check-v1/result.json').read_text())['pairs'][1]
assert pair['capacity_kib']==32 and len(pair['exact_files'])==17
old=W/'run-interval-render32-storage-v2';new=W/'run-interval-render32-storage-v3'
for n in pair['exact_files']:
 assert (old/n).read_bytes()==(new/n).read_bytes(),n
 proofs[old/n]=sha(old/n);proofs[new/n]=sha(new/n)
models=[W/'model-cache64-rvcpipe-render32-storage-v2/prepared.json',W/'model-cache64-rvcpipe-render32-storage-v3/prepared.json']
ma,mb=[json.loads(p.read_text())for p in models]
sources=[x['source_sha256']for x in [ma,mb]];assert sources[0].keys()==sources[1].keys()
delta=[n for n in sources[0]if sources[0][n]!=sources[1][n]]
assert delta==['frozen/'+m.CACHE]
assert sources[0][delta[0]]=='13fa29b4d44202be88ca22fd89ca2b1c901c86049e9e75c828de77a2e651bdb8'
assert sources[1][delta[0]]==m.CACHE_SHA
for p,d in zip(models,[ma,mb]):
 assert d['cpu_sha256']==m.CPU_SHA and d['gpu_cache_kib']==32
 for n,h in d['source_sha256'].items():assert sha(p.parent/n)==h,(p,n)
 proofs[p]=sha(p)
proofs[pathlib.Path(__file__)]=sha(pathlib.Path(__file__))
gate=dict(passed_for_map=True,component_proof_passed=True,native_byte_cycle_equivalence_passed=True,prepared_sha256=sha(O/'prepared.json'),render_cache_sha256=m.CACHE_SHA,native_model_prepared=str(models[1].relative_to(R)),scope='Root-authorized single resource-map retry after inline-genvar parsing rejection; no fitter. V2 GPU8/32 proof plus v2/v3 exact17-file GPU32 native comparison and all12 fresh component runs bind the syntax-only correction.',checked_proof_bindings=checked,native_exact_files=pair['exact_files'],source_delta=delta,evidence_sha256={str(p.relative_to(R)):h for p,h in proofs.items()})
put(O/'native-map-gate.json',gate);print('GATE',sha(O/'native-map-gate.json'),'proof bindings',checked)
