#!/usr/bin/env python3
"""Extracted clip-mask component on captured inputs and exact A CPU, SiFive5 tuning."""
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
CAMPAIGN = ROOT / 'docs/measurements/os30-fps25-20261005'
OUT = CAMPAIGN / 'phase2/branchless-clip-v1/cpu-mask-kernel'
OLD = ROOT / 'docs/measurements/os30-simple-20261005/validation/cpu-kernel'

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def read(path):
    return json.loads(path.read_text())

def generate():
    OUT.mkdir(exist_ok=False)
    fw = OUT / 'firmware'
    fw.mkdir()
    for name in ['start.S', 'link.ld', 'firmware.mif', 'captured.h']:
        shutil.copy2(OLD / 'firmware' / name, fw / name)
    parent = CAMPAIGN / 'phase2/baseline/gfx_pc.c'
    candidate = CAMPAIGN / 'phase2/branchless-clip-v1/gfx_pc.c'
    assert sha(parent) == 'a5c16f135e4b89006a08891794562329f7b0a56daf0b22bb1cd35d791f7f90b5'
    assert sha(candidate) == '48327e183557912a4c0a7c9e96795c911c67d973f158624e503524d34a83778d'
    source = parent.read_text()
    struct = source[source.index('struct LoadedVertex {'):source.index('\n};', source.index('struct LoadedVertex {'))+3]
    prefix = '#include <stdint.h>\n#include <stddef.h>\n#include <stdbool.h>\n#include <string.h>\ntypedef float rspv_t;\nstruct RGBA {uint8_t r,g,b,a;};\nvolatile unsigned traps,irq_count;\n'
    masks = re.findall(r'\b(CLIP_(?:NEAR|FAR|TOP|BOTTOM|RIGHT|LEFT))\s*=\s*(\d+)',source)
    assert len(masks)==6
    constants = '\n'.join('#define '+k+' '+v for k,v in masks)+'\n'
    functions = ''
    for label,path in [('reference',parent),('candidate',candidate)]:
        body=path.read_text().split('        // trivial clip rejection\n',1)[1].split('\n        d->x = x;',1)[0]
        functions+='static __attribute__((noinline,noclone)) void '+label+'_mask(struct LoadedVertex *d) {float x=d->x,y=d->y,z=d->z,w=d->w;\n'+body+'\n}\n'
    tail=Path(__file__).with_name('branchless_clip_cpu_tail.c')
    (fw/'kernel.c').write_text(prefix+struct+'\n'+constants+functions+'\n#include "captured.h"\n'+tail.read_text())
    old=read(OLD/'manifest.json');exe=OLD/'Vtb_system';assert sha(exe)==old['simulator_sha256']
    for name,digest in old['unchanged_rtl_object_sha256'].items():assert sha(ROOT/name)==digest
    cmd=[str(fw) if t==str(OLD/'firmware') else t for t in old['compiler_command']]
    cmd=['-mtune=sifive-5-series' if t=='-mtune=sifive-7-series' else t for t in cmd]
    with (OUT/'firmware-build.log').open('w') as log:subprocess.run(cmd,stdout=log,stderr=subprocess.STDOUT,check=True)
    container=cmd[:cmd.index('riscv64-unknown-elf-gcc')]
    subprocess.run([*container,'riscv64-unknown-elf-objcopy','-O','binary','kernel.elf','kernel.bin'],check=True)
    with (OUT/'kernel.asm').open('w') as log:subprocess.run([*container,'riscv64-unknown-elf-objdump','-d','kernel.elf'],stdout=log,check=True)
    manifest={'cpu_sha256':old['cpu_sha256'],'simulator_path':str(exe),'simulator_sha256':sha(exe),'parent_cpu_manifest_sha256':sha(OLD/'manifest.json'),'unchanged_rtl_object_sha256':old['unchanged_rtl_object_sha256'],'source_sha256':{str(p.relative_to(ROOT)):sha(p) for p in [parent,candidate,fw/'kernel.c',fw/'captured.h',Path(__file__),tail]},'compiler_command':cmd,'assembly_sha256':sha(OUT/'kernel.asm'),'firmware_sha256':{p.name:sha(p) for p in fw.iterdir() if p.is_file()},'scope':'Actual A CPU/fabric/SDRAM RTL; exact extracted six-comparison mask functions, current SiFive5 tuning,299 previously captured submitted triangles repeated8 times. No surrounding transform, lighting, fog, reciprocal or production register allocation. Component only, not whole-game FPS.'}
    (OUT/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')

def run():
    manifest = read(OUT / 'manifest.json')
    exe = Path(manifest['simulator_path'])
    assert sha(exe) == manifest['simulator_sha256']
    fw = OUT / 'firmware'
    for name, digest in manifest['firmware_sha256'].items():
        assert sha(fw / name) == digest
    def one(mode):
        env = dict(os.environ, SCAN_ENABLE='1' if mode == 'scanout' else '0', SCAN_PERIOD='700', SCAN_LEN='128', SCAN_PHASE='317', STRIKE_SCAN_PERIOD='1000000000')
        log = OUT / (mode + '.log')
        assert not log.exists()
        with log.open('w') as f:
            subprocess.run([str(exe), 'kernel.bin', '60000000'], cwd=fw, env=env, stdout=f, stderr=subprocess.STDOUT, check=True, timeout=600)
        text = log.read_text()
        assert 'KERNEL PASS HAL init' in text and 'KERNEL FAIL' not in text
        assert 'WCONF: mism=0 spurious=0 exp_fifo=0 aw_fifo=0 run_left=0' in text
        print('PASS', mode, flush=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(one, ['quiet', 'scanout']))
    result = {'passed': True, 'scope': manifest['scope'], 'runs': {}, 'summary': {},
              'correctness': 'Actual RV32 mask outputs agree for897 captured vertices plus1728 edge cases and1024 random raw-float cases; includes equal/negative bounds, signed zero, infinities, NaNs and subnormals. Timed hashes also match.',
              'measurement': 'Forward/reverse variant order; warm runs prime selected function, cold preparation scans 256KiB and fence.i. Shared loop/hash/call/marker overhead remains. Each timed batch calls the extracted function 7176 times (299 triangles x3 vertices x8 repeats).',
              'variants': {'0': 'original conditional clip mask', '1': 'branchless clip mask'},
              'limits': ['Captured submitted triangles underrepresent discarded/offscreen vertices; timings are not a complete G_VTX distribution.', 'Extraction does not reproduce full vertex-transform register allocation or scheduling overlap.', 'CPU core and compiled fabric objects are reused unchanged; no FPGA synthesis or timing claim.']}
    for mode in ['quiet', 'scanout']:
        text = (OUT / (mode + '.log')).read_text().split('=== UART BEGIN ===')[0]
        pattern = r'RUN ([0-9a-f]{8}) ([0-9a-f]{8}) ([0-9a-f]{8}) @.*?HOST_KERNEL retired=(\d+) cycles=(\d+) freeze=(\d+) hazards=(\d+)\s*.*?TIMER ([0-9a-f]{8}) ([0-9a-f]{8})'
        rows = []
        for v in re.findall(pattern, text, re.S):
            cold, order, variant = (int(x, 16) for x in v[:3])
            retired, cycles, freeze, hazards = map(int, v[3:7])
            timer, digest = (int(x, 16) for x in v[7:])
            rows.append(dict(cold_prepared=bool(cold), order=order, variant=variant, retired_with_common_marker_overhead=retired, cycles_with_common_marker_overhead=cycles, execute_freeze_cycles=freeze, dispatch_hazard_cycles=hazards, timer_cycles=timer, hash=f'{digest:08x}'))
        assert len(rows) == 8 and len({r['hash'] for r in rows}) == 1
        assert all(r['retired_with_common_marker_overhead'] > 25000 for r in rows)
        result['runs'][mode] = rows
        result['summary'][mode] = {}
        for cold in [False, True]:
            groups = {i: [r for r in rows if r['cold_prepared'] == cold and r['variant'] == i] for i in [0, 1]}
            assert all(len(g) == 2 for g in groups.values())
            base = statistics.mean(r['timer_cycles'] for r in groups[0])
            result['summary'][mode]['cold_prepared' if cold else 'warm'] = {
                str(i): dict(timer_cycles_mean=statistics.mean(r['timer_cycles'] for r in rs), cycle_saving_percent=100 * (1 - statistics.mean(r['timer_cycles'] for r in rs) / base), retired_mean=statistics.mean(r['retired_with_common_marker_overhead'] for r in rs), execute_freeze_mean=statistics.mean(r['execute_freeze_cycles'] for r in rs), dispatch_hazard_mean=statistics.mean(r['dispatch_hazard_cycles'] for r in rs)) for i, rs in groups.items()}
    result['evidence_sha256'] = {str(p.relative_to(ROOT)): sha(p) for p in [OUT / 'manifest.json', OUT / 'quiet.log', OUT / 'scanout.log']}
    (OUT / 'results.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result['summary'], indent=2))

if __name__ == '__main__':
    import sys
    {'generate': generate, 'run': run}[sys.argv[1]]()
