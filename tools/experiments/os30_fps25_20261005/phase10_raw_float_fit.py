#!/usr/bin/env python3
"""Freeze one private raw-float os30 core; retain CPU, clocks and seed2."""
import argparse,copy,hashlib,json,re,shutil,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
P=ROOT/'docs/measurements/os30-fps25-20261005/phase10'
BASE=P/'vcache-narrow-v1/fit-seed2'
GPU=P/'raw-float-gpu-v1'
CPU='src/fpga/vendor/vexriscv/VexiiRiscv/VexiiRiscv_os30.v'
CPU_A='33895e9a2fc141d8da49bb75e082e06964a49e328b6d87363516ac6f9ab26119'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def write(p,x):
    with p.open('x') as f:json.dump(x,f,indent=2);f.write('\n')

def prepare(out,component):
    # A caller supplies a source bundle that has passed the independent
    # component test. Copy every Verilog dependency from that exact bundle.
    validation=json.loads((component/'result.json').read_text())
    assert validation['passed'],component
    source_manifest=json.loads((component/'sources.json').read_text())
    rtl={}
    for name,item in source_manifest.items():
        if name.endswith('.v'):
            src=component/name
            digest=item['sha256'] if isinstance(item,dict) else item
            assert sha(src)==digest,name
            rtl[name]=src
    assert any('raw_float_processor' in n for n in rtl),rtl
    hook=json.loads((GPU/'prepared.json').read_text())
    assert sha(GPU/'gpu_core.v')==hook['gpu_sha256']
    assert sha(GPU/'geometry_raw_float_ring.v')==hook['transport_sha256']
    old=json.loads((BASE/'frozen-sources.json').read_text())
    assert old[CPU]==CPU_A
    for name,digest in old.items():assert sha(BASE/'frozen'/name)==digest,name
    out.mkdir(exist_ok=False)
    frozen=out/'frozen';shutil.copytree(BASE/'frozen',frozen)
    for name,src in rtl.items():shutil.copy2(src,frozen/'src/fpga/common'/name)
    for name in ['gpu_core.v','geometry_raw_float_ring.v']:
        shutil.copy2(GPU/name,frozen/'src/fpga/common'/name)
    qsf=frozen/'src/fpga/targets/pocket/ap_core.qsf'
    with qsf.open('a') as f:
        for name in [*rtl,'geometry_raw_float_ring.v']:
            f.write('\nset_global_assignment -name VERILOG_FILE ../../common/'+name+'\n')
    actual={str(p.relative_to(frozen)):sha(p) for p in frozen.rglob('*') if p.is_file()}
    assert actual[CPU]==CPU_A
    write(out/'prepared.json',dict(private_only=True,production_modified=False,
        seed=2,seed_sweep=False,configured_mhz=100,
        resource_probe_only=True,rendering_and_frame_time_not_qualified=True,
        component=str(component.relative_to(ROOT)),
        component_validation_sha256=sha(component/'result.json'),
        component_sources_sha256=sha(component/'sources.json'),
        gpu_hook_sha256=sha(GPU/'prepared.json'),recipe_sha256=sha(Path(__file__)),
        source_sha256=actual,changed={n:dict(before=old.get(n),after=h) for n,h in actual.items() if old.get(n)!=h}))
    print(out)

def run(out):
    sys.path.insert(0,str(ROOT/'tools'));import sweep_target_matrix as fit
    def inventory(**kwargs):return copy.deepcopy(json.loads((BASE/'inventory.json').read_text()))
    def freeze(dest,rows):
        expected=json.loads((out/'prepared.json').read_text())['source_sha256']
        for n,h in expected.items():assert sha(out/'frozen'/n)==h,n
        f=dest/'frozen';shutil.copytree(out/'frozen',f)
        fit.save(dest/'inventory.json',rows);fit.save(dest/'frozen-sources.json',expected)
        fit.save(dest/'experiment.json',dict(scope=__doc__,seed_sweep=False,
            private_only=True,prepared_sha256=sha(out/'prepared.json')))
        return f
    fit.inventory=inventory;fit.freeze=freeze
    sys.argv=[sys.argv[0],'--target','pocket','--variant','os30',
        '--extra-macro','INCLUDE_GPU_RAW_FLOAT_WORKER',
        '--seed-offsets','0','--jobs','1','--processors','4',
        '--output',str(out/'fit-seed2')]
    fit.main()

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=['prepare','fit']);p.add_argument('label')
    p.add_argument('--component',default='raw-float-processor-v1')
    args=p.parse_args()
    assert all(re.fullmatch(r'[a-z0-9][a-z0-9-]*',x) for x in [args.label,args.component])
    out=P/args.label
    prepare(out,P/args.component) if args.action=='prepare' else run(out)
