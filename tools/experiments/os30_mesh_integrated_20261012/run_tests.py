#!/usr/bin/env python3
"""Run the bundled focused queue and extracted-app tests into a NEW directory."""
from pathlib import Path
import argparse
import hashlib
import json
import os
import subprocess

P = Path(__file__).resolve().parent
p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--output', type=Path, required=True)
a = p.parse_args()
out = a.output.resolve()
assert not out.exists() and not out.is_relative_to(P)
out.mkdir(parents=True)
subprocess.run(['python3', str(P / 'verify.py')], check=True)
environment = dict(os.environ, ASAN_OPTIONS='detect_leaks=0', UBSAN_OPTIONS='halt_on_error=1')
commands = [
 ['gcc', '-std=c11', '-O2', '-Wall', '-Wextra', '-Werror', '-fsanitize=address,undefined',
  '-fno-omit-frame-pointer', str(P / 'tests/actual_wait_test.c'), '-o', str(out / 'app-wait')],
 ['verilator', '--cc', '--exe', '--build', '-j', '4', '-Wno-fatal', '--top-module', 'geometry_mesh_queue',
  '--Mdir', str(out / 'queue-obj'), '-CFLAGS', '-std=c++17 -O1 -fsanitize=address,undefined -fno-omit-frame-pointer',
  '-LDFLAGS', '-fsanitize=address,undefined', str(P / 'rtl/geometry_mesh_queue.v'), str(P / 'tests/test_queue.cpp')]
]
results = []
for index, command in enumerate(commands):
    with (out / f'build-{index}.log').open('w') as log:
        subprocess.run(command, check=True, stdout=log, stderr=subprocess.STDOUT)
for program in [out / 'app-wait', out / 'queue-obj/Vgeometry_mesh_queue']:
    run = subprocess.run([str(program)], env=environment, check=True, capture_output=True, text=True)
    results.append(json.loads(run.stdout))
result = {'status': 'PASS', 'commands': commands, 'results': results,
          'manifest_sha256': hashlib.sha256((P / 'manifest.json').read_bytes()).hexdigest(),
          'limits': 'Focused ownership/publication tests only. No game assets, full frame, physical synthesis or timing tested.'}
(out / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
print(json.dumps(result['results']))
