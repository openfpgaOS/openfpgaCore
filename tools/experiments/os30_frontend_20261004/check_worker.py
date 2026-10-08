#!/usr/bin/env python3
"""Build/run the independent worker arithmetic and queue regression."""
from pathlib import Path
import hashlib
import json
import subprocess
import sys
ROOT = Path(__file__).resolve().parents[3]
LEGACY='--legacy' in sys.argv
TESTS = ROOT / ('tools/tests' if LEGACY else 'tools/experiments/os30_frontend_20261004')
OUT = ROOT / ('docs/measurements/os30-frontend-20261004/unit-legacy' if LEGACY else 'docs/measurements/os30-frontend-20261004/unit')
OUT.mkdir(parents=True, exist_ok=True)
cmd = ['verilator', '--cc', '--exe', '--build', '-j', '3', '-Wno-fatal',
       '--top-module', 'geometry_worker_tb', '--Mdir', str(OUT / 'obj'),
       '-CFLAGS', '-std=c++17 -O2', str(TESTS / 'geometry_worker_tb.v'),
       str(ROOT / 'docs/measurements/os30-frontend-20261004/rtl/gpu_geometry_worker.v'),
       str(TESTS / 'geometry_worker.cpp')]
with (OUT / 'build.log').open('w') as f:
    subprocess.run(cmd, stdout=f, stderr=subprocess.STDOUT, check=True)
exe = OUT / 'obj/Vgeometry_worker_tb'
run = subprocess.run([str(exe)], text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
(OUT / 'run.log').write_text(run.stdout)
sources = [ROOT / 'docs/measurements/os30-frontend-20261004/rtl/gpu_geometry_worker.v',
           TESTS / 'geometry_worker_tb.v', TESTS / 'geometry_worker.cpp']
result = dict(passed=run.returncode == 0, output=run.stdout.strip(),
              sources={str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sources},
              executable_sha256=hashlib.sha256(exe.read_bytes()).hexdigest())
(OUT / 'verified.json').write_text(json.dumps(result, indent=2)+'\n')
print(run.stdout, end='')
run.check_returncode()
