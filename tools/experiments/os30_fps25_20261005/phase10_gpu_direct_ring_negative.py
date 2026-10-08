#!/usr/bin/env python3
"""Prove the real-AXI test catches the ownership-boundary posted-write loss."""
import hashlib,json,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
OUT=ROOT/'docs/measurements/os30-fps25-20261005/phase10/gpu-direct-ring-v1'
T=OUT/'tests';N=OUT/'negative-control';N.mkdir(exist_ok=False)
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
src=OUT/'gpu_core.v';text=src.read_text()
a='&& !gpu_cpu_pending && !reg_wr && !geo_abort && geo_request'
assert text.count(a)==1
(N/'gpu_core.v').write_text(text.replace(a,'&& !reg_wr && !geo_abort && geo_request'))
cmd=json.loads((T/'build.json').read_text())['command']
cmd=[str(N/'gpu_core.v') if x==str(src) else str(N/'obj') if x==str(T/'obj') else x for x in cmd]
with (N/'build.log').open('x') as f:subprocess.run(cmd,stdout=f,stderr=subprocess.STDOUT,check=True)
with (N/'run.log').open('x') as f:r=subprocess.run([str(N/'obj/Vtb_axi_periph')],stdout=f,stderr=subprocess.STDOUT)
log=(N/'run.log').read_text();print(log)
assert r.returncode!=0 and 'Assertion' in log
(N/'result.json').write_text(json.dumps(dict(passed=True,mutation='Remove posted/incoming GPU AW exclusion from geometry grant',detected=True,returncode=r.returncode,source_sha256=sha(src),mutant_sha256=sha(N/'gpu_core.v'),baseline_test_sha256=sha(T/'result.json'),recipe_sha256=sha(Path(__file__)),build_command=cmd,log_sha256=sha(N/'run.log')),indent=2)+'\n')
