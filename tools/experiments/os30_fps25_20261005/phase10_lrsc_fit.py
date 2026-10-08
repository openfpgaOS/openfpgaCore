#!/usr/bin/env python3
"""One seed2 whole-core fit, narrow-cache A plus private LR/SC-only CPU."""
import copy,json,shutil,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3];sys.path.insert(0,str(ROOT/'tools'))
import sweep_target_matrix as fit
BASE=ROOT/'docs/measurements/os30-fps25-20261005/phase10/vcache-narrow-v1/fit-seed2'
CAND=ROOT/'docs/measurements/os30-fps25-20261005/phase10/lrsc-only-v1'
OUT=CAND/'fit-narrow-seed2';CPU='src/fpga/vendor/vexriscv/VexiiRiscv/VexiiRiscv_os30.v'

def inventory(**kwargs):
 rows=copy.deepcopy(json.loads((BASE/'inventory.json').read_text()));assert len(rows)==1
 assert rows[0]['seed']==2 and rows[0]['variant']=='os30';rows[0]['cpu_sha256']=fit.digest(CAND/'candidate/VexiiRiscv.v');return rows

def freeze(out,rows):
 expected=json.loads((BASE/'frozen-sources.json').read_text())
 for p,h in expected.items():assert fit.digest(BASE/'frozen'/p)==h,p
 tests=json.loads((CAND/'cpu-tests/result.json').read_text());assert tests['passed']
 candidate=CAND/'candidate/VexiiRiscv.v';assert fit.digest(candidate)==json.loads((CAND/'candidate/generated.json').read_text())['rtl_sha256']
 assert json.loads((CAND/'control/generated.json').read_text())['retained_A_exact']
 dest=out/'frozen';shutil.copytree(BASE/'frozen',dest);shutil.copy2(candidate,dest/CPU)
 actual={str(p.relative_to(dest)):fit.digest(p) for p in dest.rglob('*') if p.is_file()};assert expected.keys()==actual.keys();assert [p for p in actual if expected[p]!=actual[p]]==[CPU]
 fit.save(out/'inventory.json',rows);fit.save(out/'frozen-sources.json',actual)
 fit.save(out/'experiment.json',dict(scope=__doc__,private_only=True,production_modified=False,seed=2,seed_sweep=False,only_changed_from_narrow_base=CPU,before_sha256=expected[CPU],after_sha256=actual[CPU],narrow_base_manifest_sha256=fit.digest(BASE/'frozen-sources.json'),cpu_test_sha256=fit.digest(CAND/'cpu-tests/result.json'),isa_contract='LR/SC retained, all arithmetic AMOs illegal, misa.A clear; only audited current binary compatibility, not general full-A binary compatibility.'))
 return dest

if __name__=='__main__':
 assert sys.argv[1:]==['run'];fit.inventory=inventory;fit.freeze=freeze
 sys.argv=[sys.argv[0],'--target','pocket','--variant','os30','--seed-offsets','0','--jobs','1','--processors','4','--output',str(OUT)];fit.main()
