#!/usr/bin/env python3
"""Freeze and couple current F9 CPU model to retained-A GPU/SDRAM/CRAM1 RTL."""
import hashlib, importlib.util, json, shutil, subprocess
from pathlib import Path

ROOT=Path(__file__).resolve().parents[3]
HERE=Path(__file__).resolve().parent
OUT=ROOT/'docs/measurements/os30-overlap-20261006/coupled'
CAMP=ROOT/'docs/measurements/os30-fps25-20261005'
BASE=CAMP/'phase4/texmem/extended'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def write(p,x):
    with p.open('x') as f:json.dump(x,f,indent=2);f.write('\n')
def module(p):
    spec=importlib.util.spec_from_file_location('old_coupled_builder',p)
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

def main():
    oldpath=ROOT/'tools/experiments/sm64_overlap_20261004/build.py'
    old=module(oldpath)
    qmanifest=json.loads((CAMP/'phase6/qsim-manifest.json').read_text())
    qbase=Path(qmanifest['simulator']).parent
    assert sha(qbase/'qsim')==qmanifest['simulator_sha256']
    for n,h in qmanifest['unchanged_sources'].items():assert sha(qbase/n)==h,n
    old.OUT,old.HERE,old.QBASE=OUT,HERE,qbase
    q,names=old.prepare_qsim()
    # Retain the old hook-only adaptation; upstream CPU/cache arithmetic unchanged.
    manifest=json.loads((BASE/'inputs.json').read_text())
    for rel,h in manifest['sources'].items():assert sha(BASE/rel)==h,rel
    dest=OUT/'model';dest.mkdir(exist_ok=False)
    frozen=dest/'frozen';shutil.copytree(BASE/'frozen',frozen)
    p=frozen/'tb_gpu.v';s=p.read_text()
    s=old.rep(s,'    output wire        busy,','''    output wire [2:0] overlap_worker_kind,
    output wire overlap_raster_active, overlap_fragment, overlap_fragment_stall,
    output wire overlap_write_take, overlap_fragment_productive,
    output wire overlap_idle_empty, overlap_payload_empty,
    output wire [11:0] overlap_upload_words, overlap_output_words,
    output wire        busy,''')
    pos=s.rindex('endmodule');s=s[:pos]+(HERE/'probes.v').read_text()+'\n'+s[pos:];p.write_text(s)
    shutil.copy2(HERE/'coupled.cpp',frozen/'coupled.cpp')
    build=json.loads((BASE/'model/build.json').read_text())
    cmd=[x.replace(str(BASE/'frozen'),str(frozen)).replace(str(BASE/'model/obj'),str(dest/'obj')) for x in build['command']]
    cmd=[x.replace('/frozen/replay.cpp','/frozen/coupled.cpp') for x in cmd]
    cmd[cmd.index('-CFLAGS')+1]+=' -I'+str(q)
    cmd+=['-LDFLAGS',' '.join(str(q/'build/qsim'/f'{n}.o') for n in names)+' -lm']
    prov=ROOT/'docs/measurements/os30-sm64-ab-20261005/provenance/manifest.json'
    a=json.loads(prov.read_text())['configurations']['A_single_geo0']
    for rel,h in a['frozen_sources'].items():
        candidate=frozen/'common'/Path(rel).name if '/common/' in rel else frozen/Path(rel).name
        if candidate.is_file():assert sha(candidate)==h,rel
    write(dest/'inputs.json',dict(scope=__doc__,command=cmd,retained_core_sha256=a['pocket_sha256'],
        parent_rtl_manifest_sha256=sha(BASE/'inputs.json'),parent_qsim_manifest_sha256=sha(CAMP/'phase6/qsim-manifest.json'),
        provenance_sha256=sha(prov),inherited_hook_builder_sha256=sha(oldpath),
        tools_sha256={p.name:sha(p) for p in HERE.iterdir() if p.is_file()},
        sources={str(p.relative_to(dest)):sha(p) for p in frozen.rglob('*') if p.is_file()}))
    with (dest/'build.log').open('x') as log:subprocess.run(cmd,stdout=log,stderr=subprocess.STDOUT,check=True)
    write(dest/'build.json',dict(command=cmd,inputs_sha256=sha(dest/'inputs.json'),simulator_sha256=sha(dest/'obj/Vtb_gpu')))
    print(dest/'obj/Vtb_gpu',flush=True)

if __name__=='__main__':main()
