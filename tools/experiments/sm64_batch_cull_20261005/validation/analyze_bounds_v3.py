#!/usr/bin/env python3
"""Audit the v3 post-eligibility read-only observer and count real lookups."""
import collections,csv,hashlib,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[4]
B=ROOT/'docs/measurements/sm64-batch-cull-20261005/validation'
A=B/'capture-candidate-v3-geo0';C=B/'capture-candidate-v3-eligible-bounds-geo0'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
assert sha(C/'sm64.elf')=='191c5bab252cbe91e4823c4951ffd74b8aeb818aec825c353bb90019cf097297'
inputs=json.loads((C/'inputs.json').read_text());assert inputs['success']
for p,digest in inputs['results_sha256'].items():assert sha(C/p)==digest,p
rels=['frames.csv','scene.csv','geometry.csv','diagnostic.csv','culling.csv','console.log']+[str(p.relative_to(A)) for p in (A/'gpuvec').glob('*.vec')]
identity={p:sha(A/p)==sha(C/p) for p in rels};assert all(identity.values())
R=list(csv.DictReader((C/'bounds-calls.csv').open()));G=[v for v in R if 360<=int(v['frame'])<=720]
def stats(rows):
 counts=collections.Counter();keys=collections.defaultdict(set);calls=collections.Counter();miss=collections.Counter();empty=0
 for v in rows:
  assert 8<=int(v['count'])<=32-int(v['first']) and int(v['first'])<32
  counts['calls']+=1;counts['immutable' if int(v['immutable']) else 'mutable']+=1
  counts['retry_skip' if int(v['expected_skip']) else 'hit_no_retry' if int(v['expected_hit']) else 'immutable_miss' if int(v['immutable']) else 'mutable_scan']+=1
  if int(v['immutable']):
   k=(v['address'],v['count']);keys[v['cache_index']].add(k);calls[k]+=1
   if not int(v['expected_hit']):miss[k]+=1;empty+=v['cached_source']=='00000000'
 return dict(totals=dict(counts),unique_immutable_keys=len(calls),used_cache_indices=len(keys),indices_with_multiple_keys=sum(len(v)>1 for v in keys.values()),max_keys_per_index=max(map(len,keys.values())),empty_slot_misses=empty,occupied_slot_misses=sum(miss.values())-empty,top_miss_sources=[dict(address=k[0],count=int(k[1]),calls=calls[k],misses=n) for k,n in miss.most_common(15)])
paths=[C/'inputs.json',C/'bounds-calls.csv',C/'bounds-symbols.json',B/'qsim-bounds-eligible-observed-manifest.json',B/'bounds-entry-v3.asm',Path(__file__)]
report=dict(scope='Actual v3 PC104cf24a reached only after level/count/range/G_ZBUFFER/no-TEXGEN/perspective gates; s1 source,a1 count,s0 first stable until bounds lookup. Observer uses actual24-byte Vtx range and32-byte cache header. All guest/model outputs unchanged.',supersedes='bounds-observer-v3.json and bounds-conflicts-v3.json were entry-only provisional estimates and used16-byte source-range classification. This post-gate24-byte result is the authoritative miss/skip count.',identity=identity,all_eligible=stats(R),gameplay_eligible=stats(G),symbols=json.loads((C/'bounds-symbols.json').read_text()),evidence_sha256={str(p.relative_to(ROOT)):sha(p) for p in paths})
(B/'bounds-eligible-observer-v3.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps(report['gameplay_eligible'],indent=2))
