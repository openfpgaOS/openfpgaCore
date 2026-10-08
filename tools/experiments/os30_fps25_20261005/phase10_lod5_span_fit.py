#!/usr/bin/env python3
"""One evidence-backed LOD5 AREA retry changing only span-v3 GPU to v6."""
import json,shutil,sys
from pathlib import Path
import phase10_lod5_fit as fit
ROOT=fit.ROOT;P=fit.P;PARENT=fit.OUT;OUT=P/'lod5-cram-span-core-v2'
GPU='src/fpga/common/gpu_core.v';CAND=P/'span-lookahead-v6/gpu_core.v'
REVIEW=P/'span-lookahead-review-v4/manifest.json'
def save(p,d):
 with p.open('x') as f:json.dump(d,f,indent=2);f.write('\n')
def prepare():
 parent=json.loads((PARENT/'prepared.json').read_text())
 assert not json.loads((PARENT/'result.json').read_text())['whole_core_fit_successful']
 for n,h in parent['source_sha256'].items():assert fit.sha(PARENT/'frozen'/n)==h,n
 proof=json.loads(REVIEW.read_text());assert proof['passed']
 assert proof['candidate_sha256']==fit.sha(CAND)
 for n,h in proof['evidence_sha256'].items():assert fit.sha(ROOT/n)==h,n
 OUT.mkdir(exist_ok=False);shutil.copytree(PARENT/'frozen',OUT/'frozen')
 shutil.copyfile(CAND,OUT/'frozen'/GPU);shutil.copyfile(__file__,OUT/'recipe.py')
 actual={str(p.relative_to(OUT/'frozen')):fit.sha(p) for p in sorted((OUT/'frozen').rglob('*')) if p.is_file()}
 assert [n for n in actual if actual[n]!=parent['source_sha256'][n]]==[GPU]
 prep=dict(parent,scope=__doc__,source_sha256=actual,only_changed_file=GPU,
  parent_prepared_sha256=fit.sha(PARENT/'prepared.json'),
  parent_final_manifest_sha256=fit.sha(PARENT/'final-manifest.json'),
  recipe_sha256=fit.sha(Path(__file__)),fit_recipe_sha256=fit.sha(Path(fit.__file__)),
  changed={GPU:dict(before=parent['source_sha256'][GPU],after=actual[GPU])})
 save(OUT/'prepared.json',prep)
 paths=[PARENT/'final-manifest.json',PARENT/'independent-review.json',REVIEW,
  P/'span-lookahead-v6/prepared.json',P/'span-lookahead-v6/map-core/result.json',
  P/'lod5-components-v1/final-manifest.json',P/'cram1-share-full-v1/manifest.json',
  Path(__file__),Path(fit.__file__)]
 save(OUT/'independent-review.json',dict(passed_for_resource_fit=True,deployment_ready=False,
  checks=['Only GPU source changes; original CPU128/clocks/AREA settings and seed2 preserved.',
   'v6 source call guards and NBA task priority reviewed; no change in cycle schedule.',
   '33 constant fields plus FIFO/tail ownership assertions pass.',
   'Twelve native and four extended synthetic comparisons exact pixels AND cycles against v3.',
   'Map-only evidence reduces GPU ALUT by239; full placement remains unproven.'],
  evidence_sha256={str(p.relative_to(ROOT)):fit.sha(p) for p in paths}))
 print(OUT)
if __name__=='__main__':
 if sys.argv[1:]==['prepare']:prepare()
 elif sys.argv[1:]==['fit']:fit.OUT=OUT;fit.fitrun()
 else:raise SystemExit('prepare|fit')
