#!/usr/bin/env python3
"""Bind exact source-only texture native comparison before the final resource map."""
import json,pathlib
import phase10_rvcpipe_gpu32_forward_texture_map as m
R,P,O,sha,put=m.R,m.P,m.O,m.sha,m.put
m.checked();W=P/'native-renderer-window-v1'
new=W/'run-interval-render32-forward-texture-v1';old=W/'run-interval-render32-storage-v3-forward-v1'
manifest=new/'manifest.json';assert sha(manifest)=='7b8671d2b05fcc21afa191a106b9de63a97f772e3aacc2e377903455aea2b9f1'
proofs={manifest:sha(manifest)};count=0
d=json.loads(manifest.read_text())
for key in ['evidence_sha256','inputs_sha256']:
 for n,h in d.get(key,{}).items():assert sha(R/n)==h,n;count+=1
names=json.loads((P/'native-cpu-load-forward-check-v1/result.json').read_text())['exact_artifacts'];assert len(names)==17
for n in names:
 assert (old/n).read_bytes()==(new/n).read_bytes(),n
 proofs[old/n]=sha(old/n);proofs[new/n]=sha(new/n)
models=[W/'model-cache64-rvcpipe-render32-storage-v3-forward-v1/prepared.json',W/'model-cache64-render32-forward-texture-v1/prepared.json']
ma,mb=[json.loads(p.read_text())for p in models];a,b=ma['source_sha256'],mb['source_sha256'];assert a.keys()==b.keys()
delta=[n for n in a if a[n]!=b[n]];assert delta==['frozen/'+m.TEXTURE] and b[delta[0]]==m.TEXTURE_SHA
for p,d in zip(models,[ma,mb]):
 assert d['cpu_sha256']==m.CPU_SHA and d['gpu_cache_kib']==32
 for n,h in d['source_sha256'].items():assert sha(p.parent/n)==h,(p,n)
 proofs[p]=sha(p)
proofs[pathlib.Path(__file__)]=sha(pathlib.Path(__file__))
put(O/'native-map-gate.json',dict(passed_for_map=True,native_byte_cycle_equivalence_passed=True,prepared_sha256=sha(O/'prepared.json'),texture_sha256=m.TEXTURE_SHA,native_model_prepared=str(models[1].relative_to(R)),scope='Root-authorized one resource map after exact17-file texture-only native comparison; independent pair review may be sealed separately. No fitter.',exact_native_artifacts=names,source_delta=delta,checked_manifest_bindings=count,evidence_sha256={str(p.relative_to(R)):h for p,h in proofs.items()}))
print('GATE',sha(O/'native-map-gate.json'))
