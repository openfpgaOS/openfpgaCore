#!/usr/bin/env python3
import json,hashlib,subprocess,os
from pathlib import Path
D=Path(__file__).resolve().parent
source=D.parents[1]/'work-core/frozen/src/fpga/common/geometry_mesh_queue.v'
rtl=D/'geometry_mesh_queue.v'
assert not rtl.exists();rtl.write_bytes(source.read_bytes())
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
inputs={str(p):sha(p) for p in [Path(__file__).resolve(),D/'test.cpp',rtl,source]}
cmd=['verilator','--cc','--exe','--build','-j','4','-Wno-fatal','--top-module','geometry_mesh_queue','--Mdir',str(D/'obj'),'-CFLAGS','-std=c++17 -O1 -fsanitize=address,undefined -fno-omit-frame-pointer','-LDFLAGS','-fsanitize=address,undefined',str(rtl),str(D/'test.cpp')]
with (D/'build.log').open('x') as f:subprocess.run(cmd,stdout=f,stderr=subprocess.STDOUT,check=True)
env=dict(os.environ);env['ASAN_OPTIONS']='detect_leaks=0'
r=subprocess.run([str(D/'obj/Vgeometry_mesh_queue')],text=True,capture_output=True,env=env)
(D/'run.log').write_text(r.stdout+r.stderr)
for p,h in inputs.items():assert sha(Path(p))==h,p
assert r.returncode==0,r.stderr
out={'status':'PASS','inputs_sha256':inputs,'command':cmd,'environment':{'ASAN_OPTIONS':'detect_leaks=0'},'result':json.loads(r.stdout),'scope':'Actual MTF2 multi-packet512word dual-port FIFO; randomized independent producer and consumer, simultaneous fill/drain, metadata captured under changing inputs, exact payload order through many wraps, full416word packets, zero/one packets, marker before commit, stale marker, short/oversized/error containment, reset during fill/drain and recovery. AddressSanitizer/UBSan enabled; LeakSanitizer disabled due host ptrace restriction. No arithmetic or physical fit claim.'}
(D/'manifest.json').write_text(json.dumps(out,indent=2)+'\n');print(json.dumps(out['result']))
