#!/usr/bin/env python3
"""Relink a frozen coupled model with only the estimated CPU D-cache changed."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[4]
OUT = ROOT / 'docs/measurements/os30-mesh-20261004'
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--base', default='coupled-v1')
p.add_argument('--label', default='coupled-v1-dc64')
a = p.parse_args()
base = OUT / a.base
dest = OUT / a.label
dest.mkdir(exist_ok=False)
(dest / 'obj').mkdir()
manifest = json.loads((base / 'manifest.json').read_text())
for rel, digest in manifest['sources'].items():
    assert sha(base / rel) == digest, rel
source = (base / 'qsim/cache.c').read_text()
assert source.count('p->dc_sets = 1024;') == 1
source = source.replace('p->dc_sets = 1024;', 'p->dc_sets = 512;')
(dest / 'cache.c').write_text(source)
compile_cmd = ['cc', '-O2', '-g', '-march=native', '-std=gnu11', '-Wall', '-Wextra',
               '-fno-strict-aliasing', '-ffp-contract=off', '-fno-fast-math', '-fno-math-errno',
               '-I' + str(base / 'qsim'), '-c', str(dest / 'cache.c'), '-o', str(dest / 'obj/cache.o')]
link_objects = [base / 'obj' / name for name in
                ['coupled.o', 'verilated.o', 'verilated_dpi.o', 'verilated_threads.o', 'Vtb_gpu__ALL.a']]
qnames = 'cpu fpu cache prof qelf lint gpu app env main'.split()
link_objects += [dest / 'obj/cache.o' if n == 'cache' else base / 'qsim/build/qsim' / (n + '.o')
                 for n in qnames]
link_cmd = ['g++', *map(str, link_objects), '-lm', '-pthread', '-lpthread', '-latomic',
            '-o', str(dest / 'obj/Vtb_gpu')]
with (dest / 'build.log').open('w') as log:
    for cmd in (compile_cmd, link_cmd):
        subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT, check=True)
(dest / 'manifest.json').write_text(json.dumps(dict(
    base=str(base), base_manifest_sha256=sha(base / 'manifest.json'),
    cpu_data_cache_kib=64, fifo_words=manifest['fifo_words'], code_pages=manifest['code_pages'],
    commands=[compile_cmd, link_cmd], sources={'cache.c': sha(dest / 'cache.c')},
    linked_objects={str(f): sha(f) for f in link_objects},
    scope='Exactly the frozen GPU RTL/qsim model; only CPU D-cache sets1024→512 changed.'), indent=2)+'\n')
print(dest / 'obj/Vtb_gpu')
