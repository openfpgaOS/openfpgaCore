#!/usr/bin/env python3
from pathlib import Path
import os
import subprocess
HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[3]
OUT=ROOT/'docs/measurements/os30-mesh-20261004/SM64/mesh-shadow/client-test'
OUT.mkdir(parents=True,exist_ok=True)
exe=OUT/'test_client'
cmd=['cc','-std=c11','-O2','-Wall','-Wextra','-Werror','-fsanitize=undefined,address',
     str(HERE/'test_client.c'),'-o',str(exe)]
subprocess.run(cmd,check=True)
r=subprocess.run([str(exe)],text=True,capture_output=True,check=True,
                 env=dict(os.environ,ASAN_OPTIONS='detect_leaks=0'))
(OUT/'result.txt').write_text(r.stdout+r.stderr)
print(r.stdout,end='')
