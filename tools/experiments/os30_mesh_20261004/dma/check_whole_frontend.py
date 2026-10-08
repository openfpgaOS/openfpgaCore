#!/usr/bin/env python3
"""Real captured raw meshes with explicit materials through ordered frontend RTL.

This checks fetch, driver, transform, clipping, projection, packet arithmetic,
packet order and backpressure. The AXI model returns immutable backing memory;
this is not yet a raster/physical SDRAM replay or a game FPS measurement.
"""
import argparse,hashlib,json,shutil,struct,subprocess,sys
from pathlib import Path
HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[3]
sys.path.insert(0,str(HERE.parent/'client'))
from check_mesh_program import bank_for,expected_stream
from mesh_program import build
from mesh_transform import OUT,read

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--out',type=Path,required=True)
    ap.add_argument('--limit',type=int,default=0);args=ap.parse_args()
    out=args.out.resolve();out.mkdir(parents=True,exist_ok=False);frozen=out/'frozen';frozen.mkdir()
    sources=[*HERE.glob('gpu_mesh*.v'),*HERE.parent.glob('engine/*.v'),*HERE.parent.glob('kernel/gpu_mesh*.v'),
             HERE/'frontend_test.cpp',HERE/'whole_frontend_test.cpp',Path(__file__).resolve(),
             *HERE.parent.glob('client/*.py'),*HERE.parent.glob('engine/*.py'),*HERE.parent.glob('kernel/*.py')]
    for p in sources:shutil.copy2(p,frozen/p.name)
    program=build().finish();jobs=[j for p in sorted((OUT/'run-360-8').glob('mesh-*.bin')) for j in read(p)[1]]
    if args.limit:jobs=jobs[:args.limit]
    with (out/'vectors.bin').open('wb') as f:
        f.write(struct.pack('<2I',len(program),len(jobs)));f.write(struct.pack('<%dI'%len(program),*program))
        for j in jobs:
            bank,state=bank_for(j);expected,_,_=expected_stream(j,state)
            nv,nt=j['header'][3:5]
            f.write(struct.pack('<4I',nv,(nt+1)//2,nt,len(expected)))
            f.write(struct.pack('<256I',*bank));f.write(struct.pack('<%dI'%len(expected),*expected))
    command=['verilator','--cc','--exe','--build','-j','3','-Wno-fatal','--top-module','gpu_mesh_frontend',
             '-GCOMPACT_LAYOUT=1','-GENABLE_DOT=0','-GEXTENDED_ISA=0','--Mdir',str(out/'obj'),
             *[str(p) for p in frozen.glob('*.v')],str(frozen/'whole_frontend_test.cpp'),'-CFLAGS','-std=c++17 -O2']
    manifest={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in frozen.iterdir()}
    (out/'manifest.json').write_text(json.dumps(dict(command=command,sources=manifest,scope=__doc__),indent=2)+'\n')
    with (out/'build.log').open('w') as f:subprocess.run(command,stdout=f,stderr=subprocess.STDOUT,check=True)
    with (out/'run.log').open('w') as f:subprocess.run([str(out/'obj/Vgpu_mesh_frontend'),str(out/'vectors.bin')],stdout=f,stderr=subprocess.STDOUT,check=True)
    result=json.loads((out/'run.log').read_text().splitlines()[-1]);result['scope']=__doc__;result['program_words']=len(program)
    (out/'result.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))
if __name__=='__main__':main()
