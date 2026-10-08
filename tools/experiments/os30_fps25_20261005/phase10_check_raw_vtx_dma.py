#!/usr/bin/env python3
"""Freeze and simulate the raw float Vtx read component; no integration claim."""
from pathlib import Path
import json
import subprocess
from phase10_experiment import CAMP,ROOT,sha,write
HERE=Path(__file__).resolve().parent;OUT=CAMP/'phase10/raw-vtx-dma-v1'
def main():
    OUT.mkdir(exist_ok=False);frozen=OUT/'src';frozen.mkdir()
    names=['phase10_raw_vtx_dma.v','phase10_raw_vtx_dma.cpp']
    for name in names:(frozen/name).write_bytes((HERE/name).read_bytes())
    write(OUT/'manifest.json',dict(source_sha256={name:sha(frozen/name) for name in names},helper_sha256=sha(Path(__file__)),scope='Standalone rawVtx DMA read only; no full-core integration, fit, scheduling or speedup claim.'))
    cmd=['verilator','--cc','--exe','--build','-j','3','-Wno-fatal','--top-module','raw_vtx_dma','--Mdir',str(OUT/'obj'),'-CFLAGS','-O2 -std=c++17',*[str(frozen/n) for n in names]]
    with (OUT/'build.log').open('x') as log:subprocess.run(cmd,stdout=log,stderr=subprocess.STDOUT,check=True)
    with (OUT/'run.log').open('x') as log:
        p=subprocess.run([str(OUT/'obj/Vraw_vtx_dma')],stdout=log,stderr=subprocess.STDOUT)
    if p.returncode:print((OUT/'run.log').read_text());raise SystemExit(p.returncode)
    result=json.loads((OUT/'run.log').read_text());write(OUT/'result.json',dict(**result,manifest_sha256=sha(OUT/'manifest.json')));print(json.dumps(result))
if __name__=='__main__':main()
