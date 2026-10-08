#!/usr/bin/env python3
"""Prove the passive 176-bit checker detects a one-bit cache-depth corruption."""
import hashlib,json,resource,shutil,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
OUT=ROOT/'docs/measurements/os30-fps25-20261005/phase10/vcache-narrow-v1'
DEST=OUT/'negative-control'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
DEST.mkdir(exist_ok=False);frozen=DEST/'frozen';shutil.copytree(OUT/'observer/frozen',frozen)
p=frozen/'common/gpu_core.v';s=p.read_text();old="3'd4: vc_mem[{3'd4, xf_load_slot}] <= vcw_explicit_depth ? xf_load_depth : vt_depth[0];";new="3'd4: vc_mem[{3'd4, xf_load_slot}] <= (vcw_explicit_depth ? xf_load_depth : vt_depth[0]) ^ 32'd1;"
assert s.count(old)==1;p.write_text(s.replace(old,new))
command=json.loads((OUT/'observer/build.json').read_text())['command'];command=[x.replace(str(OUT/'observer/frozen'),str(frozen)).replace(str(OUT/'observer/obj'),str(DEST/'obj')) for x in command]
with (DEST/'build.log').open('x') as f:subprocess.run(command,stdout=f,stderr=subprocess.STDOUT,check=True)
exe=DEST/'obj/Vtb_gpu';source=OUT/'replays/frame_00360.vec';run=[str(exe),str(source),str(DEST/'invalid.bin'),'+memory_traffic=0','+tagged_tex=1']
def limits():resource.setrlimit(resource.RLIMIT_CORE,(0,0))
with (DEST/'run.log').open('x') as f:r=subprocess.run(run,stdout=f,stderr=subprocess.STDOUT,timeout=60,preexec_fn=limits)
log=(DEST/'run.log').read_text();assert r.returncode!=0 and 'narrow cache 176-bit write mismatch' in log
# Successful checks must actually observe the expected number of full records.
obs=json.loads((OUT/'observer/result.json').read_text());assert obs['runs'][0]['observer_counts']==[['1484','854']];assert obs['runs'][1]['observer_counts']==[['2968','2562']]
result=dict(passed=True,intentional_mutation='XOR low bit of serialized depth word; candidate source unchanged',returncode=r.returncode,detected='narrow cache 176-bit write mismatch',original_candidate_sha256=sha(OUT/'gpu_core.v'),mutated_candidate_sha256=sha(p),build_command=command,run_command=run,simulator_sha256=sha(exe),log_sha256=sha(DEST/'run.log'),recipe_sha256=sha(Path(__file__)))
(DEST/'result.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(dict(passed=True,returncode=r.returncode,detected=result['detected'])))
