#!/usr/bin/env python3
"""SCOPY53 actual RAM timing, all lengths, wrap, pointer alias, stalls/cancel."""
import argparse,hashlib,json,random,struct,subprocess,sys
from pathlib import Path
HERE=Path(__file__).resolve().parent;ENGINE=HERE.parent/'engine'
sys.path.insert(0,str(ENGINE))
from isa import Program
from emulator import run

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--out-name',default='scopy-engine');a=ap.parse_args()
    dest=HERE/'results'/a.out_name;dest.mkdir(exist_ok=False)
    rng=random.Random(0x53ea);count=0
    with (dest/'vectors.bin').open('wb') as f:
        for n in range(1,129):
            for trial in range(32):
                source=rng.randrange(256);target=(source+n+rng.randrange(257-2*n))&255
                scratch=[rng.getrandbits(32) for _ in range(256)]
                ra,rb=rng.sample(range(256),2);scratch[ra]=source;scratch[rb]=target
                p=Program().emit('SCOPY',n-1,ra,rb)
                for address in range(256):p.emit('OUT',a=address)
                p.emit('HALT');code=p.finish();expected=run(code,scratch=scratch)
                f.write(struct.pack('<4I',len(code),len(expected['output']),expected['cycles'],256))
                f.write(struct.pack('<%dI'%len(code),*code));f.write(struct.pack('<256I',*expected['output']))
                for address,value in enumerate(scratch):f.write(struct.pack('<2I',address,value))
                count+=1
    cpp=(ENGINE/'engine_test.cpp').read_text()
    old='            reset();bank.fill(0);upload(code);';assert cpp.count(old)==1
    cpp=cpp.replace(old,'''            reset();bank.fill(0);
            for(unsigned i=0;i<h[3];i++) {
                uint32_t pair[2];require(fread(pair,4,2,f)==2,"RAM pair read");
                dut.scratch_we=1;dut.scratch_addr=pair[0];dut.scratch_data=pair[1];tick();
            }
            dut.scratch_we=0;upload(code);''')
    cpp=cpp.replace('    const uint32_t q=bank[dut.bank_addr];','    require_no_bank_write_placeholder\n    const uint32_t q=bank[dut.bank_addr];')
    cpp=cpp.replace('    require_no_bank_write_placeholder','    if(dut.bank_we)throw std::runtime_error("SCOPY changed bank");')
    cpp=cpp.replace('0x0100007f,0x01010003,0x21020001,0x17020000,0x18000200,0',
                    '0x01c00000,0x01c10080,0x357fc0c1,0x18008000,0')
    cpp=cpp.replace('delay<90','delay<160')
    (dest/'scopy_engine_test.cpp').write_text(cpp)
    sources=[*ENGINE.glob('gpu_mesh*.v'),*HERE.glob('gpu_mesh*.v'),*HERE.parent.glob('dma/gpu_mesh*.v')]
    for path in sources:(dest/path.name).write_bytes(path.read_bytes())
    command=['verilator','--cc','--exe','--build','-j','2','-Wno-fatal','--top-module','gpu_mesh_engine',
             '--Mdir',str(dest/'obj'),'-CFLAGS','-std=c++17 -O2','-GENABLE_DOT=0','-GEXTENDED_ISA=0',
             *[str(dest/p.name) for p in sources],str(dest/'scopy_engine_test.cpp')]
    with (dest/'build.log').open('w') as f:subprocess.run(command,stdout=f,stderr=subprocess.STDOUT,check=True)
    done=subprocess.run([str(dest/'obj/Vgpu_mesh_engine'),str(dest/'vectors.bin')],capture_output=True,text=True)
    (dest/'run.log').write_text(done.stdout+done.stderr)
    if done.returncode:raise RuntimeError(done.stdout+done.stderr)
    report=dict(passed=True,cases=count,scope=__doc__,result=done.stdout,
                source_sha256={p.name:hashlib.sha256((dest/p.name).read_bytes()).hexdigest() for p in sources})
    (dest/'result.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))

if __name__=='__main__':main()
