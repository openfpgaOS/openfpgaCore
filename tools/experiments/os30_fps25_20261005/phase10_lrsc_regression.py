#!/usr/bin/env python3
"""Reuse qualified A kernels and frontend/FPU probes on the private CPU."""
import concurrent.futures,hashlib,json,os,re,shutil,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
OUT=ROOT/'docs/measurements/os30-fps25-20261005/phase10/lrsc-only-v1'
BASE=ROOT/'docs/measurements/os30-dual-20261004/validation'
DEST=OUT/'regression';DEST.mkdir(exist_ok=False)
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
exe=OUT/'cpu-model/obj/Vtb_system';assert sha(exe)==json.loads((OUT/'cpu-model/build.json').read_text())['simulator_sha256']
programs={'kernels':BASE/'firmware/kernels.bin','i2f':BASE/'firmware/i2f.bin','frontend':BASE/'frontend-hazards/frontend.bin'}
reference=json.loads((BASE/'baseline128/results.json').read_text())['results']
for name,p in programs.items():
 if name=='frontend':expected=json.loads((BASE/'baseline128/frontend-results.json').read_text())['binary_sha256']
 else:expected=next(r['program_sha256'] for r in reference if r['program']==name)
 assert sha(p)==expected
 work=DEST/name;work.mkdir();shutil.copy2(p,work/p.name);shutil.copy2(BASE/'firmware/firmware.mif',work/'firmware.mif')
def one(job):
 name,mode=job;work=DEST/name;p=programs[name];log=work/(mode+'.log');oldlog=BASE/'baseline128'/(name+'-'+mode+'.log')
 command=[str(exe),p.name,'20000000'];env=dict(os.environ,SCAN_ENABLE='0' if mode=='quiet' else '1',SCAN_PERIOD='700',SCAN_LEN='128',SCAN_PHASE='317')
 with log.open('x') as f:subprocess.run(command,cwd=work,env=env,stdout=f,stderr=subprocess.STDOUT,check=True,timeout=600)
 text=log.read_text();old=oldlog.read_text();assert 'HAL init' in text and 'FAIL' not in text
 assert 'WCONF: mism=0 spurious=0 exp_fifo=0 aw_fifo=0 run_left=0' in text
 uart=lambda x:x.split('=== UART BEGIN ===')[1].split('=== UART END ===')[0]
 exact=uart(text)==uart(old)
 cycles=lambda x:int(re.search(r'BOOTED:.*?at cycle (\d+)',x).group(1))
 a,b=cycles(old),cycles(text)
 result=dict(program=name,mode=mode,uart_and_component_cycles_exact=exact,old_cycles_to_pass=a,new_cycles_to_pass=b,whole_probe_cycles_exact=a==b,program_sha256=sha(p),simulator_sha256=sha(exe),reference_log_sha256=sha(oldlog),log_sha256=sha(log),command=command,environment={k:env[k] for k in ('SCAN_ENABLE','SCAN_PERIOD','SCAN_LEN','SCAN_PHASE')})
 assert exact and a==b,result
 return result
with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:rows=list(pool.map(one,[(n,m) for n in programs for m in ('quiet','scanout')]))
(DEST/'result.json').write_text(json.dumps(dict(passed=True,runs=rows,recipe_sha256=sha(Path(__file__)),scope='Existing exact binaries:14 integer/FPU/SM64/vector/triangle/cache kernel timings, I2F modes/flags/hazards, frontend redirects/RVC/IRQ. UART, hashes and cycles match A exactly.'),indent=2)+'\n')
print(json.dumps(rows))
