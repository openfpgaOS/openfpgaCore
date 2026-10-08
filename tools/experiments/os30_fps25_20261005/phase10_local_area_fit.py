#!/usr/bin/env python3
"""One private seed-2 fit: module-local area effort, unchanged compact GDR RTL."""
import copy, difflib, hashlib, json, shutil, sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[3]
P=ROOT/'docs/measurements/os30-fps25-20261005/phase10'
BASE=P/'gpu-direct-core-compact-v2'
OUT=P/'gpu-direct-local-area-v1'
QSF='src/fpga/targets/pocket/ap_core.qsf'
INSTANCE='core_top:ic|geometry_direct_mmio:geometry_direct'
ASSIGNMENTS=[
 ('OPTIMIZATION_TECHNIQUE','AREA'),
 ('QII_AUTO_PACKED_REGISTERS','MINIMIZE AREA'),
 ('PHYSICAL_SYNTHESIS_COMBO_LOGIC_FOR_AREA','ON'),
]
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,x):
 with p.open('x') as f: json.dump(x,f,indent=2);f.write('\n')
def prepare():
 expected=json.loads((BASE/'prepared.json').read_text())['source_sha256']
 for p,h in expected.items(): assert sha(BASE/'frozen'/p)==h,p
 shutil.copytree(BASE/'frozen',OUT/'frozen')
 qsf=OUT/'frozen'/QSF
 original=qsf.read_text()
 with qsf.open('a') as f:
  f.write('\n# Private one-seed resource probe; only geometry hierarchy area effort.\n')
  for n,v in ASSIGNMENTS:
   f.write(f'set_instance_assignment -name {n} "{v}" -to "{INSTANCE}"\n')
 actual={str(p.relative_to(OUT/'frozen')):sha(p) for p in sorted((OUT/'frozen').rglob('*')) if p.is_file()}
 changed={p:dict(before=expected.get(p),after=h) for p,h in actual.items() if expected.get(p)!=h}
 assert set(actual)==set(expected) and set(changed)=={QSF}
 (OUT/'candidate.patch').write_text(''.join(difflib.unified_diff(original.splitlines(True),qsf.read_text().splitlines(True),fromfile='compact-v2/'+QSF,tofile='local-area-v1/'+QSF)))
 save(OUT/'prepared.json',dict(scope=__doc__,private_only=True,rtl_byte_identical=True,source_sha256=actual,changed=changed,assignments=ASSIGNMENTS,instance=INSTANCE,seed=2,seed_sweep=False,production_modified=False,base_prepared_sha256=sha(BASE/'prepared.json'),recipe_sha256=sha(Path(__file__)),option_help_sha256=sha(OUT/'option-values.log')))
 print(OUT)
def runfit():
 sys.path.insert(0,str(ROOT/'tools'));import sweep_target_matrix as fit
 def inventory(**kwargs): return copy.deepcopy(json.loads((BASE/'fit-seed2/inventory.json').read_text()))
 def freeze(out,rows):
  expected=json.loads((OUT/'prepared.json').read_text())['source_sha256']
  for p,h in expected.items(): assert sha(OUT/'frozen'/p)==h,p
  dest=out/'frozen';shutil.copytree(OUT/'frozen',dest)
  fit.save(out/'inventory.json',rows);fit.save(out/'frozen-sources.json',expected)
  fit.save(out/'experiment.json',dict(scope=__doc__,seed=2,seed_sweep=False,resource_only=True,functionally_qualified=False,prepared_sha256=sha(OUT/'prepared.json')))
  return dest
 fit.inventory=inventory;fit.freeze=freeze
 sys.argv=[sys.argv[0],'--target','pocket','--variant','os30','--seed-offsets','0','--jobs','1','--processors','4','--output',str(OUT/'fit-seed2')]
 fit.main()
if __name__=='__main__':
 if sys.argv[1:]==['prepare']: prepare()
 elif sys.argv[1:]==['fit']: runfit()
 else: raise SystemExit('prepare|fit')
