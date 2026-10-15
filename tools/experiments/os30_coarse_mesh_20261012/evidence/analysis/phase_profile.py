#!/usr/bin/env python3
"""Separate measured cold/warm CPU attribution; gap buckets are diagnostic only."""
import argparse
import bisect
import collections
import csv
import hashlib
import json
from pathlib import Path

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('candidate', type=Path)
p.add_argument('control', type=Path)
p.add_argument('symbols', type=Path)
p.add_argument('output', type=Path)
a = p.parse_args()
assert not a.output.exists()
paths = [Path(__file__).resolve(), a.symbols.resolve()]
symbols = []
for line in a.symbols.read_text().splitlines():
    words = line.split()
    if len(words) == 4 and words[2].lower() == 't':
        symbols.append((int(words[0], 16), words[3]))
symbols.sort()
keys = [x[0] for x in symbols]

def load(run):
    paths.extend([run/'boundaries.tsv', run/'manifest.json', run/'process.json', run.parent/'build.json'])
    boundaries = [[int(x) for x in line.split()] for line in (run/'boundaries.tsv').read_text().splitlines()]
    assert len(boundaries) == 3
    result = []
    for phase in (1, 2):
        path = run/f'pc-phase-{phase}.csv'
        paths.append(path)
        buckets = collections.defaultdict(lambda: [0, 0])
        for row in csv.DictReader(path.open()):
            pc, retired, gap = map(int, row.values())
            i = bisect.bisect_right(keys, pc) - 1
            name = symbols[i][1] if i >= 0 else '<before_first_ELF_symbol>'
            buckets[name][0] += retired
            buckets[name][1] += gap
        before, after = boundaries[phase-1:phase+1]
        result.append(dict(cycles=after[1]-before[1], retired=after[2]-before[2],
                           function_retire_and_preceding_gap_cycles=buckets))
    return result

candidate, control = load(a.candidate), load(a.control)
phases = []
for c, b in zip(candidate, control):
    cb, bb = (x['function_retire_and_preceding_gap_cycles'] for x in (c, b))
    delta = {name: [cb[name][i]-bb[name][i] for i in range(2)] for name in set(cb)|set(bb)}
    phases.append(dict(candidate=c, control=b, cycle_change_percent=100*(c['cycles']/b['cycles']-1),
                       function_delta=dict(sorted(delta.items(), key=lambda x: x[1][1], reverse=True))))
result = dict(phases=phases, limits=[
    'Measured two bounded native intervals, not sustained Pocket FPS.',
    'Function buckets include gaps preceding retirement; they are not exclusive function execution time.',
    'Do not add these diagnostic buckets or overlapping hardware states to predict savings.'],
    inputs_sha256={str(path.resolve()): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths})
a.output.write_text(json.dumps(result, indent=2)+'\n')
print(json.dumps([dict(candidate=x['candidate']['cycles'], control=x['control']['cycles'],
                       cycle_change_percent=x['cycle_change_percent']) for x in phases], indent=2))
