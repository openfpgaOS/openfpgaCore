#!/usr/bin/env python3
"""One isolated resource map of exact zero-cycle load forwarding on GPU32 storage-v3."""
import copy,difflib,hashlib,json,pathlib,shutil,subprocess,sys,time
R=pathlib.Path(__file__).resolve().parents[3];P=R/'docs/measurements/os30-fps25-20261005/phase10'
BASE=P/'rvcpipe-gpu32-storage-map-v2';O=P/'rvcpipe-gpu32-forward-map-v1'
CPU='src/fpga/vendor/vexriscv/VexiiRiscv/VexiiRiscv_os30.v';TOP='src/fpga/targets/pocket/core_top.v';CACHE='src/fpga/common/gpu_color_depth_cache.sv'
SOURCE=P/'cpu-load-forward-v1/VexiiRiscv.v'
CPU_SHA='82b7e440177b195cbe84f5679c7e88c44b5df2aae004f798c63b4adc3b926534'
PROOFS={BASE/'manifest.json':'464f18e6df0357cba6c26538ee6af16ecab89b0b61015ada490b572a9af75a5c',P/'cpu-load-forward-v1/manifest.json':'83ccfd8ba7d1d6c0f5cf7abb7d6ed21cb3bcbf665a6d0419f93a42abe0ba86df',P/'cpu-load-forward-source-review-v1/manifest.json':'2fef1bab824a720d64fd396c07b7da03c665f2254055f6f291b56e5d0e8acb2d',P/'native-cpu-load-forward-check-v1/manifest.json':'413b7c2307332141a8ecc729584aaa9dc93a0305b7e5eaacf01f72abbc76ccea',P/'cpu-cache64-load-forward-regenerate-v1/manifest.json':'9493db918db5d52991759698b7dc16ee1e96373480a118a6984cc607605cc9fa'}
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
 proofs();assert sha(SOURCE)==CPU_SHA
 before=json.loads((BASE/'prepared.json').read_text())['source_sha256'];assert inv(BASE/'frozen')==before
 O.mkdir(exist_ok=False);shutil.copytree(BASE/'frozen',O/'frozen');shutil.copy2(SOURCE,O/'frozen'/CPU)
 after=inv(O/'frozen');assert before.keys()==after.keys()
 assert [n for n in before if before[n]!=after[n]]==[CPU]
 assert after[CACHE]=='25343436b682e7875a170c144b32ee06803c53177bf0c999626043587741ee3a'
 assert (O/'frozen'/TOP).read_text().count('.WAYS(2), .SET_BITS(8), .WORD_BITS(4)')==1
 model=P/'native-renderer-window-v1/model-cache64-rvcpipe-render32-storage-v3-forward-v1/prepared.json'
 md=json.loads(model.read_text());assert md['cpu_sha256']==CPU_SHA and md['gpu_cache_kib']==32
 for n,h in after.items():
  if n!=TOP:assert sha(model.parent/'frozen'/n)==h,n
 n=json.loads((P/'native-cpu-load-forward-check-v1/result.json').read_text());assert n['passed'] and n['cycle_change']==0
 evidence=list(PROOFS)+[BASE/'prepared.json',BASE/'shape-clarification-manifest.json',model,model.parent/'recipe-prepare.py']
 put(O/'prepared.json',dict(scope=__doc__,source_sha256=after,parent_source_sha256=before,changed_sources=[CPU],component_cpu_sha256=CPU_SHA,component_render_cache_sha256=after[CACHE],cpu_i_kib=64,cpu_d_kib=64,gpu_capacity_kib=32,seed=2,configured_mhz=100,component_and_native_qualification_passed=True,native_exact_artifacts=n['exact_artifacts'],native_cycle_change=0,added_cpu_registers_claim=0,full_fit_authorized=False,production_modified=False,recipe_sha256=sha(pathlib.Path(__file__)),evidence_sha256={str(p.relative_to(R)):sha(p)for p in evidence}))
 patch=''.join(difflib.unified_diff((BASE/'frozen'/CPU).read_text().splitlines(True),SOURCE.read_text().splitlines(True),fromfile='aecb-gpu32-storage/'+CPU,tofile='forward-gpu32-storage/'+CPU))
 (O/'candidate.patch').write_text(patch);shutil.copy2(__file__,O/'recipe.py');print('PREPARED',sha(O/'prepared.json'),flush=True)
def checked():
 proofs();d=json.loads((O/'prepared.json').read_text());assert inv(O/'frozen')==d['source_sha256']
 assert sha(pathlib.Path(__file__))==d['recipe_sha256']
 for n,h in d['evidence_sha256'].items():assert sha(R/n)==h,n
 return d
def run():
 d=checked();m=O/'map-core';m.mkdir(exist_ok=False)
 sys.path.insert(0,str(R/'tools'));import sweep_target_matrix as sweep
 row=copy.deepcopy(json.loads((P/'compact-lod5-core-v7/fit-seed2/inventory.json').read_text())[0]);row['cpu_sha256']=CPU_SHA
 job,project=sweep.prepare(O/'frozen',m/'job',row,2,4);qsf=job/(project+'.qsf');old=next((BASE/'map-core/job').rglob('ap_core.qsf'));assert qsf.read_bytes()==old.read_bytes()
 cmd=sweep.docker(m,'pocket',job,['quartus_map',project]);put(m/'prepared.json',dict(command=cmd,qsf_sha256=sha(qsf),prepared_sha256=sha(O/'prepared.json'),fitter_invoked=False));start=time.monotonic()
 with (m/'map.log').open('x') as f:r=subprocess.run(cmd,stdout=f,stderr=subprocess.STDOUT)
 put(m/'completion.json',dict(returncode=r.returncode,seconds=time.monotonic()-start,passed=r.returncode==0));checked();print('MAP',r.returncode,flush=True);assert r.returncode==0
if __name__=='__main__':globals()[sys.argv[1]]()
