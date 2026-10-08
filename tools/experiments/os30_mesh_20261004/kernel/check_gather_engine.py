#!/usr/bin/env python3
"""GATHER50 through actual engine RAM arbitration, with exact cycles/ownership."""
import argparse
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
    parser=argparse.ArgumentParser();parser.add_argument('--lean',action='store_true');args=parser.parse_args()
    dest=HERE/'results'/('gather-engine-lean' if args.lean else 'gather-engine');dest.mkdir(exist_ok=False)
    count=0
    with (dest/'vectors.bin').open('wb') as f:
        raw=(HERE/'results/gather-rtl-final/vectors.bin').read_bytes()
        for record in struct.iter_unpack('<25I',raw):
            ordinal,packed=record[:2];index=packed>>((ordinal&1)*16)
            bank=[0]*256;scratch=[0]*256;scratch[ordinal>>1]=packed
            pairs=[(ordinal>>1,packed)]
            for i in range(3):
                for j in range(7):
                    address=7*((index>>(5*i))&31)+j;value=record[2+i*7+j]
                    bank[address]=value;pairs.append((256+address,value))
            p=Program().const(200,ordinal).const(201,64).emit('GATHER',62,200,201)
            for addr in (62,63,*range(64,85)):p.emit('OUT',a=addr)
            p.emit('HALT');program=p.finish()
            expected=[*record[23:25],*record[2:23]]
            result=run(program,bank=bank,scratch=scratch)
            assert result['output']==expected,(count,result,expected)
            f.write(struct.pack('<4I',len(program),len(expected),result['cycles'],len(pairs)))
            f.write(struct.pack('<%dI'%len(program),*program));f.write(struct.pack('<23I',*expected))
            for pair in pairs:f.write(struct.pack('<2I',*pair))
            count+=1
    cpp=(ENGINE/'engine_test.cpp').read_text()
    old='            reset();bank.fill(0);upload(code);'
    assert cpp.count(old)==1
    cpp=cpp.replace(old,'''            reset();bank.fill(0);
            for(unsigned i=0;i<h[3];i++) {
                uint32_t pair[2];require(fread(pair,4,2,f)==2,"RAM pair read");
                if(pair[0]&256)bank[pair[0]&255]=pair[1];
                else {dut.scratch_we=1;dut.scratch_addr=pair[0];dut.scratch_data=pair[1];tick();}
            }
            dut.scratch_we=0;upload(code);''')
    cpp=cpp.replace('    dut.clk=0;dut.eval();','    dut.clk=0;dut.eval();\n    if(dut.gather_ownership_violation)throw std::runtime_error("gather wrote unrelated storage");')
    # Generic cancellation fixtures are checked by the full engine campaign;
    # this second run targets the pruned configuration's GATHER behavior.
    if args.lean:
        a=cpp.index('        // Cancellation at every phase')
        b=cpp.index('        reset();upload({0xff000000});',a)
        cpp=cpp[:a]+'        unsigned aborts=0;\n'+cpp[b:]
    (dest/'gather_engine_test.cpp').write_text(cpp)
    sources=[*sorted(ENGINE.glob('gpu_mesh*.v')),*sorted(HERE.glob('gpu_mesh*.v')),HERE.parent/'dma/gpu_mesh_vertex.v']
    for source in sources:(dest/source.name).write_bytes(source.read_bytes())
    path=dest/'gpu_mesh_engine.v';text=path.read_text()
    text=text.replace('    input wire clk, reset_n, cancel,',
                      '    output wire gather_ownership_violation,\n    input wire clk, reset_n, cancel,')
    index=text.rindex('endmodule')
    monitor='assign gather_ownership_violation=gather_busy && ((scratch_write && write_addr!=62 && write_addr!=63 && (write_addr<64 || write_addr>84)) || bank_we);\n'
    path.write_text(text[:index]+monitor+text[index:])
    (dest/'emulator.py').write_bytes((ENGINE/'emulator.py').read_bytes())
    command=['verilator','--cc','--exe','--build','-j','2','-Wno-fatal','--top-module','gpu_mesh_engine',
             '--Mdir',str(dest/'obj'),'-CFLAGS','-std=c++17 -O2',
             *[str(dest/p.name) for p in sources],str(dest/'gather_engine_test.cpp')]
    if args.lean:command+=['-GENABLE_DOT=0','-GEXTENDED_ISA=0']
    with (dest/'build.log').open('w') as log:subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,check=True)
    done=subprocess.run([str(dest/'obj/Vgpu_mesh_engine'),str(dest/'vectors.bin')],capture_output=True,text=True)
    (dest/'run.log').write_text(done.stdout+done.stderr)
    if done.returncode:raise RuntimeError(done.stdout+done.stderr)
    result=dict(passed=True,cases=count,lean=args.lean,program_words=len(program),fixture_cycles=result['cycles'],
                result=done.stdout,sources={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in dest.glob('*') if p.is_file()})
    (dest/'result.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))


if __name__=='__main__':main()
