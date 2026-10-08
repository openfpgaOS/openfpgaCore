#!/usr/bin/env python3
"""Actual CPU/memory RTL correctness and kernel-cost screen for smaller queues."""
import argparse,concurrent.futures,hashlib,json,os,re,shutil,subprocess
from phase10_cpu_queue_adapter import adapt
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3];P=ROOT/'docs/measurements/os30-fps25-20261005/phase10'
OUT=P/'cpu-queues-v1';OLD=P/'lrsc-only-v1/cpu-model';BASE=ROOT/'docs/measurements/os30-dual-20261004/validation'
CPU='src/fpga/vendor/vexriscv/VexiiRiscv/VexiiRiscv_os30.v'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,x):
 with p.open('x') as f:json.dump(x,f,indent=2);f.write('\n')
def model():
 old=json.loads((OLD/'build.json').read_text());dest=OUT/'cpu-model';dest.mkdir(exist_ok=False)
 shutil.copytree(OLD/'frozen',dest/'frozen')
 for p,h in old['files_sha256'].items():assert sha(dest/p)==h,p
 cpu=OUT/'VexiiRiscv.v';assert sha(cpu)==json.loads((OUT/'generated.json').read_text())['rtl_sha256']
 shutil.copy2(cpu,dest/'frozen'/CPU)
 wrapper=dest/'frozen/src/fpga/common/cpu_system.v';wrapper.write_text(adapt(wrapper.read_text()))
 cmd=[x.replace(str(OLD),str(dest)) for x in old['command']]
 with (dest/'build.log').open('x') as f:subprocess.run(cmd,cwd=dest/'frozen/src/fpga/test',stdout=f,stderr=subprocess.STDOUT,check=True)
 hashes={str(p.relative_to(dest)):sha(p) for p in (dest/'frozen').rglob('*') if p.is_file()}
 save(dest/'build.json',dict(command=cmd,source_sha256=hashes,cpu_sha256=sha(cpu),simulator_sha256=sha(dest/'obj/Vtb_system'),recipe_sha256=sha(Path(__file__))))
def run():
 dest=OUT/'regression';dest.mkdir(exist_ok=False);exe=OUT/'cpu-model/obj/Vtb_system'
 assert sha(exe)==json.loads((OUT/'cpu-model/build.json').read_text())['simulator_sha256']
 programs={'kernels':BASE/'firmware/kernels.bin','i2f':BASE/'firmware/i2f.bin','frontend':BASE/'frontend-hazards/frontend.bin','stress':BASE/'firmware/stress.bin'}
 for n,p in programs.items():
  w=dest/n;w.mkdir();shutil.copy2(p,w/p.name);shutil.copy2(BASE/'firmware/firmware.mif',w/'firmware.mif')
 def one(job):
  name,mode=job;program=programs[name];w=dest/name;log=w/(mode+'.log');reference=BASE/'baseline128'/(name+'-'+mode+'.log')
  cmd=[str(exe),program.name,'30000000'];env=dict(os.environ,SCAN_ENABLE='0' if mode=='quiet' else '1',SCAN_PERIOD='700',SCAN_LEN='128',SCAN_PHASE='317')
  with log.open('x') as f:subprocess.run(cmd,cwd=w,env=env,stdout=f,stderr=subprocess.STDOUT,check=True,timeout=900)
  text=log.read_text();old=reference.read_text();assert 'HAL init' in text and 'FAIL' not in text
  assert 'WCONF: mism=0 spurious=0 exp_fifo=0 aw_fifo=0 run_left=0' in text
  assert 'R mismatches=0  scanout mismatches=0  rlast slips=0  orphan beats=0  DRAM strikes=0' in text
  uart=lambda s:s.split('=== UART BEGIN ===')[1].split('=== UART END ===')[0]
  a,b=uart(old),uart(text);components=[]
  if name=='kernels':
   decode=lambda s:re.findall(r'KERNEL (\w+) ([0-9a-f]+) ([0-9a-f]+)',s)
   aa,bb=decode(a),decode(b);assert len(aa)==len(bb)==14
   for x,y in zip(aa,bb):
    assert x[0]==y[0] and x[2]==y[2],(x,y)
    ca,cb=int(x[1],16),int(y[1],16);components.append(dict(name=x[0],baseline_cycles=ca,candidate_cycles=cb,delta_percent=100*(cb/ca-1),hash=x[2]))
  elif name=='i2f':assert a==b
  elif name=='frontend':
   aa=re.search(r'FRONTEND RESULT ([0-9a-f]+) ([0-9a-f]+) ([0-9a-f]+)',a).groups();bb=re.search(r'FRONTEND RESULT ([0-9a-f]+) ([0-9a-f]+) ([0-9a-f]+)',b).groups();assert aa[0]==bb[0] and int(bb[2],16)>0
  else:assert 'CPU stress PASS: integer, FPU, atomics, traps, cache.' in b and int(re.search(r'IRQ count: ([0-9a-f]+)',b).group(1),16)>0
  cyc=lambda s:int(re.search(r'BOOTED:.*?at cycle (\d+)',s).group(1));ca,cb=cyc(old),cyc(text)
  return dict(program=name,mode=mode,passed=True,old_cycles_to_pass=ca,new_cycles_to_pass=cb,delta_percent=100*(cb/ca-1),components=components,program_sha256=sha(program),log_sha256=sha(log),reference_log_sha256=sha(reference),command=cmd,environment={k:env[k] for k in ['SCAN_ENABLE','SCAN_PERIOD','SCAN_LEN','SCAN_PHASE']})
 with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:rows=list(pool.map(one,[(n,m) for n in programs for m in ['quiet','scanout']]))
 save(dest/'result.json',dict(passed=True,scope='Actual CPU+retained SDRAM/arbiter kernels; all values verified, timings are component costs rather than game FPS',cpu_sha256=sha(OUT/'VexiiRiscv.v'),simulator_sha256=sha(exe),runs=rows,recipe_sha256=sha(Path(__file__))))
 print(json.dumps(rows,indent=2))
if __name__=='__main__':
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=['model','run']);a=p.parse_args();globals()[a.action]()
