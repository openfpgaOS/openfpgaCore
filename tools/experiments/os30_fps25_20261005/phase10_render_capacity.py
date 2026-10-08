#!/usr/bin/env python3
"""Private GPU capacity screen; CPU-cache/FPGA changes require a positive gate."""
import argparse,csv,json,shutil,subprocess
from pathlib import Path
import phase10_depth_reject as prior
ROOT=prior.ROOT;P=prior.P;sha=prior.sha;save=prior.save
OLD=P/'render-line32-v1/baseline'
OUT=P/'render-capacity-v1'

def prepare(label):
    if label=='gpu32-cpu64':
        gate=json.loads((OUT/'result-gpu32-cpu128.json').read_text())
        assert gate['time_reduction_percent']>5.0,'Only test the CPU tradeoff after a substantial GPU upper bound'
    d=OUT/label;d.mkdir(parents=True,exist_ok=False)
    shutil.copytree(OLD/'model/frozen',d/'model/frozen');q=d/'qsim';q.mkdir()
    for p in (OLD/'qsim').iterdir():
        if p.suffix in ('.c','.h') or p.name=='Makefile':shutil.copy2(p,q/p.name)
    p=d/'model/frozen/tb_gpu.v';s=p.read_text()
    a='.WAYS(2), .SET_BITS(6), .WORD_BITS(4)';assert s.count(a)==1
    p.write_text(s.replace(a,'.WAYS(2), .SET_BITS(8), .WORD_BITS(4)'))
    if label=='gpu32-cpu64':
        raise AssertionError('CPU model change must be explicitly inspected after the first gate')
    cmd=[s.replace(str(OLD),str(d)) for s in json.loads((OLD/'build-command.json').read_text())]
    save(d/'build-command.json',cmd)
    save(d/'inputs.json',dict(private_only=True,baseline=str(OLD),GPU_cache_data_bytes=32768,CPU_data_cache_bytes=131072,
      ways=2,line_bytes=64,FPGA_fit_candidate=False,recipe_sha256=sha(Path(__file__)),
      sources={str(p.relative_to(d)):sha(p) for p in d.rglob('*') if p.is_file()}))
    print(d)

def build(label):
    prior.OUT=OUT;prior.build(label)

def run(label):
    prior.OUT=OUT;prior.run(label)

def analyze(label):
    a=OLD/'run-window-8';b=OUT/label/'run-window-8'
    rows=[]
    for p in (a,b):
        v=list(csv.DictReader((p/'frames.csv').open()));assert len(v)==368
        rows.append(v[360:])
    exact={n:sha(a/n)==sha(b/n) for n in ('scene.csv','geometry.csv','culling.csv')}
    assert all(exact.values()),exact
    pixels=[]
    for f in sorted(a.glob('frame_*.bin')):
        if f.is_file():
            other=b/f.name;assert other.is_file(),other
            same=sha(f)==sha(other);assert same,f
            pixels.append(dict(name=f.name,sha256=sha(f)))
    assert len(pixels)==8,len(pixels)
    x,y=[sum(int(r['est_cycles']) for r in v)/len(v)/100000 for v in rows]
    counts=[]
    import re
    for p in (a,b):counts.append({int(k):int(v) for k,v in re.findall(r'DEPTH_REJECT (\d+) (\d+)',(p/'run.log').read_text())})
    result=dict(passed=True,private_only=True,baseline_ms=x,candidate_ms=y,time_reduction_percent=100*(1-y/x),throughput_gain_percent=100*(x/y-1),
      scene_geometry_culling_exact=exact,actual_render_outputs=pixels,depth_counters=counts,
      coupled=[json.loads((p/'coupled.json').read_text()) for p in (a,b)],
      evidence_sha256={str(p/'inputs.json'):sha(p/'inputs.json') for p in (a,b)},
      scope='Estimated CPU/cache timing with actual GPU/SDRAM/CRAM1, fixed frame step and synthetic background traffic. GPU cache capacity only; not a fitting candidate or physical FPS.')
    save(OUT/('result-'+label+'.json'),result)
    print(json.dumps({k:v for k,v in result.items() if k in ('passed','baseline_ms','candidate_ms','time_reduction_percent','throughput_gain_percent')},indent=2))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('action',choices=('prepare','build','run','analyze'));p.add_argument('label',choices=('gpu32-cpu128','gpu32-cpu64'));a=p.parse_args();globals()[a.action](a.label)
