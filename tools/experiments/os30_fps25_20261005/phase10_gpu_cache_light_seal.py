#!/usr/bin/env python3
"""Seal literal source reconstruction and cache/color component evidence."""
import hashlib
import json
import shutil
from pathlib import Path
import phase10_gpu_cache_light_prepare as recipe

R = Path(__file__).resolve().parents[3]
P = R / 'docs/measurements/os30-fps25-20261005/phase10'
T = R / 'tools/experiments/os30_fps25_20261005'
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()


def put(p, value):
    with p.open('x') as f:
        json.dump(value, f, indent=2, sort_keys=True)
        f.write('\n')


def bind(out, files):
    shutil.copy2(__file__, out / 'seal.py')
    files = sorted(set(files + [p for p in out.rglob('*') if p.is_file()]))
    put(out / 'manifest.json', dict(passed=True, production_modified=False,
        quartus_invoked=False, native_full_renderer_pending=True,
        physical_timing_qualification=False,
        evidence_sha256={str(p.relative_to(R)): sha(p) for p in files}))
    print(out.name, sha(out / 'manifest.json'))


def main():
    common = [Path(__file__), T / 'phase10_gpu_cache_light_prepare.py',
        P / 'control-cones-paths-v1/manifest.json',
        P / 'control-cones-paths-v1/mapped-cones.json',
        P / 'control-endpoint-census-v1/manifest.json',
        P / 'control-endpoint-census-v1/reports/endpoints.tsv']
    out = P / 'render-cache-speculative-read-v1'
    old = out / 'baseline.sv'
    src = out / 'gpu_color_depth_cache.sv'
    assert recipe.cache(old.read_text()) == src.read_text()
    prep = json.loads((out / 'prepared.json').read_text())
    assert sha(old) == prep['parent_sha256'] and sha(src) == prep['source_sha256']
    proof = json.loads((out / 'protocol/result.json').read_text())
    assert proof['passed'] and len(proof['runs']) == 6
    runs = [r for rows in proof['runs'].values() for r in rows]
    assert len(runs) == 12
    for r in runs:
        assert min(r[k] for k in ('checks', 'speculative_addresses', 'unowned_outputs', 'owned_outputs')) > 0
    totals = {k: sum(r[k] for r in runs) for k in
        ('checks', 'speculative_addresses', 'unowned_outputs', 'owned_outputs', 'memory_reads', 'memory_writes')}
    invariant = '''R_WORD read-ahead ownership argument

The sole RTL difference removes word_valid==15 from the combinational
next-word RAM read address. Every declaration, sequential assignment,
write address/enable/payload and response/FSM validity test is unchanged.
When word_valid==15, or left==1, or !same_line, the address remains exact.
The differing case is R_WORD, invalid current word, another same-line word.
R_WORD performs no RAM write. Its unchanged invalid-word branch suppresses
s_rvalid, leaves the request registers unchanged and enters FILL_CHECK
(REGISTER_FILL_HAZARD=1) or FILL_AR (0). Neither state consumes data_q.
FILL_CHECK rereads the current word; FILL_AR always reads word zero,
including on the edge accepting the refill AR. Thus the first FILL_R
merge sees the original word-zero value, even with immediate AR acceptance.
Subsequent refill data and read addresses remain exact. A synchronous reset
may discard the differing read into INIT, which also never consumes it.

The miter retains exact FSM/register, write and owned-bus comparisons.
It requires read-address inequality iff that exact differing case holds,
checks both actual addresses, and records that inequality across the same
RAM edge. A RAM-output difference is legal only after that proven prior
read mismatch, only in FILL_CHECK/FILL_AR or reset, and never during a data
consumer, RAM write or valid read/write response. All other RAM outputs
and all consumer data are compared literally. Each of the twelve runs
must exercise both differing addresses and differing unowned data.
The 12 runs include the original six cache configurations x two seeds,
all four GPU32 physical banks, four ways, small geometry, range bypass,
legacy unstaged refill, collision poisoning, reset and backpressure.

This is a protocol/functional proof, not a synthesis or timing result.
'''
    (out / 'ownership.txt').write_text(invariant)
    put(out / 'result.json', dict(passed=True, literal_parent_reconstruction=True,
        source_sha256=sha(src), parent_sha256=sha(old), added_cycles=0, added_registers=0,
        protocol_runs=len(runs), totals=totals,
        unowned_difference='Exact prior invalid R_WORD lookahead only; consumers/writes/state/bus remain exact',
        no_ram_layout_change=True, physical_timing_qualification=False))
    bind(out, common + [T / 'phase10_cache_speculative_read_tests.py',
        P / 'render-cache-storage-v3/gpu_color_depth_cache.sv',
        P / 'render-cache-storage-v3/protocol/main.cpp', P / 'render-cache-storage-v3/manifest.json'])

    out = P / 'gpu-light-prefix-v1'
    old, src = out / 'baseline.v', out / 'gpu_core.v'
    assert recipe.light(old.read_text()) == src.read_text()
    prep = json.loads((out / 'prepared.json').read_text())
    assert sha(old) == prep['parent_sha256'] and sha(src) == prep['source_sha256']
    proof = json.loads((out / 'literal/result.json').read_text())
    assert proof['passed'] and proof['increment_inputs'] == 64
    assert proof['full_modulate_domain'] == 4194304
    put(out / 'result.json', dict(passed=True, literal_parent_reconstruction=True,
        source_sha256=sha(src), parent_sha256=sha(old), added_cycles=0, added_registers=0,
        increment_inputs=64, modulate_full_domain=4194304,
        gouraud_complete_independent_channel_domains=proof['complete_gouraud_independent_channel_domains'],
        unchanged=['Raw light and color registers', 'Five-bit R/B increments',
            'Signed combiner/palette', 'Products/truncations', 'All sequential assignments and handshakes'],
        intent='Expose independent <=6-input Boolean outputs before the existing DSP; fitter can still refactor them.',
        physical_timing_qualification=False))
    bind(out, common + [T / 'phase10_gpu_light_prefix_test.py',
        P / 'gpu-control-owner-v1/gpu_core.v', P / 'gpu-control-owner-v1/manifest.json'])


if __name__ == '__main__':
    main()
