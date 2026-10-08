#!/usr/bin/env python3
"""Break down exact uploaded-program cycles for a complete captured frame."""
import argparse,collections,inspect,json,sys
from pathlib import Path
import check_runtime_gpu as replay
import emulator
from mesh_program import build

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--vector',type=Path,required=True);ap.add_argument('--out',type=Path,required=True);a=ap.parse_args()
    counts=collections.Counter();ops=collections.Counter();program=build();labels=sorted((pc,name) for name,pc in program.labels.items())
    text=inspect.getsource(emulator.run).replace('        word=program[pc];','        profile_pc=pc\n        word=program[pc];')
    text=text.replace('        cycles+=cost','        PROFILE[profile_pc]+=cost\n        PROFILE_OPS[op]+=cost\n        cycles+=cost')
    namespace=dict(emulator.__dict__,PROFILE=counts,PROFILE_OPS=ops);exec(text,namespace)
    def profile(code,*args,**kw):
        assert code[:len(program.words)]==program.finish(), 'current labels differ from captured code'
        return namespace['run'](code,*args,**kw)
    replay.run=profile;a.out.mkdir(exist_ok=False,parents=True);info=replay.prepare(a.vector,a.out)
    regions=collections.Counter()
    for pc,cost in counts.items():regions[max((start,name) for start,name in [(0,'entry'),*labels] if start<=pc)[1]]+=cost
    report=dict(scope=__doc__,counts=info['counts'],regions=dict(regions.most_common()),
                opcodes={next(k for k,v in emulator.OP.items() if v==op):cost for op,cost in ops.most_common()})
    (a.out/'profile.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report))
if __name__=='__main__':main()
