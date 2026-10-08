#!/usr/bin/env python3
"""Single retained-seed GDR area screen; identical RTL and retained CPU."""
import difflib,json,shutil,sys
from pathlib import Path
import phase10_gpu_direct_fit as fit
P=fit.P;BASE=P/'gpu-direct-core-compact-v2';OUT=P/'gpu-direct-core-area-v1'
QSF='src/fpga/targets/pocket/ap_core.qsf'
SETTINGS='''
# Private area screen: retained CPU keeps speed-oriented synthesis.
set_global_assignment -name OPTIMIZATION_MODE BALANCED
set_global_assignment -name OPTIMIZATION_TECHNIQUE AREA
set_global_assignment -name PHYSICAL_SYNTHESIS_REGISTER_DUPLICATION OFF
set_instance_assignment -name OPTIMIZATION_TECHNIQUE SPEED -to ic|cpu|cpu
'''
def prepare():
    old=json.loads((BASE/'prepared.json').read_text())['source_sha256']
    for n,h in old.items():assert fit.sha(BASE/'frozen'/n)==h,n
    OUT.mkdir(exist_ok=False);shutil.copytree(BASE/'frozen',OUT/'frozen')
    p=OUT/'frozen'/QSF;before=p.read_text();p.write_text(before+SETTINGS)
    actual={str(p.relative_to(OUT/'frozen')):fit.sha(p) for p in (OUT/'frozen').rglob('*') if p.is_file()}
    assert [n for n in actual if actual[n]!=old[n]]==[QSF]
    fit.save(OUT/'prepared.json',dict(private_only=True,production_modified=False,
        source_sha256=actual,only_changed_file=QSF,rtl_byte_identical=True,
        configured_mhz=100,seed=2,seed_sweep=False,
        parent_prepared_sha256=fit.sha(BASE/'prepared.json'),
        recipe_sha256=fit.sha(Path(__file__)),settings=SETTINGS,
        scope='Area/timing screen only. Unchanged GDR RTL already passes component tests; real frame timing remains separately modeled.'))
    (OUT/'candidate.patch').write_text(''.join(difflib.unified_diff(before.splitlines(True),p.read_text().splitlines(True),fromfile='compact-v2/ap_core.qsf',tofile='area-v1/ap_core.qsf')))
    print(OUT)
if __name__=='__main__':
    if sys.argv[1:]==['prepare']:prepare()
    elif sys.argv[1:]==['fit']:fit.OUT=OUT;fit.fitrun()
    else:raise SystemExit('prepare|fit')
