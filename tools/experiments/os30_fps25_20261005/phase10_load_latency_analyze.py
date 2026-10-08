#!/usr/bin/env python3
"""Seal a passive count-weighted timing audit; no implementation changes."""
import csv
import hashlib
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
P = ROOT / 'docs/measurements/os30-fps25-20261005/phase10'
OUT = P / 'load-phase-audit-v1'
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()

def rows(path):
    return [{k: int(v) for k, v in r.items()} for r in csv.DictReader(path.open())]

def main():
    timing = rows(OUT / 'latency.csv')
    assert len(timing) == 128
    table = {(r['count'], r['lighting'], r['power_two_w']): r for r in timing}
    for r in timing:
        n, lit, power = r['count'], r['lighting'], r['power_two_w']
        assert r['fma_issues'] == (17 + 7 * lit) * n
        assert r['fetch'] == 6 * n + (6 * n + 15) // 16 + 2
        assert r['scan'] == 4 * n + 1 and r['wrapper'] == 3
        expected = 17 * n + 49 if power else 28 * n + 40
        if lit:
            expected = max(expected, 24 * n + 103)
        assert r['compute'] == expected
        assert r['total'] == r['fetch'] + r['compute'] + r['scan'] + r['wrapper']
    trace = P / 'lod5-real-coupled-v2/run-candidate/geometry-operations.csv'
    jobs = [r for r in rows(trace) if r['operation'] == 1]
    assert len(jobs) == 2608 and sum(r['count'] for r in jobs) == 32496
    assert all(r['status'] & 6 == 6 for r in jobs)
    assert Counter(r['frame'] for r in jobs) == {f: 326 for f in range(360, 368)}
    denom = 8 * 100000
    actual = sum(r['cycles'] for r in jobs) / denom
    fields = ['total', 'fetch', 'compute', 'scan', 'wrapper', 'fma_issues',
              'transform_busy', 'lighting_busy', 'reciprocal_tail', 'reciprocal_busy']
    weighted = {name: {field: sum(table[r['count'], r['lit'], power][field] for r in jobs) / denom
                       for field in fields}
                for name, power in [('all_ordinary_w', 0), ('all_power_of_two_w', 1)]}
    native_file = P / 'bram-alias-native-v1/result.json'
    native = json.loads(native_file.read_text())['runs']['32-nominal']['rows']
    buckets = []
    for n, lit in sorted(Counter((r['count'], r['lit']) for r in jobs)):
        group = [r for r in jobs if (r['count'], r['lit']) == (n, lit)]
        hand = [r for r in native if r[1] == 1 and r[2] == n]
        buckets.append(dict(count=n, lighting=lit, jobs=len(group),
                            mean_device_cycles=sum(r['cycles'] for r in group) / len(group),
                            min_device_cycles=min(r['cycles'] for r in group),
                            max_device_cycles=max(r['cycles'] for r in group),
                            independent_native_invalidate_copy_cycles=sum((r[5]+r[6])/r[4] for r in hand)/len(hand)))
    geometry = P / 'lod5-real-coupled-v2/run-candidate/geometry.json'
    occupancy = P / 'lod5-real-coupled-v2/run-candidate/cram-occupancy.json'
    g, c = json.loads(geometry.read_text()), json.loads(occupancy.read_text())
    source = (g['ar_wait'] + c['gd_response_cycles']) / denom
    residual = [actual - source - weighted[name]['compute'] - weighted[name]['scan'] - weighted[name]['wrapper']
                for name in weighted]
    result = dict(passed=True, scope='Unchanged frozen LOD5 datapath, 128 isolated synthetic timing cases weighted by actual warm eight-frame count/light trace; source wall time from actual coupled physical CRAM model.',
                  cpu_clock_hz=100000000, frames=8, jobs=len(jobs), vertices=sum(r['count'] for r in jobs),
                  lit_jobs=sum(r['lit'] for r in jobs), device_wait_ms=actual, weighted_ideal_source_ms=weighted,
                  actual_cram_source_queue_ms=g['ar_wait']/denom,
                  actual_cram_source_response_ms=c['gd_response_cycles']/denom,
                  unassigned_fetch_handoff_poll_ms_bounds=sorted(residual), buckets=buckets,
                  limits=['The synthetic source is ideal, not the physical CRAM bus; its FETCH timing is not substituted for observed source duration.',
                          'Compute bounds bracket all ordinary and all power-of-two W. Mixed reciprocal jobs were not directly counted in the coupled trace.',
                          'FMA, transform, lighting and reciprocal counters overlap and must not be added.',
                          'Unlit lighting_cycles CSV field retains its preceding value; only lighting_busy is a valid zero-work measure.',
                          'Native handoff microbench uses a 17-cycle linefill; actual shim uses 19 cycles. It excludes configuration, lookup and eligibility cost and is not a measured small-job speedup.',
                          'No native CPU measurements for one/two-vertex original calls, so no minimum offload threshold is selected.',
                          'No new correctness, fit, timing closure or physical Pocket FPS qualification is claimed.'])
    with (OUT / 'result.json').open('x') as f:
        json.dump(result, f, indent=2); f.write('\n')
    (OUT / 'analyze.py').write_bytes(Path(__file__).read_bytes())
    inputs = [trace, native_file, geometry, occupancy, P/'cpu-audit/results.json']
    (OUT / 'inputs.json').write_text(json.dumps({str(p.relative_to(ROOT)): sha(p) for p in inputs}, indent=2)+'\n')
    print(json.dumps({k: result[k] for k in ['device_wait_ms','actual_cram_source_queue_ms','actual_cram_source_response_ms','unassigned_fetch_handoff_poll_ms_bounds']}, indent=2))

if __name__ == '__main__':
    main()
