#!/usr/bin/env python3
"""Map-only exact cache64 CPU plus GPU render-cache sets64→256; no fitter."""
import copy,difflib,hashlib,json,pathlib,re,shutil,subprocess,sys,time
from phase10_lod5_queues_map import entities
R=pathlib.Path(__file__).resolve().parents[3];P=R/'docs/measurements/os30-fps25-20261005/phase10'
BASE=P/'cpu-cache64-resource-v1';O=P/'cache64-gpu32-map-v1';CPU='src/fpga/vendor/vexriscv/VexiiRiscv/VexiiRiscv_os30.v';TOP='src/fpga/targets/pocket/core_top.v'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def put(p,x):
 with p.open('x') as f:json.dump(x,f,indent=2,sort_keys=True);f.write('\n')
def inv(d):return {str(p.relative_to(d)):sha(p) for p in sorted(d.rglob('*')) if p.is_file()}
def parent():
 d=json.loads((BASE/'manifest.json').read_text())
 for n,h in d['evidence_sha256'].items():assert sha(R/n)==h,n
 return json.loads((BASE/'map-core/prepared.json').read_text())['source_sha256']
def prepare():
 before=parent();assert inv(BASE/'frozen')==before;O.mkdir(exist_ok=False);shutil.copytree(BASE/'frozen',O/'frozen')
 top=O/'frozen'/TOP;s=top.read_text();old='gpu_color_depth_cache #(\n    .ADDR_W(26), .CACHE_ALL(1), .WAYS(2), .SET_BITS(6), .WORD_BITS(4),\n    .REGISTER_READ_LOOKUP(1), .REGISTER_FILL_HAZARD(1), .SPECULATE_WRITE_HOT0(1)\n) gpu_render_cache (';new=old.replace('.SET_BITS(6)','.SET_BITS(8)');assert s.count(old)==1;t=s.replace(old,new);top.write_text(t)
 after=inv(O/'frozen');assert sorted(n for n in before if before[n]!=after[n])==[TOP];assert after[CPU]=='ab3205dff8e38211a223234e237a4ee083b55ecd0a02cb915c58850a34ce322e'
 native=P/'native-renderer-window-v1';evidence=[BASE/'manifest.json',P/'cache64-v7-fit-v1-retry1/manifest.json',native/'run-cache64-render32-v1/result.json',native/'run-cache64-render32-v1/process.json',native/'model-cache64-render32-v1/prepared.json']
 put(O/'prepared.json',dict(scope=__doc__,source_sha256=after,parent_source_sha256=before,only_changed_file=TOP,edit=dict(before=old,after=new),cpu_sha256=after[CPU],seed=2,configured_mhz=100,full_fit_authorized=False,production_modified=False,recipe_sha256=sha(pathlib.Path(__file__)),evidence_sha256={str(p.relative_to(R)):sha(p) for p in evidence}))
 (O/'candidate.patch').write_text(''.join(difflib.unified_diff(s.splitlines(True),t.splitlines(True),fromfile='cache64/core_top.v',tofile='cache64-gpu32/core_top.v')));shutil.copy2(__file__,O/'recipe.py');print('PREPARED',sha(O/'prepared.json'),flush=True)
def checked():
 parent();d=json.loads((O/'prepared.json').read_text());assert inv(O/'frozen')==d['source_sha256'];assert sha(pathlib.Path(__file__))==d['recipe_sha256']
 for n,h in d['evidence_sha256'].items():assert sha(R/n)==h,n
 return d
def run():
 d=checked();m=O/'map-core';m.mkdir(exist_ok=False);sys.path.insert(0,str(R/'tools'));import sweep_target_matrix as sweep
 row=copy.deepcopy(json.loads((P/'compact-lod5-core-v7/fit-seed2/inventory.json').read_text())[0]);row['cpu_sha256']=d['cpu_sha256'];job,project=sweep.prepare(O/'frozen',m/'job',row,2,4)
 qsf=job/(project+'.qsf');oldqsf=next((BASE/'map-core/job').rglob('ap_core.qsf'));assert qsf.read_bytes()==oldqsf.read_bytes()
 cmd=sweep.docker(m,'pocket',job,['quartus_map',project]);put(m/'prepared.json',dict(command=cmd,qsf_sha256=sha(qsf),prepared_sha256=sha(O/'prepared.json'),fitter_invoked=False));start=time.monotonic()
 with (m/'map.log').open('x') as f:r=subprocess.run(cmd,stdout=f,stderr=subprocess.STDOUT)
 put(m/'completion.json',dict(returncode=r.returncode,seconds=time.monotonic()-start,passed=r.returncode==0));checked();print('MAP',r.returncode,flush=True);assert r.returncode==0
if __name__=='__main__':globals()[sys.argv[1]]()
