#!/usr/bin/env python3
"""Private endpoint-control CPU/GPU/cache composition over the exact previous mapped source."""
import copy,difflib,hashlib,json,pathlib,shutil,subprocess,sys,time
R=pathlib.Path(__file__).resolve().parents[3];P=R/'docs/measurements/os30-fps25-20261005/phase10'
BASE=P/'parallel-control-map-v1';O=P/'endpoint-control-map-v1'
CPU='src/fpga/vendor/vexriscv/VexiiRiscv/VexiiRiscv_os30.v';TOP='src/fpga/targets/pocket/core_top.v';CACHE='src/fpga/common/gpu_color_depth_cache.sv';TEXTURE='src/fpga/common/gpu_tex_cache.v'
GPU='src/fpga/common/gpu_core.v'
CPU_SOURCE=P/'cpu-endpoint-control-v1/VexiiRiscv.v'
GPU_SOURCE=P/'gpu-endpoint-control-v1/gpu_core.v'
CACHE_SOURCE=P/'render-cache-owned-write-slot-v1/gpu_color_depth_cache.sv'
CACHE_SHA='ab1f1aeeb135cd2f80e44adb39616c35f28f1e164ab639b2f5645fbb447000ae'
GPU_SHA='5e9dd586e64034ba7b9f2bb97d9f4b8cba20c772da21d263f38ad74049269e5a'
CPU_SHA='6f50382aa82e6684551756e6c2473492d0bd915a0c94b2aeae9ac4a47e62dec4'
TEXTURE_SHA='c3fdc0adfe5ed583c37bf09ad3dbbbecb1e3c6ae1f5714cab5064eacc328e51c'
PROOFS={BASE/'manifest.json':'ca5ff6cc875075c84950d31e68d637ff16ab494fecb407c2437ef9277a29a16b',P/'cpu-endpoint-control-v1/manifest.json':'5a1662894ccd826515738fbf0a7543972c3d91f40e5eb1fa6136d84c479aeb71',P/'gpu-endpoint-control-v1/manifest.json':'2e6690eaf44158ff8ca4cc654473235ba7a7bfff03a67b897910623fa136e213',P/'render-cache-owned-write-slot-v1/manifest.json':'348f4827cbaed96c574b923f43970a905af8e02d868ca2cac71c71218d572dd0'}
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def put(p,x):
 with p.open('x') as f:json.dump(x,f,indent=2,sort_keys=True);f.write('\n')
def inv(p):return {str(f.relative_to(p)):sha(f)for f in sorted(p.rglob('*'))if f.is_file()}
def proofs():
 assert len(GPU_SHA)==64 and all(len(h)==64 for h in PROOFS.values()),"GPU composition pins pending; no preparation/CAD allowed"
 for p,h in PROOFS.items():
  assert sha(p)==h,p
  d=json.loads(p.read_text());assert d.get('passed',True)
  for n,v in d.get('files',{}).items():assert sha(p.parent/n)==v,(p,n)
  for key in ['evidence_sha256','inputs_sha256']:
   for n,v in d.get(key,{}).items():assert sha(R/n)==v,(p,n)
def prepare():
 proofs();assert sha(CPU_SOURCE)==CPU_SHA and sha(GPU_SOURCE)==GPU_SHA and sha(CACHE_SOURCE)==CACHE_SHA
 before=json.loads((BASE/'prepared.json').read_text())['source_sha256'];assert inv(BASE/'frozen')==before
 O.mkdir(exist_ok=False);shutil.copytree(BASE/'frozen',O/'frozen')
 shutil.copy2(CPU_SOURCE,O/'frozen'/CPU);shutil.copy2(GPU_SOURCE,O/'frozen'/GPU);shutil.copy2(CACHE_SOURCE,O/'frozen'/CACHE)
 after=inv(O/'frozen');assert before.keys()==after.keys()
 assert set(n for n in before if before[n]!=after[n])=={CPU,GPU,CACHE}
 assert after[GPU]==GPU_SHA and after[TEXTURE]==TEXTURE_SHA
 assert after[CPU]==CPU_SHA and after[CACHE]==CACHE_SHA
 assert (O/'frozen'/TOP).read_text().count('.WAYS(2), .SET_BITS(8), .WORD_BITS(4)')==1
 evidence=list(PROOFS)+[BASE/'prepared.json']
 put(O/'prepared.json',dict(scope=__doc__,source_sha256=after,parent_source_sha256=before,changed_sources=sorted([CPU,GPU,CACHE]),component_cpu_sha256=CPU_SHA,component_gpu_sha256=GPU_SHA,component_render_cache_sha256=after[CACHE],component_texture_sha256=TEXTURE_SHA,cpu_i_kib=64,cpu_d_kib=64,gpu_capacity_kib=32,seed=2,configured_mhz=100,cpu_gpu_cache_component_proofs_passed=True,current_native_qualification_pending_at_preparation=True,added_state_or_cycles_claim=False,full_fit_authorized=False,production_modified=False,recipe_sha256=sha(pathlib.Path(__file__)),evidence_sha256={str(p.relative_to(R)):sha(p)for p in evidence}))
 patch=''.join(''.join(difflib.unified_diff((BASE/'frozen'/n).read_text().splitlines(True),(O/'frozen'/n).read_text().splitlines(True),fromfile='parallel-control/'+n,tofile='endpoint-control/'+n))for n in [CPU,GPU,CACHE])
 (O/'candidate.patch').write_text(patch);shutil.copy2(__file__,O/'recipe.py');print('PREPARED',sha(O/'prepared.json'),flush=True)
def checked():
 proofs();d=json.loads((O/'prepared.json').read_text());assert inv(O/'frozen')==d['source_sha256']
 assert sha(pathlib.Path(__file__))==d['recipe_sha256']
 for n,h in d['evidence_sha256'].items():assert sha(R/n)==h,n
 return d
def run():
 d=checked();gate=O/'native-map-gate.json';g=json.loads(gate.read_text())
 assert g['passed_for_map'] and g['native_byte_cycle_equivalence_passed']
 assert g['prepared_sha256']==sha(O/'prepared.json') and g['cpu_sha256']==CPU_SHA and g['gpu_sha256']==GPU_SHA and g['cache_sha256']==CACHE_SHA
 assert g['evidence_sha256']
 for n,h in g['evidence_sha256'].items():assert sha(R/n)==h,n
 model=R/g['native_model_prepared'];assert model.is_relative_to(P/'native-renderer-window-v1')
 assert str(model.relative_to(R)) in g['evidence_sha256']
 md=json.loads(model.read_text());assert md['cpu_sha256']==CPU_SHA and md['gpu_cache_kib']==32
 for n,h in d['source_sha256'].items():
  if n!=TOP:assert sha(model.parent/'frozen'/n)==h,n
 m=O/'map-core';m.mkdir(exist_ok=False)
 sys.path.insert(0,str(R/'tools'));import sweep_target_matrix as sweep
 row=copy.deepcopy(json.loads((P/'compact-lod5-core-v7/fit-seed2/inventory.json').read_text())[0]);row['cpu_sha256']=CPU_SHA
 job,project=sweep.prepare(O/'frozen',m/'job',row,2,4);qsf=job/(project+'.qsf');old=next((BASE/'map-core/job').rglob('ap_core.qsf'));assert qsf.read_bytes()==old.read_bytes()
 cmd=sweep.docker(m,'pocket',job,['quartus_map',project]);put(m/'prepared.json',dict(command=cmd,qsf_sha256=sha(qsf),prepared_sha256=sha(O/'prepared.json'),native_map_gate_sha256=sha(gate),fitter_invoked=False));start=time.monotonic()
 with (m/'map.log').open('x') as f:r=subprocess.run(cmd,stdout=f,stderr=subprocess.STDOUT)
 put(m/'completion.json',dict(returncode=r.returncode,seconds=time.monotonic()-start,passed=r.returncode==0));checked();print('MAP',r.returncode,flush=True);assert r.returncode==0
if __name__=='__main__':globals()[sys.argv[1]]()
