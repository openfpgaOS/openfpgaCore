#!/usr/bin/env python3
"""Private unified transform/light controller; unchanged LOD5 ABI, map only."""
import argparse
import copy
import difflib
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
P = ROOT / 'docs/measurements/os30-fps25-20261005/phase10'
COMP = P / 'unified-load-components-v2'
BASE = P / 'lod5-cram-span-core-v2'
CORE = P / 'unified-load-core-v2'
WRAPPER = 'src/fpga/common/geometry_transform_light.v'
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()

def save(p, value):
    with p.open('x') as f:
        json.dump(value, f, indent=2); f.write('\n')

def rep(s, before, after):
    assert s.count(before) == 1, (before[:80], s.count(before))
    return s.replace(before, after)

def prepare():
    old = P / 'lod5-components-v1'
    sources = json.loads((old/'sources.json').read_text())
    for name, digest in sources.items():
        assert sha(old/'frozen'/name) == digest, name
    f = COMP/'frozen'
    shutil.copytree(old/'frozen', f)
    shutil.copy2(ROOT/'tools/experiments/os30_fps25_20261005/phase10_float_batch/geometry_transform_light_unified_bram.v', f/'geometry_transform_light.v')
    shutil.copy2(__file__, COMP/'recipe.py')
    save(COMP/'sources.json', {x.name:sha(x) for x in sorted(f.iterdir())})
    save(COMP/'abi.json', dict(id='0x4c4f4405', unchanged=True,
        shared='transform/lighting count, phase, vertex, read/operand/tag and outstanding state',
        production_changed=False))
    print(COMP)

def test():
    f = COMP/'frozen'; d = COMP/'wrapper'; d.mkdir(exist_ok=False)
    cmd = ['verilator','--cc','--exe','--build','-j','4','-Wno-fatal','-Wno-WIDTH',
           '--top-module','tb_load_alias','--Mdir',str(d/'obj'),'-CFLAGS','-O2 -std=c++17']
    cmd += [str(x) for x in sorted(f.glob('*.v'))] + [str(f/'phase10_load_alias_test.cpp')]
    save(d/'build-command.json', cmd)
    with (d/'build.log').open('x') as log:
        subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT, check=True)
    with (d/'run.log').open('x') as log:
        subprocess.run([str(d/'obj/Vtb_load_alias'),str(f/'native.bin')],stdout=log,stderr=subprocess.STDOUT,check=True)
    result = json.loads((d/'run.log').read_text().splitlines()[-1]); assert result['passed']
    result.update(source_id='0x4c4f4405', source_range_unchanged=True,
                  unified_scheduler=True, no_response_backpressure=True,
                  native_fixture_sha256=sha(f/'native.bin'), source_manifest_sha256=sha(COMP/'sources.json'))
    save(d/'result.json',result); print(json.dumps(result,indent=2))

def stage():
    assert json.loads((COMP/'wrapper/result.json').read_text())['passed']
    original = json.loads((BASE/'prepared.json').read_text())['source_sha256']
    for name, digest in original.items():
        assert sha(BASE/'frozen'/name)==digest,name
    CORE.mkdir(exist_ok=False); shutil.copytree(BASE/'frozen',CORE/'frozen')
    shutil.copy2(COMP/'frozen/geometry_transform_light.v',CORE/'frozen'/WRAPPER)
    actual = {str(x.relative_to(CORE/'frozen')):sha(x) for x in sorted((CORE/'frozen').rglob('*')) if x.is_file()}
    changed = [n for n,h in actual.items() if original.get(n)!=h]
    assert changed==[WRAPPER],changed
    save(CORE/'prepared.json',dict(private_only=True,source_sha256=actual,only_changed=changed,
         parent_prepared_sha256=sha(BASE/'prepared.json'),component_test_sha256=sha(COMP/'wrapper/result.json'),
         cpu_cache_kib=128,configured_mhz=100,seed=2,seed_sweep=False,fitted=False,deployed=False))
    (CORE/'integration.patch').write_text(''.join(difflib.unified_diff((BASE/'frozen'/WRAPPER).read_text().splitlines(True),
         (CORE/'frozen'/WRAPPER).read_text().splitlines(True),fromfile='LOD5/'+WRAPPER,tofile='unified/'+WRAPPER)))
    shutil.copy2(__file__,CORE/'recipe.py');print(CORE)

def map_only():
    prep=json.loads((CORE/'prepared.json').read_text())
    for name,digest in prep['source_sha256'].items(): assert sha(CORE/'frozen'/name)==digest,name
    d=CORE/'map-core';d.mkdir(exist_ok=False)
    shutil.copytree(CORE/'frozen',d/'frozen')
    sys.path.insert(0,str(ROOT/'tools'));import sweep_target_matrix as fit
    row=copy.deepcopy(json.loads((BASE/'fit-seed2/inventory.json').read_text())[0])
    assert row['seed']==2 and row['variant']=='os30'
    directory,project=fit.prepare(d/'frozen',d/'job',row,2,4)
    cmd=fit.docker(d,'pocket',directory,['quartus_map',project])
    save(d/'invocation.json',dict(command=cmd,prepared_sha256=sha(CORE/'prepared.json'),recipe_sha256=sha(Path(__file__))))
    with (d/'map.log').open('x') as log: subprocess.run(cmd,stdout=log,stderr=subprocess.STDOUT,check=True)
    report=directory/'output_files/ap_core.map.rpt'
    text=report.read_text(errors='replace');entities=[]
    for line in text.split('; Analysis & Synthesis Resource Utilization by Entity')[1].splitlines():
        r=[x.strip() for x in line.split(';')][1:-1]
        if len(r)>=10 and r[-2] in ['apf_top','gpu_core','VexiiRiscv','geometry_load_alias','geometry_raw_load',
                                  'geometry_transform_light','geometry_lighting_batch','geometry_color_rtz','GeometryFmaProbe']:
            entities.append(dict(entity=r[-2],aluts=r[1],registers=r[2],memory_bits=r[3],dsp=r[4],path=r[-3]))
    save(d/'result.json',dict(passed_map=True,fit_run=False,private_only=True,deployed=False,entities=entities,
                             report_sha256=sha(report),invocation_sha256=sha(d/'invocation.json')))
    print(json.dumps(entities,indent=2))

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['prepare','test','stage','map_only'])
    globals()[parser.parse_args().action]()
