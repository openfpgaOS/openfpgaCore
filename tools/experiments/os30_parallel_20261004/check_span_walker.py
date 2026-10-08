#!/usr/bin/env python3
"""Compare prepared-edge DDA to the current full edge walker, with stalls."""
import hashlib
import json
from pathlib import Path
import subprocess

ROOT=Path(__file__).resolve().parents[3]
HERE=Path(__file__).resolve().parent
OUT=ROOT/'docs/measurements/os30-parallel-20261004'


def main():
    dest=OUT/'span-unit';dest.mkdir(exist_ok=False)
    sources=[HERE/'span_walker_tb.v',HERE/'span_walker.cpp',OUT/'edges-rtl/gpu_span_walker.v',
             OUT/'edges-rtl/gpu_edge_walker.v']
    command=['verilator','--cc','--exe','--build','-j','2','-Wno-fatal','--top-module','span_walker_tb',
             '--Mdir',str(dest/'obj'),'+define+INCLUDE_EW_STEP_PREDECODE','-CFLAGS','-std=c++17 -O2',
             *map(str,sources)]
    with (dest/'build.log').open('w') as log:
        subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,check=True)
    result=subprocess.run([str(dest/'obj/Vspan_walker_tb')],capture_output=True,text=True,check=True)
    (dest/'run.log').write_text(result.stdout+result.stderr)
    (dest/'result.json').write_text(json.dumps(dict(passed=True,result=result.stdout,
        sources={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in sources},
        command=command),indent=2)+'\n')
    print(result.stdout,end='')


if __name__=='__main__':main()
