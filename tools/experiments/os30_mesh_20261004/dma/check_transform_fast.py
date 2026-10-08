#!/usr/bin/env python3
"""Check the fast shader against original ISA on captures and boundary inputs."""
import argparse,collections,hashlib,json,random,sys
from pathlib import Path
HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[3]
sys.path.insert(0,str(HERE.parent/'client'))
sys.path.insert(0,str(HERE.parent/'engine'))
from mesh_transform import build_transform,transform_vertex,word
from transform_fast import build_transform_fast
from analyze import OUT,read
from emulator import run

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--out',type=Path,required=True)
    ap.add_argument('--random',type=int,default=32768);a=ap.parse_args()
    original=build_transform().finish();fast=build_transform_fast().finish()
    counts=collections.Counter();rng=random.Random(0x6250414c)
    jobs=[j for p in sorted((OUT/'run-360-8').glob('mesh-*.bin')) for j in read(p)[1]]
    def check(j,label,reference=False):
        raw=j['vertices'];s=j['state'];bank=[0]*256
        bank[:len(raw)]=raw;bank[128:192]=s;bank[192:192+len(j['indices'])]=j['indices']
        scratch=[0]*256;scratch[62],scratch[63]=j['header'][3:5]
        left=run(original,bank,scratch);right=run(fast,bank,scratch)
        assert not left['fault'] and not right['fault'],label
        vertices=j['header'][3]
        assert left['bank'][:vertices*7]==right['bank'][:vertices*7],label
        assert left['scratch'][:88]==right['scratch'][:88],label
        if reference:
            expected=[word(x) for i in range(vertices) for x in transform_vertex(raw[i*4:i*4+4],s)]
            assert right['bank'][:len(expected)]==expected,label
        return left['cycles'],right['cycles'],vertices
    for i,j in enumerate(jobs):
        before,after,vertices=check(j,('capture',i),True)
        counts['capture_jobs']+=1;counts['capture_vertices']+=vertices
        counts['capture_original_cycles']+=before;counts['capture_fast_cycles']+=after
    for n in range(a.random):
        # Arbitrary signed matrix/light/UV/fog fields exercise wide fallback,
        # zero and negative W, saturation, signed high-word corrections, and
        # extrema. Compare original ISA including its explicit modular widths.
        state=[rng.getrandbits(32) for _ in range(64)]
        state[22]=[0,0x10000,0x20000,0x30000][n%4]
        raw=[rng.getrandbits(32) for _ in range(4)]
        if n%8==0:
            state[12:16]=[0,0,0,[0,1,0xffffffff,0x7fffffff,0x80000000][n//8%5]]
        j=dict(state=state,vertices=raw,indices=[0],header=[0,0,0,1,1])
        check(j,('random',n));counts['random_vertices']+=1
    result=dict(passed=True,original_instructions=len(original),fast_instructions=len(fast),
        counts=dict(counts),original_cycles_per_vertex=counts['capture_original_cycles']/counts['capture_vertices'],
        fast_cycles_per_vertex=counts['capture_fast_cycles']/counts['capture_vertices'],
        shader_throughput_gain_percent=100*(counts['capture_original_cycles']/counts['capture_fast_cycles']-1),
        source_sha256={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in [Path(__file__),HERE/'transform_fast.py']})
    a.out.parent.mkdir(parents=True,exist_ok=True);a.out.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result))
if __name__=='__main__':main()
