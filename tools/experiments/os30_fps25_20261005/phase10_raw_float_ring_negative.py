#!/usr/bin/env python3
"""Prove that the ring test catches early publication of private triangles."""
import hashlib,json,shutil,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
TOOLS=Path(__file__).resolve().parent
BASE=ROOT/'docs/measurements/os30-fps25-20261005/phase10/raw-float-ring-v1'
OUT=BASE/'negative-control'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
OUT.mkdir(exist_ok=False)
names=['phase10_raw_float_ring.v','phase10_raw_float_ring_tb.v','phase10_raw_float_ring_test.cpp']
for name in names:shutil.copy2(BASE/name,OUT/name)
p=OUT/names[0];text=p.read_text();old='if (result_commit)\n                output_wrptr <= next_append;'
assert text.count(old)==1
p.write_text(text.replace(old,'if (result_commit || take_result)\n                output_wrptr <= next_append;'))
cmd=['verilator','--cc','--exe','--build','-j','4','-Wno-fatal','--top-module','tb_raw_float_ring','--Mdir',str(OUT/'obj'),*[str(OUT/name) for name in names]]
with (OUT/'build.log').open('x') as f:subprocess.run(cmd,stdout=f,stderr=subprocess.STDOUT,check=True)
with (OUT/'test.log').open('x') as f:r=subprocess.run([str(OUT/'obj/Vtb_raw_float_ring')],stdout=f,stderr=subprocess.STDOUT)
text=(OUT/'test.log').read_text();assert r.returncode!=0 and 'private transaction was published early' in text
record={'passed_negative_control':True,'mutation':'Publish append pointer on every data transfer, before transaction commit',
 'exit_code':r.returncode,'log':text,'source_sha256':{n:sha(OUT/n) for n in names},'recipe_sha256':sha(Path(__file__))}
(OUT/'result.json').write_text(json.dumps(record,indent=2)+'\n');print(text)
