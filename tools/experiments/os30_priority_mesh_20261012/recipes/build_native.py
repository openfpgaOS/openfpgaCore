#!/usr/bin/env python3
"""Freeze and compile one full native CPU/GPU asynchronous mesh revision."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import time

I=Path(__file__).resolve().parent
BASE=I.parent/'cached-cpu-v1/native-sdram-v2'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('label')
a=p.parse_args()
out=I/a.label
assert out.parent==I and not out.exists(),out
shutil.copytree(I/'work-core-v2/frozen',out/'frozen')
baseline=json.loads((BASE/'prepared.json').read_text())
command=[v.replace(str(BASE),str(out)) for v in baseline['command']]
command.insert(command.index('--cc'),'-DINCLUDE_GPU_ASYNC_MESH')
extra=json.loads((I/'work-core-v2/new-sources.json').read_text())
for name in extra:
    source='../common/'+name
    assert (out/'frozen/src/fpga/common'/name).is_file()
    if source not in command:command.append(source)
files={str(p.relative_to(out)):sha(p) for p in sorted((out/'frozen').rglob('*')) if p.is_file()}
changed=[name for name,h in files.items()
         if not (BASE/name).exists() or sha(BASE/name)!=h]
prepared=dict(command=command,source_sha256=files,changed_inputs=changed,
    baseline_model=str(BASE),baseline_prepared_sha256=sha(BASE/'prepared.json'),
    recipe_sha256=sha(Path(__file__)),experimental=True,
    limitations=['Full native CPU/GPU simulation; physical timing not represented',
                 'Bounded guest services and synthetic display/audio traffic'])
(out/'prepared.json').write_text(json.dumps(prepared,indent=2)+'\n')
start=time.monotonic()
with(out/'build.log').open('x')as log:
    rc=subprocess.run(command,cwd=out/'frozen/src/fpga/test',stdout=log,stderr=subprocess.STDOUT).returncode
for name,h in files.items():assert sha(out/name)==h,name
(out/'build.json').write_text(json.dumps(dict(returncode=rc,seconds=time.monotonic()-start,
    prepared_sha256=sha(out/'prepared.json'),command=command),indent=2)+'\n')
print(a.label,'build',rc,flush=True)
raise SystemExit(rc)
