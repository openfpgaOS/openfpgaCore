#!/usr/bin/env python3
"""Qualify reviewed recycling app against unchanged cache-v2 actual RTL model."""
import argparse,json,os,shutil,sys
from pathlib import Path
from phase10_experiment import CAMP,ROOT,module,sha,write

P=CAMP/'phase10';OLD=P/'cache-stage-lod5-coupled-v2';OUT=P/'lod5-recycle-coupled-v1'
APP=P/'lod5-recycle-driver-v1/app.elf';FUNCTIONAL=P/'lod5-recycle-functional-v1'
PROOFS={OLD/'manifest.json':'8b45fb39c6f441512e9f7559a44861524e4913f53d47eee6ebceaab5b79c18a1',
 FUNCTIONAL/'manifest.json':'a8a4e5481088682a085be647fc113156b0a6767c81624e24d43bcd04dc6d87cc',
 P/'lod5-recycle-review-v1/manifest.json':'4349ccc4c27846d65515c2762b5f7d2d3c614e84a7b73ae4a45d33f27e39b570'}

def prepare():
 for p,h in PROOFS.items():assert sha(p)==h,(p,sha(p))
 for d in (OLD,FUNCTIONAL):
  manifest=json.loads((d/'manifest.json').read_text())
  for n,h in manifest['files'].items():assert sha(d/n)==h,(d,n)
 assert sha(APP)=='bb41f2088130935dc637d55a4a1dbd0afddb68f94794440ddb5a67e504d4a2c5'
 assert sha(APP)==sha(FUNCTIONAL/'app.elf')
 for n in ('gfx_pc.c','gfx_gpu.c','gfx_gpu.h','of_gpu.h','of_sys.h','gfx_rendering_api.h','gfx_window_manager_api.h','gfx_pc.h'):
  # The reviewed ELF remains authoritative. Flat source lists vary by harness.
  if (P/'lod5-recycle-driver-v1'/n).exists() and (FUNCTIONAL/n).exists():
   assert sha(P/'lod5-recycle-driver-v1'/n)==sha(FUNCTIONAL/n),n
 OUT.mkdir(exist_ok=False)
 shutil.copytree(OLD/'model/frozen',OUT/'model/frozen')
 shutil.copytree(OLD/'qsim',OUT/'qsim',ignore=shutil.ignore_patterns('build','qsim'))
 (OUT/'model/obj').mkdir()
 shutil.copy2(OLD/'model/obj/Vtb_gpu',OUT/'model/obj/Vtb_gpu')
 build=json.loads((OLD/'model/build.json').read_text());assert sha(OUT/'model/obj/Vtb_gpu')==build['simulator_sha256']
 build['reused_unchanged_from']=str(OLD/'model/build.json');build['parent_build_sha256']=sha(OLD/'model/build.json')
 write(OUT/'model/build.json',build)
 write(OUT/'prepared.json',dict(scope=__doc__,elf_sha256=sha(APP),baseline_elf_sha256=sha(P/'load-cram-driver-v1/app.elf'),
  simulator_reused_unchanged=True,simulator_sha256=build['simulator_sha256'],
  proofs={str(p.relative_to(ROOT)):h for p,h in PROOFS.items()},helper_sha256=sha(Path(__file__)),
  source_sha256={str(p.relative_to(OUT)):sha(p)for p in OUT.rglob('*')if p.is_file()}))

def run(window):
 m=module('lod5_recycle_coupled_runner',ROOT/'tools/experiments/os30_overlap_20261006/run.py');m.OUT=OUT
 os.environ['IDEAL_GPU_MEMORY']='0';os.environ['LOD5_MODEL_MODE']='normal'
 sys.argv=[str(Path(__file__)),window,'--elf',str(APP),'--start','360'if window=='warm'else'234','--frames','8'];m.main()
 d=OUT/f'run-{window}';assert 'FATAL'not in(d/'run.log').read_text()
 p=d/'inputs.json';v=json.loads(p.read_text());v['environment'].update(IDEAL_GPU_MEMORY='0',LOD5_MODEL_MODE='normal');p.write_text(json.dumps(v,indent=2)+'\n')

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('action',choices=['prepare','run']);p.add_argument('--window',choices=['warm','cold'],default='warm');a=p.parse_args();run(a.window)if a.action=='run'else prepare()
