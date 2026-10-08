#!/usr/bin/env python3
"""Instruction/phase cost audit of the client shader; no client source edits."""
import argparse,bisect,collections,hashlib,json,sys
from pathlib import Path
HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[3]
sys.path.insert(0,str(HERE.parent/'engine'))
sys.path.insert(0,str(HERE.parent/'client'))
from mesh_transform import build_transform,transform_vertex,word,LIGHTING,FOG
from analyze import OUT,read
from isa import OP

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--out',type=Path,required=True)
    ap.add_argument('--limit',type=int,default=256);a=ap.parse_args()
    program=build_transform();words=program.finish()
    jobs=[j for p in sorted((OUT/'run-360-8').glob('mesh-*.bin')) for j in read(p)[1]]
    selected=jobs if not a.limit else jobs[::max(1,len(jobs)//a.limit)][:a.limit]
    costs=collections.Counter();hits=collections.Counter();divs=collections.Counter();opcost=collections.Counter()
    names={v:k for k,v in OP.items()}
    def observe(pc,op,acc,numerator,denominator,cost):
        costs[pc]+=cost;hits[pc]+=1;opcost[names[op]]+=cost
        if op==OP['DIVW']:
            divs['total']+=1
            divs['positive_high_zero' if acc>>32==0 else 'positive_high_nonzero']+=1
            if acc>=255*denominator:divs['clamp255']+=1
    source=(HERE.parent/'engine/emulator.py').read_text()
    source=source.replace('word=program[pc];pc=', 'old_pc=pc;old_acc=acc;word=program[pc];pc=')
    source=source.replace('        cycles+=cost','        observe(old_pc,op,old_acc,v,u,cost)\n        cycles+=cost')
    namespace={'observe':observe};exec(compile(source,'instrumented_emulator','exec'),namespace)
    counts=collections.Counter()
    for j in selected:
        raw=j['vertices'];s=j['state'];bank=[0]*256
        bank[:len(raw)]=raw;bank[128:192]=s;bank[192:192+len(j['indices'])]=j['indices']
        scratch=[0]*256;scratch[62],scratch[63]=j['header'][3:5]
        result=namespace['run'](words,bank,scratch)
        assert not result['fault']
        expected=[word(x) for i in range(j['header'][3]) for x in transform_vertex(raw[i*4:i*4+4],s)]
        assert result['bank'][:len(expected)]==expected
        counts['jobs']+=1;counts['vertices']+=j['header'][3];counts['cycles']+=result['cycles']
        counts['lighting_vertices']+=j['header'][3]*bool(s[22]&LIGHTING)
        counts['fog_vertices']+=j['header'][3]*bool(s[22]&FOG)
    labels=sorted((pc,name) for name,pc in program.labels.items());positions=[p for p,n in labels]
    phases=collections.Counter()
    for pc,cost in costs.items():
        i=bisect.bisect_right(positions,pc)-1
        phases[labels[i][1] if i>=0 else 'initial_constants']+=cost
    result=dict(counts=dict(counts),cycles_per_vertex=counts['cycles']/counts['vertices'],
        instructions=len(words),op_costs=dict(opcost.most_common()),phase_costs=dict(phases.most_common()),
        divisions=dict(divs),hot_pc=[dict(pc=pc,op=names[words[pc]>>24],cycles=cost,hits=hits[pc])
            for pc,cost in costs.most_common(30)],
        source_sha256={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest()
            for p in [HERE.parent/'client/mesh_transform.py',HERE.parent/'engine/emulator.py']})
    a.out.parent.mkdir(parents=True,exist_ok=True);a.out.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result))
if __name__=='__main__':main()
