#!/usr/bin/env python3
"""Recreate candidate purely, then reverse/reapply its exact source patch."""
from pathlib import Path
import hashlib,json,subprocess,shutil
from prepare import OUT,GFX,BASE,EXPECTED
from prepare_v5 import transform

def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def main():
 dest=OUT/'source-replay-v5';dest.mkdir(exist_ok=True)
 files=dest/'files';files.mkdir(exist_ok=True)
 records={}
 for name,h in EXPECTED.items():
  original=(BASE/name).read_text();candidate=transform(name,original)
  assert hashlib.sha256(original.encode()).hexdigest()==h
  assert candidate==(GFX/name).read_text()
  (files/name).write_text(candidate)
  records[name]={'baseline':h,'candidate':sha(GFX/name),'pure_recipe_equal':True}
 patch=(OUT/'batch-cull-v5.patch').read_text()
 commands=[]
 for reverse in [True,False]:
  cmd=['patch','--batch','--fuzz=0','-p1']+(['-R'] if reverse else [])
  r=subprocess.run(cmd,input=patch,text=True,capture_output=True,cwd=files);r.check_returncode()
  commands.append({'command':cmd,'output':r.stdout+r.stderr})
  for name in EXPECTED:
   assert (files/name).read_bytes()==((BASE if reverse else GFX)/name).read_bytes()
 recipes=dest/'recipes';recipes.mkdir(exist_ok=True)
 for name in ['prepare.py','prepare_v2.py','prepare_v3.py','prepare_v4.py','prepare_v5.py','check_functions.py','check_geo1.py','check_replay.py']:
  shutil.copy2(Path(__file__).parent/name,recipes/name)
 (dest/'manifest.json').write_text(json.dumps({'passed':True,'candidate_version':5,'status':'correctness qualified; not selected for publication because performance gain not established','files':records,'commands':commands,'patch_sha256':sha(OUT/'batch-cull-v5.patch'),'recipes':{p.name:sha(p) for p in recipes.iterdir()},'host_function_manifest_sha256':sha(OUT/'function-checks/manifest.json'),'geo1_manifest_sha256':sha(OUT/'geo1-checks/manifest.json'),'independent_audit_sha256':sha(OUT/'independent-audit-v5/manifest.json')},indent=2)+'\n')
 print('PASS exact pure generation, zero-fuzz reverse to baseline and forward to candidate')
if __name__=='__main__':main()
