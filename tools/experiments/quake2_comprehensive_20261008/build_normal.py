#!/usr/bin/env python3
"""Build a private normal Quake2 ELF without invoking release/image scripts."""
from pathlib import Path
import hashlib
import json
import os
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / 'docs/measurements/quake2-comprehensive-20261008'
name = sys.argv[1]
assert name.replace('-', '').replace('_', '').isalnum()
work = OUT / 'work' / name
result = OUT / ('build-' + name)
result.mkdir(exist_ok=False)
elf = work / '.obj/quake2/app.elf'
cmd = ['make', '-C', str(work / 'src/quake2'), '-j4', 'GPU_PROFILE=0', str(elf)]
(result / 'command.json').write_text(json.dumps(cmd, indent=2) + '\n')
env = dict(os.environ)
env.pop('CPATH', None)
start = time.monotonic()
with (result / 'build.log').open('w') as log:
    process = subprocess.run(cmd, env=env, stdout=log, stderr=subprocess.STDOUT)
report = dict(exit_code=process.returncode, seconds=time.monotonic()-start)
if process.returncode == 0:
    data = elf.read_bytes()
    (result / 'app.elf').write_bytes(data)
    report['elf_sha256'] = hashlib.sha256(data).hexdigest()
(result / 'result.json').write_text(json.dumps(report, indent=2) + '\n')
print(json.dumps(report))
sys.exit(process.returncode)
