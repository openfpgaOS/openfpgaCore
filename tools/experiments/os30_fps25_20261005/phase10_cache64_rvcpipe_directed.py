#!/usr/bin/env python3
"""Existing native IRQ/redirect/RVC-boundary fixture on the moved RVC pipeline."""
import concurrent.futures,hashlib,json,os,pathlib,re,shutil,subprocess,sys,time
R=pathlib.Path(__file__).resolve().parents[3];P=R/'docs/measurements/os30-fps25-20261005/phase10';O=P/'cpu-cache64-rvcpipe-directed-v1';B=P/'cpu-queues-v1/cpu-model';F=R/'docs/measurements/os30-dual-20261004/validation';C=P/'cpu-cache64-rvcpipe-v1/helper-audit/candidate-matched-helpers.v';CP=pathlib.Path('src/fpga/vendor/vexriscv/VexiiRiscv/VexiiRiscv_os30.v')
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def put(p,d):
 with p.open('x')as f:json.dump(d,f,indent=2,sort_keys=True);f.write('\n')
def prepare():
 assert json.loads((P/'cpu-cache64-rvcpipe-source-review-v1/review.json').read_text())['passed_for_native_screen']
 old=json.loads((B/'build.json').read_text());assert sha(C)=='aecbd7d8f63675333909df756c072b767bab269d29188190f80cfb3cde376295'
 O.mkdir(exist_ok=False);shutil.copytree(B/'frozen',O/'frozen')
 for n,h in old['source_sha256'].items():assert sha(O/n)==h,n
 shutil.copyfile(C,O/'frozen'/CP)
 cmd=[x.replace(str(B),str(O))for x in old['command']]
 put(O/'prepared.json',dict(command=cmd,candidate_sha256=sha(C),source_sha256={str(p.relative_to(O)):sha(p)for p in (O/'frozen').rglob('*')if p.is_file()},base_build_sha256=sha(B/'build.json'),source_review_sha256=sha(P/'cpu-cache64-rvcpipe-source-review-v1/manifest.json'),recipe_sha256=sha(pathlib.Path(__file__)),cycles_expected_to_change=True,map_fit=False))
 shutil.copyfile(__file__,O/'recipe.py')
def build():
 d=json.loads((O/'prepared.json').read_text())
 for n,h in d['source_sha256'].items():assert sha(O/n)==h,n
 start=time.monotonic()
 with(O/'build.log').open('x')as f:r=subprocess.run(d['command'],cwd=O/'frozen/src/fpga/test',stdout=f,stderr=subprocess.STDOUT)
 put(O/'build.json',dict(returncode=r.returncode,seconds=time.monotonic()-start,simulator_sha256=sha(O/'obj/Vtb_system')if r.returncode==0 else None));assert r.returncode==0
 print('BUILD PASS',flush=True)
def run():
 exe=O/'obj/Vtb_system';assert sha(exe)==json.loads((O/'build.json').read_text())['simulator_sha256'];out=O/'runs';out.mkdir()
 programs={'frontend':F/'frontend-hazards/frontend.bin','stress':F/'firmware/stress.bin'}
 for name,program in programs.items():
  w=out/name;w.mkdir();shutil.copyfile(program,w/'kernel.bin');shutil.copyfile(F/'firmware/firmware.mif',w/'firmware.mif')
 def one(job):
  name,mode=job;w=out/name;log=w/(mode+'.log');env=dict(SCAN_ENABLE='1'if mode=='scanout'else'0',SCAN_PERIOD='700',SCAN_LEN='128',SCAN_PHASE='317',STRIKE_SCAN_PERIOD='1000000000');cmd=[str(exe),'kernel.bin','30000000'];start=time.monotonic()
  with log.open('x')as f:r=subprocess.run(cmd,cwd=w,env=dict(os.environ,**env),stdout=f,stderr=subprocess.STDOUT,timeout=900)
  t=log.read_text();assert r.returncode==0 and 'FAIL'not in t and 'HAL init'in t
  assert 'WCONF: mism=0 spurious=0 exp_fifo=0 aw_fifo=0 run_left=0'in t
  assert 'R mismatches=0  scanout mismatches=0  rlast slips=0  orphan beats=0  DRAM strikes=0'in t
  uart=t.split('=== UART BEGIN ===')[1].split('=== UART END ===')[0]
  if name=='frontend':
   m=re.search(r'FRONTEND RESULT ([0-9a-f]+) ([0-9a-f]+) ([0-9a-f]+)',uart);assert m
   expected=json.loads((F/'frontend-hazards/manifest.json').read_text())['expected_hash'];assert m[1]==expected and 'FRONTEND PASS HAL init'in uart
   irq=int(m[3],16);assert irq>=50;extra=dict(result_hash=m[1],kernel_cycles=int(m[2],16),entry_offsets=[0,2,4,6,30,62],blocks=128,calls=2048)
  else:
   assert 'CPU stress PASS: integer, FPU, atomics, traps, cache. HAL init'in uart
   irq=int(re.search(r'IRQ count: ([0-9a-f]+)',uart)[1],16);assert irq>=200;extra={}
  result=dict(program=name,mode=mode,passed=True,interrupts=irq,command=cmd,environment=env,wall_seconds=time.monotonic()-start,program_sha256=sha(w/'kernel.bin'),log_sha256=sha(log),**extra);print(json.dumps(result),flush=True);return result
 with concurrent.futures.ThreadPoolExecutor(max_workers=2)as pool:results=list(pool.map(one,[(n,m)for n in programs for m in ['quiet','scanout']]))
 put(O/'result.json',dict(passed=True,runs=results,actual_native_CPU=True,cycles_equal_claim=False,scope='Existing two frozen binaries; IRQ, ECALL/MRET, redirects, wrong-path poison, mixed RVC/32-bit fetch/cache boundaries, integer/FPU/atomics/cache under SDRAM backpressure.',limits=['Isolated CPU/peripheral/SDRAM fixture; GPU renderer correctness belongs to separate native warm/cold runs.','No arbitrary interrupt/exception formal proof; firmware uses machine timer and ECALL handlers.','No fitter, physical timing or full game FPS claim.']))
 files=[p for p in O.rglob('*')if p.is_file()and 'obj'not in p.relative_to(O).parts]+[pathlib.Path(__file__),C,P/'cpu-cache64-rvcpipe-source-review-v1/manifest.json',F/'frontend-hazards/manifest.json',F/'frontend-hazards/flow.S',F/'frontend-hazards/main.c',F/'firmware/start.S',F/'firmware/stress.c']
 files=[p for p in files if p.exists()]
 put(O/'manifest.json',dict(passed=True,evidence_sha256={str(p.relative_to(R)):sha(p)for p in files},simulator_sha256=sha(exe)))
 print('MANIFEST',sha(O/'manifest.json'),flush=True)
if __name__=='__main__':globals()[sys.argv[1]]()
