#!/usr/bin/env python3
"""Analyze the private retained-CPU BRAM-alias microbenchmark; never overwrite results."""
import hashlib
import json
import re
from pathlib import Path

from phase10_experiment import CAMP

P = CAMP / 'phase10'
HIST = P / 'cpu-audit/dma-native-v4/handoff-costs.json'


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def analyze(out):
    histogram = json.loads(HIST.read_text())
    frames = histogram['frames']
    hist = histogram['histogram']
    result = dict(
        scope='Actual retained os30 CPU, cache and real SDRAM controller. Private read-only synchronous BRAM alias. No GPU work or accelerator compute.',
        cpu_clock_hz=100000000, frames=frames, histogram=hist,
        histogram_source_sha256=sha(HIST),
        eligible_vertices_per_frame=sum(int(n)*count for n, count in hist.items())/frames,
        eligible_jobs_per_frame=sum(hist.values())/frames,
        absolute_cost_definition='one invalidation plus cold copy; warm-copy subtraction is a diagnostic differential, not total new application overhead',
        runs={},
    )
    for path in sorted(out.glob('*-quiet.log')) + sorted(out.glob('*-nominal.log')):
        evidence = json.loads(path.with_suffix('.json').read_text())
        assert evidence['passed'] and evidence['log_sha256'] == sha(path)
        text = path.read_text()
        assert 'R mismatches=0  scanout mismatches=0  rlast slips=0  orphan beats=0  DRAM strikes=0' in text
        for bus in ('I', 'MEM', 'PER'):
            assert re.search(rf'{bus}: beats=\d+ mism=0 rid_mism=0 rlast_slips=0 orphans=0 pending=0', text)
        assert 'WCONF: mism=0 spurious=0 exp_fifo=0 aw_fifo=0 run_left=0' in text
        # Host progress prints can interrupt a UART hexadecimal word. The final
        # UART dump repeats only the last8192 bytes and must not be counted twice.
        text = text.split('=== UART BEGIN ===')[0]
        text = re.sub(r'\[c=\d+\] PC=[^\n]*\n    m1_ar:[^\n]*\n    arb_st=[^\n]*\n', '', text)
        rows = [[int(x, 16) for x in line.split()] for line in re.findall(r'^ALIAS_METHOD (.*)$', text, re.M)]
        assert len(rows) == 192 and all(len(row) == 8 for row in rows)
        run = dict(passed=True, log_sha256=sha(path), rows=rows, methods={})
        for kind, name in ((0, 'real_sdram'), (1, 'bram_alias')):
            for n in range(1, 33):
                selected = [r for r in rows if r[1] == kind and r[2] == n]
                assert len(selected) == 3 and all(r[4] == 8 for r in selected)
            cost = [sum(sum(r[k]/r[4] for r in rows if r[1] == kind and r[2] == int(n))/3*count
                        for n, count in hist.items())/frames/100000 for k in (5, 6, 7)]
            run['methods'][name] = dict(invalidate_ms=cost[0], cold_copy_ms=cost[1], warm_copy_ms=cost[2],
                                        absolute_total_ms=cost[0]+cost[1], total_minus_warm_ms=cost[0]+cost[1]-cost[2])
        result['runs'][path.stem] = run
    assert result['runs']
    with (out/'result.json').open('x') as f:
        json.dump(result, f, indent=2)
        f.write('\n')
    return result


if __name__ == '__main__':
    for name in ('bram-alias-native-v1', 'bram-alias-recip-native-v1'):
        print(name, analyze(P/name)['eligible_vertices_per_frame'])
