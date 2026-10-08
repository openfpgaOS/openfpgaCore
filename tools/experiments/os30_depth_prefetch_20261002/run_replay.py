"""Compare frozen GPU replays. Every output byte includes FB, fence and memory hash."""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import hashlib,json,re,subprocess,sys
ROOT=Path(__file__).resolve().parents[3]
HERE=ROOT/'docs/measurements/os30-depth-prefetch-20261002'
extra='--extra' in sys.argv
labels=[x for x in sys.argv[1:] if x!='--extra'] or ['replay-baseline','replay-armed']
suffix='-extra' if extra else ''
cases=[(scene,vec) for scene in ['attract','intro'] for vec in sorted((HERE/f'capture-{scene}{suffix}/gpuvec').glob('*.vec'))]
def run(task):
    label,mode,scene,vec=task
    out=HERE/label/('runs'+suffix)/str(mode);out.mkdir(parents=True,exist_ok=True)
    stem=out/(scene+'-'+vec.stem)
    with stem.with_suffix('.log').open('w') as log:
        subprocess.run([str(HERE/label/'obj/Vtb_gpu'),str(vec),str(stem.with_suffix('.bin')),f'+memory_traffic={mode}'],stdout=log,stderr=subprocess.STDOUT,timeout=600,check=True)
    text=stem.with_suffix('.log').read_text()
    stats={k:int(v) for k,v in re.findall(r'(\w+)=(\d+)',text)}
    data=stem.with_suffix('.bin').read_bytes()
    row=dict(label=label,mode=mode,scene=scene,frame=int(vec.stem.split('_')[1]),stats=stats,sha256=hashlib.sha256(data).hexdigest(),vector_sha256=hashlib.sha256(vec.read_bytes()).hexdigest(),bytes=len(data))
    print(label,mode,scene,row['frame'],stats,flush=True)
    return row
rows=[]
with ThreadPoolExecutor(max_workers=3) as pool:
    for row in pool.map(run,[(l,m,s,v) for l in labels for m in (0,1,2) for s,v in cases]):
        rows.append(row)
        (HERE/('replay'+suffix+'-results-'+','.join(labels)+'.json')).write_text(json.dumps(rows,indent=2)+'\n')

if len(labels)==2:
    key=lambda r:(r['mode'],r['scene'],r['frame'])
    baseline={key(r):r for r in rows if r['label']==labels[0]}
    for row in (r for r in rows if r['label']==labels[1]):
        prior=baseline[key(row)]
        assert row['vector_sha256']==prior['vector_sha256']
        assert row['sha256']==prior['sha256'], ('Rendering mismatch',key(row))
    print('PASS: every paired framebuffer, fence and full-memory hash matches',flush=True)
