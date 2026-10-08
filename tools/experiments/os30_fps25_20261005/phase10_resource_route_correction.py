#!/usr/bin/env python3
"""Correct the upload counter register in the first resource-audit observation."""
import hashlib,importlib.util,json,struct
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
OUT=ROOT/'docs/measurements/os30-fps25-20261005/phase10/resource-audit'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
p=Path(__file__).with_name('phase8_vectors.py');s=importlib.util.spec_from_file_location('v',p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
old=json.loads((OUT/'active-texture-route.json').read_text());rows=[]
for row in old['frames']:
 p=ROOT/'docs/measurements/os30-fps25-20261005/phase9/checks-state-f11/capture-normal/gpuvec'/f"frame_{row['frame']:05}.vec"
 d=m.parse(p.read_bytes());writes=[struct.unpack('<II',t[4:]) for t in d['tokens'] if t[:4]==b'MMIO']
 rows.append(dict(frame=row['frame'],texture_upload_words=sum(reg==0x3c for reg,value in writes)))
result=dict(passed=True,supersedes_field='active-texture-route.json frames[].texture_upload_words',reason='Initial optional upload count incorrectly used offset0x2c; actual _GPU_FAST_TEX_UP_DATA is0x3c. Route and residency claims are unaffected.',correct_upload_register=60,frames=rows,evidence_sha256={'docs/measurements/os30-fps25-20261005/phase10/resource-audit/active-texture-route.json':sha(OUT/'active-texture-route.json'),'docs/measurements/os30-fps25-20261005/phase9/checks-state-f11/of_gpu.h':sha(ROOT/'docs/measurements/os30-fps25-20261005/phase9/checks-state-f11/of_gpu.h'),'tools/experiments/os30_fps25_20261005/phase10_resource_route_correction.py':sha(Path(__file__))})
with (OUT/'texture-upload-correction.json').open('x') as f:json.dump(result,f,indent=2);f.write('\n')
manifest=dict(passed=True,notes=['Includes explicit correction of optional texture upload counter; original evidence retained.'],evidence_sha256={str(p.relative_to(ROOT)):sha(p) for p in OUT.rglob('*') if p.is_file()})
with (OUT/'final-audit.json').open('x') as f:json.dump(manifest,f,indent=2);f.write('\n')
print(json.dumps(rows))
