#!/usr/bin/env python3
"""Compare frozen complete-frame CPU-estimate/GPU-RTL windows and image bytes."""
import argparse
import csv
import json
import struct
from pathlib import Path
from analyze_coupled import analyze,rows,OUT

def window_profile(path):
    def read(name):
        return {r['function']:{k:int(r[k]) for k in ('self_insns','self_cycles','icache_miss','dcache_miss','calls')}
                for r in csv.DictReader((path/name).open(),delimiter='\t')}
    manifest=json.loads((path/'inputs.json').read_text())
    end=read('profile.tsv');start=read(f"profile-{manifest['start']:04d}.tsv")
    return {name:{k:v-start.get(name,{}).get(k,0) for k,v in values.items()}
            for name,values in end.items()}

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--baseline',default='baseline-p4-360-t1')
    p.add_argument('--candidate',required=True)
    p.add_argument('--previous')
    p.add_argument('--out',type=Path,required=True)
    a=p.parse_args();bp,baseline=analyze(a.baseline);cp,candidate=analyze(a.candidate)
    assert (baseline['start'],baseline['frames'],baseline['traffic'])==(candidate['start'],candidate['frames'],candidate['traffic'])
    assert rows(bp/'scene.csv')==rows(cp/'scene.csv')
    frames=[]
    for frame in range(candidate['start'],candidate['start']+candidate['frames']):
        before=(bp/f'frame_{frame:05d}.bin').read_bytes();after=(cp/f'frame_{frame:05d}.bin').read_bytes()
        assert len(before)==len(after)==307200
        old=struct.unpack('<153600H',before);new=struct.unpack('<153600H',after)
        frames.append(dict(frame=frame,color_differences=sum(x!=y for x,y in zip(old[:76800],new[:76800])),
                           depth_differences=sum(x!=y for x,y in zip(old[76800:],new[76800:]))))
    result=dict(baseline=baseline,candidate=candidate,scene_trace_identical=True,frames=frames,
                throughput_gain_percent=100*(baseline['mean_submit_interval_ms']/candidate['mean_submit_interval_ms']-1),
                limitations=['CPU instruction/cache timing is an estimate, not CPU RTL or Pocket FPS.',
                'GPU engine, DMA, rasterizer and physical SDRAM timing run in RTL with synthetic external traffic.',
                'Eight sequential fixed-step scene frames establish this comparison only; fitting and hardware qualification remain separate.'])
    if a.previous:
        pp,previous=analyze(a.previous);assert rows(pp/'scene.csv')==rows(cp/'scene.csv')
        old=window_profile(pp);new=window_profile(cp)
        delta=[dict(function=name,cycle_delta=new.get(name,{}).get('self_cycles',0)-old.get(name,{}).get('self_cycles',0),
                    previous=old.get(name,{}),candidate=new.get(name,{})) for name in old.keys()|new.keys()]
        result['previous']=previous
        result['cpu_profile_delta']=sorted(delta,key=lambda r:abs(r['cycle_delta']),reverse=True)
    a.out.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({k:result[k] for k in ['throughput_gain_percent','frames']},indent=2))

if __name__=='__main__':main()
