#!/usr/bin/env python3
"""Run portable source-only focused tests; write all generated files outside the bundle."""
from pathlib import Path
import argparse, hashlib, json, os, subprocess
HERE=Path(__file__).resolve().parent
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--output',type=Path,required=True)
p.add_argument('--case',choices=['wrapper','faults','combined','app-priority','app-cache','all'],default='all')
a=p.parse_args(); out=a.output.resolve(); assert not out.is_relative_to(HERE)
subprocess.run(['python3',str(HERE/'verify.py')],check=True)
out.mkdir(parents=True,exist_ok=False)
cases=['wrapper','faults','combined','app-priority','app-cache'] if a.case=='all' else [a.case]
results={}; inputs={str(p.relative_to(HERE)):sha(p) for p in HERE.rglob('*') if p.is_file() and '__pycache__' not in p.parts}
for case in cases:
    d=out/case; d.mkdir()
    if case.startswith('app-'):
        name=case[4:]; src=HERE/'tests/app'/f'{name}.c'; exe=d/'test'
        cmd=['cc','-std=c11','-O1','-g','-fno-pie','-no-pie','-fsanitize=address,undefined',
             '-fno-omit-frame-pointer','-I'+str(HERE/'app/src/sm64/sm64/src/pc/gfx'),str(src),'-lm','-o',str(exe)]
    else:
        names=json.loads((HERE/'recipes/rtl-sources.json').read_text())
        sources=[HERE/'rtl'/n for n in names]
        top='tb_async_combined' if case=='combined' else 'geometry_load_alias'
        if case=='combined': sources.append(HERE/'tests/combined/tb_async_combined.v')
        test=HERE/'tests'/case/('test_async_'+('wrapper' if case=='wrapper' else 'faults' if case=='faults' else 'combined')+'.cpp')
        cmd=['verilator','--cc','--exe','--build','-j','4','-Wno-fatal','-DINCLUDE_GPU_ASYNC_MESH',
             '--top-module',top,'--Mdir',str(d/'obj'),'-CFLAGS',
             '-std=c++17 -O1 -fsanitize=address,undefined -fno-omit-frame-pointer',
             '-LDFLAGS','-fsanitize=address,undefined',*map(str,sources),str(test)]
        exe=d/'obj'/('V'+top)
    with (d/'build.log').open('w') as log: subprocess.run(cmd,stdout=log,stderr=subprocess.STDOUT,check=True)
    r=subprocess.run([str(exe)],capture_output=True,text=True,
                     env={**os.environ,'ASAN_OPTIONS':'detect_leaks=0','UBSAN_OPTIONS':'halt_on_error=1'})
    (d/'run.log').write_text(r.stdout+r.stderr)
    assert r.returncode==0,(case,r.stdout,r.stderr)
    result=json.loads(r.stdout); assert result['status']=='PASS',result
    results[case]=result; print(case,json.dumps(result),flush=True)
assert inputs=={str(p.relative_to(HERE)):sha(p) for p in HERE.rglob('*') if p.is_file() and '__pycache__' not in p.parts}
(out/'result.json').write_text(json.dumps(dict(status='PASS',cases=results,bundle_manifest_sha256=sha(HERE/'manifest.json'),
    runner_sha256=sha(Path(__file__)),inputs_sha256=inputs),indent=2)+'\n')
