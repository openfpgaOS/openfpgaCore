#!/usr/bin/env python3
"""Build frozen native and disabled-worker checks in a fresh output directory."""
import argparse,hashlib,json,pathlib,subprocess
p=argparse.ArgumentParser();p.add_argument('output',type=pathlib.Path);a=p.parse_args()
root=pathlib.Path(__file__).resolve().parent
out=a.output.resolve();out.mkdir(parents=True,exist_ok=False)
files=['gpu_geometry_worker.v','geometry_worker_tb.v','geometry_worker.cpp',
       'legacy-disabled/geometry_worker_tb.v','legacy-disabled/geometry_worker.cpp']
receipt={'source_sha256':{f:hashlib.sha256((root/f).read_bytes()).hexdigest() for f in files},
 'version':subprocess.check_output(['verilator','--version'],text=True).strip(),'commands':[],
 'scope':'Synthetic exact-output reference: native MD2 transform/projection/palette/indices; mixed legacy; malformed descriptors; queue stalls/wrap; paused compute; soft/ring/media reset while request/response outstanding. Does not establish full-game parity, physical timing, or FPS.'}
for name in ['native','legacy-disabled']:
 testroot=root if name=='native' else root/name
 dest=out/name;dest.mkdir()
 cmd=['verilator','--cc','--exe','--build','-j','4','-Wno-fatal','--top-module','geometry_worker_tb',
 '--Mdir',str(dest/'obj'),str(testroot/'geometry_worker_tb.v'),str(root/'gpu_geometry_worker.v'),str(testroot/'geometry_worker.cpp')]
 receipt['commands'].append(cmd)
 with (dest/'build.log').open('w') as f:
  subprocess.run(cmd,stdout=f,stderr=subprocess.STDOUT,check=True)
 cmd=[str(dest/'obj/Vgeometry_worker_tb')];receipt['commands'].append(cmd)
 r=subprocess.run(cmd,capture_output=True,text=True,check=True)
 (dest/'result.txt').write_text(r.stdout+r.stderr);print(r.stdout,end='')
receipt['passed']=True
(out/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
