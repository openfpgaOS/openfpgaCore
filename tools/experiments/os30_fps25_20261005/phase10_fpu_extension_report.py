#!/usr/bin/env python3
"""Freeze selected reciprocal/conversion improvements without changing v1 evidence."""
from pathlib import Path
import hashlib
import json
import re
import shutil

ROOT = Path(__file__).resolve().parents[3]
TOOLS = ROOT / 'tools/experiments/os30_fps25_20261005'
BASE = ROOT / 'docs/measurements/os30-fps25-20261005/phase10'
OUT = BASE / 'fpu-feasibility'
RTL = OUT / 'rtl-v2'
RTL.mkdir()
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
for name in ['phase10_float_recip2.v', 'phase10_float_fixed_pipe.v']:
    shutil.copy2(TOOLS / name, RTL / name)
checks = {key: json.loads((OUT / name).read_text()) for key, name in [
    ('reciprocal_random', 'recip2-result.json'),
    ('reciprocal_native', 'native-recip2-result.json'),
    ('fixed_random', 'fixed-pipe-result.json'),
    ('fixed_native', 'native-fixed-result.json')
]}
assert all(check['mismatches'] == 0 for check in checks.values())
assert checks['fixed_native']['native_unsupported'] == 0
fits = {}
files = [Path(__file__), OUT / 'result.json', *RTL.iterdir()]
for key, directory in [('reciprocal', 'recip2-fit-v1'), ('fixed', 'fixed-pipe-fit-v1')]:
    fit = OUT / directory
    summary = (fit / 'output_files/probe.fit.summary').read_text()
    timing = (fit / 'output_files/probe.sta.rpt').read_text()
    fits[key] = {
        'alms': int(re.search(r'Logic utilization \(in ALMs\) : ([\d,]+)', summary)[1].replace(',', '')),
        'dsps': int(re.search(r'Total DSP Blocks : (\d+)', summary)[1]),
        'memory_bits': int(re.search(r'Total block memory bits : ([\d,]+)', summary)[1].replace(',', '')),
        'worst_reported_fmax_mhz': min(float(x) for x in re.findall(r';\s+([0-9.]+) MHz\s+;\s+[0-9.]+ MHz\s+;\s+clk', timing)),
        'scope': 'Standalone saved-seed2 Cyclone V virtual-pin component fit; not integrated os30 timing.'
    }
    files += [fit / name for name in ['probe.qsf', 'timing.sdc', 'output_files/probe.fit.summary', 'output_files/probe.fit.rpt', 'output_files/probe.sta.rpt']]
    files += list(fit.glob('*.v'))
files += [TOOLS / name for name in [
    'phase10_float_recip2.v', 'phase10_float_recip2.cpp', 'phase10_recip2_native.cpp',
    'phase10_float_fixed_pipe.v', 'phase10_float_fixed_pipe.cpp', 'phase10_fixed_native.cpp', 'phase10_fixed_fixture.py'
]]
files += [OUT / name for name in [
    'recip2-result.json', 'native-recip2-result.json', 'fixed-pipe-result.json',
    'native-fixed-result.json', 'native-fixed-vectors.txt', 'native-fixed-inputs.json',
    'native-reciprocal.txt'
]]
files += [BASE / 'cpu-audit/convert-native/result.json', BASE / 'cpu-audit/native-A-reciprocal-vectors.json']
result = {
    'passed_feasibility_gates': True, 'deployable': False,
    'production_changed': False, 'physical_fps_measured': False,
    'checks': checks, 'standalone_fits': fits,
    'reciprocal_contract': 'Signed normal float input; correctly rounded RNE reciprocal. Non-normal inputs/results unsupported. Powers of two take1cycle; ordinary normal inputs take14cycles. Ready/valid output holds under backpressure.',
    'fixed_contract': 'Float32 exponent scaling, signed int32 RNE orRTZ conversion; optional F11 reciprocal-depth clamp. Four registered stages, one input each clock, no backpressure. Invalid/overflow generic conversions are flaggedunsupported. Depth clamp handles signed infinities; NaN flaggedunsupported. RNE is explicit, not dynamicFRM.',
    'native_conversion_scope': '230 fresh retained-A vertices. Expected converter outputs came from a separately compiled exact source arithmetic probe on retained-A CPU, not the original inlined F11 block bytes. RNE920 words andRTZ690words plus154boundarywords pass; six overflow boundarywords require unsupported rather than comparison of invalid result bits.',
    'rejected_predecessor': 'Two-stage converter fixed-fit-v1 (264ALM/68.28MHz) failed timing and is not selected.',
    'rtl_sha256': {p.name: sha(p) for p in RTL.iterdir()},
    'evidence_sha256': {str(p.relative_to(ROOT)): sha(p) for p in files},
    'remaining': ['Descriptor/state ownership and unsupported-job replay/fallback ordering.',
                  'Lighting/clipping/culling and raw-DMA/raster integration.',
                  'Whole-os30 fit/timing and coupled performance; actual FPS is not measured.']
}
(OUT / 'extension-result.json').write_text(json.dumps(result, indent=2) + '\n')
(OUT / 'EXTENSION-REPORT.txt').write_text('''Arithmetic frontend extension (private, not deployable)

Two-iteration reciprocal:149 ALM, no DSP/RAM, standalone106.12MHz.
The ordinary-case latency falls from26 to14cycles for20 additional ALMs.
246,156 random results plus230 native-A reciprocal results pass exactly.
3,844 exceptional/out-of-range random cases are classifiedunsupported.

Four-stage shared converter:204 ALM, no DSP/RAM, standalone145.35MHz.
It accepts one float each clock and implements RNE/RTZ signed conversion,
power-of-two scaling and the existing F11 reciprocal-depth clamp.
284,210 random/directed checks pass, including102,382 unsupported cases.
920 native-A RNE values and690 RTZ values pass.154 boundary cases pass;
six out-of-range boundary conversions correctly request fallback.
The conversion fixture executes separately compiled exact source arithmetic
on retained-A CPU. It does not replay original inlined F11 conversion bytes.

The two-stage converter's68.28MHz result is rejected; use four-stage RTL.
All fits are isolated saved-seed2 virtual-pin component fits. These results
prove arithmetic feasibility, not full-core fit, rendering or an FPS gain.
Descriptor ownership, fallback ordering, the rest of the geometry pipeline
and whole-core timing/performance remain required before deployment.
''')
print(json.dumps({'passed': True, 'standalone_fits': fits, 'rtl_sha256': result['rtl_sha256']}, indent=2))
