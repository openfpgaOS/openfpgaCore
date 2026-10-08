#!/usr/bin/env python3
"""Bounded diagnostic F9/F10 comparison using the unchanged retained-A coupled model."""
import argparse,concurrent.futures,hashlib,importlib.util,json,shutil,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
CAMP=ROOT/'docs/measurements/os30-fps25-20261005'
OUT=CAMP/'phase8/diagnostic-coupled'
OLD=ROOT/'docs/measurements/os30-overlap-20261006/coupled'
TOOLS=ROOT/'tools/experiments/os30_overlap_20261006'
ELFS={'baseline':CAMP/'phase7/combined-v1/diagnostic.elf','f10':CAMP/'phase8/kick128-tail-f10/diagnostic.elf'}
HASHES={'baseline':'02782c4a0fb731b358dd4be2d48daa3eae55ac7411351693aaeee924b2e276cf',
        'f10':'d5e16a5f76593743fa758067e3be3f591db3805e28215bce6056d44beeef447f'}
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def write(p,x):
    with p.open('x') as f:json.dump(x,f,indent=2);f.write('\n')
def module(name,p):
    spec=importlib.util.spec_from_file_location(name,p);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
def label(name,start,t):return f'{name}{"" if name=="baseline" else "-"}{start}-t{t}'
def prepare():
    seal=json.loads((OLD/'final-manifest.json').read_text())
    for p in [TOOLS/'run.py',TOOLS/'analysis_common.py']:
        assert sha(p)==seal['tool_sha256'][str(p.relative_to(ROOT))]
    for name,p in ELFS.items():assert sha(p)==HASHES[name]
    OUT.mkdir(exist_ok=False);(OUT/'model/obj').mkdir(parents=True)
    for rel in ['model/build.json','model/obj/Vtb_gpu']:shutil.copy2(OLD/rel,OUT/rel)
    assert sha(OUT/'model/obj/Vtb_gpu')==seal['simulator_sha256']
    s=(TOOLS/'analysis_common.py').read_text()
    for old,new in [("'phase7/combined-v1/capture-normal'","'phase7/combined-v1/capture-diagnostic'"),
                    ("f'phase7/combined-v1/replays/normal/","f'phase7/combined-v1/replays/diagnostic/")]:
        assert s.count(old)==1;s=s.replace(old,new)
    (OUT/'analysis_common_diagnostic.py').write_text(s)
    write(OUT/'inputs.json',dict(scope=__doc__,script_sha256=sha(Path(__file__)),
       parent_final_manifest_sha256=sha(OLD/'final-manifest.json'),simulator_sha256=seal['simulator_sha256'],
       unchanged_parent_run_script_sha256=sha(TOOLS/'run.py'),
       parent_analysis_helper_sha256=sha(TOOLS/'analysis_common.py'),
       diagnostic_analysis_helper_sha256=sha(OUT/'analysis_common_diagnostic.py'),
       elf_sha256=HASHES,elf_paths={k:str(v.relative_to(ROOT)) for k,v in ELFS.items()},
       schedule=dict(starts=[360,600],traffic=[1,2],frames=8),hardware_changes=False,deploys=False))
def run(name,start,t):
    m=module('unchanged_coupled_run',TOOLS/'run.py');m.OUT=OUT
    assert sha(ELFS[name])==HASHES[name]
    sys.argv=[str(TOOLS/'run.py'),label(name,start,t),'--elf',str(ELFS[name]),'--start',str(start),'--frames','8','--traffic',str(t)]
    m.main()
def campaign():
    jobs=[(n,s,t)for s in [360,600]for t in [1,2]for n in ELFS]
    def task(j):
        n,s,t=j
        p=OUT/('run-'+label(n,s,t))
        if p.exists():assert json.loads((p/'inputs.json').read_text())['success'];return
        subprocess.run([sys.executable,str(Path(__file__)),'run','--name',n,'--start',str(s),'--traffic',str(t)],check=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:list(pool.map(task,jobs))
def analyze():
    m=module('diagnostic_analysis',OUT/'analysis_common_diagnostic.py')
    m.ROOT,m.HERE,m.OUT,m.CAMP=ROOT,TOOLS,OUT,CAMP
    inputs=json.loads((OUT/'inputs.json').read_text());assert inputs['script_sha256']==sha(Path(__file__))
    assert sha(OUT/'analysis_common_diagnostic.py')==inputs['diagnostic_analysis_helper_sha256']
    runs=[];pairs=[]
    for start in [360,600]:
        for t in [1,2]:
            b=m.run('baseline',start,t);c=m.run('f10',start,t);runs += [b,c]
            assert b['frames']==c['frames'],(start,t)
            buffer_changes=[]
            for i in range(start,start+8):
                a,z=b['workload'][i],c['workload'][i]
                assert all(a[k]==z[k]for k in ['gpu_cmd_words','gpu_pixels']),(start,t,i)
                if a['buffer']==z['buffer']:assert a['hash_cmd']==z['hash_cmd'],(start,t,i)
                else:buffer_changes.append(i)
            pairs.append(dict(start=start,traffic=t,baseline_ms=b['modeled_frame_ms'],candidate_ms=c['modeled_frame_ms'],
               modeled_throughput_gain_percent=100*(b['modeled_frame_ms']/c['modeled_frame_ms']-1),
               frame_time_delta_ms=c['modeled_frame_ms']-b['modeled_frame_ms'],
               renderer_time_delta_ms=c['renderer_entry_to_flip_publication_ms']-b['renderer_entry_to_flip_publication_ms'],
               rdptr_poll_delta=c['rdptr_polls']-b['rdptr_polls'],exact_color_depth_frames=8,
               command_words_pixels_exact=True,raw_command_hash_exact_when_same_buffer=True,
               presentation_draw_buffer_changes=buffer_changes))
    limits=json.loads((OLD/'results.json').read_text())['limits']
    result=dict(passed=True,inputs_sha256=sha(OUT/'inputs.json'),elf_sha256=HASHES,
       simulator_sha256=sha(OUT/'model/obj/Vtb_gpu'),script_sha256=sha(Path(__file__)),
       helper_sha256=sha(OUT/'analysis_common_diagnostic.py'),runs=runs,comparisons=pairs,
       validation=dict(coupled_frames=64,paired_color_depth_frames=32,independent_diagnostic_reference_starts=8,
                       scene_geometry_culling_exact=True),limits=limits)
    write(OUT/'results.json',result)
    lines=['F9/F10 diagnostic builds: retained-A coupled publication comparison','',
      'These are modeled CPU timings with GPU/SDRAM/CRAM1 RTL, not device FPS.',
      'Start Traffic F9 ms F10 ms Throughput change Time delta ms']
    for p in pairs:lines.append(f"{p['start']:5} {p['traffic']:7} {p['baseline_ms']:9.5f} {p['candidate_ms']:9.5f} {p['modeled_throughput_gain_percent']:16.4f}% {p['frame_time_delta_ms']:13.5f}")
    lines+=['','All32paired color/depth outputs and all8independent diagnostic reference starts match.',
       'Diagnostic overhead is part of both tested executables. Negative throughput changes are regressions and must be included in the release decision.']
    with (OUT/'REPORT.txt').open('x') as f:f.write('\n'.join(lines)+'\n')
    evidence={str(p.relative_to(ROOT)):sha(p)for p in OUT.rglob('*') if p.is_file() and '__pycache__' not in str(p)}
    evidence[str(Path(__file__).relative_to(ROOT))]=sha(Path(__file__))
    write(OUT/'final-manifest.json',dict(passed=True,parent_final_manifest_sha256=sha(OLD/'final-manifest.json'),
       results_sha256=sha(OUT/'results.json'),elf_sha256=HASHES,simulator_sha256=sha(OUT/'model/obj/Vtb_gpu'),
       evidence_sha256=evidence,read_only_model_comparison=True,hardware_changes=False,deployment=False))
    print('\n'.join(lines))
if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=['prepare','run','campaign','analyze'])
    p.add_argument('--name',choices=ELFS);p.add_argument('--start',type=int);p.add_argument('--traffic',type=int,choices=[1,2]);a=p.parse_args()
    if a.action=='prepare':prepare()
    elif a.action=='run':run(a.name,a.start,a.traffic)
    elif a.action=='campaign':campaign()
    else:analyze()
