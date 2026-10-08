#!/usr/bin/env python3
"""One attributable GPUv10 map-only screen over exact compact LOD5v2."""
import json,sys
from pathlib import Path
import phase10_gpu_queue_map as m

m.BASE=m.P/'compact-lod5-core-v2'
m.OUT=m.P/'gpu-request-map-v10'
m.CAND=m.P/'gpu-request-mux-v10/gpu_core.v'
m.PROOF=m.P/'gpu-request-review-v10d/manifest.json'
DIVIDER=m.P/'gpu-request-mux-v10/divider-proof.json'

def verify_divider():
 p=json.loads(DIVIDER.read_text());assert p['passed'] and p['actual_rtl_expressions']
 for n,h in p['evidence_sha256'].items():assert m.sha(m.ROOT/n)==h,n

def prepare():
 assert m.sha(m.PROOF)=='cce2f63410e30ceaf79deb061fe123ca994c2490877e476790b339ed6d933a57'
 verify_divider();m.prepare()
 (m.OUT/'request-map-recipe.py').write_text(Path(__file__).read_text())
 m.save(m.OUT/'request-proof.json',dict(evidence_sha256={str(p.relative_to(m.ROOT)):m.sha(p)
  for p in [Path(__file__),DIVIDER,m.PROOF,m.BASE/'result.json']},
  scope='Only gpu_core.v changes from compact-v2. Existing CPU, cache-v2, folded LOAD, clock, seed2 and AREA settings unchanged. Map only.'))

def run():
 verify_divider()
 for n,h in json.loads((m.OUT/'request-proof.json').read_text())['evidence_sha256'].items():
  assert m.sha(m.ROOT/n)==h,n
 m.run()

if __name__=='__main__':globals()[sys.argv[1]]()
