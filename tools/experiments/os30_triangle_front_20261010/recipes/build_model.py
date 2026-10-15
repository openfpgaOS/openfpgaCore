#!/usr/bin/env python3
"""Freeze one experimental model revision and compile its native RTL library."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import time

I=Path(__file__).resolve().parent
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
p=argparse.ArgumentParser();p.add_argument('label');a=p.parse_args()
out=I/a.label
assert not out.exists()
base=json.loads((I/'baseline.json').read_text())
source=I/'model/frozen'
shutil.copytree(source,out/'frozen')
command=[v.replace(base['baseline_model'],str(out)) for v in base['baseline_command']]
command.insert(command.index('--cc'),'-DINCLUDE_GPU_BRAM_IMPORT')
command.insert(command.index('--cc'),'-DINCLUDE_TRIANGLE_PREFLIGHT')
for name in ['geometry_import_view.v','geometry_import_convert.v','geometry_bram_import.v','geometry_float_cull_mem.v','geometry_triangle_preflight.v']:
    assert (out/'frozen/src/fpga/common'/name).is_file()
    command.append('../common/'+name)
files={str(p.relative_to(out)):sha(p) for p in (out/'frozen').rglob('*') if p.is_file()}
changed=[name for name,h in files.items() if base['source_sha256'].get(name)!=h]
d=dict(command=command,source_sha256=files,baseline_model=base['baseline_model'],
       baseline_prepared_sha256=base['baseline_prepared_sha256'],changed_inputs=changed,
       recipe_sha256=sha(Path(__file__)),experimental=True,
       limitations=['Native RTL simulation only; FPGA fit/timing and hardware behavior unqualified.',
                    'Synthetic display/audio traffic and bounded guest services.'])
(out/'prepared.json').write_text(json.dumps(d,indent=2)+'\n')
start=time.monotonic()
with(out/'build.log').open('x')as f:
    rc=subprocess.run(command,cwd=out/'frozen/src/fpga/test',stdout=f,stderr=subprocess.STDOUT).returncode
for name,h in files.items():assert sha(out/name)==h
(out/'build.json').write_text(json.dumps(dict(returncode=rc,seconds=time.monotonic()-start,
    prepared_sha256=sha(out/'prepared.json'),command=command),indent=2)+'\n')
print(a.label,'build',rc,flush=True)
raise SystemExit(rc)
