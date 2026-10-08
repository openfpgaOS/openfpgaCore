#!/usr/bin/env python3
"""Private CPU queue-capacity screen, preserving caches, ISA and arithmetic."""
import argparse,hashlib,json,os,re,shutil,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3];P=ROOT/'docs/measurements/os30-fps25-20261005/phase10'
OUT=P/'cpu-queues-v1';CONTROL=P/'lrsc-only-v1/control';HELPERS=P/'lrsc-only-v1/helpers'
ASH='33895e9a2fc141d8da49bb75e082e06964a49e328b6d87363516ac6f9ab26119'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,x):
 with p.open('x') as f:json.dump(x,f,indent=2);f.write('\n')
def sources(source):
 files={str(p.relative_to(source)):sha(p) for p in source.rglob('*.scala') if 'target' not in p.relative_to(source).parts}
 for n in ['build.sbt','project/build.properties']:files[n]=sha(source/n)
 return files
def prepare():
 assert sha(CONTROL/'VexiiRiscv.v')==ASH
 OUT.mkdir(exist_ok=False);shutil.copytree(CONTROL/'source',OUT/'source',ignore=shutil.ignore_patterns('VexiiRiscv*.v'))
 shutil.copytree(HELPERS,OUT/'helpers')
 original=json.loads((CONTROL/'generated.json').read_text())['flags']
 changes={'--fetch-l1-refill-count=2':'--fetch-l1-refill-count=1','--lsu-l1-refill-count=2':'--lsu-l1-refill-count=1','--lsu-l1-writeback-count=2':'--lsu-l1-writeback-count=1','--lsu-l1-store-buffer-slots=2':'--lsu-l1-store-buffer-slots=1','--lsu-l1-store-buffer-ops=16':'--lsu-l1-store-buffer-ops=4'}
 assert all(original.count(k)==1 for k in changes)
 source=sources(OUT/'source');assert source==json.loads((CONTROL/'sources.json').read_text())
 save(OUT/'sources.json',source)
 save(OUT/'recipe.json',dict(scope=__doc__,original_cpu_sha256=ASH,changes=changes,flags=[changes.get(x,x) for x in original],source_manifest_sha256=sha(OUT/'sources.json'),helper_sha256={p.name:sha(p) for p in (OUT/'helpers').iterdir()},control_generated_sha256=sha(CONTROL/'generated.json'),recipe_sha256=sha(Path(__file__)),production_modified=False))
def generate():
 recipe=json.loads((OUT/'recipe.json').read_text());assert sources(OUT/'source')==json.loads((OUT/'sources.json').read_text())
 image=subprocess.check_output(['docker','image','inspect','openfpgaos-vexii','--format','{{.Id}}'],text=True).strip()
 cmd=['docker','run','--rm','--name','phase10-cpu-queues-v1','--user',f'{os.getuid()}:{os.getgid()}','--env','VEXIIRISCV_DEBUG_PARAM=0','--env','SPINALHDL_FROM_SOURCE=1','-v',f'{OUT}:{OUT}','-v',f'{ROOT}/tools/.vexii-home:/vexiihome','--tmpfs','/tmp:exec','-w',str(OUT/'source'),image,'sbt','-Duser.home=/vexiihome','-Dsbt.boot.directory=/vexiihome/.sbt/boot','-Dsbt.global.base=/vexiihome/.sbt','-Dsbt.ivy.home=/vexiihome/.ivy2','-Dsbt.server.forcestart=true','--batch','Test/runMain vexiiriscv.Generate '+' '.join(recipe['flags'])]
 with (OUT/'generate.log').open('x') as f:subprocess.run(cmd,stdout=f,stderr=subprocess.STDOUT,check=True)
 raw=(OUT/'source/VexiiRiscv.v').read_text();header,body=raw.split('\n\n',1)
 assert all(re.fullmatch(r'// (Generator : .*|Component : VexiiRiscv|Git hash  : .*)',l) for l in header.splitlines())
 data=(CONTROL/'VexiiRiscv.v').read_text().split('\n\n',1)[0]+'\n\n'+body
 a='  wire                execute_freeze_valid;';assert data.count(a)==1;data=data.replace(a,'  (* maxfan = 16 *) wire                execute_freeze_valid;')
 data=data.replace('= {$urandom};','= 0;');assert '$urandom' not in data
 a='\nmodule VexiiRiscv (';assert data.count(a)==1;data=data.replace(a,'\n(* altera_attribute = "-name AUTO_SHIFT_REGISTER_RECOGNITION OFF" *)\nmodule VexiiRiscv (')
 output=OUT/'VexiiRiscv.v';output.write_text(data)
 for n,h in recipe['helper_sha256'].items():
  assert sha(OUT/'helpers'/n)==h
  with (OUT/(n+'.log')).open('x') as f:subprocess.run(['perl',str(OUT/'helpers'/n),str(output)],stdout=f,stderr=subprocess.STDOUT,check=True)
 save(OUT/'generated.json',dict(flags=recipe['flags'],command=cmd,docker_image=image,rtl_sha256=sha(output),rtl_bytes=output.stat().st_size,source_manifest_sha256=sha(OUT/'sources.json'),recipe_sha256=sha(OUT/'recipe.json'),scope=__doc__))
 print(sha(output))
if __name__=='__main__':
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=['prepare','generate']);a=p.parse_args();globals()[a.action]()
