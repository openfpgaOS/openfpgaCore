#!/usr/bin/env python3
"""Retained real GPU/memory replay of GPU-direct functional output, with depth."""
import pathlib,subprocess,concurrent.futures,json,hashlib,sys
from phase10_experiment import CAMP,module,sha
HERE=pathlib.Path(__file__).resolve().parent
label=sys.argv[1];mode=sys.argv[2] if len(sys.argv)>2 else 'normal'
p=CAMP/'phase10'/label;base=CAMP/'phase9/checks-state-f11/capture-normal/gpuvec';exe=CAMP/'phase10/vcache-narrow-v1/model/obj/Vtb_gpu';out=p/f'replay-depth-{mode}';out.mkdir(exist_ok=False)
adapter=module('gdr_depth_adapter',HERE/'phase4_combined_validate.py');jobs=[];depth={}
for frame in [40,120,200,360,400,500,600,720]:
 for which,source in [('baseline',base),('candidate',p/f'capture-{mode}/gpuvec')]:
  name=f'{which}-{frame}';vec=out/f'{name}.vec';_,has_depth,_=adapter.with_depth(source/f'frame_{frame:05}.vec',vec);depth[name]=has_depth
  args=[str(exe),'+memory_traffic=1','+fast_tex=1','+tagged_tex=1',str(vec),str(out/f'{name}.bin')];jobs.append((name,args))
def run(j):
 name,args=j
 with (out/f'{name}.log').open('w') as o:r=subprocess.run(args,stdout=o,stderr=subprocess.STDOUT)
 return dict(name=name,command=args,returncode=r.returncode)
with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:runs=list(pool.map(run,jobs))
comparisons=[]
for f in [40,120,200,360,400,500,600,720]:
 a=(out/f'baseline-{f}.bin').read_bytes();b=(out/f'candidate-{f}.bin').read_bytes();assert depth[f'baseline-{f}']==depth[f'candidate-{f}'];has=depth[f'baseline-{f}'];assert len(a)==len(b)==153612+(153600 if has else 0)
 comparisons.append(dict(frame=f,depth_present=has,color_exact=a[:153600]==b[:153600],fence_exact=a[153600:153604]==b[153600:153604],depth_exact=a[153612:]==b[153612:],whole_memory_hash_exact=a[153604:153612]==b[153604:153612]))
result=dict(passed=all(x['returncode']==0 for x in runs) and all(x['color_exact'] and x['fence_exact'] and x['depth_exact'] for x in comparisons),scope='Actual retained GPU+arbiter+SDRAM/PSRAM chips with audio+scanout stress. Replays software-produced command streams; does not include live raw geometry engine or CPU overlap. Full memory hash intentionally differs because ELF/CPU memory differs.',comparisons=comparisons,runs=runs,simulator_sha256=sha(exe),input_sha256={str(x.relative_to(out)):sha(x) for x in out.glob('*.vec')})
(out/'result.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps({k:result[k] for k in ('passed','comparisons')},indent=2))
