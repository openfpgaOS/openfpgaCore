#!/usr/bin/env python3
"""Compare retained mixed lazy contexts against eager original CPU math."""
from pathlib import Path
import argparse,json,os,shutil,subprocess
HERE=Path(__file__).resolve().parent
p=argparse.ArgumentParser();p.add_argument('--out-name',default='lazy-pool');a=p.parse_args()
OUT=HERE/'results'/a.out_name;OUT.mkdir(exist_ok=False)
base=(HERE/'results/lazy-fixed/test.c').read_text();prefix=base[:base.index('/* Harness tail:')]
tail=(HERE/'test_lazy_pool.c').read_text()
(OUT/'test.c').write_text(prefix+tail)
for name in ('sm64_lazy_state.h','sm64_lazy_capture.h'):shutil.copy2(HERE/'retained_contexts'/name,OUT/name)
shutil.copy2(HERE/'results/lazy-cpu-fixed-control/sm64_lazy_cpu.h',OUT/'sm64_lazy_cpu.h')
shutil.copy2(HERE/'results/lazy-cpu-fixed-control/scene.bin',OUT/'scene.bin')
results=[]
for fast,noinline in [(0,0),(0,1),(1,0),(1,1)]:
    for label,flags in [('optimized',['-O3','-ffast-math','-ffp-contract=off']),('sanitized',['-O1','-fsanitize=address,undefined','-fno-omit-frame-pointer'])]:
        name=f'{label}-depth{fast}-noinline{noinline}';exe=OUT/name
        subprocess.run(['gcc','-std=gnu99','-fno-strict-aliasing','-fwrapv',*flags,
           f'-DMESH_LAZY_FAST_DEPTH={fast}',f'-DMESH_LAZY_NOINLINE={noinline}',str(OUT/'test.c'),'-lm','-o',str(exe)],check=True)
        done=subprocess.run([str(exe),str(OUT/'scene.bin')],capture_output=True,text=True,
             env=dict(os.environ,ASAN_OPTIONS='detect_leaks=0'))
        print(done.stdout,end='');print(done.stderr,end='');done.check_returncode()
        results.append(dict(configuration=name,output=done.stdout))
(OUT/'result.json').write_text(json.dumps(dict(passed=True,results=results),indent=2)+'\n')
