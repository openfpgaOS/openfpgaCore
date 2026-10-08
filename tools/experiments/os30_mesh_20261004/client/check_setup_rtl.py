#!/usr/bin/env python3
"""SETUP56 stable sorting/determinants against original scenes and full ranges."""
import argparse
import hashlib
import itertools
import json
from pathlib import Path
import random
import struct
import subprocess
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
KERNEL = HERE.parent / 'kernel'
sys.path.insert(0, str(KERNEL))
from check_microcode import records

ADDRESSES = [137,138,139,140,141,142,143,144,145,176,177,178,179,180,181,199,200]
def signed16(v):
    return (v & 32767) - (v & 32768)

def expected(xy):
    vertices = [(signed16(v), signed16(v >> 16), 235 + 7*i) for i, v in enumerate(xy)]
    original = (vertices[1][0]-vertices[0][0])*(vertices[2][1]-vertices[0][1])
    original -= (vertices[2][0]-vertices[0][0])*(vertices[1][1]-vertices[0][1])
    vertices.sort(key=lambda v: v[1])
    a,b,c = vertices
    delta = [b[0]-a[0], c[0]-a[0], b[1]-a[1], c[1]-a[1]]
    determinant = delta[0]*delta[3] - delta[1]*delta[2]
    return [v & 0xffffffff for v in [*[v[2] for v in vertices],
            *[q for v in vertices for q in v[:2]], *delta,
            determinant, determinant >> 32, -original, (-original) >> 32]]

def fixtures():
    for path in sorted((KERNEL / 'results/reference').glob('*/kernel.bin')):
        for inputs, _ in records(path):
            yield [inputs[3], inputs[10], inputs[17]]
    # Repeated Y/X and corner coordinates cover all stable sorting parities,
    # zero areas, 17-bit deltas, and signed high determinant halves.
    for coords in itertools.product([-32768, -1, 0, 1, 32767], repeat=6):
        yield [(coords[2*i]&65535)|((coords[2*i+1]&65535)<<16) for i in range(3)]
    rng = random.Random(0x560004)
    for _ in range(50000):
        yield [rng.getrandbits(32) for i in range(3)]

CPP = r'''
#include "Vgpu_mesh_setup.h"
#include "verilated.h"
#include <array>
#include <cstdint>
#include <cstdio>
#include <stdexcept>
static Vgpu_mesh_setup dut;
static std::array<uint32_t,256> scratch;
static unsigned writes;
static bool monitor;
static const std::array<unsigned,17> addresses={137,138,139,140,141,142,143,144,145,176,177,178,179,180,181,199,200};
static void require(bool ok,const char *what){if(!ok)throw std::runtime_error(what);}
static void tick(){
    dut.clk=0;dut.eval();
    const uint32_t qa=scratch[dut.scratch_ra],qb=scratch[dut.scratch_rb];
    const int64_t product=int64_t(int32_t(dut.mul_a))*int32_t(dut.mul_b);
    if(dut.scratch_we){
        bool permitted=false;for(auto a:addresses)permitted|=a==dut.scratch_wa;
        require(permitted,"write outside contract");scratch[dut.scratch_wa]=dut.scratch_wd;
        if(monitor)writes++;
    }
    dut.clk=1;dut.eval();dut.scratch_qa=qa;dut.scratch_qb=qb;dut.product=uint64_t(product);dut.eval();
}
static void reset(){dut.reset_n=0;dut.start=0;dut.cancel=0;tick();tick();dut.reset_n=1;tick();}
static unsigned execute(){
    writes=0;monitor=true;dut.start=1;tick();dut.start=0;
    for(unsigned cycles=1;cycles<100;cycles++){
        tick();if(dut.done){monitor=false;require(writes==17,"wrong write count");return cycles;}
    }throw std::runtime_error("timeout");
}
int main(int argc,char **argv){try{
    if(argc!=2)return 2;FILE*f=fopen(argv[1],"rb");require(f,"open vectors");reset();
    unsigned cases=0,expected_cycles=0;uint32_t record[20];
    while(fread(record,4,20,f)==20){
        for(unsigned i=0;i<256;i++)scratch[i]=0x35170000u^i;
        scratch[235]=record[0];scratch[242]=record[1];scratch[249]=record[2];
        auto before=scratch;unsigned cycles=execute();
        if(!cases)expected_cycles=cycles;require(cycles==expected_cycles,"data dependent cycles");
        for(unsigned i=0;i<17;i++){
            if(scratch[addresses[i]]!=record[3+i]){
                fprintf(stderr,"case%u reg%u got%08x expected%08x\n",cases,addresses[i],scratch[addresses[i]],record[3+i]);
                throw std::runtime_error("arithmetic mismatch");
            }before[addresses[i]]=record[3+i];
        }
        require(scratch==before,"unlisted scratch changed");cases++;
    }fclose(f);
    unsigned aborts=0;
    for(unsigned delay=0;delay<45;delay++){
        reset();dut.start=1;tick();dut.start=0;for(unsigned i=0;i<delay;i++)tick();
        dut.cancel=1;dut.clk=0;dut.eval();require(!dut.scratch_we,"cancel write leak");tick();
        dut.cancel=0;auto before=scratch;for(unsigned i=0;i<4;i++)tick();
        require(!dut.busy&&!dut.done&&scratch==before,"cancel stale activity");execute();aborts++;
    }
    printf("PASS %u cases, %u cycles/setup, %u cancellation phases, exact writes and ownership\n",cases,expected_cycles,aborts);
}catch(const std::exception&e){fprintf(stderr,"FAIL %s\n",e.what());return 1;}return 0;}
'''

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--label', default='setup-rtl-v1')
    a = p.parse_args()
    dest = ROOT / 'docs/measurements/os30-mesh-20261004' / a.label
    dest.mkdir(exist_ok=False)
    count=0
    with (dest / 'vectors.bin').open('wb') as f:
        for xy in fixtures():
            f.write(struct.pack('<20I', *xy, *expected(xy)));count+=1
    (dest / 'setup_test.cpp').write_text(CPP)
    (dest / 'gpu_mesh_setup.v').write_bytes((KERNEL / 'gpu_mesh_setup.v').read_bytes())
    command=['verilator','--cc','--exe','--build','-j','2','-Wno-fatal','--top-module','gpu_mesh_setup',
             '--Mdir',str(dest/'obj'),'-CFLAGS','-std=c++17 -O2',str(dest/'gpu_mesh_setup.v'),str(dest/'setup_test.cpp')]
    with (dest/'build.log').open('w') as log:
        subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,check=True)
    done=subprocess.run([str(dest/'obj/Vgpu_mesh_setup'),str(dest/'vectors.bin')],capture_output=True,text=True)
    (dest/'run.log').write_text(done.stdout+done.stderr)
    if done.returncode:raise RuntimeError(done.stdout+done.stderr)
    report=dict(passed=True,cases=count,result=done.stdout,command=command,
                sources={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in dest.iterdir() if p.is_file()})
    (dest/'result.json').write_text(json.dumps(report,indent=2)+'\n');print(done.stdout,end='')

if __name__=='__main__':main()
