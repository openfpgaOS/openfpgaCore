#!/usr/bin/env python3
"""Compare shared-resource vertex RTL against integer reference, captures+fuzz."""
import argparse,hashlib,json,random,shutil,struct,subprocess,sys
from pathlib import Path
HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[3]
sys.path.insert(0,str(HERE.parent/'client'))
from mesh_transform import transform_vertex,word
from analyze import OUT,read

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--out',type=Path,required=True)
    ap.add_argument('--random',type=int,default=32768);a=ap.parse_args()
    out=a.out.resolve();out.mkdir(parents=True,exist_ok=False)
    rng=random.Random(0x48465047);counts={'captured_vertices':0,'random_vertices':0}
    with (out/'vectors.bin').open('wb') as f:
        def emit(raw,state):
            result=[word(x) for x in transform_vertex(raw,state)]
            record=[*raw,*state[:24],*result,rng.randrange(253),rng.randrange(250)]
            assert len(record)==37;f.write(struct.pack('<37I',*record))
        for p in sorted((OUT/'run-360-8').glob('mesh-*.bin')):
            for j in read(p)[1]:
                for i in range(j['header'][3]):emit(j['vertices'][i*4:i*4+4],j['state']);counts['captured_vertices']+=1
        for n in range(a.random):
            state=[rng.getrandbits(32) for _ in range(64)]
            state[22]=[0,0x10000,0x20000,0x30000][n%4]
            raw=[rng.getrandbits(32) for _ in range(4)]
            if n%8==0:state[12:16]=[0,0,0,[0,1,0xffffffff,0x7fffffff,0x80000000][n//8%5]]
            emit(raw,state);counts['random_vertices']+=1
    source=[HERE/'gpu_mesh_vertex.v',HERE/'vertex_test.cpp',HERE.parent/'client/mesh_transform.py']
    for p in source:shutil.copy2(p,out/p.name)
    (out/'manifest.json').write_text(json.dumps({str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in source},indent=2)+'\n')
    command=['verilator','--cc','--exe','--build','-j','3','--top-module','gpu_mesh_vertex',
        '--Mdir',str(out/'obj'),'-CFLAGS','-O2 -std=c++17',str(out/'gpu_mesh_vertex.v'),str(out/'vertex_test.cpp')]
    with (out/'build.log').open('w') as f:subprocess.run(command,stdout=f,stderr=subprocess.STDOUT,check=True)
    with (out/'run.log').open('w') as f:subprocess.run([str(out/'obj/Vgpu_mesh_vertex'),str(out/'vectors.bin')],stdout=f,stderr=subprocess.STDOUT,check=True)
    result=json.loads((out/'run.log').read_text().splitlines()[-1]);result.update(counts)
    result['captured_cycles_per_vertex']=result['captured_cycles']/counts['captured_vertices']
    result['scope']='Actual vertex RTL with synchronous scratch/bank and shared-DSP model; divider callback holds its agreed bits+2 latency. Full engine integration remains separate.'
    (out/'result.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))
if __name__=='__main__':main()
