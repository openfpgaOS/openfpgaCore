#!/usr/bin/env python3
"""Run the same paired RV32 fixture through exact single-issue A RTL."""
import sys,os,subprocess,concurrent.futures
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from common import *
out=OUT/'cpu-kernel';manifest=read(out/'manifest.json');exe=out/'Vtb_system';fw=out/'firmware'
assert sha(exe)==manifest['simulator_sha256']
for name,digest in manifest['firmware_sha256'].items():assert sha(fw/name)==digest

def run(mode):
 log=out/(mode+'.log');assert not log.exists(),log
 env=dict(os.environ,SCAN_ENABLE='1' if mode=='scanout' else '0',SCAN_PERIOD='700',SCAN_LEN='128',SCAN_PHASE='317',STRIKE_SCAN_PERIOD='1000000000')
 with log.open('w') as f:subprocess.run([str(exe),'kernel.bin','60000000'],cwd=fw,env=env,stdout=f,stderr=subprocess.STDOUT,check=True,timeout=600)
 s=log.read_text();assert 'KERNEL PASS HAL init' in s and 'KERNEL FAIL' not in s and 'WCONF: mism=0 spurious=0 exp_fifo=0 aw_fifo=0 run_left=0' in s
 print('PASS',mode,flush=True)
with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:list(pool.map(run,['quiet','scanout']))
