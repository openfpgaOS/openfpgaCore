#!/usr/bin/env python3
"""Summarize an explicitly requested v12 STA export; never invoke Quartus."""
import collections
import csv
import json
import shutil
from pathlib import Path

from phase10_experiment import CAMP, ROOT, sha, write
from phase10_timing_paths_parse import parse

P = CAMP / 'phase10'
OUT = P / 'compact-timing-audit-v12'
CORE = P / 'compact-lod5-core-v12'
MODULES = ('cpu', 'gpu_internal', 'geometry', 'render_cache', 'sdram',
           'dispatch', 'rvc', 'aligner_pc', 'ring_status',
           'cpu_writeback', 'texture_ram', 'audio_accum')


def main():
    assert not (OUT / 'manifest.json').exists(), 'Do not overwrite a sealed audit'
    census = list(csv.DictReader((OUT / 'reports/census.tsv').open(), delimiter='\t'))
    assert len(census) == 2
    assert {r['temperature'] for r in census} == {'0', '85'}
    threshold = 10 - 1000 / 90
    assert all(r['model'] == 'slow' and r['complete'] == '1'
               and float(r['last_slack']) >= threshold for r in census)
    counts = {'slow_' + r['temperature']: int(r['below_90']) for r in census}
    data = {p.stem: parse(p) for p in sorted((OUT / 'reports').glob('*.rpt'))}
    expected = {c + '_' + suffix for c in counts
                for suffix in (*MODULES, 'global', 'gradient', 'below90')}
    assert set(data) == expected, (set(data) - expected, expected - set(data))

    # As in the validated v7 adapter, no generic missing/empty-report waiver:
    # only a below90 report with a complete independent zero census may be empty.
    empty = []
    for name, rows in data.items():
        if not rows:
            assert name.endswith('_below90'), name
            assert counts[name.removesuffix('_below90')] == 0, name
            empty.append(name)

    endpoints = list(csv.DictReader((OUT / 'reports/endpoints.tsv').open(), delimiter='\t'))
    modules = {}
    families = {}
    equal_slack_launch_ties = []
    for corner, count in counts.items():
        relevant = [r for r in data[corner + '_below90'] if r['relationship_ns'] == 10]
        assert len(relevant) == count, (corner, len(relevant), count)
        assert all(r['period_bound_mhz'] < 90 for r in relevant)
        temperature = corner.removeprefix('slow_')
        csv_rows = [r for r in endpoints if r['model'] == 'slow'
                    and r['temperature'] == temperature]
        assert len(csv_rows) == count
        assert {(r['to'], float(r['slack']), float(r['relationship'])) for r in csv_rows} == {
            (r['to_node'], r['slack_ns'], r['relationship_ns']) for r in relevant}
        for row in csv_rows:
            detail = next(r for r in relevant if r['to_node'] == row['to'])
            if row['from'] != detail['from_node']:
                # STA APIs may choose distinct equally critical launches for
                # one endpoint. Preserve both; do not drop endpoint or slack.
                equal_slack_launch_ties.append(dict(corner=corner,
                    endpoint=row['to'], slack_ns=float(row['slack']),
                    census_from=row['from'], detail_from=detail['from_node']))
        buckets = collections.defaultdict(list)
        for row in relevant:
            # Keep complete endpoint identities; do not assume v7's CPU family
            # remains limiting after placement of the revised source.
            hierarchy = row['to_node'].rsplit('|', 1)[0]
            buckets[hierarchy].append(row)
        families[corner] = dict(buckets)
        modules[corner] = {}
        for module in MODULES:
            rows = [r for r in data[corner + '_' + module] if r['relationship_ns'] == 10]
            assert rows, (corner, module)
            modules[corner][module] = min(rows, key=lambda r: r['slack_ns'])
    assert len(endpoints) == sum(counts.values())

    fit = json.loads((CORE / 'result.json').read_text())
    assert fit['whole_core_fit_successful'] and fit['seed'] == 2
    invocation = json.loads((OUT / 'invocation.json').read_text())
    assert not invocation['fit_invoked']
    assert invocation['completed_parent_sha256'] == sha(CORE / 'fit-seed2/complete.json')
    assert invocation['tcl_sha256'] == sha(OUT / 'audit.tcl')
    source = CORE / 'fit-seed2/jobs/pocket-os30-s2'
    for name, digest in invocation['inputs_sha256'].items():
        assert sha(source / name) == digest, name
    worst = {c: data[c + '_global'][0]['period_bound_mhz'] for c in counts}
    for corner, value in worst.items():
        assert abs(value - fit['main_clock_slow_fmax_mhz'][corner.removeprefix('slow_')]) < .01

    write(OUT / 'paths.json', data)
    write(OUT / 'families.json', families)
    write(OUT / 'module-worst.json', modules)
    result = dict(
        passed_readonly_export=True,
        timing_passed_90mhz=fit['exceeds_90mhz'],
        timing_passed_configured_100mhz=fit['all_configured_timing_checks_pass'],
        worst_mhz=worst, census=census, below_90_counts=counts,
        empty_reports_accepted_only_with_complete_zero_census=sorted(empty),
        equal_slack_launch_ties=equal_slack_launch_ties,
        resources=fit['resources'], worst_timing=fit['worst_timing'],
        configured_mhz=fit['configured_mhz'], fitter_invoked=False,
        rtl_or_physical_options_changed=False, frozen_inputs_modified=False)
    write(OUT / 'result.json', result)
    lines = ['Compact-v12 routed timing audit', '',
             'Complete register endpoint census and copied-netlist STA export.',
             'No fitter, source, clock, seed or physical-option change.', '']
    for corner in ('slow_85', 'slow_0'):
        lines.append(f"{corner}: {worst[corner]:.6f} MHz worst path bound; "
                     f"{counts[corner]} endpoints below 90 MHz.")
    lines += ['', f"Configured clock: {fit['configured_mhz']} MHz.",
              f"Greater than 90 MHz across slow corners: {fit['exceeds_90mhz']}.",
              f"All configured timing checks pass: {fit['all_configured_timing_checks_pass']}.",
              'Worst timing slacks: ' + json.dumps(fit['worst_timing'], sort_keys=True),
              '', 'These are timing results, not native Pocket FPS measurements.',
              'External-clock paths retain their original clock relationships.',
              'Empty below90 reports are accepted only with an independent complete',
              'zero census; every other report is mandatory and nonempty.']
    (OUT / 'REPORT.txt').write_text('\n'.join(lines) + '\n')
    shutil.copy2(Path(__file__), OUT / 'summary-recipe.py')
    evidence = [CORE / 'final-manifest.json',
                P / 'compact-timing-audit-v11/manifest.json', Path(__file__),
                Path(__file__).with_name('phase10_timing_paths_parse.py'),
                Path(__file__).with_name('phase10_compact_timing_audit_v12_paths.py')]
    write(OUT / 'manifest.json', dict(
        files={str(p.relative_to(OUT)): sha(p) for p in sorted(OUT.rglob('*'))
               if p.is_file() and 'job' not in p.relative_to(OUT).parts
               and p.name != 'manifest.json'},
        evidence_sha256={str(p.relative_to(ROOT)): sha(p) for p in evidence}))
    print(json.dumps(result, indent=2))
    print(sha(OUT / 'manifest.json'))


if __name__ == '__main__':
    main()
