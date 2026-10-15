#!/usr/bin/env python3
"""Actual frozen render-cache ordering with adversarial posted-memory timing."""
from pathlib import Path
import argparse, hashlib, json, subprocess
HERE=Path(__file__).resolve().parent
P=HERE.parents[1]
CACHE=P/'cached-cpu-v1/native-sdram-v2/frozen/src/fpga/common/gpu_color_depth_cache.sv'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
parser=argparse.ArgumentParser();parser.add_argument('--out',default='cache-v1');args=parser.parse_args()
out=HERE/args.out;out.mkdir(exist_ok=False)
sources=[Path(__file__).resolve(),HERE/'test_cache_order.cpp',CACHE]
before={str(p):sha(p) for p in sources}
command=['verilator','--cc','--exe','--build','--assert','-j','3','-Wno-fatal','-Wno-WIDTHEXPAND','-Wno-WIDTHTRUNC',
         '--top-module','gpu_color_depth_cache','--Mdir',str(out/'obj'),'-CFLAGS','-std=c++17 -O2',
         '-GADDR_W=26','-GCACHE_ALL=1','-GWAYS=2','-GSET_BITS=8','-GWORD_BITS=4',
         '-GREGISTER_READ_LOOKUP=1','-GREGISTER_FILL_HAZARD=1','-GSPECULATE_WRITE_HOT0=1',str(CACHE),str(HERE/'test_cache_order.cpp')]
with (out/'build.log').open('w') as f:subprocess.run(command,stdout=f,stderr=subprocess.STDOUT,check=True)
exe=out/'obj/Vgpu_color_depth_cache'
run=subprocess.run([str(exe)],capture_output=True,text=True)
(out/'run.log').write_text(run.stdout+run.stderr)
assert run.returncode==0,run.stdout+run.stderr
result=json.loads(run.stdout.strip().splitlines()[-1]);assert result['pass']
assert before=={str(p):sha(p) for p in sources}
record={'status':'PASS_ACTUAL_RENDER_CACHE_ORDERING','inputs_sha256':before,'command':command,
        'executable_sha256':sha(exe),'result':result,
        'scope':'Actual unchanged512-line render cache with exact native parameters. CPU test driver supplies legal AW/W/AR, independent byte oracle and delayed in-order downstream B; no GPU queue/FSM or physical SDRAM controller in this layer.'}
(out/'manifest.json').write_text(json.dumps(record,indent=2)+'\n');print(json.dumps(result))
