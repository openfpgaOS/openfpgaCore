#!/usr/bin/env python3
"""Compare optional driver changes on captured real-material/fallback jobs."""
import argparse,collections,inspect,json,sys
from pathlib import Path
HERE=Path(__file__).resolve().parent
sys.path[:0]=[str(HERE.parent/'dma'),str(HERE.parent/'client'),str(HERE.parent/'engine')]
import check_runtime_gpu as replay
import emulator
from mesh_program import build

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--vector',type=Path,required=True)
    ap.add_argument('--out-name',default='driver-variants');a=ap.parse_args()
    dest=HERE/'results'/a.out_name;dest.mkdir(exist_ok=False)
    variants=dict(original={},scopy=dict(fused_copy=True),
                  scopy_unroll=dict(fused_copy=True,unroll_project=True),
                  scopy_unroll_outblock=dict(fused_copy=True,unroll_project=True,outblock=True),
                  batched=dict(fused_copy=True,unroll_project=True,outblock=True,fused_planes=True,unroll_attributes=True),
                  setup=dict(fused_copy=True,unroll_project=True,outblock=True,fused_planes=True,unroll_attributes=True,fused_setup=True))
    legacy=dict(fused_copy=False,unroll_project=False,outblock=False,fused_planes=False,unroll_attributes=False,fused_setup=False,honor_guardband=False)
    programs={name:build(**(legacy|options)) for name,options in variants.items()}
    counts={name:collections.Counter() for name in variants}
    opcounts={name:collections.Counter() for name in variants}
    pcs={name:collections.Counter() for name in variants};runners={}
    text=inspect.getsource(emulator.run).replace('        word=program[pc];','        profile_pc=pc\n        word=program[pc];')
    text=text.replace('        cycles+=cost','        PROFILE[profile_pc]+=cost\n        PROFILE_OPS[op]+=cost\n        cycles+=cost')
    for name in variants:
        namespace=dict(emulator.__dict__,PROFILE=pcs[name],PROFILE_OPS=opcounts[name]);exec(text,namespace);runners[name]=namespace['run']
    original=programs['original'];original_code=original.finish()
    def compare(code,*args,**kwargs):
        assert code[:len(original_code)]==original_code,'capture no longer matches default program'
        results={}
        for name,program in programs.items():
            params=kwargs.copy()
            if params.get('entry',0):
                assert params['entry']==original.labels['projected_fallback']
                params['entry']=program.labels['projected_fallback']
            result=runners[name](program.finish(),*args,**params);results[name]=result
            counts[name]['jobs']+=1;counts[name]['cycles']+=result['cycles'];counts[name]['output_words']+=len(result['output'])
            if name!='original':
                assert result['output']==results['original']['output'] and result['fault']==results['original']['fault']
                assert result['bank']==results['original']['bank']
        return results['original']
    replay.run=compare;info=replay.prepare(a.vector,dest)
    reports={}
    for name,program in programs.items():
        labels=sorted((pc,label) for label,pc in program.labels.items());regions=collections.Counter()
        for pc,cost in pcs[name].items():regions[max((start,label) for start,label in [(0,'entry'),*labels] if start<=pc)[1]]+=cost
        reports[name]=dict(program_words=len(program.words),counts=dict(counts[name]),regions=dict(regions.most_common()),
                           opcodes={next(k for k,v in emulator.OP.items() if v==op):cost for op,cost in opcounts[name].most_common()})
    report=dict(passed=True,scope=__doc__,source=str(a.vector),source_sha256=info['source_sha256'],variants=reports,
                limitation='Interpreter cycle sums exclude DMA and backpressure; functional packet equivalence is exact to the frozen mesh program, not CPU float shading.')
    (dest/'result.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({name:{'words':r['program_words'],**r['counts']} for name,r in reports.items()},indent=2))

if __name__=='__main__':main()
