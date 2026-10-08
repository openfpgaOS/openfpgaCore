#!/usr/bin/env python3
"""ATTR through actual engine scratch/bank/DSP; preserve inputs and driver state."""
import argparse,hashlib,json,shutil,struct,subprocess,sys
from pathlib import Path
HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[3]
sys.path.insert(0,str(HERE.parent/'engine'))
from isa import Program
from emulator import run

def make_program(record):
    vertex,combine,reference,src,dst,cr=record[:7],record[7],record[8:18],record[18],record[19],record[20]
    inputs=[(src+i)&255 for i in range(7)];protected=inputs+[cr]
    a,b,tmp=[r for r in range(256) if r not in protected][:3]
    guards=[0,1,16,31,62,63,94,127,133,191,194,231,232,255]
    p=Program();rf={}
    def C(r,v):p.const(r,v);rf[r]=v&0xffffffff
    for r in guards:C(r,0xaca00000+r)
    for r,v in zip(inputs,vertex):C(r,v)
    C(cr,combine);C(a,src);C(b,dst)
    for i in range(10):C(tmp,0xd00a0000+i);p.emit('STORE',tmp,b,i)
    p.emit('ATTR',cr,a,b)
    for i in range(10):p.emit('LOAD',tmp,b,i).emit('OUT',a=tmp)
    checked=[r for r in dict.fromkeys(inputs+guards+[cr,a,b]) if r!=tmp]
    for r in checked:p.emit('OUT',a=r)
    expected=list(reference if combine else reference[:7])+([] if combine else [0xd00a0000+i for i in range(7,10)])
    expected += [rf[r] for r in checked]
    # Following multiply proves the shared operand mux returns to ordinary ISA.
    C(a,0x87654321);C(b,0x1234567);p.emit('MUL',tmp,a,b).emit('OUT',a=tmp).emit('ACCHI',tmp).emit('OUT',a=tmp)
    p.emit('HALT');code=p.finish();result=run(code)
    assert result['fault']==0 and result['output'][:-2]==expected
    return code,result

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--out',type=Path,required=True)
    ap.add_argument('--vectors',type=Path,default=ROOT/'docs/measurements/os30-mesh-20261004/attributes-unit-v1/vectors.bin')
    ap.add_argument('--limit',type=int);args=ap.parse_args()
    out=args.out.resolve();out.mkdir(parents=True,exist_ok=False);frozen=out/'frozen';frozen.mkdir()
    sources=[*HERE.parent.glob('engine/*.v'),*HERE.parent.glob('kernel/gpu_mesh*.v'),*HERE.glob('gpu_mesh*.v'),
             HERE.parent/'engine/engine_test.cpp',HERE.parent/'engine/emulator.py',HERE.parent/'engine/isa.py',Path(__file__)]
    for source in sources:shutil.copy2(source,frozen/source.name)
    counts=dict(programs=0,words=0,cycles=0,max_code=0)
    with args.vectors.open('rb') as inp,(out/'vectors.bin').open('wb') as f:
        while (row:=inp.read(84)):
            if args.limit and counts['programs']>=args.limit:break
            code,result=make_program(struct.unpack('<21I',row));words=result['output']
            f.write(struct.pack('<4I',len(code),len(words),result['cycles'],0))
            f.write(struct.pack('<%dI'%len(code),*code));f.write(struct.pack('<%dI'%len(words),*words))
            counts['programs']+=1;counts['words']+=len(words);counts['cycles']+=result['cycles'];counts['max_code']=max(counts['max_code'],len(code))
    command=['verilator','--cc','--exe','--build','-j','3','-Wno-fatal','--top-module','gpu_mesh_engine',
             '--Mdir',str(out/'obj'),*[str(p) for p in frozen.glob('*.v')],str(frozen/'engine_test.cpp'),'-CFLAGS','-std=c++17 -O2']
    with (out/'build.log').open('w') as f:subprocess.run(command,stdout=f,stderr=subprocess.STDOUT,check=True)
    with (out/'run.log').open('w') as f:subprocess.run([str(out/'obj/Vgpu_mesh_engine'),str(out/'vectors.bin')],stdout=f,stderr=subprocess.STDOUT,check=True)
    counts['sources']={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in frozen.iterdir()}
    counts['result']=(out/'run.log').read_text().strip();(out/'result.json').write_text(json.dumps(counts,indent=2)+'\n');print(json.dumps(counts))
if __name__=='__main__':main()
