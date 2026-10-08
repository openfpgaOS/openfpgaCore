#!/usr/bin/env python3
"""Analyze the retained UART transcript once, excluding its live echo."""
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[3]
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()


def main():
    label = sys.argv[1]
    assert label in ('w-guard-v1', 'w-guard-v2')
    out = ROOT/'docs/measurements/os30-fps25-20261005/phase9'/label/'cpu-guard-timer'
    manifest = json.loads((out/'manifest.json').read_text())
    for path, digest in manifest['source_sha256'].items():
        assert sha(ROOT/path) == digest, path
    for name, digest in manifest['firmware_sha256'].items():
        assert sha(out/'firmware'/name) == digest, name
    for path, digest in manifest['unchanged_rtl_object_sha256'].items():
        assert sha(ROOT/path) == digest, path
    assert sha(Path(manifest['simulator_path'])) == manifest['simulator_sha256']
    assert sha(out/'kernel.asm') == manifest['assembly_sha256']
    log = (out/'quiet.log').read_text()
    assert log.count('=== UART BEGIN ===') == log.count('=== UART END ===') == 1
    uart = log.split('=== UART BEGIN ===\n')[1].split('=== UART END ===')[0]
    assert 'KERNEL PASS HAL init' in uart and 'KERNEL FAIL' not in log
    assert 'WCONF: mism=0 spurious=0 exp_fifo=0 aw_fifo=0 run_left=0' in log
    assert re.search(r'cycles=\d+ PC=0x[0-9a-f]+ exit=0', log)
    count = int(re.search(r'^CHECK PASS ([0-9a-f]+) $', uart, re.M).group(1), 16)
    assert count == (69120 if label == 'w-guard-v1' else 135000)
    nan = int(re.search(r'^NAN BASELINE ([0-9a-f]+) $', uart, re.M).group(1), 16)
    assert nan == 1
    candidate_nan = None
    if label == 'w-guard-v1':
        candidate_nan = int(re.search(r'^NAN CANDIDATE ([0-9a-f]+) $', uart, re.M).group(1), 16)
        assert candidate_nan == 0
    rows = [dict(category=int(c, 16), pass_index=int(p, 16), reference_cycles=int(a, 16),
                 candidate_cycles=int(b, 16), saving_percent=100*(1-int(b, 16)/int(a, 16)))
            for c, p, a, b in re.findall(r'^TIME ([0-9a-f]+) ([0-9a-f]+) ([0-9a-f]+) ([0-9a-f]+) $', uart, re.M)]
    assert len(rows) == 12
    assert {(r['category'], r['pass_index']) for r in rows} == {(c,p) for c in range(3) for p in range(4)}
    categories = []
    for c, name in enumerate(('positive_varying', 'mixed_range_reject', 'uniform_positive')):
        subset = [r for r in rows if r['category'] == c]
        a, b = (sum(r[k] for r in subset) for k in ('reference_cycles', 'candidate_cycles'))
        categories.append(dict(category=c, name=name, reference_cycles=a, candidate_cycles=b,
                               saving_percent=100*(1-b/a)))
    paths = [Path(__file__), out/'manifest.json', out/'quiet.log', out/'kernel.asm', out/'compiler.txt']
    result = dict(passed=True, candidate=label, comparisons=count, rounding_modes=5,
                  standalone_baseline_nan_accepts=True, standalone_candidate_nan_accepts=candidate_nan,
                  timings=rows, category_totals=categories, scope=manifest['scope'],
                  parser_note='Parse canonical UART summary only; initial run parser counted live echo too and therefore declined a result. No firmware or simulation rerun.',
                  evidence_sha256={str(p.relative_to(ROOT)): sha(p) for p in paths})
    target = out/'result.json'
    with target.open('x') as f:
        f.write(json.dumps(result, indent=2)+'\n')
    print(json.dumps(dict(candidate=label, comparisons=count, category_totals=categories), indent=2))


if __name__ == '__main__':
    main()
