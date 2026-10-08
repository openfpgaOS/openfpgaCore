#!/usr/bin/env python3
"""Private same-span scheduler paired against simple F11 and LOD4/CRAM source."""
import argparse,importlib.util,json,os,shutil,subprocess,sys
from pathlib import Path
import phase10_gpu_critical as c
ROOT=c.ROOT;P=c.P;sha=c.sha;save=c.save
OUT=P/'span-lookahead-coupled-v1';CAND=P/'span-lookahead-v2'
PARENTS={'simple':P/'gpu-critical-bound-v2/coupled','combined':P/'cram-source-coupled-v1'}
def prepare(label):
    old=PARENTS[label];d=OUT/label;d.mkdir(parents=True,exist_ok=False)
    shutil.copytree(old/'model/frozen',d/'model/frozen')
    shutil.copytree(old/'qsim',d/'qsim',ignore=shutil.ignore_patterns('build','qsim'))
    assert sha(CAND/'gpu_core.v')=='d9e58835f5c2e8dacfcba8ac60a4cb905e523471e566df3293cd0542708530c7'
    assert sha(old/'model/frozen/common/gpu_core.v')==sha(P/'render-line32-v1/baseline/model/frozen/common/gpu_core.v')
    shutil.copy2(CAND/'gpu_core.v',d/'model/frozen/common/gpu_core.v')
    cmd=[x.replace(str(old),str(d)) for x in json.loads((old/'build-command.json').read_text())]
    cmd.append('+define+INCLUDE_SPAN_LOOKAHEAD')
    save(d/'build-command.json',cmd)
    save(d/'inputs.json',dict(scope=__doc__,parent=str(old),candidate=str(CAND),sources={str(f.relative_to(d)):sha(f) for f in d.rglob('*') if f.is_file()}))
def build(label):
    d=OUT/label
    with (d/'build-qsim.log').open('x') as f:subprocess.run(['make','-j3',*[f'build/qsim/{n}.o' for n in 'cpu fpu cache prof qelf lint gpu app env main'.split()]],cwd=d/'qsim',stdout=f,stderr=subprocess.STDOUT,check=True)
    cmd=json.loads((d/'build-command.json').read_text())
    with (d/'model/build.log').open('x') as f:subprocess.run(cmd,stdout=f,stderr=subprocess.STDOUT,check=True)
    save(d/'model/build.json',dict(command=cmd,simulator_sha256=sha(d/'model/obj/Vtb_gpu')))
def run(label):
    p=ROOT/'tools/experiments/os30_overlap_20261006/run.py';spec=importlib.util.spec_from_file_location('runner',p);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    m.OUT=OUT/label
    elf=P.parent/'phase9/checks-state-f11/app.elf' if label=='simple' else P/'load-alias-driver-v1/app.elf'
    os.environ['IDEAL_GPU_MEMORY']='0';os.environ['CRAM_VERTEX_ROUTE']=str(int(label=='combined'))
    sys.argv=[str(p),'window-8','--elf',str(elf)];m.main();d=m.OUT/'run-window-8';assert 'FATAL' not in (d/'run.log').read_text()
    f=d/'inputs.json';v=json.loads(f.read_text());v['environment'].update(IDEAL_GPU_MEMORY='0',CRAM_VERTEX_ROUTE=str(int(label=='combined')));f.write_text(json.dumps(v,indent=2)+'\n')
if __name__=='__main__':
    a=argparse.ArgumentParser();a.add_argument('action');a.add_argument('label',choices=PARENTS);n=a.parse_args();globals()[n.action](n.label)
