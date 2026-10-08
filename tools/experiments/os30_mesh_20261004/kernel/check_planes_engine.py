#!/usr/bin/env python3
"""Batched planes against independent exact oracle, real RAM/stalls/cancel."""
import argparse,hashlib,json,random,struct,subprocess,sys
from pathlib import Path
from check_microcode import records
from check_plane_rtl import contexts,plane
from reference import signed,word
HERE=Path(__file__).resolve().parent;ENGINE=HERE.parent/'engine'
sys.path.insert(0,str(ENGINE))
from isa import Program
from emulator import run

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--out-name',default='planes-engine');a=ap.parse_args()
    dest=HERE/'results'/a.out_name;dest.mkdir(exist_ok=False)
    rng=random.Random(0x54ba);cases=0;total_planes=0
    def fixture(groups,f):
        nonlocal cases,total_planes
        base=rng.randrange(256);scratch=[rng.getrandbits(32) for _ in range(256)];bank=[0]*256
        ctx=groups[0][3:13]
        for address,value in zip((100,101,102,103,106,107,108,12,21,22),ctx):scratch[address]=value
        scratch[17]=len(groups);scratch[110]=base;scratch[111]=(base+10)&255
        expected=[]
        for index,record in enumerate(groups):
            for vertex in range(3):bank[(base+10*vertex+index)&255]=record[vertex]
            width=32 if index in (0,1,2,6) else 24
            expected.extend(word(signed(x,width)) for x in record[13:16])
        p=Program().emit('PLANES',17,110,111)
        for index in range(256):p.emit('OUT',a=index)
        p.emit('HALT');code=p.finish();expected+=scratch
        result=run(code,bank=bank,scratch=scratch)
        assert result['output']==expected and result['scratch']==scratch and result['bank']==bank,cases
        pairs=[*enumerate(scratch),*((256+i,x) for i,x in enumerate(bank))]
        f.write(struct.pack('<4I',len(code),len(expected),result['cycles'],len(pairs)))
        f.write(struct.pack('<%dI'%len(code),*code));f.write(struct.pack('<%dI'%len(expected),*expected))
        for pair in pairs:f.write(struct.pack('<2I',*pair))
        cases+=1;total_planes+=len(groups)
    with (dest/'vectors.bin').open('wb') as f:
        for source in sorted((HERE/'results/reference').glob('*/kernel.bin')):
            for inputs,expected in records(source):fixture(list(contexts(inputs)),f)
        for trial in range(10000):
            count=trial%10+1
            ctx=[rng.randrange(-65535,65536) for _ in range(4)]
            ctx += [rng.getrandbits(31),rng.randrange(2),rng.randrange(2),rng.randrange(2),rng.randrange(-32768,32768),rng.randrange(-32768,32768)]
            groups=[]
            for index in range(count):
                values=[signed(rng.getrandbits(32)) for _ in range(3)]
                if trial%11==0 or index<trial%count:values=[values[0]]*3
                if trial%13==0:values=[-2**31,2**31-1,0]
                groups.append([*map(word,values+ctx),*plane(values,ctx)])
            fixture(groups,f)
    cpp=(ENGINE/'engine_test.cpp').read_text();old='            reset();bank.fill(0);upload(code);';assert cpp.count(old)==1
    cpp=cpp.replace(old,'''            reset();bank.fill(0);
            for(unsigned i=0;i<h[3];i++) {
                uint32_t pair[2];require(fread(pair,4,2,f)==2,"RAM pair read");
                if(pair[0]&256)bank[pair[0]&255]=pair[1];
                else {dut.scratch_we=1;dut.scratch_addr=pair[0];dut.scratch_data=pair[1];tick();}
            }
            dut.scratch_we=0;upload(code);''')
    cpp=cpp.replace('    const uint32_t q=bank[dut.bank_addr];','    if(dut.bank_we)throw std::runtime_error("PLANES wrote bank");\n    const uint32_t q=bank[dut.bank_addr];')
    cpp=cpp.replace('0x0100007f,0x01010003,0x21020001,0x17020000,0x18000200,0',
                    '0x0111000a,0x016e0040,0x016f004a,0x36116e6f,0')
    cpp=cpp.replace('delay<90','delay<450')
    (dest/'planes_engine_test.cpp').write_text(cpp)
    sources=[*ENGINE.glob('gpu_mesh*.v'),*HERE.glob('gpu_mesh*.v'),*HERE.parent.glob('dma/gpu_mesh*.v')]
    for path in sources:(dest/path.name).write_bytes(path.read_bytes())
    command=['verilator','--cc','--exe','--build','-j','2','-Wno-fatal','--top-module','gpu_mesh_engine',
             '--Mdir',str(dest/'obj'),'-CFLAGS','-std=c++17 -O2','-GENABLE_DOT=0','-GEXTENDED_ISA=0',
             *[str(dest/p.name) for p in sources],str(dest/'planes_engine_test.cpp')]
    with (dest/'build.log').open('w') as f:subprocess.run(command,stdout=f,stderr=subprocess.STDOUT,check=True)
    done=subprocess.run([str(dest/'obj/Vgpu_mesh_engine'),str(dest/'vectors.bin')],capture_output=True,text=True)
    (dest/'run.log').write_text(done.stdout+done.stderr)
    if done.returncode:raise RuntimeError(done.stdout+done.stderr)
    report=dict(passed=True,cases=cases,planes=total_planes,scope=__doc__,result=done.stdout,
                source_sha256={p.name:hashlib.sha256((dest/p.name).read_bytes()).hexdigest() for p in sources})
    (dest/'result.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))

if __name__=='__main__':main()
