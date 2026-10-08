#!/usr/bin/env python3
"""Execute PROJ47 on the real shared-arithmetic engine for every unit fixture."""
import hashlib
import json
from pathlib import Path
import struct
import subprocess

from microcode import Program
from emulator import run

HERE=Path(__file__).resolve().parent
ENGINE=HERE.parent/'engine'


def main():
    dest=HERE/'results/project-engine';dest.mkdir(exist_ok=False)
    p=Program().const(200,64).const(201,232).emit('PROJ',a=200,b=201)
    for i in range(232,239):p.emit('OUT',a=i)
    p.emit('HALT');program=p.finish();count=0
    with (dest/'vectors.bin').open('wb') as f:
        raw=(HERE/'results/project-rtl/vectors.bin').read_bytes()
        for record in struct.iter_unpack('<18I',raw):
            pairs=[(64+i,value) for i,value in enumerate(record[:7])]+[(49+i,value) for i,value in enumerate(record[7:11])]
            scratch=[0]*256
            for address,value in pairs:scratch[address]=value
            result=run(program,scratch=scratch)
            assert result['output']==list(record[11:])
            f.write(struct.pack('<4I',len(program),7,result['cycles'],len(pairs)))
            f.write(struct.pack('<%dI'%len(program),*program));f.write(struct.pack('<7I',*record[11:]))
            for pair in pairs:f.write(struct.pack('<2I',*pair))
            count+=1
    cpp=(ENGINE/'engine_test.cpp').read_text()
    old='            reset();bank.fill(0);upload(code);'
    assert cpp.count(old)==1
    cpp=cpp.replace(old,'''            reset();bank.fill(0);
            for(unsigned i=0;i<h[3];i++) {
                uint32_t pair[2];require(fread(pair,4,2,f)==2,"scratch pair read");
                dut.scratch_we=1;dut.scratch_addr=pair[0];dut.scratch_data=pair[1];tick();
            }
            dut.scratch_we=0;upload(code);''')
    (dest/'project_engine_test.cpp').write_text(cpp)
    sources=[*sorted(ENGINE.glob('gpu_mesh*.v')),*sorted(HERE.glob('gpu_mesh*.v')),HERE.parent/'dma/gpu_mesh_vertex.v']
    for source in sources:(dest/source.name).write_bytes(source.read_bytes())
    (dest/'emulator.py').write_bytes((ENGINE/'emulator.py').read_bytes())
    command=['verilator','--cc','--exe','--build','-j','2','-Wno-fatal','--top-module','gpu_mesh_engine',
             '--Mdir',str(dest/'obj'),'-CFLAGS','-std=c++17 -O2',
             *[str(dest/p.name) for p in sources],str(dest/'project_engine_test.cpp')]
    with (dest/'build.log').open('w') as log:subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,check=True)
    done=subprocess.run([str(dest/'obj/Vgpu_mesh_engine'),str(dest/'vectors.bin')],capture_output=True,text=True)
    (dest/'run.log').write_text(done.stdout+done.stderr)
    if done.returncode:raise RuntimeError(done.stdout+done.stderr)
    result=dict(passed=True,cases=count,program_words=len(program),cycles_per_vertex=result['cycles'],
                result=done.stdout,sources={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in dest.glob('*') if p.is_file()})
    (dest/'result.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))


if __name__=='__main__':main()
