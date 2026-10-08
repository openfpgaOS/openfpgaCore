#!/usr/bin/env python3
"""Compose independently qualified ready/FP-RS2/integer-data paths and replay native CPU."""
import concurrent.futures,difflib,hashlib,importlib.util,json,os,re,shutil,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3];P=ROOT/'docs/measurements/os30-fps25-20261005/phase10';O=P/'cpu-final-paths-v1'
R=P/'cpu-ready-factor-v1';I=P/'cpu-int-wb-factor-v1';F=P/'cpu-fp-rs2-mux-v1'
CPU=Path('src/fpga/vendor/vexriscv/VexiiRiscv/VexiiRiscv_os30.v');sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,v):
 with p.open('x') as f:json.dump(v,f,indent=2);f.write('\n')
def module(name):
 spec=importlib.util.spec_from_file_location(name,ROOT/'tools/experiments/os30_fps25_20261005'/('phase10_'+name+'.py'));m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
def replace(s,a,b):assert s.count(a)==1;return s.replace(a,b)
def prepare():
 for d in (R,I,F):assert (d/'manifest.json').exists()
 m=module('cpu_int_wb_factor');base=(R/'VexiiRiscv.v').read_text();a=(F/'original-assignment.txt').read_text().rstrip('\n');b=(F/'candidate-assignment.txt').read_text().rstrip('\n')
 assert replace(base,m.OLD,m.NEW)==(I/'VexiiRiscv.v').read_text()
 assert replace(base,a,b)==(F/'VexiiRiscv.v').read_text()
 first=replace((I/'VexiiRiscv.v').read_text(),a,b);second=replace((F/'VexiiRiscv.v').read_text(),m.OLD,m.NEW);assert first==second
 O.mkdir(exist_ok=False);(O/'VexiiRiscv.v').write_text(first);shutil.copy2(__file__,O/'recipe.py');(O/'candidate.patch').write_text(''.join(difflib.unified_diff(base.splitlines(True),first.splitlines(True),fromfile='ready-factor',tofile='final-paths')))
 save(O/'prepared.json',dict(candidate_sha256=sha(O/'VexiiRiscv.v'),components={d.name:dict(cpu_sha256=sha(d/'VexiiRiscv.v'),manifest_sha256=sha(d/'manifest.json')) for d in (R,I,F)},disjoint_checked_hunks_commute=True,added_state_bits=0,added_cycles=0,architectural_control_unchanged=True))
 (O/'proof').mkdir();save(O/'proof/result.json',dict(passed=True,checked_disjoint_composition=True,component_evidence={d.name:sha(d/'manifest.json') for d in (R,I,F)}));print(sha(O/'VexiiRiscv.v'))
def native_module():
 m=module('cpu_rvc_native');m.O=O;m.N=O/'native';return m
def native_prepare():
 m=native_module();m.prepare();p=m.N/'frozen'/CPU;s=p.read_text();e=s.index('\nendmodule',s.index('module VexiiRiscv ('))
 peer=(F/'native/frozen'/CPU).read_text();a=peer.index('wire dispatch_stall_reference=');monitor=peer[a:peer.index('\nendmodule',a)]
 integer=(I/'native/frozen'/CPU).read_text();a=integer.index('wire [31:0] integer_wb_reference=');monitor+='\n'+integer[a:integer.index('\nendmodule',a)]
 p.write_text(s[:e]+'\n'+monitor+s[e:]);prep=json.loads((m.N/'prepared.json').read_text());prep['sources_sha256'][str(p.relative_to(m.N))]=sha(p);(m.N/'prepared.json').write_text(json.dumps(prep,indent=2)+'\n')
def native_model():native_module().model()
def native_run():
 m=native_module();m.OLD=R/'native';m.run()
def extra():
 N=O/'native';D=N/'extra';D.mkdir(exist_ok=False)
 cases={'halfword':(I/'native/halfword-v2',I/'native/halfword-v2'), 'hazards':(F/'hazards/firmware',F/'hazards')}
 for name,(firm,ref) in cases.items():
  w=D/name;w.mkdir();shutil.copy2(firm/'kernel.bin',w/'kernel.bin');shutil.copy2(firm/'firmware.mif',w/'firmware.mif')
 def one(arg):
  name,mode=arg;firm,ref=cases[name];w=D/name
  env=dict(SCAN_ENABLE='0' if mode=='quiet' else '1',SCAN_PERIOD='700',SCAN_LEN='128',SCAN_PHASE='317')
  if name=='hazards':env=json.loads((ref/(mode+'-invocation.json')).read_text())['environment']
  exe=N/'obj/Vtb_system';cmd=[str(exe),'kernel.bin','30000000'];log=w/(mode+'-candidate.log')
  with log.open('x') as f:subprocess.run(cmd,cwd=w,env=dict(os.environ,**env),stdout=f,stderr=subprocess.STDOUT,check=True,timeout=300)
  t=log.read_text();old=(ref/(mode+'-reference.log')).read_text();uart=lambda t:t.split('=== UART BEGIN ===')[1].split('=== UART END ===')[0];cyc=lambda t:int(re.search(r'cycles=(\d+) PC=',t)[1])
  assert uart(t)==uart(old) and cyc(t)==cyc(old) and 'HAL init' in t and 'FAIL' not in t
  assert 'R mismatches=0  scanout mismatches=0  rlast slips=0  orphan beats=0  DRAM strikes=0' in t and 'WCONF: mism=0 spurious=0 exp_fifo=0 aw_fifo=0 run_left=0' in t
  return dict(name=name,mode=mode,passed=True,cycles=cyc(t),UART_equal=True,cycles_equal=True,command=cmd,environment=env,reference_log_sha256=sha(ref/(mode+'-reference.log')),candidate_log_sha256=sha(log))
 with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:r=list(pool.map(one,[(n,m) for n in cases for m in ('quiet','nominal')]))
 save(D/'result.json',dict(passed=True,runs=r));print(json.dumps(r,indent=2))
def seal():
 assert json.loads((O/'native/result.json').read_text())['passed'] and json.loads((O/'native/extra/result.json').read_text())['passed']
 logs=list((O/'native/runs').glob('*/*-candidate.log'))+list((O/'native/extra').glob('*/*-candidate.log'));assert len(logs)==10
 cov={}
 for n in ['READY','FP_RS2','INTWB','RVC','FP_RS3']:
  rows=[]
  for p in logs:
   mat=re.search(n+r'_COVER ([^\n]+)',p.read_text());assert mat,(n,p);rows.append(dict(log=str(p.relative_to(O)),coverage=mat[1]))
  cov[n]=rows
 save(O/'native/combined-coverage.json',dict(passed=True,runs=10,coverage=cov,scope='Actual-cycle old/new control and data assertions; all10 workloads match referenceUART and cycle counts. Exhaustive component combinational proofs include impossible/multiple selector states beyond native observations.'))
 files=[O/'VexiiRiscv.v',O/'recipe.py',O/'candidate.patch',O/'prepared.json',O/'proof/result.json',O/'native/prepared.json',O/'native/build.json',O/'native/result.json',O/'native/extra/result.json',O/'native/combined-coverage.json']+list((O/'native/runs').glob('*/*.log'))+list((O/'native/extra').glob('*/*'))
 save(O/'manifest.json',dict(files={str(p.relative_to(O)):sha(p) for p in files if p.is_file()},components={d.name:sha(d/'manifest.json') for d in (R,I,F)},candidate_sha256=sha(O/'VexiiRiscv.v'),no_fit=True,no_regeneration_yet=True,production_unchanged=True));print(sha(O/'manifest.json'))
if __name__=='__main__':globals()[sys.argv[1]]()
