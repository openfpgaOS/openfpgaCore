#!/usr/bin/env python3
"""Map exact I64/D64, GPU8 and three proven cycle-neutral timing factors."""
import copy,difflib,hashlib,json,pathlib,shutil,subprocess,sys,time
R=pathlib.Path(__file__).resolve().parents[3];P=R/'docs/measurements/os30-fps25-20261005/phase10';BASE=P/'cpu-cache64-resource-v1';O=P/'cache64-timing-compose-v2'
CPU='src/fpga/vendor/vexriscv/VexiiRiscv/VexiiRiscv_os30.v'
SOURCES={CPU:(P/'cpu-cache64-rvc19-v1/VexiiRiscv.v','32cf8619ebd6beb9b7c62e593e300ba85486daf9c70c199ad0f978a23b80b3ea'),'src/fpga/common/audio_mixer.v':(P/'audio-ramp-flat-v1/audio_mixer.v','f9cbfe37da81ae6d5564df076e75c6d28f46feabb992bb695392d06aa55e0ba4'),'src/fpga/common/gpu_core.v':(P/'gpu-recip-write-v1/gpu_core.v','238f44273b6bb0188447485d1501e898c8df0c1cb9b758841e8af7ceac414adb')}
PROOFS=[BASE/'manifest.json',P/'cpu-cache64-rvc19-source-review-v1/manifest.json',P/'cpu-cache64-rvc19-native-review-v1/manifest.json',P/'audio-ramp-flat-source-review-v1/manifest.json',P/'audio-ramp-flat-v1/manifest.json',P/'gpu-recip-write-v1/manifest.json',P/'native-renderer-window-v1/run-cache64-timing-v1/manifest.json']
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def put(p,x):
 with p.open('x') as f:json.dump(x,f,indent=2,sort_keys=True);f.write('\n')
def inv(p):return {str(f.relative_to(p)):sha(f) for f in sorted(p.rglob('*')) if f.is_file()}
def check_proof(f):
 d=json.loads(f.read_text());assert d.get('passed',True)
 for n,h in d.get('files',{}).items():assert sha(f.parent/n)==h,(f,n)
 for field in ('evidence_sha256','inputs_sha256'):
  for n,h in d.get(field,{}).items():
   original=R/n
   relocation={
    'tools/experiments/os30_fps25_20261005/native_renderer_main.cpp':P/'native-renderer-window-v1/runner-cache64-timing-v1/main.cpp',
    'tools/experiments/os30_fps25_20261005/phase10_native_renderer_run.py':P/'native-renderer-window-v1/runner-cache64-timing-v1/recipe.py',
   }
   if f==P/'native-renderer-window-v1/run-cache64-timing-v1/manifest.json' and field=='inputs_sha256' and n in relocation:
    frozen=relocation[n]
    assert d['evidence_sha256'][str(frozen.relative_to(R))]==h
    assert sha(frozen)==h,(f,n,'frozen original runner')
   else:assert sha(original)==h,(f,n)
def prepare():
 for f in PROOFS:check_proof(f)
 before=json.loads((BASE/'map-core/prepared.json').read_text())['source_sha256'];assert inv(BASE/'frozen')==before
 assert json.loads((P/'gpu-recip-write-v1/result.json').read_text())['passed']
 O.mkdir(exist_ok=False);shutil.copytree(BASE/'frozen',O/'frozen')
 patch=''
 for n,(src,h) in SOURCES.items():
  assert sha(src)==h;shutil.copy2(src,O/'frozen'/n)
  patch+=''.join(difflib.unified_diff((BASE/'frozen'/n).read_text().splitlines(True),src.read_text().splitlines(True),fromfile='cache64/'+n,tofile='cache64-timing/'+n))
 after=inv(O/'frozen');assert sorted(n for n in before if before[n]!=after[n])==sorted(SOURCES)
 top=(O/'frozen/src/fpga/targets/pocket/core_top.v').read_text();assert top.count('.WAYS(2), .SET_BITS(6), .WORD_BITS(4)')==1
 # Exact combined actual-CPU replay agrees with plain cache64 control.
 native=P/'native-renderer-window-v1';control=native/'run-cache64-v1';candidate=native/'run-cache64-timing-v1';exact=['result.json','pc.csv','mmio.tsv','frame.bin','commands.bin','entry-regs.bin']
 assert all((control/n).read_bytes()==(candidate/n).read_bytes() for n in exact)
 evidence=PROOFS+[control/n for n in exact]+[candidate/n for n in exact]
 put(O/'prepared.json',dict(scope=__doc__,source_sha256=after,parent_source_sha256=before,changed_sources=sorted(SOURCES),component_cpu_sha256=after[CPU],gpu_capacity_kib=8,cpu_i_kib=64,cpu_d_kib=64,seed=2,configured_mhz=100,combined_native_all_bytes_and_cycles_exact=True,full_fit_authorized=False,production_modified=False,recipe_sha256=sha(pathlib.Path(__file__)),evidence_sha256={str(p.relative_to(R)):sha(p) for p in evidence}))
 (O/'candidate.patch').write_text(patch);shutil.copy2(__file__,O/'recipe.py');print('PREPARED',sha(O/'prepared.json'),flush=True)
def checked():
 d=json.loads((O/'prepared.json').read_text());assert inv(O/'frozen')==d['source_sha256'];assert sha(pathlib.Path(__file__))==d['recipe_sha256']
 for n,h in d['evidence_sha256'].items():assert sha(R/n)==h,n
 for f in PROOFS:check_proof(f)
 return d
def run():
 d=checked();m=O/'map-core';m.mkdir(exist_ok=False);sys.path.insert(0,str(R/'tools'));import sweep_target_matrix as sweep
 row=copy.deepcopy(json.loads((P/'compact-lod5-core-v7/fit-seed2/inventory.json').read_text())[0]);row['cpu_sha256']=d['component_cpu_sha256'];job,project=sweep.prepare(O/'frozen',m/'job',row,2,4)
 qsf=job/(project+'.qsf');old=next((BASE/'map-core/job').rglob('ap_core.qsf'));assert qsf.read_bytes()==old.read_bytes()
 cmd=sweep.docker(m,'pocket',job,['quartus_map',project]);put(m/'prepared.json',dict(command=cmd,qsf_sha256=sha(qsf),prepared_sha256=sha(O/'prepared.json'),fitter_invoked=False));start=time.monotonic()
 with (m/'map.log').open('x') as f:r=subprocess.run(cmd,stdout=f,stderr=subprocess.STDOUT)
 put(m/'completion.json',dict(returncode=r.returncode,seconds=time.monotonic()-start,passed=r.returncode==0));checked();print('MAP',r.returncode,flush=True);assert r.returncode==0
if __name__=='__main__':globals()[sys.argv[1]]()
