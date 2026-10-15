#!/usr/bin/env python3
"""Bind unchanged coverage receipt to independent opcode source-safety audit."""
import hashlib,json
from pathlib import Path
H=Path(__file__).resolve().parent
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
a=H.parent/'source-audit-v1.json';p=H/'result.json';r=json.loads(p.read_text());s=json.loads(a.read_text())
assert s['status']=='PASS_SCOPED_SOURCE_AUDIT' and s['coverage_sha256']==sha(p)
assert set(s['whitelist'])==set(r['allowed_skip_opcodes'])
for path,h in s['inputs_sha256'].items():assert sha(Path(path))==h,path
for path,h in r['inputs_sha256'].items():assert sha(Path(path))==h,path
out={'status':'PASS_SOURCE_AUDITED_STRUCTURAL_COVERAGE','cases':r['cases'],'limits':r['limits'],'source_safety_scope':s['scope'],'inputs_sha256':{str(x):sha(x) for x in [p,a,Path(__file__)]},'implementation_or_speed_claim':False}
f=H/'final.json';assert not f.exists();f.write_text(json.dumps(out,indent=2)+'\n');print(sha(f))
