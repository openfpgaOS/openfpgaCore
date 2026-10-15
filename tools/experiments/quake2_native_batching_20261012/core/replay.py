#!/usr/bin/env python3
"""Rebuild and replay the four pinned captures; no copyrighted game data included."""
import argparse
import concurrent.futures
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
sys.dont_write_bytecode = True
import package

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('vectors', type=Path)
p.add_argument('output', type=Path)
a = p.parse_args()
m = package.verify()
vectors, output = a.vectors.resolve(), a.output.resolve()
for name, expected in m['replay_expected'].items():
    if package.sha(vectors/(name+'.vec')) != expected['vector_sha256']:
        raise ValueError('Capture differs: '+name)
output.mkdir(parents=True, exist_ok=False)
shutil.copytree(package.HERE/'replay/rtl', output/'rtl')
cmd = json.loads((package.HERE/'replay/build-command.json').read_text())
old = str(Path(cmd[cmd.index('--Mdir')+1]).parent)
cmd = [s.replace(old, str(output)) for s in cmd]
(output/'build-command.json').write_text(json.dumps(cmd, indent=2)+'\n')
with (output/'build.log').open('w') as log:
    subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT, check=True)

def replay(item):
    name, expected = item
    cmd = [str(output/'obj/Vtb_gpu'), '+fast_tex=1', '+tagged_tex=1', '+memory_traffic=0',
           str(vectors/(name+'.vec')), str(output/(name+'.bin'))]
    with (output/(name+'.log')).open('w') as log:
        subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=600)
    text = (output/(name+'.log')).read_text()
    cycles = int(re.search(r'^RESULT cycles=(\d+)', text, re.M)[1])
    exact = package.sha(output/(name+'.bin')) == expected['output_sha256']
    result = dict(frame=name, exact=exact, cycles=cycles,
                  cycles_exact=cycles == expected['cycles'], command=cmd)
    (output/(name+'.json')).write_text(json.dumps(result, indent=2)+'\n')
    assert exact and result['cycles_exact'], result
    print('PASS', name, cycles, flush=True)
    return result

with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
    results = list(pool.map(replay, m['replay_expected'].items()))
(output/'result.json').write_text(json.dumps(dict(passed=True, frames=results), indent=2)+'\n')
