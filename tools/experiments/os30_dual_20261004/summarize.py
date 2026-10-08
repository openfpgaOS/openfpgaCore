#!/usr/bin/env python3
"""Collect explicit full-core fits and separate CPU/component evidence."""
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / 'docs/measurements/os30-dual-20261004'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def collect():
    full = []
    for directory in sorted(OUT.glob('fit-*')):
        results = directory / 'results.json'
        if not results.is_file():
            continue
        rows = json.loads(results.read_text())
        assert len(rows) == 1 and rows[0]['variant'] == 'os30'
        row = rows[0]
        record = dict(label=directory.name, seed=row['seed'], state=row['state'],
                      fit_completed=row['valid'], timing_closed=row.get('all_checks_pass', False),
                      worst_slack_ns=row.get('worst'), error=row.get('error'),
                      settings=json.loads((directory / 'experiment.json').read_text()))
        assert row['seed'] == record['settings'].get('placement_seed', 2)
        job = Path(row['directory'])
        report = job / 'output_files/ap_core.fit.rpt'
        if report.is_file():
            text = report.read_text(errors='replace')
            record['fit_report_sha256'] = sha(report)
            record['ignored_receive_location_nodes'] = sorted(set(re.findall(
                r'Node "([^"\n]*isr0\|(?:burst_data|word_q)[^"\n]*)" '
                r'is assigned to location or region, but does not exist in design', text)))
            for field, title in [('alms', r'Logic utilization \(in ALMs\)'),
                                 ('labs', r'Total LABs:\s*partially or completely used'),
                                 ('m10ks', r'M10K blocks')]:
                match = re.search(r';\s*' + title + r'\s*;\s*([\d,.]+)\s*/\s*([\d,]+)', text)
                if match:
                    record[field] = float(match[1].replace(',', ''))
                    record[field + '_capacity'] = int(match[2].replace(',', ''))
            match = re.search(r';\s*\|VexiiRiscv:cpu\|\s*;\s*([\d.]+)', text)
            if match:
                record['cpu_inclusive_alms'] = float(match[1])
        sta = job / 'output_files/ap_core.sta.rpt'
        if sta.is_file():
            values = re.findall(r';\s*([\d.]+) MHz\s*;\s*[\d.]+ MHz\s*;[^\n;]*\|mp_ram\|[^\n;]*general\[0\][^\n;]*;',
                                sta.read_text(errors='replace'))
            if values:
                record['minimum_main_clock_fmax_mhz'] = min(map(float, values))
        full.append(record)
    standalone = []
    for path in sorted(OUT.glob('cpu-fit-*/result.json')):
        row = json.loads(path.read_text())
        rejected = path.parent / 'REJECTED.txt'
        if rejected.is_file():
            row['architectural_rejection'] = rejected.read_text().strip()
        standalone.append(row)
    evidence = {'scope': 'Private experiment: kernel throughput is not game FPS; '
                        'standalone CPU timing does not qualify complete os30.',
                'full_core': full, 'standalone_cpu': standalone}
    summary = OUT / 'validation/summary-all.json'
    if summary.is_file():
        evidence['validation_summary'] = str(summary)
        evidence['validation_summary_sha256'] = sha(summary)
    validation_inventory = OUT / 'validation/validation-inventory.json'
    if validation_inventory.is_file():
        evidence['validation_inventory'] = str(validation_inventory)
        evidence['validation_inventory_sha256'] = sha(validation_inventory)
    (OUT / 'results.json').write_text(json.dumps(evidence, indent=2) + '\n')
    label_width = max([len(row['label']) for row in full] + [29])
    lines = ['OS30 LEAN DUAL-ISSUE: FULL-CORE FITS', '',
             'All candidates keep the complete ordinary triangle GPU and texture-memory interface.',
             'No geometry worker. Clock configured at 100 MHz. Saved seed 2 unless explicitly listed otherwise.',
             'A completed fit and a passing 100 MHz timing result are separate requirements.', '',
             f"{'label':{label_width}} seed   ALMs   LABs M10K main MHz setup ns hold ns status"]
    for row in full:
        w = row['worst_slack_ns'] or {}
        def number(value):
            return '-' if value is None else str(value)
        lines.append(f"{row['label']:{label_width}} {row['seed']:>4} {number(row.get('alms')):>6} {number(row.get('labs')):>6} "
                     f"{number(row.get('m10ks')):>4} {number(row.get('minimum_main_clock_fmax_mhz')):>8} "
                     f"{number(w.get('setup')):>8} {number(w.get('hold')):>7} "
                     + ('timing closed' if row['timing_closed'] else
                        'timing fails' if row['fit_completed'] else 'fit failed'))
        if row.get('ignored_receive_location_nodes'):
            lines.append('  WARNING: receive-register location assignments were ignored; '
                         'this result has unconstrained receive placement.')
    lines += ['', 'Raw reports, source hashes and all timing corners are retained under each fit directory.',
              'Validation: validation/REPORT.txt and validation-inventory.json; historical summary-all.json.',
              'No whole-game FPS claim.',
              'Standalone CPU fits have virtual/unconstrained external interfaces; see CPU_FITS.txt.']
    (OUT / 'REPORT.txt').write_text('\n'.join(lines) + '\n')
    print('\n'.join(lines))


if __name__ == '__main__':
    collect()
