#!/usr/bin/env python3
"""One bounded F11 actual-GPU coupled screen for unchanged-producer local cursor."""
import argparse,importlib.util,json,os,shutil,sys
from pathlib import Path
from phase10_gpu_critical import P,ROOT,sha,save
OUT=P/'batch-cursor-coupled-v1'
PARENT=P/'gpu-critical-bound-v2/coupled'
def prepare():
 OUT.mkdir(exist_ok=False)
 (OUT/'model/obj').mkdir(parents=True)
 for name in ('build.json',):shutil.copy2(PARENT/'model'/name,OUT/'model'/name)
 shutil.copy2(PARENT/'model/obj/Vtb_gpu',OUT/'model/obj/Vtb_gpu')
 save(OUT/'prepared.json',dict(scope=__doc__,frozen_model=str(PARENT),simulator_sha256=sha(OUT/'model/obj/Vtb_gpu'),app_sha256=sha(P/'batch-cursor-v1/app.elf'),baseline_run=str(PARENT/'run-baseline-8'),no_rtl_or_model_changes=True))
def run():
 p=ROOT/'tools/experiments/os30_overlap_20261006/run.py';spec=importlib.util.spec_from_file_location('cursor_runner',p);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
 m.OUT=OUT;os.environ['IDEAL_GPU_MEMORY']='0'
 sys.argv=[str(p),'candidate','--elf',str(P/'batch-cursor-v1/app.elf')];m.main()
 d=OUT/'run-candidate';assert 'FATAL' not in(d/'run.log').read_text()
 f=d/'inputs.json';v=json.loads(f.read_text());v['environment']['IDEAL_GPU_MEMORY']='0';f.write_text(json.dumps(v,indent=2)+'\n')
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('action',choices=['prepare','run']);a=p.parse_args();globals()[a.action]()
