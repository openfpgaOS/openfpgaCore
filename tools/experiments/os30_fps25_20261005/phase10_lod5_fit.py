#!/usr/bin/env python3
"""One private LOD5 CRAM + span-v3 AREA fit: original CPU128, seed 2."""
import copy, hashlib, json, shutil, sys
from pathlib import Path
import phase10_load_bram_fit as base

ROOT=base.ROOT; P=base.P; OUT=P/'lod5-cram-span-core-v1'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()

def fitrun():
    prep=json.loads((OUT/'prepared.json').read_text())
    review=json.loads((OUT/'independent-review.json').read_text())
    assert review['passed_for_resource_fit'] and not review['deployment_ready']
    assert prep['cpu_cache_kib']==128 and prep['seed']==2 and not prep['seed_sweep']
    for name,h in prep['source_sha256'].items():
        assert sha(OUT/'frozen'/name)==h,name
    for name,h in review['evidence_sha256'].items():
        assert sha(ROOT/name)==h,name
    assert prep['source_sha256']['src/fpga/vendor/vexriscv/VexiiRiscv/VexiiRiscv_os30.v']=='33895e9a2fc141d8da49bb75e082e06964a49e328b6d87363516ac6f9ab26119'
    assert json.loads((P/'lod5-components-v1/wrapper/result.json').read_text())['passed']
    assert json.loads((P/'cram1-share-full-v1/manifest.json').read_text())['passed']
    qsf=(OUT/'frozen'/base.QSF).read_text()
    for text in ['OPTIMIZATION_MODE BALANCED','OPTIMIZATION_TECHNIQUE AREA',
                 'PHYSICAL_SYNTHESIS_REGISTER_DUPLICATION OFF',
                 'OPTIMIZATION_TECHNIQUE SPEED -to ic|cpu|cpu',
                 'VERILOG_MACRO INCLUDE_SPAN_LOOKAHEAD']:
        assert text in qsf,text
    sys.path.insert(0,str(ROOT/'tools'));import sweep_target_matrix as fit
    def inventory(**kwargs):
        rows=copy.deepcopy(json.loads((base.BASE/'inventory.json').read_text()))
        assert len(rows)==1 and rows[0]['seed']==2 and rows[0]['variant']=='os30'
        return rows
    def freeze(dest,rows):
        frozen=dest/'frozen';shutil.copytree(OUT/'frozen',frozen)
        fit.save(dest/'inventory.json',rows)
        fit.save(dest/'frozen-sources.json',prep['source_sha256'])
        fit.save(dest/'experiment.json',dict(scope=__doc__,resource_only=True,
            component_tests_passed=True,physical_16mib_tests_passed=True,
            full_core_functionally_qualified=False,deployment_ready=False,
            seed=2,seed_sweep=False,cpu_cache_kib=128,configured_mhz=100,
            prepared_sha256=sha(OUT/'prepared.json'),
            review_sha256=sha(OUT/'independent-review.json'),
            recipe_sha256=sha(Path(__file__)),
            sweep_recipe_sha256=sha(Path(fit.__file__))))
        shutil.copyfile(__file__,dest/'recipe.py')
        return frozen
    fit.inventory=inventory;fit.freeze=freeze
    sys.argv=[sys.argv[0],'--target','pocket','--variant','os30',
        '--seed-offsets','0','--jobs','1','--processors','4',
        '--output',str(OUT/'fit-seed2')]
    fit.main()

if __name__=='__main__':
    assert sys.argv[1:]==['fit'],'fit'
    fitrun()
