#!/usr/bin/env python3
"""Capture and compare only the isolated aspect candidate with retained F5."""
import argparse,collections,csv,hashlib,importlib.util,json,re,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[4]
CAMPAIGN=ROOT/'docs/measurements/os30-fps25-20261005'
OUT=CAMPAIGN/'phase4/division'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
read=lambda p:json.loads(p.read_text())

def capture(mode):
    path=ROOT/'tools/experiments/sm64_batch_cull_20261005/validation/capture.py'
    spec=importlib.util.spec_from_file_location('retained_capture',path)
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);m.OUT=OUT
    elf=OUT/'aspect-v1'/('app.elf' if mode=='normal' else 'diagnostic.elf')
    exe=ROOT/'docs/measurements/os30-simple-20261005/validation/qsim-observed/qsim'
    sys.argv=[str(Path(__file__)),'aspect-'+mode,'--elf',str(elf),'--exe',str(exe),'--geometry','0','--audio']
    m.main()

def analyze(mode):
    a=CAMPAIGN/'validation'/('capture-phase2-exact-v1-'+mode)
    b=OUT/('capture-aspect-'+mode)
    am,bm=read(a/'inputs.json'),read(b/'inputs.json')
    assert am['success'] and bm['success']
    for key in ['geometry_capability','hw_features','command_dma','audio','panel']:assert am[key]==bm[key],key
    inputs=lambda m:{Path(p).name:h for p,h in m['sha256'].items()if Path(p).name!='sm64.elf'}
    assert inputs(am)==inputs(bm)
    for p,m in [(a,am),(b,bm)]:
        for rel,h in m['results_sha256'].items():assert sha(p/rel)==h,(p,rel)
    identical={name:sha(a/name)==sha(b/name) for name in ['scene.csv','geometry.csv','culling.csv'] if (a/name).exists()}
    assert all(identical.values()),identical
    diagnostic_changes=collections.Counter()
    if (a/'diagnostic.csv').exists():
        da=list(csv.DictReader((a/'diagnostic.csv').open()));db=list(csv.DictReader((b/'diagnostic.csv').open()));assert len(da)==len(db)==721
        for x,y in zip(da,db):
            for key in x:
                if x[key]!=y[key]:diagnostic_changes[key]+=1
        # PerfSnapshot fields: elapsed ticks, three FPS rates, render duration.
        # These are expected to respond to changed instruction/model costs.
        assert set(diagnostic_changes)<=set(['stat5','stat6','stat7','stat8','stat10']),diagnostic_changes
    vectors={p.name:{'baseline_sha256':sha(p),'candidate_sha256':sha(b/'gpuvec'/p.name),'identical':sha(p)==sha(b/'gpuvec'/p.name)} for p in (a/'gpuvec').glob('*.vec')}
    frames=lambda p:list(csv.DictReader((p/'frames.csv').open()))
    aa,bb=frames(a),frames(b);assert len(aa)==len(bb)==721
    totals={}
    for key in ['insns','est_cycles','gpu_cmd_words','icache_miss_sdram','dcache_miss','mispredict']:
        x,y=sum(int(r[key])for r in aa[360:]),sum(int(r[key])for r in bb[360:])
        totals[key]={'baseline':x,'candidate':y,'saving_percent':100*(1-y/x),'delta':y-x}
    profile=lambda p:{r['function']:r for r in csv.DictReader((p/'profile.tsv').open(),delimiter='\t')}
    ap,bp=profile(a),profile(b);functions={}
    for fn in ['gfx_sp_vertex','gfx_start_frame','gfx_cpu_indexed_run','gfx_clip_triangle.constprop.0','gfx_gpu_vtx_cache_batch']:
        x,y=ap[fn],bp[fn];functions[fn]={k:{'baseline':int(x[k],0),'candidate':int(y[k],0),'delta':int(y[k],0)-int(x[k],0)}for k in ['addr','size','self_insns','self_cycles','icache_miss','dcache_miss','mispredict','calls']}
    counters={}
    for key in ['fdiv','fdiv_extra','dependency_stalls','icache','dcache','mispredict','btb_taken']:
        def val(p,name):return int(re.search(r'^\s*'+key+r'\s+(\d+)',(p/(name+'.txt')).read_text(),re.M)[1])
        x=val(a,'profile-0720')-val(a,'profile-0360');y=val(b,'profile-0720')-val(b,'profile-0360')
        counters[key]={'baseline':x,'candidate':y,'delta':y-x}
    result={'mode':mode,'scene_and_state_identical':identical,'diagnostic_timing_changes':dict(diagnostic_changes),'all_diagnostic_nontiming_fields_identical':True,'vectors':vectors,'all_gpu_vectors_byte_identical':all(x['identical']for x in vectors.values()),'gameplay_frames360_through720':totals,'cumulative_function_profiles':functions,'profile_frames360_to720':counters,'baseline_elf_sha256':sha(a/'sm64.elf'),'candidate_elf_sha256':sha(b/'sm64.elf'),'evidence_sha256':{str(p.relative_to(ROOT)):sha(p)for p in [a/'inputs.json',b/'inputs.json',Path(__file__)]},'scope':'Actual guest instruction counts and modeled single-issue cycles. No hardware FPS claim; nonidentical GPU vectors require separate color/depth replay.'}
    (OUT/f'results-{mode}.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({k:v for k,v in result.items()if k in ['mode','gameplay_frames360_through720','all_gpu_vectors_byte_identical','profile_frames360_to720']},indent=2))

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=['capture','analyze']);p.add_argument('mode',choices=['normal','fps']);a=p.parse_args()
    (capture if a.action=='capture' else analyze)(a.mode)
