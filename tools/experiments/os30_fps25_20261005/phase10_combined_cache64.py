#!/usr/bin/env python3
"""Modeled D-cache capacity cost on matched LOD4+CRAM+span-v2 workload."""
import csv,importlib.util,json,os,shutil,subprocess,sys
from pathlib import Path
import phase10_gpu_critical as c
P=c.P;ROOT=c.ROOT;sha=c.sha;save=c.save;rep=c.rep
OLD=P/'span-lookahead-coupled-v1/combined';OUT=P/'combined-cache64-model-v1'
def prepare():
    OUT.mkdir(exist_ok=False);shutil.copytree(OLD/'model/frozen',OUT/'model/frozen')
    shutil.copytree(OLD/'qsim',OUT/'qsim',ignore=shutil.ignore_patterns('build','qsim'))
    p=OUT/'qsim/cache.c';s=p.read_text();p.write_text(rep(s,'p->dc_sets = 1024; p->dc_ways = 2; p->dc_line = 64;','p->dc_sets = 512; p->dc_ways = 2; p->dc_line = 64;'))
    cmd=[x.replace(str(OLD),str(OUT)) for x in json.loads((OLD/'build-command.json').read_text())]
    save(OUT/'build-command.json',cmd)
    save(OUT/'inputs.json',dict(scope=__doc__,parent=str(OLD),change='Only qsim default dc_sets1024 ->512; ways2,line64 and all other source behavior unchanged. No CPU RTL generation or ALM claim.',sources={str(p.relative_to(OUT)):sha(p) for p in OUT.rglob('*') if p.is_file()}))
def build():
    with (OUT/'build-qsim.log').open('x') as f:subprocess.run(['make','-j3',*[f'build/qsim/{n}.o' for n in 'cpu fpu cache prof qelf lint gpu app env main'.split()]],cwd=OUT/'qsim',stdout=f,stderr=subprocess.STDOUT,check=True)
    cmd=json.loads((OUT/'build-command.json').read_text())
    with (OUT/'model/build.log').open('x') as f:subprocess.run(cmd,stdout=f,stderr=subprocess.STDOUT,check=True)
    save(OUT/'model/build.json',dict(command=cmd,simulator_sha256=sha(OUT/'model/obj/Vtb_gpu')))
def run():
    p=ROOT/'tools/experiments/os30_overlap_20261006/run.py';spec=importlib.util.spec_from_file_location('runner',p);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    m.OUT=OUT;os.environ['CRAM_VERTEX_ROUTE']='1';sys.argv=[str(p),'window-8','--elf',str(P/'load-alias-driver-v1/app.elf')];m.main()
    d=OUT/'run-window-8';f=d/'inputs.json';v=json.loads(f.read_text());v['environment']['CRAM_VERTEX_ROUTE']='1';f.write_text(json.dumps(v,indent=2)+'\n')
def analyze():
    a=OLD/'run-window-8';b=OUT/'run-window-8';assert 'FATAL' not in (b/'run.log').read_text()
    for name in ['scene.csv','geometry.csv','culling.csv',*[f'frame_{n:05}.bin' for n in range(360,368)]]:assert (a/name).read_bytes()==(b/name).read_bytes(),name
    for p in (OLD/'model/frozen').rglob('*'):
        if p.is_file():assert sha(p)==sha(OUT/'model/frozen'/p.relative_to(OLD/'model/frozen'))
    for p in (OLD/'qsim').iterdir():
        if p.suffix in ('.c','.h') and p.name!='cache.c':assert sha(p)==sha(OUT/'qsim'/p.name)
    j={label:json.loads((d/'coupled.json').read_text()) for label,d in [('dc128',a),('dc64',b)]}
    ms={k:v['cpu_cycles']/800000 for k,v in j.items()}
    result=dict(passed=True,private_only=True,all8_actual_color_depth_exact=True,all368_scene_geometry_culling_exact=True,
        modeled_frame_ms=ms,extra_ms=ms['dc64']-ms['dc128'],time_increase_pct=100*(ms['dc64']/ms['dc128']-1),
        scope=__doc__,limits=['Only estimated cache capacity changes. This does not quantify actual CPU placement, timing, ALMs or achieved freed RAM blocks.',
            'Input/output accelerator and GPU remain actualRTL; original source pool startup/remap overhead exclusions remain.',
            'Eight fixed-step frames; no broadworld CPU-cache guarantee, physical CPU timing or deployment.'])
    save(OUT/'result.json',result);save(OUT/'final-manifest.json',dict(files={str(p.relative_to(OUT)):sha(p) for p in OUT.rglob('*') if p.is_file() and 'obj' not in p.parts and 'build' not in p.parts},helper_sha256=sha(Path(__file__))))
    print(json.dumps(result,indent=2))
if __name__=='__main__':
    for a in sys.argv[1:]:globals()[a]()
