#!/usr/bin/env python3
"""Compare standalone C packets with independent integer/old-RTL fixtures."""
import argparse,hashlib,json,os,random,struct,subprocess,sys
from pathlib import Path
HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[3]
sys.path[:0]=[str(HERE.parent/'kernel'),str(HERE.parent/'engine')]
from check_microcode import records
from reference import decode_inputs,prepared_packet

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--label',default='companion-triangle-host-v1')
    args=ap.parse_args();out=ROOT/'docs/measurements/os30-mesh-20261004'/args.label
    out.mkdir(exist_ok=False)
    cases=[];scenes={}
    for path in sorted((HERE.parent/'kernel/results/reference').glob('*/kernel.bin')):
        start=len(cases);cases.extend(records(path));scenes[path.parent.name]=len(cases)-start
    assert cases,'local scene fixtures required'
    rng=random.Random(0x301cf00)
    for i in range(5000):
        flags=(i&1)|(2 if i%3 else 0)|(4 if i%5==0 else 0)
        w=[flags,320<<16,240<<16]
        for j in range(3):w.extend([rng.getrandbits(32) for _ in range(5)]+[rng.getrandbits(16),rng.getrandbits(16)])
        v,c,o=decode_inputs(w);cases.append((w,prepared_packet(v,c,**o)))
    def write_cases(path,items):
        path.write_bytes(struct.pack('<I',len(items))+b''.join(struct.pack('<24I',*w) for w,e in items))
    def expected(items):return b''.join(struct.pack('<I',len(e))+struct.pack('<%dI'%len(e),*e) for w,e in items)
    write_cases(out/'input.bin',cases);(out/'expected.bin').write_bytes(expected(cases))
    # First scene separately avoids conflating randomized stress with game cost.
    n=next(iter(scenes.values()));write_cases(out/'scene-input.bin',cases[:n])
    (out/'scene-expected.bin').write_bytes(expected(cases[:n]))
    reports=[]
    for name,flags in [('optimized',['-O3']),('sanitized',['-O1','-fsanitize=address,undefined','-fno-sanitize-recover=all'])]:
        exe=out/name
        subprocess.run(['gcc','-std=c99','-DHOST',*flags,str(HERE/'triangle_firmware.c'),'-o',str(exe)],check=True)
        with (out/'input.bin').open('rb') as f:
            result=subprocess.run([str(exe)],stdin=f,capture_output=True,check=True,env={**os.environ,'ASAN_OPTIONS':'detect_leaks=0'})
        want=expected(cases)
        if result.stdout!=want:
            (out/(name+'-actual.bin')).write_bytes(result.stdout)
            for i,(a,b) in enumerate(zip(struct.iter_unpack('<I',want),struct.iter_unpack('<I',result.stdout))):
                if a!=b:raise AssertionError((name,i,hex(a[0]),hex(b[0])))
            raise AssertionError((name,len(want),len(result.stdout)))
        reports.append(dict(configuration=name,exact_output_words=len(want)//4))
    result=dict(passed=True,cases=len(cases),scenes=scenes,random_cases=5000,reports=reports,
                source_sha256=hashlib.sha256((HERE/'triangle_firmware.c').read_bytes()).hexdigest())
    (out/'result.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))
if __name__=='__main__':main()
