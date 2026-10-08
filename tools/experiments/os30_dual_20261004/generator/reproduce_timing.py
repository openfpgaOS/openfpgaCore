#!/usr/bin/env python3
"""Replay frozen, exact-hash private timing transformations into a fresh folder."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--snapshot',type=Path,required=True)
parser.add_argument('--profile',choices=['full-alu','immediate','address'],default='full-alu')
parser.add_argument('--base',type=Path,required=True,help='Qualified masked-skid RTL, original or reproduced')
parser.add_argument('--output',type=Path,required=True,help='Must not exist')
args=parser.parse_args()
snapshot=args.snapshot.resolve()
recipe=json.loads((snapshot/'recipe.json').read_text())
digest=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
for name,sha in recipe['file_sha256'].items():
    assert digest(snapshot/name)==sha,name
profile=recipe['profiles'][args.profile]
assert digest(args.base)==profile['base_sha256'],'Base RTL hash differs'
out=args.output.resolve()
out.mkdir(parents=True,exist_ok=False)
current=out/'base.v'
shutil.copyfile(args.base,current)
completed=[]
for stage in profile['stages']:
    assert digest(current)==stage['reference_sha256']
    target=out/(stage['name']+'.v')
    with (out/(stage['name']+'.log')).open('w') as log:
        subprocess.run(['patch','--batch','--fuzz=0','--output',str(target),str(current),str(snapshot/stage['patch'])],check=True,stdout=log,stderr=subprocess.STDOUT)
    assert digest(target)==stage['candidate_sha256'],stage['name']
    completed.append(dict(stage=stage['name'],sha256=digest(target)))
    current=target
result=dict(profile=args.profile,recipe_sha256=digest(snapshot/'recipe.json'),
            base_sha256=profile['base_sha256'],stages=completed,
            final_sha256=digest(current),normalization=False)
(out/'reproduction.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result,indent=2))
