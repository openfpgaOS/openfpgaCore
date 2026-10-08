#!/usr/bin/env python3
"""Read-only mechanical composition review, without repeated component tests."""
from pathlib import Path
import hashlib,json,re,shutil
R=Path(__file__).resolve().parents[3];P=R/'docs/measurements/os30-fps25-20261005/phase10';S=P/'cpu-endpoint-control-v1';O=P/'cpu-endpoint-control-source-review-v1'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def put(p,x):
 with p.open('x') as f:json.dump(x,f,indent=2,sort_keys=True);f.write('\n')
def main():
 assert not O.exists();m=json.loads((S/'manifest.json').read_text());assert m['passed'];n=0
 for p,h in m['evidence_sha256'].items():assert sha(R/p)==h,p;n+=1
 b=(P/'cpu-parallel-control-v1/VexiiRiscv.v').read_text();c=(S/'VexiiRiscv.v').read_text();hs=json.loads((S/'hunks.json').read_text());assert len(hs)==4
 positions=[];parts={}
 for h in hs:
  assert b.count(h['old'])==1;start=b.index(h['old']);positions.append((start,start+len(h['old'])))
  parts.setdefault(h['component'],[]).append(h)
 assert len(parts)==2
 positions.sort();assert all(a[1]<=z[0] for a,z in zip(positions,positions[1:]))
 for name,rows in parts.items():
  component=json.loads((P/name/'manifest.json').read_text());assert component['passed']
  for p,h in component['evidence_sha256'].items():assert sha(R/p)==h,p;n+=1
  direct=b
  original=json.loads((P/name/'hunks.json').read_text());original=[original] if isinstance(original,dict) else original
  assert [(h['old'],h['new']) for h in rows]==[(h['old'],h['new']) for h in original]
  for h in rows:direct=direct.replace(h['old'],h['new'],1)
  assert direct==(P/name/'VexiiRiscv.v').read_text()
 for order in [hs,list(reversed(hs))]:
  q=b
  for h in order:assert q.count(h['old'])==1;q=q.replace(h['old'],h['new'],1)
  assert q==c
  for h in reversed(order):assert q.count(h['new'])==1;q=q.replace(h['new'],h['old'],1)
  assert q==b
 decl=lambda t:re.findall(r'^\s*(?:input|output|inout|reg|parameter|localparam)\s+.*$',t,re.M)
 assert decl(b)==decl(c) and sorted(l.strip() for l in b.splitlines() if '<=' in l)==sorted(l.strip() for l in c.splitlines() if '<=' in l)
 O.mkdir();shutil.copy2(__file__,O/'recipe.py')
 put(O/'review.json',dict(passed=True,blocking_findings=[],candidate_sha256=sha(S/'VexiiRiscv.v'),components=2,literal_hunks=4,nonoverlapping_parent_ranges=positions,component_sources_reconstructed=True,forward_reverse_exact=True,state_declarations_sequential_lines_exact=True,evidence_rehashed=n,no_simulation_or_build=True,limitation='Component equivalence inherited; physical timing/native composition qualification is separate.'))
 files=[Path(__file__),S/'manifest.json',S/'hunks.json',S/'VexiiRiscv.v',P/'cpu-frontend-endpoints-source-review-v1/manifest.json',O/'recipe.py',O/'review.json']
 put(O/'manifest.json',dict(passed=True,evidence_sha256={str(p.relative_to(R)):sha(p) for p in files}));print(sha(O/'manifest.json'))
if __name__=='__main__':main()
