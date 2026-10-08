#!/usr/bin/env python3
"""Private transform-only LOD6 with CPU lighting; native proof and map only."""
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
COMP = P / 'lod6-components-v1'
BASE = P / 'lod5-cram-span-core-v2'
CORE = P / 'lod6-transform-core-v1'
WRAPPER = 'src/fpga/common/phase10_load_alias.v'
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
    p = f/'phase10_load_alias.v'
    s = p.read_text()
    s = rep(s, 'Private LOD5:', 'Private LOD6 (transform only):')
    s = rep(s, '// All hardware jobs use dest0.', '// Lighting registers are ignored; RGBA always preserves source bytes.\n// CPU may calculate lighting while this synchronous LOAD runs.\n// All hardware jobs use dest0.')
    s = rep(s, 'source_base,aspect,cx,cy,cz', 'source_base,aspect')
    s = rep(s, ' reg [23:0] ambient,diffuse;\n', '')
    s = rep(s, ' reg lit,active,', ' reg active,')
    s = rep(s, '.coefficient_x(cx),.coefficient_y(cy),.coefficient_z(cz)',
            ".coefficient_x(32'd0),.coefficient_y(32'd0),.coefficient_z(32'd0)")
    s = rep(s, '.ambient_rgb(ambient),.diffuse_rgb(diffuse),.lighting_enabled(lit)',
            ".ambient_rgb(24'd0),.diffuse_rgb(24'd0),.lighting_enabled(1'b0)")
    s = rep(s, "32'h4c4f4405", "32'h4c4f4406")
    s = rep(s, 'cx<=0;cy<=0;cz<=0;', '')
    s = rep(s, '   ambient<=0;diffuse<=0;', '   ')
    s = rep(s, 'matrix_valid<=0;lit<=0;', 'matrix_valid<=0;')
    for line in ["     10'h006:cx<=reg_wdata;\n", "     10'h007:cy<=reg_wdata;\n",
                 "     10'h008:cz<=reg_wdata;\n", "     10'h009:ambient<=reg_wdata[23:0];\n",
                 "     10'h00a:diffuse<=reg_wdata[23:0];\n", "     10'h00c:lit<=reg_wdata[0];\n"]:
        s = rep(s, line, '')
    p.write_text(s)
    p = f/'phase10_load_alias_test.cpp'
    s = p.read_text().replace('0x4c4f4405', '0x4c4f4406')
    s = rep(s, 'j.h[1]=0x00fc0000+i*4096;jobs.push_back(j);', '''j.h[1]=0x00fc0000+i*4096;
  // Native XYZ/W/inverse/UV/clip remain untouched. LOD6 always preserves
  // input RGBA, even for fixtures whose original native CPU lit the vertex.
  for(unsigned v=0;v<j.h[0];v++){
   j.d[v*15+12]=(j.d[v*15+4]>>16)|((j.d[v*15+5]&0xffffu)<<16);
   j.d[v*15+14]=1;
  }
  jobs.push_back(j);''')
    s = rep(s, ' // Physical CRAM offsets:', ''' // Removed lighting registers must not affect geometry, including invalid
 // floating coefficients and requests to enable hardware lighting.
 configure(jobs[0]);for(auto off:{0x18u,0x1cu,0x20u,0x24u,0x28u,0x30u}){
  put(off,0xffffffffu);need(reg(off)==0,"removed lighting register readable");
 }
 put(8,1);done();check(jobs[0]);
 // Physical CRAM offsets:''')
    s = s.replace('native_independent_RGB\\\":145', 'raw_RGBA_exact\\\":230')
    p.write_text(s)
    shutil.copy2(__file__, COMP/'recipe.py')
    save(COMP/'sources.json', {x.name:sha(x) for x in sorted(f.iterdir())})
    save(COMP/'abi.json', dict(id='0x4c4f4406', source='CRAM1 physical byte offset 0..0xffffff, 4-byte aligned, whole24*count range checked',
         count='1..32, hardware dest0', result='same read-only raw32B alias0x13f80000',
         ignored_byte_offsets=['0x18','0x1c','0x20','0x24','0x28','0x30'],
         lighting=False, rgba='Original source bytes, CPU must preserve independently computed color for lit jobs',
         status_and_reset='unchanged LOD5', production_changed=False))
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
    assert result['raw_RGBA_exact']==230
    result.update(source_id='0x4c4f4406', source_range_unchanged=True,
                  ignored_lighting_registers_tested=True, no_response_backpressure=True,
                  native_fixture_sha256=sha(f/'native.bin'), source_manifest_sha256=sha(COMP/'sources.json'))
    save(d/'result.json',result); print(json.dumps(result,indent=2))

def stage():
    assert json.loads((COMP/'wrapper/result.json').read_text())['passed']
    original = json.loads((BASE/'prepared.json').read_text())['source_sha256']
    for name, digest in original.items():
        assert sha(BASE/'frozen'/name)==digest,name
    CORE.mkdir(exist_ok=False); shutil.copytree(BASE/'frozen',CORE/'frozen')
    shutil.copy2(COMP/'frozen/phase10_load_alias.v',CORE/'frozen'/WRAPPER)
    actual = {str(x.relative_to(CORE/'frozen')):sha(x) for x in sorted((CORE/'frozen').rglob('*')) if x.is_file()}
    changed = [n for n,h in actual.items() if original.get(n)!=h]
    assert changed==[WRAPPER],changed
    save(CORE/'prepared.json',dict(private_only=True,source_sha256=actual,only_changed=changed,
         parent_prepared_sha256=sha(BASE/'prepared.json'),component_test_sha256=sha(COMP/'wrapper/result.json'),
         cpu_cache_kib=128,configured_mhz=100,seed=2,seed_sweep=False,fitted=False,deployed=False))
    (CORE/'integration.patch').write_text(''.join(difflib.unified_diff((BASE/'frozen'/WRAPPER).read_text().splitlines(True),
         (CORE/'frozen'/WRAPPER).read_text().splitlines(True),fromfile='LOD5/'+WRAPPER,tofile='LOD6/'+WRAPPER)))
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
