#!/usr/bin/env python3
"""Attributable private cache read-stage synthesis over fitted compact LOD5."""
import copy
import difflib
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[3]
P=ROOT/'docs/measurements/os30-fps25-20261005/phase10'
BASE=P/'compact-lod5-core-v1'
COMP=P/'cache-read-stage-v2'
OUT=P/'cache-read-stage-core-v2'
CACHE='src/fpga/common/gpu_color_depth_cache.sv'
TOP='src/fpga/targets/pocket/core_top.v'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()

def save(p,v):
    with p.open('x') as f: json.dump(v,f,indent=2);f.write('\n')

def stage():
    original=json.loads((BASE/'prepared.json').read_text())['source_sha256']
    for n,h in original.items(): assert sha(BASE/'frozen'/n)==h,n
    proof=json.loads((COMP/'protocol/result.json').read_text())
    assert proof['passed'] and proof['default_cycle_identical']
    assert sha(COMP/'gpu_color_depth_cache.sv')==json.loads((COMP/'prepared.json').read_text())['source_sha256']
    OUT.mkdir(exist_ok=False)
    shutil.copytree(BASE/'frozen',OUT/'frozen')
    shutil.copy2(COMP/'gpu_color_depth_cache.sv',OUT/'frozen'/CACHE)
    p=OUT/'frozen'/TOP;s=p.read_text()
    needle='gpu_color_depth_cache #('
    assert s.count(needle)==1
    s=s.replace(needle,needle+'\n    .REGISTER_READ_LOOKUP(1),\n    .REGISTER_FILL_HAZARD(1),')
    p.write_text(s)
    sources={str(p.relative_to(OUT/'frozen')):sha(p) for p in sorted((OUT/'frozen').rglob('*')) if p.is_file()}
    changed=[n for n,h in sources.items() if original[n]!=h]
    assert set(changed)=={CACHE,TOP},changed
    (OUT/'integration.patch').write_text(''.join(''.join(difflib.unified_diff((BASE/'frozen'/n).read_text().splitlines(True),(OUT/'frozen'/n).read_text().splitlines(True),fromfile='compact-v1/'+n,tofile='read-stage/'+n)) for n in changed))
    save(OUT/'prepared.json',dict(source_sha256=sources,only_changed=changed,seed=2,seed_sweep=False,
        cpu_cache_kib=128,configured_mhz=100,parent_prepared_sha256=sha(BASE/'prepared.json'),
        component_manifest_sha256=sha(COMP/'manifest.json'),private_only=True,fit_run=False,deployed=False))
    shutil.copy2(__file__,OUT/'recipe.py')

def map_only():
    prep=json.loads((OUT/'prepared.json').read_text())
    for n,h in prep['source_sha256'].items(): assert sha(OUT/'frozen'/n)==h,n
    d=OUT/'map-core';d.mkdir(exist_ok=False)
    shutil.copytree(OUT/'frozen',d/'frozen')
    sys.path.insert(0,str(ROOT/'tools'));import sweep_target_matrix as fit
    row=copy.deepcopy(json.loads((BASE/'fit-seed2/inventory.json').read_text())[0])
    assert row['seed']==2 and row['variant']=='os30'
    directory,project=fit.prepare(d/'frozen',d/'job',row,2,4)
    cmd=fit.docker(d,'pocket',directory,['quartus_map',project])
    save(d/'invocation.json',dict(command=cmd,prepared_sha256=sha(OUT/'prepared.json'),recipe_sha256=sha(Path(__file__))))
    with (d/'map.log').open('x') as log: subprocess.run(cmd,stdout=log,stderr=subprocess.STDOUT,check=True)
    report()

def report():
    d=OUT/'map-core';r=d/'job/pocket-os30-s2/src/fpga/targets/pocket/output_files/ap_core.map.rpt'
    # sweep_target_matrix.prepare places its single job at the requested directory.
    if not r.exists():
        found=list((d/'job').rglob('ap_core.map.rpt'));assert len(found)==1; r=found[0]
    text=r.read_text(errors='replace');rows=[]
    assert 'Analysis & Synthesis was successful' in text
    for line in text.split('; Analysis & Synthesis Resource Utilization by Entity')[1].splitlines():
        v=[x.strip() for x in line.split(';')][1:-1]
        if len(v)>=10 and v[-2] in ('apf_top','core_top','gpu_color_depth_cache','gpu_core'):
            rows.append(dict(entity=v[-2],aluts=v[1],registers=v[2],memory_bits=v[3],dsp=v[4],path=v[-3]))
    save(d/'result.json',dict(passed_map=True,fit_run=False,private_only=True,deployed=False,entities=rows,
        report_sha256=sha(r),invocation_sha256=sha(d/'invocation.json')))
    print(json.dumps(rows,indent=2))

if __name__=='__main__':
    assert sys.argv[1] in ('stage','map_only','report')
    globals()[sys.argv[1]]()
