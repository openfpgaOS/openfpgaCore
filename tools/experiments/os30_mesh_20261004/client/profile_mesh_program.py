#!/usr/bin/env python3
"""Attribute real whole-program ISA costs; no work or cycles are discounted."""
from pathlib import Path
import argparse,collections,inspect,json
from mesh_program import build
import emulator
from mesh_transform import OUT,read
from check_mesh_program import bank_for,expected_stream
p=argparse.ArgumentParser();p.add_argument('--stride',type=int,default=8);a=p.parse_args()
program=build();code=program.finish();counts=collections.Counter();ops=collections.Counter();totals=collections.Counter()
text=inspect.getsource(emulator.run)
text=text.replace('        word=program[pc];','        profile_pc=pc\n        word=program[pc];')
text=text.replace('        cycles+=cost','        PROFILE[profile_pc]+=cost\n        PROFILE_OPS[op]+=cost\n        cycles+=cost')
namespace=dict(emulator.__dict__,PROFILE=counts,PROFILE_OPS=ops);exec(text,namespace)
profile=namespace['run']
jobs=[j for path in sorted((OUT/'run-360-8').glob('mesh-*.bin')) for j in read(path)[1]]
for i in range(0,len(jobs),a.stride):
    job=jobs[i];bank,state=bank_for(job);scratch=[0]*256;scratch[62],scratch[63]=job['header'][3:5]
    expected,vertices,draws=expected_stream(job,state)
    result=profile(code,bank=bank,scratch=scratch,compact=True)
    assert result['output']==expected and not result['fault']
    totals['jobs']+=1;totals['vertices']+=len(vertices);totals['triangles']+=len(job['indices']);totals['draws']+=draws;totals['cycles']+=result['cycles']
labels=sorted((pc,name) for name,pc in program.labels.items())
regions=collections.Counter()
for pc,value in counts.items():
    name=max((start,name) for start,name in [(0,'entry'),*labels] if start<=pc)[1]
    regions[name]+=value
report=dict(scope=__doc__,stride=a.stride,totals=dict(totals),regions=dict(regions.most_common()),
            opcodes={next(k for k,v in emulator.OP.items() if v==op):cost for op,cost in ops.most_common()})
dest=OUT/'whole-program';dest.mkdir(exist_ok=True)
(dest/'profile.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))
