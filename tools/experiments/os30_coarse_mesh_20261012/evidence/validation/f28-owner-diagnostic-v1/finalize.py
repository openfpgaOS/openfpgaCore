#!/usr/bin/env python3
"""Preserve the failed ambiguity checks and bind the stronger final proof."""
import hashlib,json
from pathlib import Path
H=Path(__file__).resolve().parent;V=H.parent
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
inputs=[H/'analyze.py',H/'result.json',Path(__file__),V/'check_replays_v3.py',V/'independent_lifetime_v3.py',V/'independent_single_v3.py',V/'independent_group_v2.py',V/'candidate-v4/check-independent-v3.log',V/'single-v4/check-independent-v3.log',V/'check_replays_v4.py',V/'independent_lifetime_v4.py',V/'independent_single_v4.py',V/'independent_group_v3.py',V/'independent_dl_v1.py',V/'f28-dl-binding-v1.json',V/'independent-negative-v5/run.py',V/'independent-negative-v5/result.json']
receipts=[V/name/'check-independent-v4.json' for name in ('control-v4','candidate-v4','single-v4')]
for p in receipts:assert json.loads(p.read_text())['status'].startswith('PASS_')
negative=json.loads((V/'independent-negative-v5/result.json').read_text());assert negative['status']=='PASS_TWENTYTWO_CORRUPTIONS_REJECTED' and negative['measured_inputs_unchanged']
result={'status':'PASS_STRONGER_UNIQUE_CONTEXT_OWNERSHIP_WITH_PRESERVED_FAILURES','reason':'Newly admitted repeated immutable display lists expose four nonunique source/count/command identities. The AJ02 v3 matcher rejected them; AJ03 first-unclaimed matching selected at least one legacy invocation and then correctly failed async/legacy disjointness. Those failures and source versions remain unchanged.','strengthening':'Version4 requires exactly one baseline CPU call matching source/count/command/phase AND independently captured consumed raw transform context. This precedes the unchanged complete raw-context equality, coverage, disjointness, actual caller-stack, source lifetime, packet and draw-order checks. No LOAD omission, state normalization or replay change was introduced.','all_jobs_unique':True,'ambiguous_source_jobs_per_candidate':4,'jobs_per_candidate':229,'negative_cases':22,'inputs_sha256':{str(p):sha(p) for p in inputs+receipts}}
p=H/'supersession.json';assert not p.exists();p.write_text(json.dumps(result,indent=2)+'\n');print(sha(p))
