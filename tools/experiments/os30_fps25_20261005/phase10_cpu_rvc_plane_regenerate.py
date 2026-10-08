#!/usr/bin/env python3
"""One real private os30 Scala generation through the checked selected helper chain."""
import hashlib,json,os,shutil,subprocess,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3];P=ROOT/'docs/measurements/os30-fps25-20261005/phase10'
O=P/'cpu-os30-regenerate-v5';PROPOSAL=P/'cpu-os30-repro-v5';Q=P/'cpu-queues-v1';V=ROOT/'src/fpga/vendor/vexriscv'
EXPECTED='8fca28712f731dab4b8c81c3e57ddf2529c6bf4222c292f120e4108c758689f1'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,v):
 with p.open('x') as f:json.dump(v,f,indent=2);f.write('\n')
def sources(src):
 out={str(p.relative_to(src)):sha(p) for p in src.rglob('*.scala') if 'target' not in p.relative_to(src).parts}
 for n in ('build.sbt','project/build.properties'):out[n]=sha(src/n)
 return out
def prepare():
 O.mkdir(exist_ok=False);vendor=O/'vendor';vendor.mkdir();src=vendor/'VexiiRiscv'
 shutil.copytree(Q/'source',src,ignore=shutil.ignore_patterns('target','.git','VexiiRiscv*.v','vexii_active.v'))
 original=json.loads((Q/'sources.json').read_text());assert sources(src)==original
 for h in V.glob('*.pl'):shutil.copy2(h,vendor/h.name)
 proposed=PROPOSAL/'proposed/src/fpga/vendor/vexriscv'
 for n in ('generate_vexii.sh','factor_os30_fp_rs3.pl','factor_os30_frontend.pl'):shutil.copy2(proposed/n,vendor/n)
 (vendor/'configs').mkdir();shutil.copy2(proposed/'configs/os30.cfg',vendor/'configs/os30.cfg')
 shutil.copytree(V/'patches',vendor/'patches')
 # Copy, never hard-link, dependency caches so generation cannot mutate shared inputs.
 subprocess.run(['cp','-a','--reflink=auto',str(ROOT/'tools/.vexii-home'),str(O/'dependency-cache')],check=True)
 image=json.loads((Q/'generated.json').read_text())['docker_image']
 cmd=['docker','run','--rm','--name','phase10-cpu-os30-regenerate-v5','--network','none','--user',f'{os.getuid()}:{os.getgid()}',
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
 result['production_unchanged']=True;result['passed']=r.returncode==0 and result.get('exact_match',False)
 save(O/'result.json',result);print(json.dumps(result,indent=2));assert result['passed']
if __name__=='__main__':globals()[sys.argv[1]]()
