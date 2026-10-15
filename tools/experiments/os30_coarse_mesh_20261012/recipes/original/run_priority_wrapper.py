#!/usr/bin/env python3
import argparse,hashlib,json,subprocess,os
from pathlib import Path
A=argparse.ArgumentParser();A.add_argument('--out',type=Path,required=True);a=A.parse_args();o=a.out.resolve();o.mkdir(parents=True,exist_ok=False)
w=Path(__file__).resolve().parents[1];c=w/'frozen/src/fpga/common';sources=[c/n for n in json.loads((w/'tests/sources.json').read_text())];test=w/'tests/test_async_wrapper.cpp';sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest();before={str(p):sha(p) for p in [*sources,test,Path(__file__).resolve()]}
cmd=['verilator','--cc','--exe','--build','-j','4','-Wno-fatal','-DINCLUDE_GPU_ASYNC_MESH','--top-module','geometry_load_alias','--Mdir',str(o/'obj'),'-CFLAGS','-std=c++17 -O1 -fsanitize=address,undefined -fno-omit-frame-pointer','-LDFLAGS','-fsanitize=address,undefined',*map(str,sources),str(test)]
r=subprocess.run(cmd,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True);(o/'build.log').write_text(r.stdout)
if r.returncode:print(r.stdout[-6000:]);raise SystemExit(r.returncode)
r=subprocess.run([str(o/'obj/Vgeometry_load_alias')],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,env={**os.environ,'ASAN_OPTIONS':'detect_leaks=0','UBSAN_OPTIONS':'halt_on_error=1'});(o/'run.log').write_text(r.stdout);print(r.stdout)
assert before=={p:sha(Path(p)) for p in before},'sources changed during test'
(o/'manifest.json').write_text(json.dumps({'status':'PASS' if r.returncode==0 else 'FAIL','inputs_sha256':before,'build_command':cmd,'result':json.loads(r.stdout) if r.returncode==0 else r.stdout},indent=2)+'\n');raise SystemExit(r.returncode)
