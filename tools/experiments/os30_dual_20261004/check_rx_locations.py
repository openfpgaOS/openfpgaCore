#!/usr/bin/env python3
"""Check the SDRAM receive LAB proposal against a post-fit register extract."""
import argparse
import csv
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[3]
PROPOSAL = ROOT / 'docs/measurements/os30-dual-20261004/receive-lab-proposal-v2/manifest.json'

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('registers', type=Path, help='registers.tsv from audit_placement.tcl')
args = parser.parse_args()
expected = {}
for group in json.loads(PROPOSAL.read_text())['groups']:
    for bit in group['bits']:
        expected[(group['bus'], bit['index'])] = group['location']
seen, mismatches, clones = set(), [], []
for row in csv.DictReader(args.registers.open(), delimiter='\t'):
    match = re.search(r'\|(?:io_sdram:)?isr0\|(burst_data|word_q)\[(\d+)\](.*)$', row['name'])
    if not match:
        continue
    key = (match[1], int(match[2]))
    if key not in expected:
        continue
    location = re.fullmatch(r'FF_X(\d+)_Y(\d+)_N\d+', row['location'])
    lab = f'LAB_X{location[1]}_Y{location[2]}' if location else row['location']
    entry = dict(name=row['name'], actual=row['location'], expected=expected[key],
                 in_requested_lab=lab == expected[key])
    if match[3]:
        clones.append(entry)
    else:
        assert key not in seen, row
        seen.add(key)
        if not entry['in_requested_lab']:
            mismatches.append(entry)
missing = sorted(set(expected) - seen)
report = dict(expected=64, observed=len(seen), missing=missing, mismatches=mismatches,
              copies=clones, base_registers_match=not missing and not mismatches,
              timing_qualified=False)
print(json.dumps(report, indent=2))
raise SystemExit(0 if report['base_registers_match'] else 1)
