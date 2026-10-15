#!/usr/bin/env python3
"""Run portable actual-RTL tests. Results go outside this source-only bundle."""
import argparse,json,os,subprocess
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);p.add_argument('--case',choices=['wrapper','combined','front','all'],default='all');a=p.parse_args();here=Path(__file__).resolve().parent;out=a.output.resolve();assert not out.is_relative_to(here);out.mkdir(parents=True,exist_ok=False)
cases=['wrapper','combined','front']if a.case=='all'else[a.case]
source_names=json.loads((here/'recipes/wrapper-sources.json').read_text());sources=[here/'rtl'/n for n in source_names]
for case in cases:
 d=out/case;d.mkdir();top={'wrapper':'geometry_load_alias','combined':'tb_async_combined','front':'tb_front'}[case]
 chosen=([here/'rtl/gpu_mesh_job_front.v',here/'tests/front/tb_front.v']if case=='front'else sources+([here/'tests/combined/tb_async_combined.v']if case=='combined'else []))
 test=here/'tests'/case/('test_front.cpp'if case=='front'else'test_async_combined.cpp'if case=='combined'else'test_async_wrapper.cpp')
 cmd=['verilator','--cc','--exe','--build','-j','4','-Wno-fatal','-DINCLUDE_GPU_ASYNC_MESH','--top-module',top,'--Mdir',str(d/'obj'),'-CFLAGS','-std=c++17 -O1 -fsanitize=address,undefined -fno-omit-frame-pointer','-LDFLAGS','-fsanitize=address,undefined',*map(str,chosen),str(test)]
 with(d/'build.log').open('w')as log:subprocess.run(cmd,stdout=log,stderr=subprocess.STDOUT,check=True)
 result=subprocess.run([str(d/'obj'/('V'+top))],capture_output=True,text=True,env={**os.environ,'ASAN_OPTIONS':'detect_leaks=0','UBSAN_OPTIONS':'halt_on_error=1'})
 (d/'run.log').write_text(result.stdout+result.stderr);print(case,result.stdout.strip());assert result.returncode==0,result.stderr
(out/'result.json').write_text(json.dumps({'status':'PASS','cases':cases},indent=2)+'\n')
