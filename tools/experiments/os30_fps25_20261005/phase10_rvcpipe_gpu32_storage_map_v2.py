#!/usr/bin/env python3
"""Prospective resource map: unchanged RVC CPU plus GPU32 banked storage/victim RAM."""
import copy,difflib,hashlib,json,pathlib,shutil,subprocess,sys,time
R=pathlib.Path(__file__).resolve().parents[3];P=R/'docs/measurements/os30-fps25-20261005/phase10'
BASE=P/'cache64-rvcpipe-map-v1';COMP=P/'render-cache-storage-v3'
O=P/'rvcpipe-gpu32-storage-map-v2'
CPU='src/fpga/vendor/vexriscv/VexiiRiscv/VexiiRiscv_os30.v'
TOP='src/fpga/targets/pocket/core_top.v';CACHE='src/fpga/common/gpu_color_depth_cache.sv'
CPU_SHA='aecbd7d8f63675333909df756c072b767bab269d29188190f80cfb3cde376295'
CACHE_SHA='25343436b682e7875a170c144b32ee06803c53177bf0c999626043587741ee3a'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def put(p,x):
 with p.open('x') as f:json.dump(x,f,indent=2,sort_keys=True);f.write('\n')
def inv(p):return {str(f.relative_to(p)):sha(f) for f in sorted(p.rglob('*')) if f.is_file()}
def parent():
 p=BASE/'manifest.json';assert sha(p)=='df38686d02bbed0540b5e44c8262f6d7789f80b6bf3dc060672748d1072ef042'
 for n,h in json.loads(p.read_text())['evidence_sha256'].items():assert sha(R/n)==h,n
 d=json.loads((BASE/'prepared.json').read_text());assert inv(BASE/'frozen')==d['source_sha256'];return d
def prepare():
 before=parent()['source_sha256'];assert sha(COMP/'gpu_color_depth_cache.sv')==CACHE_SHA
 assert json.loads((COMP/'prepared.json').read_text())['source_sha256']==CACHE_SHA
 O.mkdir(exist_ok=False);shutil.copytree(BASE/'frozen',O/'frozen')
 top=O/'frozen'/TOP;s=top.read_text()
 old='gpu_color_depth_cache #(\n    .ADDR_W(26), .CACHE_ALL(1), .WAYS(2), .SET_BITS(6), .WORD_BITS(4),\n    .REGISTER_READ_LOOKUP(1), .REGISTER_FILL_HAZARD(1), .SPECULATE_WRITE_HOT0(1)\n) gpu_render_cache ('
 new=old.replace('.SET_BITS(6)','.SET_BITS(8)');assert s.count(old)==1;top.write_text(s.replace(old,new))
 shutil.copy2(COMP/'gpu_color_depth_cache.sv',O/'frozen'/CACHE)
 after=inv(O/'frozen');assert before.keys()==after.keys()
 assert sorted(n for n in before if before[n]!=after[n])==sorted([TOP,CACHE])
 assert after[CPU]==CPU_SHA
 patch=''
 for n in [TOP,CACHE]:patch+=''.join(difflib.unified_diff((BASE/'frozen'/n).read_text().splitlines(True),(O/'frozen'/n).read_text().splitlines(True),fromfile='rvcpipe-gpu8/'+n,tofile='rvcpipe-gpu32-storage/'+n))
 evidence=[BASE/'manifest.json',BASE/'prepared.json',COMP/'prepared.json',COMP/'recipe.py',COMP/'candidate.patch',COMP/'v2-to-v3.patch',P/'cache64-rvcpipe-fit-v1/manifest.json',P/'rvcpipe-gpu32-storage-map-v1/manifest.json']
 put(O/'prepared.json',dict(scope=__doc__,source_sha256=after,parent_source_sha256=before,changed_sources=sorted([TOP,CACHE]),component_cpu_sha256=CPU_SHA,component_render_cache_sha256=CACHE_SHA,cpu_i_kib=64,cpu_d_kib=64,gpu_capacity_kib=32,seed=2,configured_mhz=100,top_edit=dict(before=old,after=new),storage='Synchronous M10K victim array; at most2048x40 data banks with registered index and direct output selection',extra_transaction_cycles_claim=0,component_and_native_qualification_pending_at_preparation=True,actual_block_packing_unknown=True,full_fit_authorized=False,production_modified=False,recipe_sha256=sha(pathlib.Path(__file__)),evidence_sha256={str(p.relative_to(R)):sha(p) for p in evidence}))
 (O/'candidate.patch').write_text(patch);shutil.copy2(__file__,O/'recipe.py');print('PREPARED',sha(O/'prepared.json'),flush=True)
def checked():
 parent();d=json.loads((O/'prepared.json').read_text());assert inv(O/'frozen')==d['source_sha256']
 assert sha(pathlib.Path(__file__))==d['recipe_sha256']
 for n,h in d['evidence_sha256'].items():assert sha(R/n)==h,n
 return d
def run():
 d=checked();gate=O/'native-map-gate.json';g=json.loads(gate.read_text())
 assert g['passed_for_map'] and g['component_proof_passed'] and g['native_byte_cycle_equivalence_passed']
 assert g['prepared_sha256']==sha(O/'prepared.json') and g['render_cache_sha256']==CACHE_SHA
 assert g['evidence_sha256']
 for n,h in g['evidence_sha256'].items():assert sha(R/n)==h,n
 model=R/g['native_model_prepared'];assert model.is_relative_to(P/'native-renderer-window-v1')
 assert str(model.relative_to(R)) in g['evidence_sha256']
 native=json.loads(model.read_text());assert native['cpu_sha256']==CPU_SHA and native['gpu_cache_kib']==32
 # Test fixture elaborates its capacity directly; core_top is not its top.
 for n,h in d['source_sha256'].items():
  if n!=TOP:assert sha(model.parent/'frozen'/n)==h,n
 m=O/'map-core';m.mkdir(exist_ok=False)
 sys.path.insert(0,str(R/'tools'));import sweep_target_matrix as sweep
 row=copy.deepcopy(json.loads((P/'compact-lod5-core-v7/fit-seed2/inventory.json').read_text())[0]);row['cpu_sha256']=CPU_SHA
 job,project=sweep.prepare(O/'frozen',m/'job',row,2,4)
 qsf=job/(project+'.qsf');old=next((BASE/'map-core/job').rglob('ap_core.qsf'));assert qsf.read_bytes()==old.read_bytes()
 cmd=sweep.docker(m,'pocket',job,['quartus_map',project])
 put(m/'prepared.json',dict(command=cmd,qsf_sha256=sha(qsf),prepared_sha256=sha(O/'prepared.json'),native_map_gate_sha256=sha(gate),fitter_invoked=False));start=time.monotonic()
 with (m/'map.log').open('x') as f:r=subprocess.run(cmd,stdout=f,stderr=subprocess.STDOUT)
 put(m/'completion.json',dict(returncode=r.returncode,seconds=time.monotonic()-start,passed=r.returncode==0));checked();print('MAP',r.returncode,flush=True);assert r.returncode==0
if __name__=='__main__':globals()[sys.argv[1]]()
