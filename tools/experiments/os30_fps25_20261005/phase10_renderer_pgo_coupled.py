#!/usr/bin/env python3
"""One warm/cold PGO comparison with byte-identical selected coupled RTL."""
import argparse,csv,json,os,shutil,sys
from pathlib import Path
from phase10_experiment import CAMP,ROOT,module,sha,write
P=CAMP/'phase10'; OLD=P/'load-submit-coupled-v1'; OUT=P/'renderer-pgo-coupled-v1'; APP=P/'renderer-pgo-v1/use/app.elf'; PROOF=P/'renderer-pgo-v1'

def prepare():
    assert sha(OLD/'manifest.json')=='7c8fbe0056c708bcc31275a2b3dfad3d728f7f9a2c7bfacfd24c8bf0592432e6'
    for n,h in json.loads((OLD/'manifest.json').read_text())['files'].items():assert sha(OLD/n)==h,n
    p=json.loads((PROOF/'result.json').read_text());assert p['passed']and p['candidate_elf_sha256']==sha(APP)
    for n,h in json.loads((PROOF/'manifest.json').read_text())['files'].items():assert sha(PROOF/n)==h,n
    OUT.mkdir(exist_ok=False)
    shutil.copytree(OLD/'model/frozen',OUT/'model/frozen')
    shutil.copytree(OLD/'qsim',OUT/'qsim',ignore=shutil.ignore_patterns('build','qsim'))
    (OUT/'model/obj').mkdir();shutil.copy2(OLD/'model/obj/Vtb_gpu',OUT/'model/obj/Vtb_gpu')
    build=json.loads((OLD/'model/build.json').read_text());assert sha(OUT/'model/obj/Vtb_gpu')==build['simulator_sha256']
    write(OUT/'model/build.json',dict(build,parent_build_sha256=sha(OLD/'model/build.json'),reused_byte_identical=True))
    shutil.copy2(__file__,OUT/'recipe.py')
    write(OUT/'prepared.json',dict(elf_sha256=sha(APP),baseline_manifest_sha256=sha(OLD/'manifest.json'),
        pgo_manifest_sha256=sha(PROOF/'manifest.json'),simulator_sha256=build['simulator_sha256'],
        source_sha256={str(f.relative_to(OUT)):sha(f)for f in OUT.rglob('*')if f.is_file()},
        rtl_changed=False,instrumentation=False,profile_summary_modified=False,deployment_ready=False))
    print(OUT)

def run(window):
    prep=json.loads((OUT/'prepared.json').read_text());assert sha(APP)==prep['elf_sha256']
    assert sha(PROOF/'manifest.json')==prep['pgo_manifest_sha256']
    for n,h in prep['source_sha256'].items():assert sha(OUT/n)==h,n
    m=module('pgo_coupled_runner',ROOT/'tools/experiments/os30_overlap_20261006/run.py');m.OUT=OUT
    os.environ['IDEAL_GPU_MEMORY']='0';os.environ['LOD5_MODEL_MODE']='normal'
    sys.argv=[str(Path(__file__)),window,'--elf',str(APP),'--start','360'if window=='warm'else'234','--frames','8'];m.main()
    d=OUT/f'run-{window}';assert 'FATAL'not in(d/'run.log').read_text()
    summarize(window)

def summarize(window):
    start=360 if window=='warm'else 234;a,b=OLD/f'run-{window}',OUT/f'run-{window}'
    for d in(a,b):
        inv=json.loads((d/'inputs.json').read_text());assert inv['success']and inv['start']==start and inv['frames']==8 and inv['traffic']==1
        for n,h in inv['results_sha256'].items():assert sha(d/n)==h,n
    for n in('scene.csv','geometry.csv','culling.csv'):assert(a/n).read_bytes()==(b/n).read_bytes(),n
    if not (b/'environment-proof.json').exists():
        write(b/'environment-proof.json',dict(inputs_sha256=sha(b/'inputs.json'),IDEAL_GPU_MEMORY='0',LOD5_MODEL_MODE='normal',functional_proof_sha256=sha(PROOF/'manifest.json'),runner_recipe_sha256=sha(OUT/'recipe.py'),note='Capture completed successfully; initial metadata append used exclusive writer on existing inputs and failed after run. Preserve inputs; attach environment separately. No rerun.'))
    images=[]
    for i in range(start,start+8):
        n=f'frame_{i:05}.bin';assert(a/n).read_bytes()==(b/n).read_bytes(),n;images.append(dict(frame=i,sha256=sha(a/n)))
    timing={}
    for label,d in[('baseline',a),('pgo',b)]:
        c=json.loads((d/'coupled.json').read_text());assert c['presented']==c['frames']==8
        assert not c['memory_errors']and not c['cram1_errors']and not c['ring_overflow']
        row=list(csv.DictReader((d/'counters.csv').open()))[-1]
        timing[label]=dict(frame_ms=c['cpu_cycles']/800000,gpu_busy_ms=int(row['all_gpu_busy'])/800000,
            rdptr_polls=int(row['rdptr_polls']),publications=int(row['publications']),uploaded_words=int(row['uploaded_words']))
    assert timing['baseline']['uploaded_words']==timing['pgo']['uploaded_words']
    x,y=timing['baseline']['frame_ms'],timing['pgo']['frame_ms']
    write(OUT/f'result-{window}.json',dict(passed=True,window=window,timing=timing,images=images,
        actual8_color_depth_exact=True,histories_exact=True,saving_ms=x-y,saving_percent=100*(1-y/x),
        unchanged_rtl=True,unchanged_profiles=True,deployment_ready=False))
    print(json.dumps(timing,indent=2))

def seal():
    a=json.loads((OUT/'result-warm.json').read_text());b=json.loads((OUT/'result-cold.json').read_text());assert a['passed']and b['passed']
    write(OUT/'result.json',dict(passed=True,warm=a,cold=b,deployment_ready=False))
    (OUT/'REPORT.txt').write_text('Renderer-only PGO actual coupled screen\n\n'+''.join(f"{r['window']}: {r['timing']['baseline']['frame_ms']:.9f} -> {r['timing']['pgo']['frame_ms']:.9f} ms/frame; saving {r['saving_ms']:.9f} ms ({r['saving_percent']:.4f}%).\n"for r in(a,b))+'\nAll16 actual color/depth buffers and scene/geometry/culling histories exact. Baselines are the frozen selected load-submit runs. Model binary/source, traffic, hardware, assets and source arithmetic are unchanged; only uninstrumented profile-use ELF differs.\n\nCPU/cache timings are modeled with actual GPU/LOAD/SDRAM/CRAM RTL. This is the same in-sample Bob-omb workload used for training, not hardware FPS or generalization proof. Real edge counters retain the original GCC summary runs=0/sum_max=0 metadata; no synthetic profile correction. No production/deployment.\n')
    shutil.copy2(__file__,OUT/'recipe-final.py')
    write(OUT/'manifest.json',dict(passed=True,files={str(f.relative_to(OUT)):sha(f)for f in OUT.rglob('*')if f.is_file()and f.name!='manifest.json'},evidence_sha256={str(p.relative_to(ROOT)):sha(p)for p in[OLD/'manifest.json',PROOF/'manifest.json']},deployment_ready=False))
    print(sha(OUT/'manifest.json'))
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('action',choices=['prepare','run','summarize','seal']);p.add_argument('--window',choices=['warm','cold'],default='warm');a=p.parse_args()
    globals()[a.action](a.window)if a.action in('run','summarize')else globals()[a.action]()
