#!/usr/bin/env python3
"""Execute an immutable RV32 actual-function test app in the retained qsim."""
import argparse,hashlib,json,os,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[4]
OUT=ROOT/'docs/measurements/sm64-batch-cull-20261005/validation'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
p=argparse.ArgumentParser(description=__doc__);p.add_argument('--elf',type=Path,required=True);p.add_argument('--label',required=True);a=p.parse_args()
dest=OUT/a.label;dest.mkdir(exist_ok=False)
elf=dest/'app.elf';elf.write_bytes(a.elf.read_bytes());exe=OUT/'qsim-functions/qsim'
cmd=[str(exe),str(elf),'--out',str(dest),'--max-insns','500000000']
env=os.environ.copy()
for k in ['SM64_GEOMETRY_CAP','SM64_WORKER_CAP','SM64_ATTRIBUTES_CAP','SM64_AUDIO','SM64_COMMAND_DMA','SM64_PERF_PANEL']:env.pop(k,None)
with (dest/'run.log').open('w') as f:
 r=subprocess.run(cmd,env=env,stdout=f,stderr=subprocess.STDOUT,timeout=300)
logs='\n'.join(p.read_text() for p in [dest/'run.log',dest/'console.log'] if p.exists())
passed=r.returncode==0 and 'PASS actual CPU comparison' in logs and 'ASSERTION FAILED' not in logs
summary=json.loads((dest/'summary.json').read_text()) if (dest/'summary.json').exists() else {}
report=dict(passed=passed,returncode=r.returncode,command=cmd,elf_sha256=sha(elf),exe_sha256=sha(exe),summary=summary,
 evidence_sha256={str(p.relative_to(ROOT)):sha(p) for p in dest.iterdir() if p.is_file()},source_elf=str(a.elf.resolve()))
(dest/'qualification.json').write_text(json.dumps(report,indent=2)+'\n')
print(logs[-8000:]);assert passed,'Missing successful actual CPU comparison marker'
