#!/usr/bin/env python3
"""Stage one ADDI/LUI/AUIPC-only lane experiment from the frozen immediate source."""
import argparse
import difflib
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tarfile

ROOT=Path(__file__).resolve().parents[4]
ART=ROOT/'docs/measurements/os30-dual-20261004'
SNAP=ART/'generator-qualified-v1'
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--output',type=Path,default=ART/'generator-v10')
args=parser.parse_args()
digest=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
recipe=json.loads((SNAP/'recipe.json').read_text())
assert digest(SNAP/'recipe.json')=='69ed091ca760fe0c5e0c02ca22a6233fdd6f4b664133852bb74fd20727168eab'
for name,sha in recipe['file_sha256'].items():assert digest(SNAP/name)==sha,name
profile=recipe['profiles']['immediate']
out=args.output.resolve();out.mkdir(parents=True,exist_ok=False)
source=out/'source';source.mkdir()
assert digest(SNAP/profile['source_archive'])==recipe['file_sha256'][profile['source_archive']]
with tarfile.open(SNAP/profile['source_archive'],'r:gz') as archive:
    archive.extractall(source,filter='data')
for name,sha in profile['source_file_sha256'].items():assert digest(source/name)==sha,name
changed=[]
def edit(name,replacements):
    path=source/name;before=path.read_text();after=before
    for old,new in replacements:
        assert after.count(old)==1,(name,old)
        after=after.replace(old,new,1)
    path.write_text(after)
    changed.append(dict(path=name,base_sha256=hashlib.sha256(before.encode()).hexdigest(),modified_sha256=digest(path)))
    return ''.join(difflib.unified_diff(before.splitlines(True),after.splitlines(True),fromfile='a/'+name,tofile='b/'+name))
patch=edit('src/main/scala/vexiiriscv/Param.scala',[
    ('  var lane1ImmediateOnly = false','  var lane1ImmediateOnly = false\n  var lane1AddressOnly = false'),
    ('    opt[Unit]("lane1-immediate-only") action { (v, c) => lane1AluOnly = true; lane1ImmediateOnly = true }','    opt[Unit]("lane1-immediate-only") action { (v, c) => lane1AluOnly = true; lane1ImmediateOnly = true }\n    opt[Unit]("lane1-address-only") action { (v, c) => lane1AluOnly = true; lane1ImmediateOnly = true; lane1AddressOnly = true }'),
    ('new IntAluPlugin(early1, formatAt = 0, withRegisterOperands = !lane1ImmediateOnly)','new IntAluPlugin(early1, formatAt = 0, withRegisterOperands = !lane1ImmediateOnly, withLogicAndCompare = !lane1AddressOnly)')])
int_path='src/main/scala/vexiiriscv/execute/IntAluPlugin.scala'
alu=(source/int_path).read_text()
start=alu.index('    add(Rvi.SLTI )');end=alu.index('\n\n    add(Rvi.LUI ',start)
immediate_logic=alu[start:end]
assert immediate_logic.count('    add(')==5
start=alu.index('      val bitwise =');end=alu.index('\n    }',start)
datapath=alu[start:end]
assert 'val result = bitwise |' in datapath
patch+=edit(int_path,[
    ('var withRegisterOperands : Boolean = true) extends','var withRegisterOperands : Boolean = true,\n                   var withLogicAndCompare : Boolean = true) extends'),
    ('    awaitBuild()','    awaitBuild()\n    require(withLogicAndCompare || (!withRegisterOperands && Riscv.XLEN.get == 32), "Address-only lane requires RV32 and no register-register operations")'),
    (immediate_logic,'    if(withLogicAndCompare) {\n'+immediate_logic+'\n    }'),
    (datapath,'      if(withLogicAndCompare) {\n'+datapath+'\n      } else {\n        ALU_RESULT := srcp.ADD_SUB.asBits\n      }')])
(out/'address-only.patch').write_text(patch)
flags=profile['flags']+['--lane1-address-only']
cache=ROOT/'tools/.vexii-home'
command=['docker','run','--rm','--user',f'{os.getuid()}:{os.getgid()}',
         '--env','VEXIIRISCV_DEBUG_PARAM=0','--env','SPINALHDL_FROM_SOURCE=1',
         '-v',f'{out}:{out}','-v',f'{cache}:/vexiihome','--tmpfs','/tmp:exec',
         '-w',str(source),recipe['docker_image_id'],'sbt','-Duser.home=/vexiihome',
         '-Dsbt.boot.directory=/vexiihome/.sbt/boot','-Dsbt.global.base=/vexiihome/.sbt',
         '-Dsbt.ivy.home=/vexiihome/.ivy2','-Dsbt.server.forcestart=true','--batch',
         'Test/runMain vexiiriscv.Generate '+' '.join(flags)]
manifest=dict(profile='rvc-skid-address-only',source_snapshot_recipe_sha256=digest(SNAP/'recipe.json'),
              source_changes=changed,patch_sha256=digest(out/'address-only.patch'),
              flags=flags,command=command,source_files_verified=len(profile['source_file_sha256']),
              lane1_instructions=['ADDI','LUI','AUIPC'],lane0_unchanged=True,
              cache_configuration='I32/D128',predictors='BTB256/GShare1KiB')
(out/'source-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
def run(command,log):
    with (out/log).open('w') as f:subprocess.run(command,check=True,stdout=f,stderr=subprocess.STDOUT)
run(command,'generate.log')
raw=(source/'VexiiRiscv.v').read_text()
(out/'generated.v').write_text(raw)
candidate=out/'VexiiRiscv.v'
text,count=re.subn(r'  wire +execute_freeze_valid;','  (* maxfan = 16 *) wire execute_freeze_valid;',raw)
assert count==1
text=text.replace('= {$urandom};','= 0;');assert '$urandom' not in text
text,count=re.subn(r'^module VexiiRiscv \(','(* altera_attribute = "-name AUTO_SHIFT_REGISTER_RECOGNITION OFF" *)\nmodule VexiiRiscv (',text,flags=re.M);assert count==1
candidate.write_text(text)
for helper in profile['helpers']:run(['perl',str(SNAP/'helpers'/helper),str(candidate)],helper+'.log')
postprocessed=digest(candidate)
run(['perl',str(SNAP/'control-transform.pl'),str(candidate)],'control-transform.log')
manifest.update(generated_sha256=digest(out/'generated.v'),postprocessed_sha256=postprocessed,
                candidate_sha256=digest(candidate),control_transform_sha256=digest(SNAP/'control-transform.pl'),
                whole_cpu_validation='pending')
(out/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
print(json.dumps(manifest,indent=2))
