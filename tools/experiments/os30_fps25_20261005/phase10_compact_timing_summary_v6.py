#!/usr/bin/env python3
"""Seal complete v6 routed timing census and compare measured module paths."""
import collections
import csv
import hashlib
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
P = ROOT / 'docs/measurements/os30-fps25-20261005/phase10'
OUT = P / 'compact-timing-audit-v6'
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()


def save(p, value):
    with p.open('x') as f:
        json.dump(value, f, indent=2)
        f.write('\n')


def family(row):
    end = row['to_node']
    if '|VexiiRiscv:cpu|' in end:
        return 'CPU_compressed_instruction_decode'
    if ':gpu_render_cache|' in end:
        return 'cache_registered_read_address_mux'
    if 'dsp_a[' in end:
        return 'GPU_shared_DSP_operand_control'
    if 'tex_cache|' in end:
        return 'texture_cache_reset_state_control'
    if '|io_sdram:' in end:
        return 'SDRAM_delay_counter'
    if 'fbwq_addr_rtl' in end:
        return 'GPU_write_queue_read_enable'
    raise AssertionError(end)


def main():
    data = json.loads((OUT / 'paths.json').read_text())
    census = list(csv.DictReader((OUT / 'reports/census.tsv').open(), delimiter='\t'))
    assert all(r['complete'] == '1' and float(r['last_slack']) >= 10 - 1000 / 90 for r in census)
    groups = {}
    modules = {}
    for corner in ('slow_85', 'slow_0'):
        buckets = collections.defaultdict(list)
        for row in data[corner + '_below90']:
            assert row['relationship_ns'] == 10 and row['period_bound_mhz'] < 90
            buckets[family(row)].append(row)
        groups[corner] = dict(buckets)
        assert sum(map(len, buckets.values())) == int(next(r for r in census if corner == 'slow_' + r['temperature'])['below_90'])
        modules[corner] = {}
        for module in ('cpu', 'gpu_internal', 'geometry', 'render_cache', 'sdram', 'dispatch', 'rvc', 'aligner_pc'):
            rows = [r for r in data[corner + '_' + module] if r['relationship_ns'] == 10]
            modules[corner][module] = min(rows, key=lambda r: r['slack_ns'])
    result = dict(passed_readonly_export=True, timing_passed_90mhz=False,
        worst_mhz={c: data[c + '_global'][0]['period_bound_mhz'] for c in groups},
        census=census, family_counts={c: {f: len(rows) for f, rows in fs.items()} for c, fs in groups.items()},
        fitter_invoked=False, frozen_inputs_modified=False)
    save(OUT / 'families.json', groups)
    save(OUT / 'module-worst.json', modules)
    save(OUT / 'result.json', result)
    report = '''Compact-v6 routed timing audit

The complete endpoint census finds13 endpoints below90MHz at slow85C and2
at slow0C, versus73/18 in v5. Current worst85.690/88.637MHz is GPU
DRV_PLANE_ORG -> dsp_a[10], six logic levels with74% routing delay at85C.
The CPU dependency/forwarding family no longer limits90MHz. Current exported
dispatch paths are above95MHz; the only sub90 CPU endpoints are compressed
instruction bits16/18 at89.000/89.534MHz at85C. GPU attribute and reciprocal
clamp endpoints, and previous walker/DSP2 endpoints, no longer appear below90.

Other remaining85C families are live GPU AW -> render data-RAM read address
(87.912MHz), texture-cache reset/state control (~89.04MHz), SDRAM delay counter
(89.445MHz), and GPU framebuffer write-queue read-enable (~89.534MHz).
These are routed observations for this composition, not isolated guarantees
for individual edits. The full detailed paths retain clock skew/setup terms.
External SDRAM paths have different timing relationships and are not converted
into the internal100MHz period bound.

V6 fits17952 needed ALMs,18018 placed ALMs,1843/1848LABs,307/308M10Ks,
45DSPs. Configured100MHz setup remains -1.670ns; hold+0.103ns. Neither the
historical >90MHz target nor100MHz closure is met. No deployment recommendation.
Only a copied completed database was read; original inputs were hash checked.
'''
    with (OUT / 'REPORT.txt').open('x') as f:
        f.write(report)
    shutil.copy2(__file__, OUT / 'summary-recipe.py')
    save(OUT / 'manifest.json', dict(
        files={str(p.relative_to(OUT)): sha(p) for p in OUT.rglob('*')
               if p.is_file() and 'job' not in p.relative_to(OUT).parts and p.name != 'manifest.json'},
        evidence_sha256={str(p.relative_to(ROOT)): sha(p) for p in (
            P / 'compact-lod5-core-v6/final-manifest.json',
            P / 'compact-timing-audit-v5/manifest.json', Path(__file__))}))
    print(json.dumps(result, indent=2))
    print(sha(OUT / 'manifest.json'))


if __name__ == '__main__':
    main()
