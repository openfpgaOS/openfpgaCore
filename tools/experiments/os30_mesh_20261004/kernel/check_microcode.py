#!/usr/bin/env python3
"""Execute real instruction words against scene and random reference vectors."""
import json
from pathlib import Path
import random
import statistics
import struct
import sys

from microcode import build
from reference import decode_inputs, prepared_packet, word
from emulator import run

HERE=Path(__file__).resolve().parent


def records(path):
    raw=path.read_bytes(); w=struct.unpack('<'+'I'*(len(raw)//4),raw);i=0
    while i<len(w):
        n,present=w[i:i+2];i+=2
        inputs=list(w[i:i+24]);i+=24
        edges=list(w[i:i+9]) if present else [];i+=9 if present else 0
        planes=list(w[i:i+n]);i+=n
        yield inputs, [0x61000000|(9+n),*edges,*planes] if present else []


def main():
    program=build().finish()
    results=[];cycles=[];executed=[];passed=0
    def check(inputs, expected, label):
        nonlocal passed
        result=run(program,bank=inputs+[0]*(256-len(inputs)))
        got=result['output']
        if got!=expected or result['fault']:
            path=HERE/'results/microcode-failure.json'
            path.write_text(json.dumps(dict(label=label,inputs=inputs,expected=expected,result=result),indent=2)+'\n')
            raise AssertionError((label,[(i,hex(a),hex(b)) for i,(a,b) in enumerate(zip(expected,got)) if a!=b],len(expected),len(got),str(path)))
        passed+=1
        return result
    for path in sorted((HERE/'results/reference').glob('*/kernel.bin')):
        sample=[];instructions=[];triangles=0
        for n,(inputs,expected) in enumerate(records(path)):
            result=check(inputs,expected,(path.parent.name,n))
            sample.append(result['cycles']);instructions.append(result['instructions']);triangles+=1
        results.append(dict(scene=path.parent.name,triangles=triangles,cycles=sum(sample),
                            mean_triangle_cycles=statistics.mean(sample),max_triangle_cycles=max(sample),
                            instructions=sum(instructions)))
        print(json.dumps(results[-1]),flush=True)
        cycles+=sample;executed+=instructions
    rng=random.Random(0x61004104)
    for n in range(1000):
        subpixel=n&1
        flags=subpixel | (2 if n%3 else 0) | (4 if n%5==0 else 0)
        inputs=[flags,320<<16,240<<16]
        for vertex in range(3):
            xy=rng.getrandbits(32)
            inputs += [xy,rng.getrandbits(32),rng.getrandbits(32),rng.getrandbits(31),rng.getrandbits(32),rng.getrandbits(16),rng.getrandbits(16)]
        vertices,clip,options=decode_inputs(inputs)
        check(inputs,prepared_packet(vertices,clip,**options),('random',n))
    out=HERE/'results/microcode.json'
    result=dict(passed=True,program_words=len(program),cases=passed,random_cases=1000,
                scene_mean_triangle_cycles=statistics.mean(cycles),scene_max_triangle_cycles=max(cycles),
                scene_mean_instructions=statistics.mean(executed),results=results)
    out.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result))


if __name__=='__main__':main()
