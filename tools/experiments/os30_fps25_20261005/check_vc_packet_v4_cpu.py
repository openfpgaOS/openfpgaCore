#!/usr/bin/env python3
"""Paired exact packet-loop component on the frozen single-issue A CPU RTL."""
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
OUT = CAMPAIGN / 'vc-packet-v4/cpu-quantize-kernel'
OLD = ROOT / 'docs/measurements/os30-simple-20261005/validation/cpu-kernel'

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def read(path):
    return json.loads(path.read_text())

def generate():
    OUT.mkdir(exist_ok=False)
    fw = OUT / 'firmware'
    fw.mkdir()
    for name in ['start.S', 'link.ld', 'firmware.mif']:
        shutil.copy2(OLD / 'firmware' / name, fw / name)
    parent = CAMPAIGN / 'vc-packet-v3/gfx_gpu.c'
    candidate = CAMPAIGN / 'vc-packet-v4/gfx_gpu.c'
    assert sha(parent) == 'ab87a1a1351dc266240fa7b8d023f4816bbcfb7453fa9b44bed02c819872bd91'
    assert sha(candidate) == '3104663e853092024c32c2ca52ea0e7ab7034875983333066d59f185a5013548'
    header = CAMPAIGN / 'baseline/gfx_gpu.h'
    struct = re.search(r'struct gfx_vc_vtx \{.*?\n\};', header.read_text(), re.S)[0]
    prefix = '''#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>
#include <math.h>
volatile unsigned traps,irq_count;
#define VC_W_PRESCALE (1.0f / 256.0f)
#define GPU_CMD_LOAD_VERT_CLIP 0x56u
#define GPU_CMD_DRAW_INDEXED_TRI 0x54u
static int g_vc_rgb_input;
static uint32_t g_vc_words,g_vc_loads;
static uint16_t rgba_to_565(uint8_t r,uint8_t g,uint8_t b) {
 return ((uint16_t)(r>>3)<<11)|((uint16_t)(g>>2)<<5)|(b>>3);
}
'''
    functions = ''
    for label, path in [('reference', parent), ('candidate', candidate)]:
        source = path.read_text().split('void gfx_gpu_vtx_cache_tri(', 1)[1]
        start = source.index('    for (int k = 0; k < 3; k++) {')
        end = source.index('    _gpu_ring_commit(packet_words);', start)
        body = source[start:end] + '#endif\n'
        functions += ('static __attribute__((noinline,noclone)) unsigned ' + label +
                      '(const struct gfx_vc_vtx v[3],const uint8_t slot[3],unsigned dirty_mask,uint32_t *out) {\n'
                      ' uint32_t *packet=out;\n' + body + ' return (unsigned)(packet-out);\n}\n')
    tail = Path(__file__).with_name('vc_packet_v4_cpu_tail.c').read_text()
    (fw / 'kernel.c').write_text(prefix + struct + '\n' + functions + tail)
    old = read(OLD / 'manifest.json')
    exe = OLD / 'Vtb_system'
    assert sha(exe) == old['simulator_sha256']
    for name, digest in old['unchanged_rtl_object_sha256'].items():
        assert sha(ROOT / name) == digest
    manifest = {
        'cpu_sha256': old['cpu_sha256'],
        'simulator_path': str(exe), 'simulator_sha256': sha(exe),
        'parent_cpu_manifest_sha256': sha(OLD / 'manifest.json'),
        'unchanged_rtl_object_sha256': old['unchanged_rtl_object_sha256'],
        'source_sha256': {str(p.relative_to(ROOT)): sha(p) for p in [parent, candidate, header, fw / 'kernel.c', Path(__file__), Path(__file__).with_name('vc_packet_v4_cpu_tail.c')]},
        'scope': 'Exact extracted dirty-vertex quantization and packet-writing loops on synthetic finite payloads, frozen A CPU/fabric/SDRAM RTL. No material, viewport, ring reservation, MMIO, or surrounding production register pressure in timed function. Component measurement, not whole-game FPS.'
    }
    cmd = [str(fw) if t == str(OLD / 'firmware') else t for t in old['compiler_command']]
    with (OUT / 'firmware-build.log').open('w') as log:
        subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT, check=True)
    container = cmd[:cmd.index('riscv64-unknown-elf-gcc')]
    subprocess.run([*container, 'riscv64-unknown-elf-objcopy', '-O', 'binary', 'kernel.elf', 'kernel.bin'], check=True)
    with (OUT / 'kernel.asm').open('w') as log:
        subprocess.run([*container, 'riscv64-unknown-elf-objdump', '-d', 'kernel.elf'], stdout=log, check=True)
    manifest.update(compiler_command=cmd, assembly_sha256=sha(OUT / 'kernel.asm'),
                    firmware_sha256={p.name: sha(p) for p in fw.iterdir() if p.is_file()})
    (OUT / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')

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
              'correctness': 'On actual RV32 CPU: 512 synthetic triangles times all five legal rounding modes; equal returned word counts, all packet words, untouched output sentinel words, per-loop word/load counters and cumulative FP exception flags. Timed hashes also match. Normal application mode RNE restored before timing.',
              'measurement': 'Forward/reverse variant order; warm runs prime selected function, cold preparation scans 256KiB and fence.i. Shared loop/hash/call/marker overhead remains. Each timed batch calls the extracted function 1024 times.',
              'variants': {'0': 'vc-packet-v3', '1': 'vc-packet-v4 grouped conversions'},
              'limits': ['Synthetic inputs are not measured scene frequencies.', 'Extraction does not reproduce full writer register allocation, ring/cache pressure or downstream GPU overlap.', 'CPU core and compiled fabric objects are reused unchanged; no FPGA synthesis or timing claim.']}
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
