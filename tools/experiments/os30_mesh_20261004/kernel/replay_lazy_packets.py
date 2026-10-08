#!/usr/bin/env python3
"""Paired real GPU RTL replay, including an explicit depth-buffer dump."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import hashlib,json,struct,subprocess
HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[3]
OUT=HERE/'results/lazy-packets'
EXE=ROOT/'docs/measurements/gpu-edge-overflow-20261004/replay-parallel0/obj/Vtb_gpu'

def depth_address(data):
    p=0;addresses=set()
    while p<len(data):
        tag=data[p:p+4];p+=4
        if tag==b'MEM1':
            _,n=struct.unpack_from('<II',data,p);p+=8+(n+3&~3)
        elif tag==b'CMDS':
            n=struct.unpack_from('<I',data,p)[0];p+=4
            words=struct.unpack_from('<'+'I'*n,data,p);p+=n*4;at=0
            while at<n:
                header=words[at];count=header&0xffffff
                if header>>24==0x4a and ((words[at+7]>>24)&15):
                    addresses.add(words[at+12]&0x3ffffff)
                at+=1+count
        elif tag==b'PALB':p+=4
        elif tag==b'DUMP':p+=8
        elif tag in (b'FLSH',b'FENC',b'HASH',b'END.'):pass
        else:raise ValueError((tag,p))
    assert len(addresses)==1,addresses
    return addresses.pop()

def run(item):
    label,frame=item;dest=OUT/label/f'replay-depth-{frame}';dest.mkdir(exist_ok=False)
    data=(OUT/label/'gpuvec'/f'frame_{frame:05}.vec').read_bytes()
    assert data[-4:]==b'END.'
    address=depth_address(data)
    vector=dest/'frame.vec'
    vector.write_bytes(data[:-4]+b'DUMP'+struct.pack('<II',address,153600)+b'END.')
    with (dest/'run.log').open('w') as log:
        subprocess.run([str(EXE),str(vector),str(dest/'output.bin'),'+memory_traffic=0'],
                       stdout=log,stderr=subprocess.STDOUT,check=True,timeout=600)
    output=(dest/'output.bin').read_bytes();assert len(output)==307212,len(output)
    result=dict(label=label,frame=frame,depth_address=address,
                framebuffer_sha256=hashlib.sha256(output[:153600]).hexdigest(),
                depth_sha256=hashlib.sha256(output[153612:]).hexdigest(),
                fence=struct.unpack_from('<I',output,153600)[0],
                vector_sha256=hashlib.sha256(vector.read_bytes()).hexdigest())
    print(result,flush=True);return result

def main():
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(run,[(label,frame) for label in ('before','after') for frame in (360,367)]))
    for frame in (360,367):
        a,b=[r for r in results if r['frame']==frame]
        for key in ('framebuffer_sha256','depth_sha256','fence'):assert a[key]==b[key],(frame,key)
    report=dict(results=results,executable_sha256=hashlib.sha256(EXE.read_bytes()).hexdigest(),
                scope='Old-capability app equivalence on original corrected GPU RTL, not mesh performance')
    (OUT/'replay-depth-result.json').write_text(json.dumps(report,indent=2)+'\n')
    print('PASS exact framebuffer, depth and fence for both frames',flush=True)

if __name__=='__main__':main()
