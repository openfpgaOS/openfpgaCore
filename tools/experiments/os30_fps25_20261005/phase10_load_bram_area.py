#!/usr/bin/env python3
"""One private unchanged-RTL LOD4 area screen, original CPU and seed 2."""
import copy,difflib,json,shutil,sys
from pathlib import Path
import phase10_load_bram_fit as base
ROOT=base.ROOT; P=base.P; BASE=P/'load-bram-core-v1'; OUT=P/'load-bram-core-area-v1'
QSF=base.QSF
SETTINGS='''
# Private LOD4 area screen; original CPU retains speed-oriented synthesis.
set_global_assignment -name OPTIMIZATION_MODE BALANCED
set_global_assignment -name OPTIMIZATION_TECHNIQUE AREA
set_global_assignment -name PHYSICAL_SYNTHESIS_REGISTER_DUPLICATION OFF
set_instance_assignment -name OPTIMIZATION_TECHNIQUE SPEED -to ic|cpu|cpu
'''
def prepare():
    old=json.loads((BASE/'prepared.json').read_text())['source_sha256']
    for n,h in old.items():assert base.sha(BASE/'frozen'/n)==h,n
    assert json.loads((BASE/'result.json').read_text())['whole_core_fit_successful'] is False
    base.bound_inputs(BASE/'component-binding.json')
    OUT.mkdir(exist_ok=False);shutil.copytree(BASE/'frozen',OUT/'frozen')
    shutil.copy2(BASE/'component-binding.json',OUT/'component-binding.json')
    p=OUT/'frozen'/QSF;before=p.read_text();p.write_text(before+SETTINGS)
    actual={str(p.relative_to(OUT/'frozen')):base.sha(p) for p in (OUT/'frozen').rglob('*') if p.is_file()}
    assert [n for n in actual if actual[n]!=old[n]]==[QSF]
    base.save(OUT/'prepared.json',dict(private_only=True,production_modified=False,
        source_sha256=actual,only_changed_file=QSF,rtl_byte_identical=True,
        configured_mhz=100,seed=2,seed_sweep=False,
        parent_prepared_sha256=base.sha(BASE/'prepared.json'),
        parent_manifest_sha256=base.sha(BASE/'final-manifest.json'),
        component_binding_sha256=base.sha(OUT/'component-binding.json'),
        recipe_sha256=base.sha(Path(__file__)),base_recipe_sha256=base.sha(Path(base.__file__)),
        settings=SETTINGS,full_core_functionally_qualified=False,deployment_ready=False))
    (OUT/'candidate.patch').write_text(''.join(difflib.unified_diff(before.splitlines(True),p.read_text().splitlines(True),fromfile='load-bram-core-v1/ap_core.qsf',tofile='load-bram-core-area-v1/ap_core.qsf')))
    print(OUT)
def fitrun():
    base.bound_inputs(OUT/'component-binding.json')
    sys.path.insert(0,str(ROOT/'tools'));import sweep_target_matrix as fit
    def inventory(**kwargs):return copy.deepcopy(json.loads((base.BASE/'inventory.json').read_text()))
    def freeze(dest,rows):
        prep=json.loads((OUT/'prepared.json').read_text());expected=prep['source_sha256']
        assert prep['recipe_sha256']==base.sha(Path(__file__))
        assert prep['base_recipe_sha256']==base.sha(Path(base.__file__))
        for p,h in expected.items():assert base.sha(OUT/'frozen'/p)==h,p
        F=dest/'frozen';shutil.copytree(OUT/'frozen',F)
        fit.save(dest/'inventory.json',rows);fit.save(dest/'frozen-sources.json',expected)
        fit.save(dest/'experiment.json',dict(scope=__doc__,resource_only=True,component_tests_passed=True,full_core_functionally_qualified=False,seed=2,seed_sweep=False,prepared_sha256=base.sha(OUT/'prepared.json')))
        return F
    fit.inventory=inventory;fit.freeze=freeze
    sys.argv=[sys.argv[0],'--target','pocket','--variant','os30','--seed-offsets','0','--jobs','1','--processors','4','--output',str(OUT/'fit-seed2')]
    fit.main()
if __name__=='__main__':
    if sys.argv[1:]==['prepare']:prepare()
    elif sys.argv[1:]==['fit']:fitrun()
    else:raise SystemExit('prepare|fit')
