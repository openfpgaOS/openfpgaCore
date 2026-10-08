#!/usr/bin/env python3
"""SETUP56 real-engine shared DSP and register ownership regression."""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import sys
from check_setup_rtl import ADDRESSES, expected

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[3]
ENGINE=HERE.parent/'engine'
sys.path.insert(0,str(ENGINE))
from isa import Program
from emulator import run

p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--label',default='setup-engine-v1')
a=p.parse_args()
dest=ROOT/'docs/measurements/os30-mesh-20261004'/a.label;dest.mkdir(exist_ok=False)
code=Program().emit('SETUP')
for address in ADDRESSES:code.emit('OUT',a=address)
code.emit('HALT');program=code.finish()
count=0
with (dest/'vectors.bin').open('wb') as f:
    raw=(dest.parent/'setup-rtl-v1/vectors.bin').read_bytes()
    for record in struct.iter_unpack('<20I',raw):
        scratch=[0]*256
        for address,value in zip((235,242,249),record[:3]):scratch[address]=value
        result=run(program,scratch=scratch,compact=True)
        assert result['output']==list(record[3:])==expected(record[:3])
        f.write(struct.pack('<4I',len(program),17,result['cycles'],3))
        f.write(struct.pack('<%dI'%len(program),*program));f.write(struct.pack('<17I',*record[3:]))
        for pair in zip((235,242,249),record[:3]):f.write(struct.pack('<2I',*pair))
        count+=1
cpp=(ENGINE/'engine_test.cpp').read_text()
cpp=cpp.replace('#include "Vgpu_mesh_engine.h"','#include "Vgpu_mesh_engine.h"\n#include "Vgpu_mesh_engine___024root.h"')
cpp=cpp.replace('static unsigned ticks;','static unsigned ticks;\nstatic bool setup_monitor;')
cpp=cpp.replace('    const uint32_t q=bank[dut.bank_addr];','''    if(setup_monitor && dut.rootp->gpu_mesh_engine__DOT__scratch_write) {
        unsigned a=dut.rootp->gpu_mesh_engine__DOT__scratch_port_a_addr;
        require((a>=137&&a<=145)||(a>=176&&a<=181)||a==199||a==200,"setup wrote forbidden scratch");
    }
    const uint32_t q=bank[dut.bank_addr];''')
# require() is declared below tick in the reused harness.
cpp=cpp.replace('static void tick(){','static void require(bool,const char *);\nstatic void tick(){')
old='            reset();bank.fill(0);upload(code);';assert cpp.count(old)==1
cpp=cpp.replace(old,'''            setup_monitor=false;reset();bank.fill(0);
            for(unsigned i=0;i<h[3];i++) {
                uint32_t pair[2];require(fread(pair,4,2,f)==2,"scratch pair read");
                dut.scratch_we=1;dut.scratch_addr=pair[0];dut.scratch_data=pair[1];tick();
            }
            dut.scratch_we=0;upload(code);setup_monitor=true;''')
cpp=cpp.replace('        // Cancellation at every phase','        setup_monitor=false;\n        // Cancellation at every phase')
(dest/'setup_engine_test.cpp').write_text(cpp)
sources=sorted(p for directory in ('engine','dma','kernel') for p in (HERE.parent/directory).glob('gpu_mesh*.v'))
for source in sources:(dest/source.name).write_bytes(source.read_bytes())
(dest/'emulator.py').write_bytes((ENGINE/'emulator.py').read_bytes())
command=['verilator','--cc','--exe','--build','-j','2','-Wno-fatal','--top-module','gpu_mesh_engine',
         '--Mdir',str(dest/'obj'),'-CFLAGS','-std=c++17 -O2','-GCOMPACT_LAYOUT=1','-GENABLE_DOT=0','-GEXTENDED_ISA=0',
         '-GCODE_PAGES=2',*[str(dest/source.name) for source in sources],str(dest/'setup_engine_test.cpp')]
with (dest/'build.log').open('w') as log:subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,check=True)
done=subprocess.run([str(dest/'obj/Vgpu_mesh_engine'),str(dest/'vectors.bin')],capture_output=True,text=True)
(dest/'run.log').write_text(done.stdout+done.stderr)
if done.returncode:raise RuntimeError(done.stdout+done.stderr)
report=dict(passed=True,cases=count,result=done.stdout,cycles_per_setup=32,command=command,
            sources={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in dest.iterdir() if p.is_file()})
(dest/'result.json').write_text(json.dumps(report,indent=2)+'\n');print(done.stdout,end='')
