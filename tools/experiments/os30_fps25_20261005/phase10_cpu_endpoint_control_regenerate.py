#!/usr/bin/env python3
"""One real private os30 Scala generation through the checked selected helper chain."""
import hashlib,json,os,shutil,subprocess,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3];P=ROOT/'docs/measurements/os30-fps25-20261005/phase10'
O=P/'cpu-endpoint-control-regenerate-v1';PROPOSAL=P/'cpu-endpoint-control-repro-v1';Q=P/'cpu-queues-v1';V=ROOT/'src/fpga/vendor/vexriscv'
EXPECTED='6f50382aa82e6684551756e6c2473492d0bd915a0c94b2aeae9ac4a47e62dec4'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,v):
 with p.open('x') as f:json.dump(v,f,indent=2);f.write('\n')
def sources(src):
 out={str(p.relative_to(src)):sha(p) for p in src.rglob('*.scala') if 'target' not in p.relative_to(src).parts}
 for n in ('build.sbt','project/build.properties'):out[n]=sha(src/n)
 return out
def prepare():
 for n,h in json.loads((PROPOSAL/'manifest.json').read_text())['files'].items():assert sha(PROPOSAL/n)==h,n
 O.mkdir(exist_ok=False);vendor=O/'vendor';vendor.mkdir();src=vendor/'VexiiRiscv'
 shutil.copytree(Q/'source',src,ignore=shutil.ignore_patterns('target','.git','VexiiRiscv*.v','vexii_active.v'))
 original=json.loads((Q/'sources.json').read_text());assert sources(src)==original
 for h in V.glob('*.pl'):shutil.copy2(h,vendor/h.name)
 proposed=PROPOSAL/'proposed/src/fpga/vendor/vexriscv'
 for n in ('generate_vexii.sh','factor_os30_fp_rs3.pl','factor_os30_frontend.pl','factor_os30_load_forward.pl','factor_os30_control_paths.pl','factor_os30_parallel_paths.pl','factor_os30_endpoint_paths.pl','os30-rvc-pipeline.patch'):shutil.copy2(proposed/n,vendor/n)
 (vendor/'configs').mkdir();shutil.copy2(proposed/'configs/os30.cfg',vendor/'configs/os30.cfg')
 shutil.copytree(V/'patches',vendor/'patches')
 # Copy, never hard-link, dependency caches so generation cannot mutate shared inputs.
 subprocess.run(['cp','-a','--reflink=auto',str(ROOT/'tools/.vexii-home'),str(O/'dependency-cache')],check=True)
 image=json.loads((Q/'generated.json').read_text())['docker_image']
 cmd=['docker','run','--rm','--name','phase10-cpu-endpoint-control-regenerate-v1','--network','none','--user',f'{os.getuid()}:{os.getgid()}',
      '--env','HOME=/vexiihome','--env','VEXIIRISCV_DEBUG_PARAM=0','--env','SPINALHDL_FROM_SOURCE=1',
      '--env','JAVA_TOOL_OPTIONS=-Duser.home=/vexiihome -Dsbt.boot.directory=/vexiihome/.sbt/boot -Dsbt.global.base=/vexiihome/.sbt -Dsbt.ivy.home=/vexiihome/.ivy2',
      '-v',f'{vendor}:{vendor}','-v',f'{O}/dependency-cache:/vexiihome','--tmpfs','/tmp:exec','-w',str(src),image,'bash','../generate_vexii.sh','os30']
 save(O/'prepared.json',dict(command=cmd,pinned_image=image,expected_output_sha256=EXPECTED,source_manifest=original,source_manifest_sha256=sha(Q/'sources.json'),proposal_manifest_sha256=sha(PROPOSAL/'manifest.json'),
      generator_inputs_sha256={str(p.relative_to(vendor)):sha(p) for p in sorted(vendor.rglob('*')) if p.is_file() and src not in p.parents},
      production_guard_sha256={str(p.relative_to(ROOT)):sha(p) for p in [V/'generate_vexii.sh',V/'configs/os30.cfg',V/'VexiiRiscv/VexiiRiscv_os30.v']},network_disabled=True,private_dependency_cache=True))
 shutil.copy2(__file__,O/'recipe.py');print('prepared',O)
def generate():
 p=json.loads((O/'prepared.json').read_text());src=O/'vendor/VexiiRiscv';assert sources(src)==p['source_manifest']
 for n,h in p['generator_inputs_sha256'].items():assert sha(O/'vendor'/n)==h,n
 t=time.monotonic()
 with (O/'generate.log').open('x') as f:r=subprocess.run(p['command'],stdout=f,stderr=subprocess.STDOUT,timeout=900)
 output=src/'VexiiRiscv_os30.v';result=dict(returncode=r.returncode,elapsed_seconds=time.monotonic()-t,output_exists=output.exists(),expected_output_sha256=EXPECTED)
 if output.exists():result['output_sha256']=sha(output);result['exact_match']=sha(output)==EXPECTED
 for n,h in p['production_guard_sha256'].items():assert sha(ROOT/n)==h,n
 assert sources(src)==p['source_manifest'],'generator source changed unexpectedly'
 assert not list((O/'vendor').glob('.os30-rvc.*')), 'isolated source cleanup'
 result['shared_source_unchanged']=True;result['production_unchanged']=True;result['passed']=r.returncode==0 and result.get('exact_match',False)
 save(O/'result.json',result);print(json.dumps(result,indent=2));assert result['passed']
def seal():
 p=json.loads((O/'prepared.json').read_text());r=json.loads((O/'result.json').read_text())
 assert r['passed'] and r['exact_match'] and r['output_sha256']==EXPECTED
 assert sha(PROPOSAL/'manifest.json')==p['proposal_manifest_sha256']
 for n,h in p['production_guard_sha256'].items():assert sha(ROOT/n)==h,n
 assert sources(O/'vendor/VexiiRiscv')==p['source_manifest']
 for n,h in p['generator_inputs_sha256'].items():assert sha(O/'vendor'/n)==h,n
 with (O/'REPORT.txt').open('x') as f:f.write(
  'PASS: one actual offline Scala/sbt os30 elaboration followed by the complete checked I64/D64 registered-RVC source overlay and retained four-hunk helper chain plus the exact two-hunk integer forwarding pass and five-hunk branch/LSU-sign/RS3-tag/decode-ready composition followed by the exact seven-hunk uniform FP/ready/GShare composition and four-hunk frontend endpoint/GShare BTB prehash composition.\n'
  f'CPU SHA256: {EXPECTED}\nElapsed {r["elapsed_seconds"]:.3f} seconds. Exact byte identity to cpu-endpoint-control-v1.\n'
  'ISA and five qualified queue arguments unchanged; only os30 selects cache sets512/512, RVC stage1, decoder2, dispatcher3; obsolete bitplanes and dispatch2 factors are omitted. os30_worker and other variants unchanged.\n'
  'Private source/dependency copies only. Exact command, all Scala source and generator inputs are bound. Production rehashed unchanged. No fitter or deployment.\n')
 names={'REPORT.txt','generate.log','prepared.json','recipe.py','result.json','vendor/VexiiRiscv/VexiiRiscv_os30.v'}
 names.update('vendor/VexiiRiscv/'+n for n in p['source_manifest'])
 names.update('vendor/'+n for n in p['generator_inputs_sha256'])
 save(O/'manifest.json',{n:sha(O/n) for n in sorted(names)})
 print(sha(O/'manifest.json'))

if __name__=='__main__':globals()[sys.argv[1]]()
