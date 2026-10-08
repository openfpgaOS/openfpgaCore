#!/usr/bin/env python3
"""Compare independent setup to corrected RTL on10k random/boundary triangles."""
import hashlib
import json
from pathlib import Path
import struct
import subprocess

from reference import Vertex, prepare_edges, signed, prestep_shared32

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[3]
PREV=ROOT/'tools/experiments/os30_parallel_20261004'


def main():
    dest=HERE/'results/edges'
    dest.mkdir(parents=True,exist_ok=False)
    text=(PREV/'span_walker.cpp').read_text()
    text=text.replace('    unsigned prepared_cases=0,aborted_cases=0;',
                      '    FILE *capture=fopen("'+str(dest/'vectors.bin')+'","wb");assert(capture);\n'
                      '    unsigned prepared_cases=0,aborted_cases=0;')
    text=text.replace('        spans+=old_out.size();', '''        uint32_t header[12]={t.subpix,t.x0,t.y0,t.x1,t.y1,t.x2,t.y2,t.cx0,t.cx1,t.cy0,t.cy1,captured};
        fwrite(header,4,12,capture);if(captured)fwrite(packet.data(),4,9,capture);
        spans+=old_out.size();''')
    text=text.replace('    printf("PASS cases=', '    fclose(capture);\n    printf("PASS cases=')
    cpp=dest/'edge_capture.cpp';cpp.write_text(text)
    rtl=ROOT/'docs/measurements/os30-parallel-20261004/edges-rtl'
    sources=[PREV/'span_walker_tb.v',cpp,rtl/'gpu_span_walker.v',rtl/'gpu_edge_walker.v']
    cmd=['verilator','--cc','--exe','--build','-j','2','-Wno-fatal','--top-module','span_walker_tb',
         '--Mdir',str(dest/'obj'),'+define+INCLUDE_EW_STEP_PREDECODE','-CFLAGS','-std=c++17 -O2',*map(str,sources)]
    with (dest/'build.log').open('w') as log:
        subprocess.run(cmd,stdout=log,stderr=subprocess.STDOUT,check=True)
    done=subprocess.run([str(dest/'obj/Vspan_walker_tb')],capture_output=True,text=True,check=True)
    (dest/'run.log').write_text(done.stdout+done.stderr)
    raw=(dest/'vectors.bin').read_bytes(); words=struct.unpack('<'+'I'*(len(raw)//4),raw)
    offset=count=prepared=0
    while offset<len(words):
        values=words[offset:offset+12];offset+=12
        subpixel=bool(values[0]);present=values[11]
        vertices=[Vertex(signed(values[1+2*i],16),signed(values[2+2*i],16),0,0,0,0,0) for i in range(3)]
        clip=tuple(signed(v,16) for v in values[7:11])
        expected=list(words[offset:offset+9]) if present else None
        offset+=9 if present else 0
        got=prepare_edges(vertices,clip,subpixel=subpixel)
        assert got==expected,(count,vertices,clip,subpixel,got,expected)
        prepared+=present;count+=1
    # Exercise the explicit signed33 correction for every endpoint extremum,
    # including negative distances, independent of observable clipped coverage.
    extreme_cases=0
    for slope in [-4294901760,-2147483649,-2147483648,-1,0,1,2147483647,2147483648,4294901760]:
        for distance in [-65536,-65535,-17,-16,-15,-1,0,1,7,8,15,16,17,65535]:
            for subpixel in (False,True):
                assert prestep_shared32(slope,distance,subpixel)==signed((slope*distance)>>(4 if subpixel else 0))
                extreme_cases+=1
    result=dict(passed=True,triangles=count,prepared=prepared,extreme_prestep_cases=extreme_cases,
                vectors_sha256=hashlib.sha256(raw).hexdigest(),original_walker_result=done.stdout)
    (dest/'result.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result))


if __name__=='__main__':main()
