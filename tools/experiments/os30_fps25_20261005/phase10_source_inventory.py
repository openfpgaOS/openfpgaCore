#!/usr/bin/env python3
"""Count immutable geometry storage independently of memory timing."""
import csv
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
DATA = ROOT / 'docs/measurements/os30-fps25-20261005/phase10'
SOURCE = DATA / 'raw-frontend-observer/capture-normal/raw-frontend.csv'
OUT = DATA / 'cram1-source-inventory-v1/result.json'


def analyze(rows):
    keys = sorted({(int(row['address']), int(row['count'])) for row in rows})
    merged = []
    for start, end in sorted((address, address + 24 * count) for address, count in keys):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(end, merged[-1][1])
        else:
            merged.append([start, end])
    return dict(loads=len(rows), unique_keys=len(keys),
                unique_bytes=sum(end - start for start, end in merged),
                separate_block_bytes=sum((24 * count + 63) // 64 * 64
                                         for address, count in keys),
                merged_ranges=len(merged),
                stream_bytes=sum(int(row['count']) * 24 for row in rows), keys=keys)


def main():
    with SOURCE.open() as stream:
        rows = [row for row in csv.DictReader(stream)
                if row['kind'] == 'vertex' and int(row['immutable'])]
    result = dict(
        all721=analyze(rows),
        gameplay360_720=analyze([row for row in rows if 360 <= int(row['frame']) <= 720]),
        source_sha256=hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
        scope='Read-only source inventory from unchanged F11 passive observer. '
              'Includes immutable LOADs regardless of accelerator math/material '
              'eligibility. No allocation, bandwidth or performance claim.')
    serialized = json.dumps(result, indent=2) + '\n'
    if OUT.exists():
        assert OUT.read_text() == serialized, 'Preserve prior evidence'
    else:
        OUT.parent.mkdir(exist_ok=False)
        OUT.write_text(serialized)
    for name in ('all721', 'gameplay360_720'):
        print(name, {key: value for key, value in result[name].items() if key != 'keys'})


if __name__ == '__main__':
    main()
