#!/usr/bin/env python3
"""Full assembled geometry kernel on real engine RTL with output backpressure."""
import hashlib
import argparse
import json
from pathlib import Path
import random
import struct
import subprocess

from check_microcode import records
from emulator import run
from microcode import build
from reference import decode_inputs,prepared_packet

HERE=Path(__file__).resolve().parent
ENGINE=HERE.parent/'engine'


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--out-name',default='kernel-rtl')
    parser.add_argument('--edges',action='store_true')
    parser.add_argument('--norm',action='store_true')
    parser.add_argument('--compact',action='store_true')
    args=parser.parse_args()
    dest=HERE/'results'/args.out_name;dest.mkdir(parents=True,exist_ok=False)
    program=build(fused=True,fused_edges=args.edges,compact=args.compact,fused_norm=args.norm).finish()
    stats=[];cases=0
    with (dest/'vectors.bin').open('wb') as f:
        def add(inputs,expected):
            nonlocal cases
            bank=[0x12340000|i for i in range(256)] if args.compact else inputs+[0]*(256-len(inputs))
            scratch=[0xa55a0000|i for i in range(256)]
            if args.compact:scratch[232:256]=inputs
            result=run(program,bank=bank,scratch=scratch,compact=args.compact)
            assert result['fault']==0 and result['output']==expected,(cases,inputs,result)
            if args.compact:
                assert result['bank'][:224]==bank[:224]
                assert result['scratch'][:64]==scratch[:64]
                assert result['scratch'][64:127]==scratch[64:127]
            f.write(struct.pack('<4I',len(program),len(expected),result['cycles'],len(inputs)|(0x10000 if args.compact else 0)))
            f.write(struct.pack('<%dI'%len(program),*program))
            f.write(struct.pack('<%dI'%len(expected),*expected))
            f.write(struct.pack('<%dI'%len(inputs),*inputs));cases+=1
            return result['cycles']
        for path in sorted((HERE/'results/reference').glob('*/kernel.bin')):
            scene_cycles=[]
            for inputs,expected in records(path):scene_cycles.append(add(inputs,expected))
            stats.append(dict(scene=path.parent.name,triangles=len(scene_cycles),cycles=sum(scene_cycles),
                              mean_cycles=sum(scene_cycles)/len(scene_cycles),max_cycles=max(scene_cycles)))
        rng=random.Random(0x661104)
        for n in range(1000):
            inputs=[(n&1)|(2 if n%3 else 0)|(4 if n%5==0 else 0),320<<16,240<<16]
            for vertex in range(3):
                inputs += [rng.getrandbits(32),rng.getrandbits(32),rng.getrandbits(32),rng.getrandbits(31),rng.getrandbits(32),rng.getrandbits(16),rng.getrandbits(16)]
            vertices,clip,options=decode_inputs(inputs)
            add(inputs,prepared_packet(vertices,clip,**options))
    cpp=(ENGINE/'engine_test.cpp').read_text()
    old='            reset();bank.fill(0);upload(code);'
    assert cpp.count(old)==1
    cpp=cpp.replace(old,'''            reset();bank.fill(0);
            const bool compact=h[3]&0x10000;h[3]&=65535;
            require(h[3]<=256,"bank count");
            if(compact) {
                for(unsigned i=0;i<256;i++) {
                    bank[i]=0x12340000|i;
                    dut.scratch_we=1;dut.scratch_addr=i;dut.scratch_data=0xa55a0000|i;tick();
                }
                for(unsigned i=0;i<h[3];i++) {
                    uint32_t word;require(fread(&word,4,1,f)==1,"scratch input read");
                    dut.scratch_addr=232+i;dut.scratch_data=word;tick();
                }
                dut.scratch_we=0;
            } else require(fread(bank.data(),4,h[3],f)==h[3],"bank read");
            ownership_check=compact;
            upload(code);''')
    cpp=cpp.replace('static unsigned ticks;', 'static unsigned ticks;\nstatic bool ownership_check;')
    cpp=cpp.replace('    dut.clk=0;dut.eval();', '    dut.clk=0;dut.eval();\n    if(ownership_check&&dut.ownership_violation)throw std::runtime_error("persistent RAM overwritten");')
    cpp=cpp.replace('            words+=expected.size();++count;',
                    '            if(compact)for(unsigned i=0;i<224;i++)require(bank[i]==(0x12340000|i),"mesh vertices overwritten");\n'
                    '            ownership_check=false;words+=expected.size();++count;')
    (dest/'kernel_test.cpp').write_text(cpp)
    sources=[*sorted(ENGINE.glob('gpu_mesh*.v')),HERE/'gpu_mesh_plane.v']
    if (HERE/'gpu_mesh_edges.v').exists():sources.append(HERE/'gpu_mesh_edges.v')
    for source in sources:(dest/source.name).write_bytes(source.read_bytes())
    path=dest/'gpu_mesh_engine.v';text=path.read_text()
    text=text.replace('    input wire clk, reset_n, cancel,',
                      '    output wire ownership_violation,\n    input wire clk, reset_n, cancel,')
    index=text.rindex('endmodule')
    monitor='assign ownership_violation=busy && ((scratch_write && write_addr<=126) || (bank_we && bank_addr<224));\n'
    path.write_text(text[:index]+monitor+text[index:])
    (dest/'emulator.py').write_bytes((ENGINE/'emulator.py').read_bytes())
    (dest/'microcode.py').write_bytes((HERE/'microcode.py').read_bytes())
    command=['verilator','--cc','--exe','--build','-j','2','-Wno-fatal','--top-module','gpu_mesh_engine',
             '--Mdir',str(dest/'obj'),'-CFLAGS','-std=c++17 -O2',
             *[str(dest/p.name) for p in sources],str(dest/'kernel_test.cpp')]
    if args.compact:command+=['-GCOMPACT_LAYOUT=1']
    with (dest/'build.log').open('w') as log:subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,check=True)
    result=subprocess.run([str(dest/'obj/Vgpu_mesh_engine'),str(dest/'vectors.bin')],capture_output=True,text=True)
    (dest/'run.log').write_text(result.stdout+result.stderr)
    if result.returncode:raise RuntimeError(result.stdout+result.stderr)
    summary=dict(passed=True,cases=cases,program_words=len(program),result=result.stdout,scenes=stats,
                 sources={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in dest.glob('*') if p.is_file()})
    (dest/'result.json').write_text(json.dumps(summary,indent=2)+'\n');print(json.dumps(summary))


if __name__=='__main__':main()
