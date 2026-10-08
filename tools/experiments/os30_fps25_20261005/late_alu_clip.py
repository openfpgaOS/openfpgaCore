#!/usr/bin/env python3
"""Unchanged production-derived SM64 clipping fixture on private late-ALU RTL."""
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import statistics
import subprocess

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT/'docs/measurements/os30-fps25-20261005/late-alu-v1/validation/actual-clip'
OLD = ROOT/'docs/measurements/os30-simple-20261005/validation/cpu-kernel'
OBJ = OUT.parent/'candidate/obj'
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
OUT.mkdir(exist_ok=False)
baseline = json.loads((OLD/'manifest.json').read_text())
for name, expected in baseline['firmware_sha256'].items():
    assert sha(OLD/'firmware'/name) == expected
shutil.copytree(OLD/'firmware', OUT/'firmware')
shutil.copy2(OLD/'results.json', OUT/'baseline-results.json')
text = (OUT.parent/'candidate/main.cpp').read_text()
assert text.count('c - last_m1_activity > 1500000') == 1
text = text.replace('c - last_m1_activity > 1500000', 'c - last_m1_activity > 60000000')
(OUT/'main.cpp').write_text(text)
objects = [OBJ/n for n in ['Vtb_system__ALL.a', 'verilated.o', 'verilated_dpi.o', 'verilated_threads.o']]
command = ['g++', '-std=c++17', '-O2', '-DSYSTEM_NO_REGFILE_PEEK', '-I'+str(OBJ),
    '-I/usr/share/verilator/include', '-I/usr/share/verilator/include/vltstd',
    str(OUT/'main.cpp'), *map(str, objects), '-pthread', '-lpthread', '-latomic', '-o', str(OUT/'Vtb_system')]
with (OUT/'build.log').open('w') as f: subprocess.run(command, check=True, stdout=f, stderr=subprocess.STDOUT)
manifest = dict(scope=__doc__, command=command, simulator_sha256=sha(OUT/'Vtb_system'),
    fixture_sha256=baseline['firmware_sha256'], unchanged_candidate_objects={str(p):sha(p) for p in objects},
    only_host_change='Same no-SDRAM watchdog extension as original fixture. No RTL observer or mutation.',
    baseline_manifest_sha256=sha(OLD/'manifest.json'))
(OUT/'manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')


def run(mode):
    env = dict(os.environ, SCAN_ENABLE='1' if mode == 'scanout' else '0', SCAN_PERIOD='700',
        SCAN_LEN='128', SCAN_PHASE='317', STRIKE_SCAN_PERIOD='1000000000')
    log = OUT/(mode+'.log')
    with log.open('w') as f:
        subprocess.run([str(OUT/'Vtb_system'), 'kernel.bin', '60000000'], cwd=OUT/'firmware',
                       env=env, stdout=f, stderr=subprocess.STDOUT, check=True, timeout=600)
    text = log.read_text()
    assert 'KERNEL PASS HAL init' in text and 'KERNEL FAIL' not in text
    assert 'WCONF: mism=0 spurious=0 exp_fifo=0 aw_fifo=0 run_left=0' in text
    uart = text.split('=== UART BEGIN ===')[1].split('=== UART END ===')[0]
    rows = []
    for cold, order, variant, timer, digest in re.findall(r'RUN (\w{8}) (\w{8}) (\w{8}) @# TIMER (\w{8}) (\w{8})', uart):
        rows.append(dict(cold_prepared=bool(int(cold,16)), order=int(order,16), variant=int(variant,16), timer_cycles=int(timer,16), hash=digest))
    assert len(rows) == 16, len(rows)
    old = json.loads((OUT/'baseline-results.json').read_text())['runs'][mode]
    for a,b in zip(old,rows):
        for field in ['cold_prepared','order','variant','hash']: assert a[field] == b[field], (field,a,b)
    summary = []
    for cold in [False,True]:
        for variant in range(4):
            before = statistics.mean(r['timer_cycles'] for r in old if r['cold_prepared']==cold and r['variant']==variant)
            after = statistics.mean(r['timer_cycles'] for r in rows if r['cold_prepared']==cold and r['variant']==variant)
            summary.append(dict(cold=cold, variant=variant, baseline_cycles=before, candidate_cycles=after, speedup=before/after-1))
    print(mode, json.dumps(summary), flush=True)
    return mode, dict(runs=rows, summary=summary, passed=True, log_sha256=sha(log))


with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
    results = dict(pool.map(run, ['quiet', 'scanout']))
(OUT/'results.json').write_text(json.dumps(dict(scope=__doc__, results=results,
    limitation='Actual extracted clipper/mask body and 299 captured submitted triangles; includes fixture memcpy/hash overhead. Not full renderer or FPS.',
    current_software_variant=1), indent=2)+'\n')
