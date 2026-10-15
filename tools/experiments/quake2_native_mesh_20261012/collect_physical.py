#!/usr/bin/env python3
"""Retain completed fit/timing evidence; never classify negative slack as a pass."""
import argparse
import csv
import hashlib
import io
import json
from pathlib import Path
import re
import shutil
import tarfile

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('fit', type=Path)
p.add_argument('output', type=Path)
a = p.parse_args()
fit = a.fit.resolve()
receipt = json.loads((fit / 'build-receipt.json').read_text())
codes = receipt['returncodes']
if not any(codes.values()) and 'corners' not in codes:
    raise SystemExit('Fit is still incomplete')
out = a.output.resolve()
out.mkdir(parents=True, exist_ok=False)
quartus = fit / 'src/fpga/targets/pocket/output_files'
files = [fit / name for name in ('build-receipt.json', 'fit.log', 'corners.log')]
files += [quartus / ('ap_core.' + suffix) for suffix in
          ('map.summary', 'fit.summary', 'sta.summary', 'sta.rpt')]
files += sorted((fit / 'timing').glob('*'))
files = [f for f in files if f.is_file()]
pins = {str(f.relative_to(fit)): hashlib.sha256(f.read_bytes()).hexdigest()
        for f in files}
with tarfile.open(out / 'reports.tar.gz', 'w:gz') as archive:
    for f in files:
        data = f.read_bytes()
        entry = tarfile.TarInfo(str(f.relative_to(fit)))
        entry.size = len(data)
        entry.mode = 0o644
        archive.addfile(entry, io.BytesIO(data))
for f in files:
    if f.name in ('build-receipt.json', 'ap_core.fit.summary', 'summary.tsv',
                   'clocks.tsv', 'ap_core.sta.summary'):
        shutil.copy2(f, out / f.name)
result = dict(fit=str(fit), phase_returncodes=codes,
              configured_clock_MHz=100, seed=2,
              constrained_timing_passed=receipt['timing_passed'],
              source_recipe_sha256=receipt['recipe_sha256'],
              report_sha256=pins, deployed=False)
summary = quartus / 'ap_core.fit.summary'
if summary.exists():
    text = summary.read_text()
    result['fit_successful'] = 'Fitter Status : Successful' in text
    for key, label in [('ALMs', 'Logic utilization (in ALMs)'),
                       ('M10Ks', 'Total RAM Blocks'),
                       ('DSPs', 'Total DSP Blocks')]:
        m = re.search(re.escape(label) + r'\s*:\s*([\d,]+) / ([\d,]+)', text)
        if m:
            result[key] = {'used': int(m[1].replace(',', '')),
                           'available': int(m[2].replace(',', ''))}
sta = quartus / 'ap_core.sta.rpt'
if sta.exists():
    fmax = {}
    corner = None
    for line in sta.read_text().splitlines():
        m = re.search(r'; (Slow|Fast) 1100mV (\d+)C Model Fmax Summary', line)
        if m:
            corner = m[1].lower() + '_' + m[2]
        if corner and 'ic|mp_ram|' in line and 'MHz' in line:
            cells = [s.strip() for s in line.split(';')]
            fmax[corner] = float(cells[2].split()[0])
    result['main_clock_restricted_Fmax_MHz'] = fmax
timing = fit / 'timing/summary.tsv'
if timing.exists():
    rows = list(csv.DictReader(io.StringIO(timing.read_text()), delimiter='\t'))
    result['all_corner_checks'] = rows
    for check in ('setup', 'hold', 'recovery', 'removal', 'pulse_width'):
        values = [float(row['slack']) for row in rows
                  if row['check'] == check and row['slack'] != 'no_paths']
        if values:
            result['worst_' + check + '_ns'] = min(values)
for name, key in [('os30-native-mesh.rbf_r', 'bitstream_sha256'),
                  ('src/fpga/targets/pocket/output_files/ap_core.rbf', 'raw_rbf_sha256')]:
    f = fit / name
    if f.exists():
        result[key] = hashlib.sha256(f.read_bytes()).hexdigest()
if 'bitstream_sha256' in result:
    assert result['bitstream_sha256'] == receipt['bitstream_sha256']
(out / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
print(json.dumps({k: v for k, v in result.items()
                  if k not in ('report_sha256', 'all_corner_checks')}, indent=2))
