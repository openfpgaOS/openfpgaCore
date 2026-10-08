#!/usr/bin/env python3
"""Prospective final composition: reuse exact texture-owned-address factor on CPU-forward/GPU32."""
import copy,difflib,hashlib,json,pathlib,shutil,subprocess,sys,time
R=pathlib.Path(__file__).resolve().parents[3];P=R/'docs/measurements/os30-fps25-20261005/phase10'
BASE=P/'rvcpipe-gpu32-forward-map-v1';O=P/'rvcpipe-gpu32-forward-texture-map-v1'
CPU='src/fpga/vendor/vexriscv/VexiiRiscv/VexiiRiscv_os30.v';TOP='src/fpga/targets/pocket/core_top.v';CACHE='src/fpga/common/gpu_color_depth_cache.sv';TEXTURE='src/fpga/common/gpu_tex_cache.v'
SOURCE=P/'texture-read-address-v1/gpu_tex_cache.v'
CPU_SHA='82b7e440177b195cbe84f5679c7e88c44b5df2aae004f798c63b4adc3b926534'
TEXTURE_SHA='c3fdc0adfe5ed583c37bf09ad3dbbbecb1e3c6ae1f5714cab5064eacc328e51c'
PROOFS={BASE/'manifest.json':'b8e8ad675ac3a07e78466b475386e5627e583e1c949a27d02a79fd136fefc35c',P/'texture-read-address-v1/manifest.json':'341fd8e14c6b006b6fc25bdfd06861779db7309427b0d1512052680b484f525e',P/'texture-owned-reuse-review-v1/manifest.json':'2de8a8430b129787da0b35e81687b44aa0563ea1215c8cbcd0c2172b64c9b266'}
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def put(p,x):
 with p.open('x') as f:json.dump(x,f,indent=2,sort_keys=True);f.write('\n')
def inv(p):return {str(f.relative_to(p)):sha(f)for f in sorted(p.rglob('*'))if f.is_file()}
def proofs():
 for p,h in PROOFS.items():
  assert sha(p)==h,p
  d=json.loads(p.read_text());assert d.get('passed',True)
  for n,v in d.get('files',{}).items():assert sha(p.parent/n)==v,(p,n)
  for key in ['evidence_sha256','inputs_sha256']:
   for n,v in d.get(key,{}).items():assert sha(R/n)==v,(p,n)
def prepare():
 proofs();assert sha(SOURCE)==TEXTURE_SHA
 before=json.loads((BASE/'prepared.json').read_text())['source_sha256'];assert inv(BASE/'frozen')==before
 O.mkdir(exist_ok=False);shutil.copytree(BASE/'frozen',O/'frozen');shutil.copy2(SOURCE,O/'frozen'/TEXTURE)
 after=inv(O/'frozen');assert before.keys()==after.keys()
 assert [n for n in before if before[n]!=after[n]]==[TEXTURE]
 assert after[CPU]==CPU_SHA and after[CACHE]=='25343436b682e7875a170c144b32ee06803c53177bf0c999626043587741ee3a'
 assert (O/'frozen'/TOP).read_text().count('.WAYS(2), .SET_BITS(8), .WORD_BITS(4)')==1
 evidence=list(PROOFS)+[BASE/'prepared.json']
 put(O/'prepared.json',dict(scope=__doc__,source_sha256=after,parent_source_sha256=before,changed_sources=[TEXTURE],component_cpu_sha256=CPU_SHA,component_render_cache_sha256=after[CACHE],component_texture_sha256=TEXTURE_SHA,cpu_i_kib=64,cpu_d_kib=64,gpu_capacity_kib=32,seed=2,configured_mhz=100,reused_component_proof_passed=True,current_native_qualification_pending_at_preparation=True,added_state_or_cycles_claim=False,full_fit_authorized=False,production_modified=False,recipe_sha256=sha(pathlib.Path(__file__)),evidence_sha256={str(p.relative_to(R)):sha(p)for p in evidence}))
 patch=''.join(difflib.unified_diff((BASE/'frozen'/TEXTURE).read_text().splitlines(True),SOURCE.read_text().splitlines(True),fromfile='forward-gpu32-storage/'+TEXTURE,tofile='forward-gpu32-texture/'+TEXTURE))
 (O/'candidate.patch').write_text(patch);shutil.copy2(__file__,O/'recipe.py');print('PREPARED',sha(O/'prepared.json'),flush=True)
def checked():
 proofs();d=json.loads((O/'prepared.json').read_text());assert inv(O/'frozen')==d['source_sha256']
 assert sha(pathlib.Path(__file__))==d['recipe_sha256']
 for n,h in d['evidence_sha256'].items():assert sha(R/n)==h,n
 return d
def run():
 d=checked();gate=O/'native-map-gate.json';g=json.loads(gate.read_text())
 assert g['passed_for_map'] and g['native_byte_cycle_equivalence_passed']
 assert g['prepared_sha256']==sha(O/'prepared.json') and g['texture_sha256']==TEXTURE_SHA
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
