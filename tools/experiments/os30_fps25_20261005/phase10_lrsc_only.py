#!/usr/bin/env python3
"""Private generator experiment: preserve LR/SC, omit unused arithmetic AMOs."""
import argparse,difflib,hashlib,importlib.util,json,os,re,shutil,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
VENDOR=ROOT/'src/fpga/vendor/vexriscv'
OUT=ROOT/'docs/measurements/os30-fps25-20261005/phase10/lrsc-only-v1'
A=VENDOR/'VexiiRiscv/VexiiRiscv_os30.v'
ASH='33895e9a2fc141d8da49bb75e082e06964a49e328b6d87363516ac6f9ab26119'
HELPERS=['retime_fetch_reads.pl','retime_aligner_pc.pl','factor_fetch_ready.pl','retime_dcache_writes.pl']
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,x):p.write_text(json.dumps(x,indent=2)+'\n')
def replace(s,a,b):assert s.count(a)==1,(s.count(a),a[:100]);return s.replace(a,b)
def flags():
 p=ROOT/'tools/experiments/os30_dual_20261004/generator/prepare.py';spec=importlib.util.spec_from_file_location('generator_base',p);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
 return [a.replace('--lsu-l1-sets=512','--lsu-l1-sets=1024') for a in m.BASE_FLAGS]
def sources(source):
 files={str(p.relative_to(source)):sha(p) for p in source.rglob('*.scala') if 'target' not in p.relative_to(source).parts}
 for n in ['build.sbt','project/build.properties']:files[n]=sha(source/n)
 return files

def prepare():
 assert sha(A)==ASH
 OUT.mkdir(exist_ok=False);stage=OUT/'control';stage.mkdir()
 shutil.copytree(VENDOR/'VexiiRiscv',stage/'source',ignore=shutil.ignore_patterns('.git','VexiiRiscv*.v','*.vcd','simWorkspace'))
 (OUT/'helpers').mkdir()
 for n in HELPERS:shutil.copy2(VENDOR/n,OUT/'helpers'/n)
 save(stage/'sources.json',sources(stage/'source'))
 save(OUT/'recipe.json',dict(scope=__doc__,private_only=True,original_cpu_sha256=ASH,flags=flags(),helper_sha256={n:sha(OUT/'helpers'/n) for n in HELPERS},production_config_sha256=sha(VENDOR/'configs/os30.cfg'),production_generator_sha256=sha(VENDOR/'generate_vexii.sh')))
 print('private unchanged control prepared')

def generate(label):
 stage=OUT/label;source=stage/'source'
 expected=json.loads((stage/'sources.json').read_text())
 assert sources(source)==expected
 args=flags()+(['--lsu-lrsc-only'] if label=='candidate' else [])
 image=subprocess.check_output(['docker','image','inspect','openfpgaos-vexii','--format','{{.Id}}'],text=True).strip()
 cmd=['docker','run','--rm','--name','phase10-lrsc-'+label,'--user',f'{os.getuid()}:{os.getgid()}','--env','VEXIIRISCV_DEBUG_PARAM=0','--env','SPINALHDL_FROM_SOURCE=1','-v',f'{OUT}:{OUT}','-v',f'{ROOT}/tools/.vexii-home:/vexiihome','--tmpfs','/tmp:exec','-w',str(source),image,'sbt','-Duser.home=/vexiihome','-Dsbt.boot.directory=/vexiihome/.sbt/boot','-Dsbt.global.base=/vexiihome/.sbt','-Dsbt.ivy.home=/vexiihome/.ivy2','-Dsbt.server.forcestart=true','--batch','Test/runMain vexiiriscv.Generate '+' '.join(args)]
 with (stage/'generate.log').open('x') as log:subprocess.run(cmd,stdout=log,stderr=subprocess.STDOUT,check=True)
 raw=(source/'VexiiRiscv.v').read_text();header,body=raw.split('\n\n',1)
 assert all(re.fullmatch(r'// (Generator : .*|Component : VexiiRiscv|Git hash  : .*)',line) for line in header.splitlines())
 # Private clone has no .git: normalize only provenance comments to retained A.
 data=A.read_text().split('\n\n',1)[0]+'\n\n'+body
 data=replace(data,'  wire                execute_freeze_valid;','  (* maxfan = 16 *) wire                execute_freeze_valid;')
 data=data.replace('= {$urandom};','= 0;');assert '$urandom' not in data
 data=replace(data,'\nmodule VexiiRiscv (','\n(* altera_attribute = "-name AUTO_SHIFT_REGISTER_RECOGNITION OFF" *)\nmodule VexiiRiscv (')
 output=stage/'VexiiRiscv.v';output.write_text(data)
 for n in HELPERS:
  with (stage/(n+'.log')).open('x') as log:subprocess.run(['perl',str(OUT/'helpers'/n),str(output)],stdout=log,stderr=subprocess.STDOUT,check=True)
 result=dict(label=label,flags=args,command=cmd,docker_image=image,source_manifest_sha256=sha(stage/'sources.json'),rtl_sha256=sha(output),rtl_bytes=output.stat().st_size,normalized_provenance_only=True,retained_A_exact=sha(output)==ASH)
 save(stage/'generated.json',result)
 if label=='control':assert result['retained_A_exact'],'Unchanged generator does not reproduce A; stop.'
 else:
  assert 'rva_alu_' not in output.read_text() and 'rva_lrsc_reserved' in output.read_text()
 print(json.dumps(result))

def patch():
 control=OUT/'control';assert json.loads((control/'generated.json').read_text())['retained_A_exact']
 stage=OUT/'candidate';stage.mkdir(exist_ok=False);shutil.copytree(control/'source',stage/'source',ignore=shutil.ignore_patterns('VexiiRiscv*.v'))
 root=stage/'source/src/main/scala/vexiiriscv';changes={}
 def edit(rel,fn):
  p=root/rel;old=p.read_text();new=fn(old);assert new!=old;p.write_text(new)
  changes[str(p.relative_to(stage/'source'))]=dict(before_sha256=hashlib.sha256(old.encode()).hexdigest(),after_sha256=sha(p))
  return ''.join(difflib.unified_diff(old.splitlines(True),new.splitlines(True),fromfile='a/'+rel,tofile='b/'+rel))
 patches=[]
 def param(s):
  s=replace(s,'  var withRvcbmLlc = false','  var withRvcbmLlc = false\n  var lsuLrscOnly = false // Private os30 area probe: no arithmetic AMO ISA')
  s=replace(s,'    opt[Unit]("with-rva")', '    opt[Unit]("lsu-lrsc-only") action { (_, _) => lsuLrscOnly = true } text("Retain LR/SC only; omit arithmetic AMOs and clear misa.A")\n    opt[Unit]("with-rva")')
  s=replace(s,'    if(!lsuL1Enable) {','    assert(!lsuLrscOnly || (lsuL1Enable && withRva), "LR/SC-only requires cached LSU and internal A support")\n    if(!lsuL1Enable) {')
  s=replace(s,'        withRva = withRva,','        withRva = withRva,\n        withAmo = !lsuLrscOnly,')
  return s
 patches.append(edit('Param.scala',param))
 patches.append(edit('riscv/Misc.scala',lambda s:replace(s,'RVF, RVA, RVZba','RVF, RVA, RVAMO, RVZba')))
 def agu(s):
  s=replace(s,'    withRvcbm : Boolean = false\n','    withRvcbm : Boolean = false,\n    withAmo : Boolean = true\n')
  first=s.index('    uops ++= List(\n      Rvi.AMOSWAPW');end=s.index('    for (amo <- uops)',first)
  s=s[:first]+'    if(withAmo) {\n'+''.join('  '+line+'\n' for line in s[first:end].splitlines())+'    }\n'+s[end:]
  return s
 patches.append(edit('execute/lsu/Agu.scala',agu))
 def lsu(s):
  s=replace(s,'                var storeBufferOps : Int = 0) extends','                var storeBufferOps : Int = 0,\n                var withAmo : Boolean = true) extends')
  s=replace(s,'    Riscv.RVA.set(withRva)','    Riscv.RVA.set(withRva)\n    Riscv.RVAMO.set(withRva && withAmo)')
  s=replace(s,'new AguFrontend(layer, host, withRvcbm = withCbm)','new AguFrontend(layer, host, withRvcbm = withCbm, withAmo = withAmo)')
  start=s.index('        val srcBuffer = RegNext[Bits]');end=s.index('\n        assert(Global.HART_COUNT',start)
  s=s[:start]+'        val arithmetic = withAmo generate new Area {\n'+''.join('  '+line+'\n' for line in s[start:end].splitlines())+'        }\n'+s[end:]
  return s
 patches.append(edit('execute/lsu/LsuPlugin.scala',lsu))
 patches.append(edit('execute/lsu/LsuCachelessPlugin.scala',lambda s:replace(s,'    Riscv.RVA.set(withAmo)','    Riscv.RVA.set(withAmo)\n    Riscv.RVAMO.set(withAmo)')))
 patches.append(edit('misc/PrivilegedPlugin.scala',lambda s:replace(s,"    if (RVA) addMisa('A')","    if (RVA && RVAMO) addMisa('A') // LR/SC alone is not the full A extension")))
 (stage/'candidate.patch').write_text(''.join(patches));save(stage/'changes.json',changes);save(stage/'sources.json',sources(stage/'source'))
 print('private candidate generator patched')

if __name__=='__main__':
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=['prepare','control','patch','candidate']);a=p.parse_args()
 if a.action=='prepare':prepare()
 elif a.action=='patch':patch()
 else:generate(a.action)
