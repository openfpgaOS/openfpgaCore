#!/usr/bin/env python3
"""The accepted-load flag suppresses only X/Y clipping, retaining depth safety."""
import collections,json,sys
from pathlib import Path
HERE=Path(__file__).resolve().parent
sys.path[:0]=[str(HERE.parent/'client'),str(HERE.parent/'engine')]
from mesh_transform import OUT,read,clip_polygon,word
from mesh_program import build
from check_mesh_program import bank_for,expected_stream
from emulator import run

def main():
    dest=HERE/'results/guardband';dest.mkdir(exist_ok=False)
    code=build(honor_guardband=True).finish();counts=collections.Counter()
    jobs=[j for path in sorted((OUT/'run-360-8').glob('mesh-*.bin')) for j in read(path)[1]]
    for ordinal,job in enumerate(jobs):
        bank,state=bank_for(job,ordinal%64);state[47]|=1<<19;bank[128:192]=state
        scratch=[0]*256;scratch[62],scratch[63]=job['header'][3:5]
        expected,vertices,draws=expected_stream(job,state)
        result=run(code,bank=bank,scratch=scratch,compact=True)
        assert not result['fault'] and result['output']==expected,ordinal
        assert result['bank'][:7*len(vertices)]==[word(x) for v in vertices for x in v]
        counts['jobs']+=1;counts['triangles']+=len(job['indices']);counts['draws']+=draws
        counts['cycles']+=result['cycles'];counts['words']+=len(expected)
    # The flag must not bypass GATHER's inclusive coordinate-range contract.
    job=jobs[0];bank,state=bank_for(job);state[47]|=1<<19
    state[:16]=[0]*16;state[0]=0x7fffffff;state[15]=256
    bank[128:192]=state;bank[0]=32767;bank[4]=32767;bank[8]=32767
    bank[192]=0|(1<<5)|(2<<10)
    scratch=[0]*256;scratch[62]=3;scratch[63]=1
    result=run(code,bank=bank,scratch=scratch,compact=True)
    assert result['fault']==1 and result['scratch'][63]==1
    # Independently establish enabled-plane behavior, including trivial rejects.
    xy_out=[[1000,0,0,100,0,0,0xff0000ff],[1100,50,0,100,0,0,0xff00ff00],[1000,-50,0,100,0,0,0xffff0000]]
    assert not clip_polygon(xy_out) and clip_polygon(xy_out,plane_mask=3)==xy_out
    depth_out=[[v[0],v[1],200,*v[3:]] for v in xy_out]
    assert not clip_polygon(depth_out,plane_mask=3)
    report=dict(passed=True,program_words=len(code),counts=dict(counts),malformed_range_rejected=True,
                scope=__doc__,limitation='This validates the flag semantics, not whether a producer may safely assert the accepted-load flag.')
    (dest/'result.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))

if __name__=='__main__':main()
