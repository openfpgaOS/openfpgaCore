#!/usr/bin/env python3
"""Prospective map of single-issue RVC pipeline, I64/D64 and unchanged GPU8."""
import copy,difflib,hashlib,json,pathlib,shutil,subprocess,sys,time
R=pathlib.Path(__file__).resolve().parents[3]
P=R/'docs/measurements/os30-fps25-20261005/phase10'
BASE=P/'cache64-timing-compose-v2'
O=P/'cache64-rvcpipe-map-v1'
CPU='src/fpga/vendor/vexriscv/VexiiRiscv/VexiiRiscv_os30.v'
SOURCE=P/'cpu-cache64-rvcpipe-v1/VexiiRiscv.v'
CPU_SHA='aecbd7d8f63675333909df756c072b767bab269d29188190f80cfb3cde376295'
PROOFS={
 BASE/'manifest.json':'e74508b1a97b0373bbae9717feabd9ee4b770cd22f63a09edcd1829a42778b7b',
 P/'cpu-cache64-rvcpipe-v1/manifest.json':'9ffac4b098f2a1760811a045947b2c163693e4c5a63115773992c4e230dced03',
 P/'cpu-cache64-rvcpipe-source-review-v1/manifest.json':'5917e78ef2fdd0364ebcdcda845e7c243afb696b706283083c89b5df04f18f85',
 P/'audio-ramp-flat-source-review-v1/manifest.json':'d439f36414ff0a15b3dbf59b45ec472bab926a2c70b91ebd73b106af2ff83bff',
 P/'gpu-recip-write-source-review-v1/manifest.json':'d05027159bf0e05db1d409d9b6da2e28c86e3388b41b76776ef9811880c1d39c',
}
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def put(p,x):
 with p.open('x') as f:json.dump(x,f,indent=2,sort_keys=True);f.write('\n')
def inv(p):return {str(f.relative_to(p)):sha(f) for f in sorted(p.rglob('*')) if f.is_file()}
def check_proofs():
 for p,h in PROOFS.items():
  assert sha(p)==h,p
  d=json.loads(p.read_text())
  for n,v in d.get('evidence_sha256',{}).items():assert sha(R/n)==v,(p,n)
def prepare():
 check_proofs();assert sha(SOURCE)==CPU_SHA
 before=json.loads((BASE/'prepared.json').read_text())['source_sha256']
 assert inv(BASE/'frozen')==before
 O.mkdir(exist_ok=False);shutil.copytree(BASE/'frozen',O/'frozen')
 shutil.copy2(SOURCE,O/'frozen'/CPU)
 after=inv(O/'frozen');assert sorted(n for n in before if before[n]!=after[n])==[CPU]
 assert before.keys()==after.keys()
 native=P/'native-renderer-window-v1/model-cache64-rvcpipe-v1'
 model=json.loads((native/'prepared.json').read_text())
 assert model['cpu_sha256']==CPU_SHA and model['gpu_cache_kib']==8
 assert not model['rvc_bit19_plane'] and model['equivalent_timing_components']
 for n,h in after.items():assert sha(native/'frozen'/n)==h,n
 top=(O/'frozen/src/fpga/targets/pocket/core_top.v').read_text()
 assert top.count('.WAYS(2), .SET_BITS(6), .WORD_BITS(4)')==1
 patch=''.join(difflib.unified_diff((BASE/'frozen'/CPU).read_text().splitlines(True),SOURCE.read_text().splitlines(True),fromfile='cache64-timing/'+CPU,tofile='cache64-rvcpipe/'+CPU))
 evidence=list(PROOFS)+[BASE/'prepared.json',native/'prepared.json',native/'build.json',native/'recipe-prepare.py']
 put(O/'prepared.json',dict(scope=__doc__,source_sha256=after,parent_source_sha256=before,changed_sources=[CPU],component_cpu_sha256=CPU_SHA,gpu_capacity_kib=8,cpu_i_kib=64,cpu_d_kib=64,seed=2,configured_mhz=100,added_frontend_cycles=1,peak_issue_width=1,rvc_bit19_overlay=False,cycle_equivalence_claim=False,preparation_only=True,native_qualification_pending_at_preparation=True,full_fit_authorized=False,production_modified=False,recipe_sha256=sha(pathlib.Path(__file__)),evidence_sha256={str(p.relative_to(R)):sha(p) for p in evidence}))
 (O/'candidate.patch').write_text(patch);shutil.copy2(__file__,O/'recipe.py')
 print('PREPARED',sha(O/'prepared.json'),flush=True)
def checked():
 d=json.loads((O/'prepared.json').read_text())
 assert inv(O/'frozen')==d['source_sha256']
 assert sha(pathlib.Path(__file__))==d['recipe_sha256']
 for n,h in d['evidence_sha256'].items():assert sha(R/n)==h,n
 check_proofs();return d
def run():
 d=checked()
 # Native qualification is deliberately separate from prospective preparation.
 gate=O/'native-map-gate.json';g=json.loads(gate.read_text())
 assert g['passed_for_map'] and g['native_correctness_passed'] and g['native_performance_reviewed']
 assert g['prepared_sha256']==sha(O/'prepared.json') and g['cpu_sha256']==CPU_SHA
 assert g['evidence_sha256']
 for n,h in g['evidence_sha256'].items():assert sha(R/n)==h,n
 m=O/'map-core';m.mkdir(exist_ok=False)
 sys.path.insert(0,str(R/'tools'));import sweep_target_matrix as sweep
 row=copy.deepcopy(json.loads((P/'compact-lod5-core-v7/fit-seed2/inventory.json').read_text())[0])
 row['cpu_sha256']=CPU_SHA
 job,project=sweep.prepare(O/'frozen',m/'job',row,2,4)
 qsf=job/(project+'.qsf');old=next((BASE/'map-core/job').rglob('ap_core.qsf'))
 assert qsf.read_bytes()==old.read_bytes()
 cmd=sweep.docker(m,'pocket',job,['quartus_map',project])
 put(m/'prepared.json',dict(command=cmd,qsf_sha256=sha(qsf),prepared_sha256=sha(O/'prepared.json'),native_map_gate_sha256=sha(gate),fitter_invoked=False))
 start=time.monotonic()
 with (m/'map.log').open('x') as f:r=subprocess.run(cmd,stdout=f,stderr=subprocess.STDOUT)
 put(m/'completion.json',dict(returncode=r.returncode,seconds=time.monotonic()-start,passed=r.returncode==0))
 checked();print('MAP',r.returncode,flush=True);assert r.returncode==0
if __name__=='__main__':globals()[sys.argv[1]]()
