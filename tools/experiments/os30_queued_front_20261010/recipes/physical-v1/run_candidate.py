#!/usr/bin/env python3
"""Run one fresh map/fit/asm/sta, after checking every bound local input."""
from pathlib import Path
import argparse,hashlib,json,subprocess,time
B=Path(__file__).resolve().parent

def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def load(p):return json.loads(p.read_text())
def write(p,d):p.write_text(json.dumps(d,indent=2)+'\n')
def verify():
 m=load(B/'manifest.json')
 for rel,h in m['inputs_sha256'].items():assert sha(B/rel)==h,rel
 for rel,h in m['external_inputs_sha256'].items():assert sha(Path(rel))==h,rel
 d=load(B/'inputs.json')
 for rel,h in d['sources'].items():assert sha(B/'frozen'/rel)==h,rel
 for p,h in d['selected_files_sha256'].items():assert sha(Path(p))==h,p
 assert not (B/'build/db').exists(),'existing synthesis database; this candidate requires a fresh map'
 assert not (B/'build/incremental_db').exists(),'existing incremental database; this candidate requires a fresh map'
 assert not (B/'compile.log').exists(),'candidate already launched'
 return dict(status='READY_FOR_ONE_FRESH_COMPILE',source_files=len(d['sources']),semantic_changed_sources=d['changed_sources'],seed=d['seed'],placement_effort_multiplier=d['placement_effort_multiplier'],configured_mhz=d['configured_mhz'],inherited_checkpoint=False,manifest_sha256=sha(B/'manifest.json'))

def run():
 p=verify();write(B/'launch-preflight.json',p)
 commands=[]
 # Exclusive log creation prevents accidental duplicate starts of this job.
 with(B/'compile.log').open('x')as log:
  log.write('Fresh isolated map -> fit -> asm -> sta. No inherited database/checkpoint.\n')
  log.flush()
  for stage in ('quartus_map','quartus_fit','quartus_asm','quartus_sta'):
   argv=['bash',str(B/'tools/quartus-container.sh'),str(B/'build'),stage,'ap_core']
   print('Starting '+stage,flush=True);start=time.monotonic()
   rc=subprocess.run(argv,stdout=log,stderr=subprocess.STDOUT).returncode
   commands.append(dict(stage=stage,argv=argv,exit_code=rc,seconds=time.monotonic()-start))
   write(B/'compile-stages.json',commands)
   print('Completed '+stage+': '+str(rc),flush=True)
   if rc:raise SystemExit(rc)
 # Report generation never authorizes deployment; post-fit timing/pin/reset
 # qualification is a separate step for the parent experiment.
 for rel,h in load(B/'inputs.json')['sources'].items():assert sha(B/'frozen'/rel)==h,rel
 write(B/'compile-complete.json',dict(status='FOUR_QUARTUS_STAGES_COMPLETED_POSTFIT_QUALIFICATION_REQUIRED',commands=commands,manifest_sha256=sha(B/'manifest.json')))

if __name__=='__main__':
 ap=argparse.ArgumentParser();ap.add_argument('--run',action='store_true');ap.add_argument('--verify-only',action='store_true');a=ap.parse_args()
 if a.run:run()
 else:print(json.dumps(verify(),indent=2))
