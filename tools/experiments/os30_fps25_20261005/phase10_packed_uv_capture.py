#!/usr/bin/env python3
"""Native FMA26 software cost/protocol qualification for packed canonical UV."""
import argparse
import json
import os
from pathlib import Path
import shutil
import sys
from phase10_experiment import CAMP, ROOT, module, sha, write, verify_capture, canonical

HERE=Path(__file__).resolve().parent
OUT=CAMP/'phase10/packed-uv-native-v1'
CANDIDATE=CAMP/'phase10/packed-uv-v1'
MODEL=CAMP/'phase10/load-alias-functional-v2'
BASE=MODEL/'capture-baseline'

def prepare():
    OUT.mkdir(exist_ok=False)
    for name in ('app.elf','gfx_pc.c'):
        shutil.copy2(CANDIDATE/name,OUT/name)
    shutil.copy2(Path(__file__),OUT/'recipe.py')
    source=json.loads((MODEL/'functional-model.json').read_text())
    exe=MODEL/'functional-qsim/qsim'
    assert sha(exe)==source['simulator_sha256']
    write(OUT/'inputs.json',dict(private_only=True,elf_sha256=sha(OUT/'app.elf'),
        source_sha256=sha(OUT/'gfx_pc.c'),candidate_build_sha256=sha(CANDIDATE/'build-normal.json'),
        baseline_capture_sha256=sha(BASE/'inputs.json'),
        baseline_elf_sha256=sha(BASE/'sm64.elf'),
        simulator=str(exe.relative_to(ROOT)),simulator_sha256=sha(exe),
        functional_model_sha256=sha(MODEL/'functional-model.json'),
        recipe_sha256=sha(OUT/'recipe.py'),
        scope='Both apps use the same validated FMA26 simulator with LOD4 absent. CPU timing is modeled; qsim vertex-cache opcodes do not render pixels.'))

def capture():
    record=json.loads((OUT/'inputs.json').read_text());exe=ROOT/record['simulator']
    assert sha(exe)==record['simulator_sha256'] and sha(OUT/'app.elf')==record['elf_sha256']
    os.environ['LOD4_MODEL_MODE']='absent'
    os.environ.pop('LOD4_STATS_ADDR',None)
    run=module('packed_uv_capture',ROOT/'tools/experiments/sm64_batch_cull_20261005/validation/capture.py')
    run.OUT=OUT
    sys.argv=[str(Path(__file__)),'normal','--elf',str(OUT/'app.elf'),'--exe',str(exe),'--geometry','0','--audio']
    run.main()

def analyze():
    record=json.loads((OUT/'inputs.json').read_text());after=OUT/'capture-normal'
    a=verify_capture(BASE,record['baseline_elf_sha256']);b=verify_capture(after,record['elf_sha256'])
    assert len(a)==len(b)==721
    assert 'FATAL' not in (BASE/'run.log').read_text() and 'FATAL' not in (after/'run.log').read_text()
    exact={n:sha(BASE/n)==sha(after/n) for n in ('scene.csv','geometry.csv','culling.csv')}
    assert all(exact.values()),exact
    assert [r['gpu_cmd_words'] for r in a]==[r['gpu_cmd_words'] for r in b]
    v=module('packed_uv_vectors',HERE/'phase7_validate.py');vectors=[]
    for p in sorted((BASE/'gpuvec').glob('*.vec')):
        target=after/'gpuvec'/p.name;old,new=(v.v.vector(x.read_bytes()) for x in (p,target))
        assert canonical(old)[0]==canonical(new)[0],p.name
        assert all(old[k]==new[k] for k in ('depth','fence','uploads','hardware')),p.name
        diffs=[dict(offset=i,before=old['texture'][i:i+4].hex(),after=new['texture'][i:i+4].hex())
               for i in range(0,len(old['texture']),4) if old['texture'][i:i+4]!=new['texture'][i:i+4]]
        vectors.append(dict(frame=int(p.stem.split('_')[1]),texture_changes=diffs,
            baseline_sha256=sha(p),candidate_sha256=sha(target)))
    assert len(vectors)==8
    metrics={}
    for key in ('insns','est_cycles','gpu_cmd_words','dcache_miss','icache_miss_sdram','mispredict'):
        old=sum(int(x[key]) for x in a[360:]);new=sum(int(x[key]) for x in b[360:])
        metrics[key]=dict(baseline=old,candidate=new,saving_percent=(old-new)*100/old)
    result=dict(passed=True,private_only=True,all721_scene_geometry_culling_exact=exact,
        all721_wordcounts_exact=True,all8_ordered_streams_exact=True,vectors=vectors,
        padding_check=v.padding(vectors,BASE,after,CANDIDATE/'SM64'),gameplay_frames=361,
        metrics=metrics,baseline_ms=metrics['est_cycles']['baseline']/361/1e5,
        candidate_ms=metrics['est_cycles']['candidate']/361/1e5,
        candidate_capture_sha256=sha(after/'inputs.json'),input_binding_sha256=sha(OUT/'inputs.json'),
        scope='Actual guest instruction counts and estimated CPU cycles. Eight ordered command/MMIO streams, not 721 rendered framebuffers. No physical FPS or RTL timing claim.')
    write(OUT/'result.json',result)
    print(json.dumps({k:result[k] for k in ('passed','baseline_ms','candidate_ms','metrics')},indent=2))

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('prepare','capture','analyze'))
    globals()[parser.parse_args().action]()
