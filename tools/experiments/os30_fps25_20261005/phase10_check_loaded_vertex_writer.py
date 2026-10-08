#!/usr/bin/env python3
"""Freeze/test exact36byte LoadedVertex DMA output writer."""
import argparse,json,subprocess
from pathlib import Path
from phase10_experiment import CAMP,sha,write
HERE=Path(__file__).resolve().parent
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--label',default='loaded-vertex-writer-v1');args=ap.parse_args();out=CAMP/'phase10'/args.label;out.mkdir(exist_ok=False)
 files=[HERE/'phase10_float_batch/geometry_loaded_vertex_writer.v',HERE/'phase10_loaded_vertex_writer_test.cpp']
 for p in files:(out/p.name).write_bytes(p.read_bytes())
 native=CAMP/'phase10/raw-load-v1/native.bin'
 write(out/'manifest.json',dict(source_sha256={p.name:sha(out/p.name)for p in files},native_sha256=sha(native),helper_sha256=sha(Path(__file__)),scope='Completed raw slots to exact36byte packedLoadedVertex AXI write bursts. NoCPUcache/invalidation/fullcoreFPS claim.'))
 cmd=['verilator','--cc','--exe','--build','-j','3','-Wno-fatal','--top-module','geometry_loaded_vertex_writer','--Mdir',str(out/'obj'),'-CFLAGS','-O2 -std=c++17',*[str(out/p.name)for p in files]]
 with (out/'build.log').open('x')as log:subprocess.run(cmd,stdout=log,stderr=subprocess.STDOUT,check=True)
 with (out/'run.log').open('x')as log:p=subprocess.run([str(out/'obj/Vgeometry_loaded_vertex_writer'),str(native)],stdout=log,stderr=subprocess.STDOUT)
 print((out/'run.log').read_text())
 if p.returncode:raise SystemExit(p.returncode)
 write(out/'result.json',dict(**json.loads((out/'run.log').read_text()),manifest_sha256=sha(out/'manifest.json')))
if __name__=='__main__':main()
