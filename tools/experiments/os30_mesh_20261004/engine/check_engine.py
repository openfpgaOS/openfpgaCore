#!/usr/bin/env python3
"""Independent arithmetic, memory, control and stalled-output engine test."""
import hashlib,json,random,struct,subprocess,shutil
from pathlib import Path
from isa import OP,Program
from emulator import run
HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[3]
OUT=ROOT/'docs/measurements/os30-mesh-20261004/engine-unit'
def programs():
    rng=random.Random(0x6430166)
    special=[0,1,2,0x7fffffff,0x80000000,0xffffffff,0xffff,0x10000]
    for case in range(2048):
        p=Program()
        for r in range(12):p.const(r,special[(case+r)%len(special)] if case<64 else rng.getrandbits(32))
        # Random arithmetic with deliberately wide and overlapping operands.
        for i in range(18):
            op=rng.choice(['ADD','SUB','ADDC','SUBB','AND','OR','XOR','SHL','SHR','SAR','ADDI','NEG','MUL','MAC','DIVU','DIVS'])
            p.emit(op,12,i%12,(i+1)%12).emit('OUT',a=12)
            if op in ['MUL','MAC','DIVU','DIVS']:
                p.emit('ACCHI',13).emit('OUT',a=13).emit('REM',14).emit('OUT',a=14)
        p.const(20,0).const(21,4).emit('MATROW',15,20,21).emit('OUT',a=15).emit('DOT3',15,20,21).emit('OUT',a=15).emit('ACCHI',15).emit('OUT',a=15)
        p.emit('ACCSET',a=0,b=1).emit('ACCADD',a=2,b=3).emit('ACCSUB',a=4,b=5)
        p.emit('ACCSAR',15,b=case%64).emit('OUT',a=15).emit('SAT',15).emit('OUT',a=15)
        p.emit('ACCSHL',b=(case*7)%64).emit('ACCLO',15).emit('OUT',a=15)
        p.emit('DIVW',15,6,7).emit('OUT',a=15).emit('ACCHI',15).emit('OUT',a=15)
        for mode in range(3):p.emit('SEXT',15,mode,mode).emit('OUT',a=15)
        # Consecutive writes/readback use separate mesh bank and actual sync RAM.
        for i in range(12):
            p.const(20,case+i).emit('STORE',i,20,255).emit('LOAD',15,20,255).emit('OUT',a=15)
        p.const(20,240).emit('SSTORE',0,20,0).emit('SLOAD',15,20,0).emit('OUT',a=15)
        p.const(20,0).emit('NORM',a=20)
        for i in (0,1,2,10,11,12,20,21,22):p.emit('LOAD',15,20,i).emit('OUT',a=15)
        p.const(20,case&255).const(21,220).emit('COPY',15,20,21)
        for r in range(220,236):p.emit('OUT',a=r)
        p.emit('CMP',a=0,b=1)
        for condition in ['EQ','NE','LT','GE','LTU','GEU','C','NC']:
            p.jump(condition,condition).const(15,0).jump(condition+'_end')
            p.label(condition).const(15,1).label(condition+'_end').emit('OUT',a=15)
        p.jump('sub',link=19).jump('end').label('sub').emit('OUT',a=5).emit('JREG',a=19)
        p.label('end').emit('HALT')
        yield p.finish()
    # Counted backward branches and aliases; no zero-register assumption.
    p=Program().const(0,0).const(1,128).const(2,0).label('loop')
    p.emit('ADDI',0,0,1).emit('STORE',0,0,0).emit('ADD',2,2,0)
    p.emit('CMP',a=0,b=1).jump('loop','NE').emit('OUT',a=2).emit('HALT')
    yield p.finish()

def main():
    OUT.mkdir(parents=True,exist_ok=False)
    frozen=OUT/'frozen';frozen.mkdir()
    for p in [*HERE.glob('*.v'),*HERE.glob('*.cpp'),*HERE.glob('*.py'),*HERE.glob('*.txt'),
              *HERE.parent.glob('kernel/gpu_mesh*.v'),*HERE.parent.glob('dma/gpu_mesh*.v')]:
        shutil.copy2(p,frozen/p.name)
    counts=dict(programs=0,words=0,instructions=0,cycles=0)
    with (OUT/'vectors.bin').open('wb') as f:
        for code in programs():
            expected=run(code)
            assert expected['fault']==0
            words=expected['output']
            f.write(struct.pack('<4I',len(code),len(words),expected['cycles'],0))
            f.write(struct.pack('<%dI'%len(code),*code));f.write(struct.pack('<%dI'%len(words),*words))
            counts['programs']+=1;counts['words']+=len(words)
            counts['instructions']+=expected['instructions'];counts['cycles']+=expected['cycles']
    cmd=['verilator','--cc','--exe','--build','-j','4','-Wno-fatal','--top-module','gpu_mesh_engine',
         '--Mdir',str(OUT/'obj'),*[str(p) for p in frozen.glob('*.v')],str(frozen/'engine_test.cpp'),
         '-CFLAGS','-std=c++17 -O2']
    with (OUT/'build.log').open('w') as log:subprocess.run(cmd,stdout=log,stderr=subprocess.STDOUT,check=True)
    with (OUT/'run.log').open('w') as log:
        subprocess.run([str(OUT/'obj/Vgpu_mesh_engine'),str(OUT/'vectors.bin')],stdout=log,stderr=subprocess.STDOUT,check=True)
    counts['sources']={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in frozen.glob('*') if p.is_file()}
    (OUT/'result.json').write_text(json.dumps(counts,indent=2)+'\n')
    print((OUT/'run.log').read_text(),end='');print(json.dumps(counts,indent=2))
if __name__=='__main__':main()
