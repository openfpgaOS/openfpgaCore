#!/usr/bin/env python3
"""Exercise frozen cacheless IMF RTL with real memory/subword/FP/branch hazards."""
import argparse,json,struct,subprocess
from pathlib import Path

def words(values):return struct.pack('<%dI'%len(values),*[x&0xffffffff for x in values])
def build():
    p=[]
    def emit(x):p.append(x)
    def li(d,v):
        v &= 0xffffffff;hi=(v+0x800)>>12;lo=v-(hi<<12)
        emit(((hi&0xfffff)<<12)|(d<<7)|0x37);emit(((lo&4095)<<20)|(d<<15)|(d<<7)|0x13)
    def load(d,a,off=0,fun=2):emit(((off&4095)<<20)|(a<<15)|(fun<<12)|(d<<7)|3)
    def store(s,a,off=0,fun=2):emit((((off&4095)>>5)<<25)|(s<<20)|(a<<15)|(fun<<12)|((off&31)<<7)|0x23)
    def rr(d,a,b,fun=0,upper=0):emit((upper<<25)|(b<<20)|(a<<15)|(fun<<12)|(d<<7)|0x33)
    def fp(d,a,b,upper,rm=0):emit((upper<<25)|(b<<20)|(a<<15)|(rm<<12)|(d<<7)|0x53)
    def out(r):store(r,1,4)
    li(1,0x20000);li(2,0x6000);emit((0x300<<20)|(2<<15)|(2<<12)|0x73)
    li(3,0x10000);li(4,0x123);li(5,-2)
    store(4,3);store(4,3,1,0);store(5,3,2,1)
    load(6,3);out(6);load(7,3,2,5);out(7);load(8,3,3,0);out(8)
    rr(9,5,4,0,1);out(9);rr(10,9,5,4,1);out(10)
    load(11,1,16);load(12,1,16);rr(13,11,12);load(14,1,16);rr(13,13,14);out(13)
    li(20,0x3fc00000);fp(1,20,0,0x78);li(21,0x40100000);fp(2,21,0,0x78)
    fp(3,1,2,0);fp(22,3,0,0x70);out(22)
    fp(3,1,2,8);fp(22,3,0,0x70);out(22)
    fp(3,1,2,12);fp(22,3,0,0x70);out(22)
    emit((1<<27)|(2<<20)|(1<<15)|(3<<7)|0x43);fp(22,3,0,0x70);out(22)
    fp(22,2,0,0x60,1);out(22);fp(3,9,0,0x68);fp(22,3,0,0x70);out(22)
    li(23,5);li(24,0);loop=len(p)*4
    rr(24,24,23);emit((0xfff<<20)|(23<<15)|(23<<7)|0x13)
    delta=loop-len(p)*4;imm=delta&8191
    emit((((imm>>12)&1)<<31)|(((imm>>5)&63)<<25)|(23<<15)|(1<<12)|(((imm>>1)&15)<<8)|(((imm>>11)&1)<<7)|0x63)
    out(24);li(25,1);store(25,1);emit(0x6f)
    expect=[0xfffe2323,0xfffe,0xffffffff,-582,291,0x12345678+0x2468+17,
            0x40700000,0x40580000,0x3f2aaaab,0x409c0000,2,struct.unpack('<I',struct.pack('<f',-582))[0],15]
    return words(p),words(expect)
def main():
    ap=argparse.ArgumentParser();ap.add_argument('--sim',type=Path,required=True);ap.add_argument('--out',type=Path,required=True);a=ap.parse_args()
    a.out.mkdir(parents=True,exist_ok=True);program,expected=build();(a.out/'smoke.bin').write_bytes(program);(a.out/'input.bin').write_bytes(words([0x12345678,0x2468,17]));(a.out/'expected.bin').write_bytes(expected)
    records=[]
    cases=[(0,1,1,None)]+[(45,9,s,None) for s in range(1,17)]+[(35,7,99,r) for r in [1,20,60,100,150,225,300,400]]
    for i,(stall,delay,seed,reset) in enumerate(cases):
        stem=a.out/f'case-{i}';cmd=[str(a.sim.resolve()),'--bin',str(a.out/'smoke.bin'),'--input',str(a.out/'input.bin'),'--out',str(stem.with_suffix('.bin')),'--stats',str(stem.with_suffix('.json')),'--stall-pct',str(stall),'--max-delay',str(delay),'--seed',str(seed),'--max-cycles','100000']
        if reset is not None:cmd+=['--reset-at',str(reset)]
        with stem.with_suffix('.log').open('w') as log:subprocess.run(cmd,stdout=log,stderr=subprocess.STDOUT,check=True)
        assert stem.with_suffix('.bin').read_bytes()==expected,(i,'output mismatch')
        records.append(json.loads(stem.with_suffix('.json').read_text()))
    (a.out/'result.json').write_text(json.dumps({'cases':len(records),'exact_words_per_case':len(expected)//4,'program_words':len(program)//4,'results':records},indent=2)+'\n')
    print(f'PASS {len(records)} native-bus cases; {len(expected)//4} exact integer/FP/subword/branch output words each')
if __name__=='__main__':main()
