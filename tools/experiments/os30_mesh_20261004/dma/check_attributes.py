#!/usr/bin/env python3
"""Independent attribute expansion reference versus shared-DSP RTL."""
import argparse,hashlib,json,random,shutil,struct,subprocess,sys
from pathlib import Path
HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[3]
from check_gpu_replay import kernel_records

def signed(x):return x-(1<<32) if x&(1<<31) else x

def attributes(vertex,combine):
    _,s,t,zi,depth,c,d=vertex
    return [((signed(s)*signed(zi))>>16)&0xffffffff,((signed(t)*signed(zi))>>16)&0xffffffff,zi,
            ((c>>5)&63)<<16,((c>>11)&31)<<16,(c&31)<<16,depth,
            ((d>>11)&31)<<16,((d>>5)&63)<<16,(d&31)<<16]

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--out',type=Path,required=True);a=ap.parse_args()
    out=a.out.resolve();out.mkdir(parents=True,exist_ok=False);rng=random.Random(0x525252);counts=dict(captured_vertices=0,random_vertices=0)
    with (out/'vectors.bin').open('wb') as f:
        def emit(vertex,combine):
            src=rng.randrange(256);dst=rng.randrange(256);cr=rng.randrange(256)
            while cr in [(src+i)&255 for i in range(7)]:cr=rng.randrange(256)
            f.write(struct.pack('<21I',*vertex,combine,*attributes(vertex,combine),src,dst,cr))
        for path in sorted(HERE.parent.glob('kernel/results/reference/*/kernel.bin')):
            for words,_ in kernel_records(path):
                for i in range(3):emit(words[3+i*7:10+i*7],words[0]&4);counts['captured_vertices']+=1
        for n in range(65536):
            vertex=[rng.getrandbits(32) for _ in range(7)]
            vertex[5]=(vertex[5]&0xffff0000)|n;vertex[6]=(vertex[6]&0xffff0000)|((n*573+997)&65535)
            if n<16:
                vertex[1:4]=[[0,1,0xffffffff,0x80000000][(n+i)%4] for i in range(3)]
            emit(vertex,0 if n&1 else rng.randrange(1,1<<32));counts['random_vertices']+=1
    sources=[HERE/'gpu_mesh_attributes.v',HERE/'attributes_test.cpp',Path(__file__)]
    for p in sources:shutil.copy2(p,out/p.name)
    (out/'manifest.json').write_text(json.dumps({p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sources},indent=2)+'\n')
    command=['verilator','--cc','--exe','--build','-j','3','--top-module','gpu_mesh_attributes','--Mdir',str(out/'obj'),
             str(out/'gpu_mesh_attributes.v'),str(out/'attributes_test.cpp'),'-CFLAGS','-std=c++17 -O2']
    with (out/'build.log').open('w') as f:subprocess.run(command,stdout=f,stderr=subprocess.STDOUT,check=True)
    with (out/'run.log').open('w') as f:subprocess.run([str(out/'obj/Vgpu_mesh_attributes'),str(out/'vectors.bin')],stdout=f,stderr=subprocess.STDOUT,check=True)
    result=json.loads((out/'run.log').read_text().splitlines()[-1]);result.update(counts)
    (out/'result.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))
if __name__=='__main__':main()
