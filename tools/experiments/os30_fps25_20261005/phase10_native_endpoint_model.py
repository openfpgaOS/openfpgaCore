#!/usr/bin/env python3
"""Clone the qualified native fixture; replace only three proved RTL files."""
from pathlib import Path
import hashlib,json,shutil,subprocess,sys,time
R=Path(__file__).resolve().parents[3]
P=R/'docs/measurements/os30-fps25-20261005/phase10'
W=P/'native-renderer-window-v1'
BASE=W/'model-parallel-control-v1'
OUT=W/'model-endpoint-control-v1'
CONFIG=P/'endpoint-native-inputs-v1.json'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def put(p,d):
 with p.open('x') as f:json.dump(d,f,indent=2);f.write('\n')
def checked_config():
 c=json.loads(CONFIG.read_text())
 assert len(c['sources'])==3
 for n,v in c['sources'].items():assert sha(R/v['source'])==v['sha256']
 for n,h in c['proof_manifests'].items():
  p=R/n;assert sha(p)==h,n
  m=json.loads(p.read_text());assert m.get('passed',True)
  for key in ['evidence_sha256','inputs_sha256']:
   for a,b in m.get(key,{}).items():assert sha(R/a)==b,(n,a)
  for a,b in m.get('files',{}).items():assert sha(p.parent/a)==b,(n,a)
 return c
def prepare():
 c=checked_config();d=json.loads((BASE/'prepared.json').read_text())
 assert json.loads((BASE/'build.json').read_text())['returncode']==0
 for n,h in d['source_sha256'].items():assert sha(BASE/n)==h,n
 for n,v in c['sources'].items():assert d['source_sha256'][n]==v['parent_sha256']
 OUT.mkdir(exist_ok=False);shutil.copytree(BASE/'frozen',OUT/'frozen')
 for n,v in c['sources'].items():shutil.copyfile(R/v['source'],OUT/n)
 before=dict(d['source_sha256'])
 d['source_sha256']={str(p.relative_to(OUT)):sha(p) for p in (OUT/'frozen').rglob('*') if p.is_file()}
 assert before.keys()==d['source_sha256'].keys()
 assert {n for n in before if before[n]!=d['source_sha256'][n]}==set(c['sources'])
 d['command']=[x.replace(str(BASE),str(OUT)) for x in d['command']]
 d['cpu_sha256']=c['sources']['frozen/src/fpga/vendor/vexriscv/VexiiRiscv/VexiiRiscv_os30.v']['sha256']
 d['recipe_sha256']=sha(Path(__file__))
 if 'parallel_components' in d:d['inherited_parallel_components']=d.pop('parallel_components')
 d['endpoint_components']=dict(config_sha256=sha(CONFIG),parent_prepared_sha256=sha(BASE/'prepared.json'),changed_model_sources=sorted(c['sources']),intended_extra_cycles=0,physical_timing_unmeasured=True)
 put(OUT/'prepared.json',d);shutil.copyfile(CONFIG,OUT/'inputs.json');shutil.copyfile(__file__,OUT/'recipe-prepare.py')
 print('PREPARED',sha(OUT/'prepared.json'),flush=True)
def build():
 checked_config();d=json.loads((OUT/'prepared.json').read_text())
 assert sha(CONFIG)==d['endpoint_components']['config_sha256'] and sha(Path(__file__))==d['recipe_sha256']
 for n,h in d['source_sha256'].items():assert sha(OUT/n)==h,n
 start=time.monotonic()
 with (OUT/'build.log').open('x') as f:r=subprocess.run(d['command'],cwd=OUT/'frozen/src/fpga/test',stdout=f,stderr=subprocess.STDOUT)
 put(OUT/'build.json',dict(returncode=r.returncode,seconds=time.monotonic()-start));assert r.returncode==0
 print('native endpoint-control model build passed',flush=True)
if __name__=='__main__':globals()[sys.argv[1]]()
