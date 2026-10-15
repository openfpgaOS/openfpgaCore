#!/usr/bin/env python3
"""Build the candidate worker and independent integer references in a new directory."""
import argparse,hashlib,json,pathlib,subprocess
p=argparse.ArgumentParser();p.add_argument('output',type=pathlib.Path);p.add_argument('--worker',type=pathlib.Path);a=p.parse_args()
root=pathlib.Path(__file__).resolve().parent
worker=a.worker.resolve() if a.worker else root.parent/'rtl-descriptor-candidate/src/fpga/common/gpu_geometry_worker.v'
out=a.output.resolve();out.mkdir(parents=True,exist_ok=False)
receipt={'worker_sha256':hashlib.sha256(worker.read_bytes()).hexdigest(),
 'version':subprocess.check_output(['verilator','--version'],text=True).strip(),'commands':[],
 'scope':'Exact synthetic worker output, canonical integer particles, mixed native MD2/legacy geometry, queue stalls/wrap, enable holds, malformed inputs, soft/ring/media reset. Full GPU image comparisons and physical timing are separate.'}
for name in ['enabled','legacy-disabled']:
 testroot=root if name=='enabled' else root/name
 dest=out/name;dest.mkdir()
 receipt[name]={'sources':{f:hashlib.sha256((testroot/f).read_bytes()).hexdigest() for f in ['geometry_worker_tb.v','geometry_worker.cpp']}}
 cmd=['verilator','--cc','--exe','--build','-j','4','-Wno-fatal','--top-module','geometry_worker_tb',
 '--Mdir',str(dest/'obj'),str(testroot/'geometry_worker_tb.v'),str(worker),str(testroot/'geometry_worker.cpp')]
 receipt['commands'].append(cmd)
 with (dest/'build.log').open('w') as f:subprocess.run(cmd,stdout=f,stderr=subprocess.STDOUT,check=True)
 cmd=[str(dest/'obj/Vgeometry_worker_tb')];receipt['commands'].append(cmd)
 r=subprocess.run(cmd,capture_output=True,text=True,check=True)
 (dest/'result.txt').write_text(r.stdout+r.stderr);print(r.stdout,end='')
 receipt[name]['result']=r.stdout+r.stderr
receipt['passed']=True
(out/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
