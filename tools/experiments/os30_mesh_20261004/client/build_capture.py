#!/usr/bin/env python3
"""Build functional CPU scene capture; no accelerator timing claims."""
from pathlib import Path
import shutil,subprocess
ROOT=Path(__file__).resolve().parents[4]
HERE=Path(__file__).resolve().parent
BASE=ROOT/'docs/measurements/sm64-vertex-perf-20261004/qsim'
OUT=ROOT/'docs/measurements/os30-mesh-20261004/SM64/mesh-shadow/qsim'
OUT.mkdir(parents=True,exist_ok=False)
for p in BASE.iterdir():
    if p.suffix in ('.c','.h') or p.name=='Makefile':shutil.copy2(p,OUT/p.name)
s=(OUT/'main.c').read_text()
s=s.replace('/* ------------------------------------------------------------------------ */\n/* sha256',
            (HERE/'capture.c').read_text()+'\n/* ------------------------------------------------------------------------ */\n/* sha256',1)
needle='                env_frame_rec *fr = &E.pend[k];'
assert s.count(needle)==1
s=s.replace(needle,needle+'\n                mesh_capture(&E,out,fr->seq);')
(OUT/'main.c').write_text(s)
with (OUT/'build.log').open('w') as log:
    subprocess.run(['make','-j3'],cwd=OUT,stdout=log,stderr=subprocess.STDOUT,check=True)
print(OUT/'qsim')
