#!/usr/bin/env python3
"""Clone the qualified native fixture; change only CPU/GPU control cones."""
from pathlib import Path
import hashlib
import json
import shutil
import subprocess
import sys
import time

R = Path(__file__).resolve().parents[3]
P = R/'docs/measurements/os30-fps25-20261005/phase10'
W = P/'native-renderer-window-v1'
BASE = W/'model-cache64-render32-forward-texture-v1'
OUT = W/'model-control-cones-v1'
CPU = 'frozen/src/fpga/vendor/vexriscv/VexiiRiscv/VexiiRiscv_os30.v'
GPU = 'frozen/src/fpga/common/gpu_core.v'
SOURCES = {
    CPU:(P/'cpu-control-compose-v1/VexiiRiscv.v','878e6978d7bceb10c9c5323c983c025f32eff2fdde4e24369c4e114d7edf230e'),
    GPU:(P/'gpu-control-owner-v1/gpu_core.v','e75ac93db5483ee8525c44ef1aeed4e610f8deecf8fa42765997367873dd4f15'),
}
PARENTS = {CPU:'82b7e440177b195cbe84f5679c7e88c44b5df2aae004f798c63b4adc3b926534',GPU:'238f44273b6bb0188447485d1501e898c8df0c1cb9b758841e8af7ceac414adb'}
sha = lambda p:hashlib.sha256(p.read_bytes()).hexdigest()


def save(p,d):
    with p.open('x') as f:json.dump(d,f,indent=2);f.write('\n')


def prepare():
    cp=P/'cpu-control-compose-v1/manifest.json'
    gp=P/'gpu-control-owner-v1/manifest.json'
    assert sha(cp)=='f37b54462c09a97aca6df36e71d71e8f225f27c8cb58987c7afa4dbee4e19228'
    assert sha(gp)=='dd8754de06809670370cacff821574cc009e7745c192770ea50a8774fdc23ae2'
    # Existence requires the component's literal, broad replay and active
    # abort/recovery checks to have finished and been sealed by its author.
    manifests={str(p.relative_to(R)):sha(p) for p in [cp,gp]}
    for p in [cp,gp]:
        m=json.loads(p.read_text())
        for n,h in m.get('evidence_sha256',{}).items():assert sha(R/n)==h,n
        for n,h in m.get('files',{}).items():assert sha(p.parent/n)==h,n
    assert json.loads((P/'gpu-control-owner-v1/result.json').read_text())['passed']
    assert json.loads((P/'gpu-control-owner-v1/literal/result.json').read_text())['passed']
    d=json.loads((BASE/'prepared.json').read_text())
    for n,h in d['source_sha256'].items():assert sha(BASE/n)==h,n
    assert json.loads((BASE/'build.json').read_text())['returncode']==0
    assert all(d['source_sha256'][n]==h for n,h in PARENTS.items())
    for p,h in SOURCES.values():assert sha(p)==h,p
    OUT.mkdir(exist_ok=False)
    shutil.copytree(BASE/'frozen',OUT/'frozen')
    for n,(p,h) in SOURCES.items():shutil.copyfile(p,OUT/n)
    before=dict(d['source_sha256'])
    d['source_sha256']={str(p.relative_to(OUT)):sha(p) for p in (OUT/'frozen').rglob('*') if p.is_file()}
    assert set(before)==set(d['source_sha256'])
    assert {n for n in before if before[n]!=d['source_sha256'][n]}==set(SOURCES)
    d['command']=[x.replace(str(BASE),str(OUT)) for x in d['command']]
    d['cpu_sha256']=SOURCES[CPU][1]
    d['recipe_sha256']=sha(Path(__file__))
    d['control_components']=dict(manifest_sha256=manifests,
        parent_prepared_sha256=sha(BASE/'prepared.json'),changed_model_sources=[CPU,GPU],
        intended_extra_cycles=0,physical_timing_unmeasured=True)
    save(OUT/'prepared.json',d)
    shutil.copyfile(__file__,OUT/'recipe-prepare.py')
    print('PREPARED',OUT,sha(OUT/'prepared.json'),flush=True)


def build():
    d=json.loads((OUT/'prepared.json').read_text())
    for n,h in d['source_sha256'].items():assert sha(OUT/n)==h,n
    for n,h in d['control_components']['manifest_sha256'].items():assert sha(R/n)==h,n
    started=time.monotonic()
    with (OUT/'build.log').open('x') as f:
        result=subprocess.run(d['command'],cwd=OUT/'frozen/src/fpga/test',stdout=f,stderr=subprocess.STDOUT)
    save(OUT/'build.json',dict(returncode=result.returncode,seconds=time.monotonic()-started))
    assert result.returncode==0
    print('native control model build passed',flush=True)


if __name__=='__main__':
    assert len(sys.argv)==2 and sys.argv[1] in ['prepare','build']
    globals()[sys.argv[1]]()
