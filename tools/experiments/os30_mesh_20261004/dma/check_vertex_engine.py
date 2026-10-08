#!/usr/bin/env python3
"""Exercise VERTEX through the actual engine, shared RAM/DSP/divider and COPY."""
import argparse,hashlib,json,shutil,struct,subprocess,sys
from pathlib import Path
HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[3]
sys.path.insert(0,str(HERE.parent/'engine'))
from isa import Program
from emulator import run

def make_program(record):
    raw,state,expected,rawptr,dstptr=record[:4],record[4:28],list(record[28:35]),record[35],record[36]
    p=Program().const(20,128).const(21,64)
    # Stage immutable context in bank, COPY it to scratch, then write raw input.
    # Raw/output may overlap this staging bank area safely after COPY completes.
    for i,value in enumerate(state):p.const(22,value).emit('STORE',22,20,i)
    p.emit('COPY',23,20,21)
    p.const(192,rawptr).const(193,dstptr)
    for i,value in enumerate(raw):p.const(22,value).emit('STORE',22,192,i)
    guards=[0,1,16,31,62,63,94,191,194,255]
    for index in guards:p.const(index,(0xa5a50000+index)&0xffffffff)
    p.emit('VERTEX',a=192,b=193)
    for i in range(7):p.emit('LOAD',22,193,i).emit('OUT',a=22)
    # Guard persistent driver registers/context against implicit scratch writes.
    for index in guards+[192,193]+list(range(64,88)):p.emit('OUT',a=index)
    expected += [(0xa5a50000+i)&0xffffffff for i in guards]+[rawptr,dstptr]+list(state)
    # Shader must relinquish shared resources before ordinary MUL/DIV executes.
    p.const(10,0x92345678).const(11,0x17315).emit('MUL',12,10,11).emit('OUT',a=12)
    p.emit('ACCHI',12).emit('OUT',a=12).emit('DIVU',12,10,11).emit('OUT',a=12)
    p.emit('HALT')
    code=p.finish();result=run(code)
    assert result['fault']==0 and result['output'][:-3]==expected,(result['output'][:7],expected[:7])
    return code,result

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--out',type=Path,required=True)
    ap.add_argument('--vectors',type=Path,default=ROOT/'docs/measurements/os30-mesh-20261004/vertex-unit-v1/vectors.bin')
    ap.add_argument('--limit',type=int);args=ap.parse_args()
    out=args.out.resolve();out.mkdir(parents=True,exist_ok=False);frozen=out/'frozen';frozen.mkdir()
    sources=[*HERE.parent.glob('engine/*.v'),*HERE.parent.glob('kernel/gpu_mesh*.v'),*HERE.glob('gpu_mesh*.v'),
             HERE.parent/'engine/engine_test.cpp',HERE.parent/'engine/emulator.py',HERE.parent/'engine/isa.py',Path(__file__).resolve()]
    for source in sources:shutil.copy2(source,frozen/source.name)
    counts=dict(programs=0,words=0,cycles=0,max_code=0)
    with args.vectors.open('rb') as inp,(out/'vectors.bin').open('wb') as f:
        while (row:=inp.read(148)):
            if args.limit and counts['programs']>=args.limit:break
            code,result=make_program(struct.unpack('<37I',row));words=result['output']
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
