#!/usr/bin/env python3
"""Build and check private ordered raw-float ring transport; no arithmetic."""
import hashlib,json,shutil,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
TOOLS=Path(__file__).resolve().parent
OUT=ROOT/'docs/measurements/os30-fps25-20261005/phase10/raw-float-ring-v1'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
OUT.mkdir(exist_ok=False)
names=['phase10_raw_float_ring.v','phase10_raw_float_ring_tb.v','phase10_raw_float_ring_test.cpp']
for n in names:shutil.copy2(TOOLS/n,OUT/n)
cmd=['verilator','--cc','--exe','--build','-j','4','-Wno-fatal','--top-module','tb_raw_float_ring','--Mdir',str(OUT/'obj'),str(OUT/names[0]),str(OUT/names[1]),str(OUT/names[2])]
with (OUT/'build.log').open('x') as f:subprocess.run(cmd,stdout=f,stderr=subprocess.STDOUT,check=True)
with (OUT/'test.log').open('x') as f:subprocess.run([str(OUT/'obj/Vtb_raw_float_ring')],stdout=f,stderr=subprocess.STDOUT,check=True)
result={'passed':True,'scope':'Generic ordered transport only; arithmetic/protocol parser and fullGPU not included',
 'command':cmd,'sources':{n:sha(OUT/n) for n in names},'recipe_sha256':sha(Path(__file__)),
 'simulator_sha256':sha(OUT/'obj/Vtb_raw_float_ring'),'log':(OUT/'test.log').read_text()}
(OUT/'result.json').write_text(json.dumps(result,indent=2)+'\n');print(result['log'])
