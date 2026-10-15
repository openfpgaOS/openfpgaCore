#!/usr/bin/env python3
"""Strict four-corner Pocket timing parsing and explicit >90 versus 100 MHz gates."""
import csv
import math
from pathlib import Path
import re

CHECKS = ('setup', 'hold', 'recovery', 'removal', 'pulse_width')
CORNERS = {('slow', '0'), ('slow', '85'), ('fast', '0'), ('fast', '85')}
MAIN = re.compile(r'^ic\|mp_ram\|.*general\[0\].*PLL_OUTPUT_COUNTER\|divclk$')
DDIO = re.compile(r'^core_top:ic\|io_sdram:isr0\|altddio_in:u_dq_ddio_in\|ddio_in_[^|]+:auto_generated\|(dataout_h|dataout_l|ddio_ina)\[([0-9]+)\](~DFFLO)?$')


def table(path):
    with Path(path).open() as stream:
        return list(csv.DictReader(stream, delimiter='\t'))


def number(value):
    result = float(value)
    if not math.isfinite(result):
        raise ValueError('Non-finite timing value: ' + value)
    return result


def fmax_table(path):
    rows = []
    for line in Path(path).read_text(errors='replace').splitlines():
        cells = [c.strip() for c in line.split(';')]
        if len(cells) < 5 or not re.fullmatch(r'[\d.]+ MHz', cells[1]):
            continue
        if not re.fullmatch(r'[\d.]+ MHz', cells[2]):
            raise ValueError('Missing restricted Fmax: ' + line)
        rows.append(dict(clock=cells[3], fmax_mhz=number(cells[1].split()[0]),
                         restricted_fmax_mhz=number(cells[2].split()[0])))
    if not rows:
        raise ValueError('Missing Fmax rows: ' + str(path))
    return rows


def ddio_proof(timing):
    rows = table(timing / 'ddio_proof.tsv')
    expected = {(role, str(bit)) for role in ('high', 'low', 'falling') for bit in range(16)}
    if len(rows) != 48 or {(r['role'], r['bit']) for r in rows} != expected:
        raise ValueError('Missing/changed exact 16-bit SDRAM DDIO topology')
    for row in rows:
        match = DDIO.fullmatch(row['name'])
        role = {'dataout_h': 'high', 'dataout_l': 'low', 'ddio_ina': 'falling'}
        if (not match or role[match[1]] != row['role'] or match[2] != row['bit']
                or bool(match[3]) != (row['role'] == 'falling')
                or row['type'] != 'reg' or not row['location'].startswith('DDIOINCELL_')):
            raise ValueError('Unexpected SDRAM DDIO primitive identity')
        bit = int(row['bit'])
        fans = row['fanouts'].split(',') if row['fanouts'] else []
        wanted = {f'core_top:ic|io_sdram:isr0|{name}[{b}]'
                  for name in ('word_q', 'burst_data') for b in (bit, bit + 16)} if row['role'] == 'low' else set()
        if len(fans) != len(wanted) or set(fans) != wanted or int(row['fanout_count']) != len(wanted):
            raise ValueError('SDRAM DDIO functional connectivity changed')
    checks = table(timing / 'ddio_checks.tsv')
    keys = [(r['model'], r['temperature'], r['check']) for r in checks]
    expected_checks = {(m, t, c) for m, t in CORNERS for c in ('high_output', 'low_input', 'low_output')}
    if len(keys) != len(expected_checks) or set(keys) != expected_checks:
        raise ValueError('Incomplete used SDRAM DDIO timing proof')
    used = []
    for row in checks:
        if row['check'] == 'high_output':
            if row['slack'] != 'no_paths':
                raise ValueError('Unused high SDRAM DDIO unexpectedly has a timing consumer')
        else:
            used.append(number(row['slack']))
    return dict(nodes=rows, checks=checks, used_setup_clean=all(v >= 0 for v in used),
                worst_used_setup_ns=min(used), excluded_unused_high_endpoint_count=16)


def parse(project, timing):
    project, timing = Path(project), Path(timing)
    rows = table(timing / 'summary.tsv')
    keys = [(r['model'], r['temperature'], r['check']) for r in rows]
    expected = {(m, t, check) for m, t in CORNERS for check in CHECKS}
    if len(keys) != len(expected) or set(keys) != expected:
        raise ValueError('Missing or duplicate corner/check; require five checks at all four corners')
    conditions = table(timing / 'conditions.tsv')
    if len(conditions) != 4 or {(r['model'], r['temperature']) for r in conditions} != CORNERS:
        raise ValueError('Unexpected operating conditions')
    worst = {}
    for check in CHECKS:
        values = []
        for row in rows:
            if row['check'] != check:
                continue
            if row['slack'] == 'no_paths':
                if check in ('setup', 'hold', 'pulse_width'):
                    raise ValueError('Missing required paths: ' + check)
            else:
                values.append(number(row['slack']))
        worst[check] = min(values) if values else None
    clocks = table(timing / 'clocks.tsv')
    clock_keys = [(r['model'], r['temperature'], r['check'], r['clock']) for r in clocks]
    if len(clock_keys) != len(set(clock_keys)):
        raise ValueError('Duplicate per-clock timing row')
    if any((r['model'], r['temperature']) not in CORNERS or r['check'] not in CHECKS[:4] for r in clocks):
        raise ValueError('Unexpected per-clock timing row')
    clock_sets = [{r['clock'] for r in clocks if (r['model'], r['temperature'], r['check']) == (m, t, c)}
                  for m, t in sorted(CORNERS) for c in CHECKS[:4]]
    if not clock_sets[0] or any(s != clock_sets[0] for s in clock_sets):
        raise ValueError('Incomplete per-clock timing checks')
    main_clocks = [c for c in clock_sets[0] if MAIN.fullmatch(c)]
    if len(main_clocks) != 1:
        raise ValueError('Expected one exact main 100 MHz clock')
    main = main_clocks[0]
    ddio = ddio_proof(timing)
    crossings = table(timing / 'cross_setup.tsv')
    crossing_keys = [(r['model'], r['temperature'], r['source_kind'], r['source'], r['target']) for r in crossings]
    expected_crossings = {(m, t, 'clock', source, target)
                          for m, t in CORNERS for source in clock_sets[0]
                          for target in clock_sets[0] if source != target}
    expected_crossings |= {(m, t, 'input', 'all_input_ports', target)
                           for m, t in CORNERS for target in clock_sets[0]}
    if len(crossing_keys) != len(expected_crossings) or set(crossing_keys) != expected_crossings:
        raise ValueError('Missing or duplicate cross-clock/input setup checks')
    crossing_values = [number(r['slack']) for r in crossings
                       if r['source_kind'] == 'clock' and r['slack'] != 'no_paths']
    input_values = [number(r['slack']) for r in crossings
                   if r['source_kind'] == 'input' and r['slack'] != 'no_paths']
    used_crossing_values, used_input_values = [], []
    for row in crossings:
        raw, used = row['slack'], row['used_slack']
        if raw == 'no_paths':
            if used != 'no_paths':
                raise ValueError('Used crossing path without a raw path')
            continue
        raw_value = number(raw)
        if used == 'no_paths':
            raise ValueError('Dropping an entire populated crossing is not permitted')
        value = number(used)
        if value < raw_value:
            raise ValueError('Filtered crossing is worse than raw query')
        if used != raw and not (row['target'] == main and
                                (row['source_kind'] == 'input' or row['source'] == 'dram_clk_pin')):
            raise ValueError('Unexpected non-DDIO crossing qualification change')
        (used_crossing_values if row['source_kind'] == 'clock' else used_input_values).append(value)
    crossing_clean = all(v >= 0 for v in used_crossing_values) and ddio['used_setup_clean']
    input_clean = all(v >= 0 for v in used_input_values) and ddio['used_setup_clean']
    other_setup = []
    main_setup = []
    for row in clocks:
        if row['slack'] == 'no_paths':
            if row['check'] == 'setup' and row['clock'] == main:
                raise ValueError('Missing main-clock setup check')
            continue
        value = number(row['slack'])
        if row['check'] == 'setup':
            (main_setup if row['clock'] == main else other_setup).append(value)
    fmax = {}
    all_fmax = {}
    for model, temperature in sorted(CORNERS):
        name = model + '_' + temperature
        values = fmax_table(timing / (name + '_fmax.rpt'))
        selected = [row for row in values if row['clock'] == main]
        if len(selected) != 1:
            raise ValueError('Missing/duplicate main Fmax at ' + name)
        fmax[name] = selected[0]['restricted_fmax_mhz']
        all_fmax[name] = values
    summary = (project / 'output_files/ap_core.fit.summary').read_text(errors='replace')
    successful = 'Fitter Status : Successful' in summary
    resources = {}
    for key, label in [('ALMs', 'Logic utilization (in ALMs)'), ('M10Ks', 'Total RAM Blocks'), ('DSPs', 'Total DSP Blocks')]:
        match = re.search(re.escape(label) + r'\s*:\s*([\d,]+)\s*/\s*([\d,]+)', summary)
        if not match:
            raise ValueError('Missing fitted resource: ' + label)
        used, available = (int(v.replace(',', '')) for v in match.groups())
        resources[key] = dict(used=used, available=available)
        successful &= used <= available
    text = (project / 'output_files/ap_core.fit.rpt').read_text(errors='replace')
    match = re.search(r'Total LABs:\s*partially or completely used\s*;\s*([\d,]+)\s*/\s*([\d,]+)', text)
    if not match:
        raise ValueError('Missing physical LAB occupancy')
    used, available = (int(v.replace(',', '')) for v in match.groups())
    resources['LABs'] = dict(used=used, available=available)
    successful &= used <= available
    nonsetup_clean = all(worst[c] is None or worst[c] >= 0 for c in CHECKS[1:])
    nonmain_setup_clean = all(v >= 0 for v in other_setup)
    minimum = min(fmax.values())
    closed = successful and crossing_clean and input_clean and all(v is None or v >= 0 for v in worst.values())
    eligible = successful and nonsetup_clean and nonmain_setup_clean and crossing_clean and input_clean and minimum > 90.0
    return dict(fit_successful=successful, resources=resources, main_clock=main,
                all_corner_checks=rows, per_clock_checks=clocks, all_clock_fmax=all_fmax,
                main_restricted_fmax_mhz=fmax, minimum_main_restricted_fmax_mhz=minimum,
                worst_slack_ns=worst, worst_main_setup_ns=min(main_setup),
                worst_nonmain_setup_ns=min(other_setup) if other_setup else None,
                nonsetup_clean=nonsetup_clean, nonmain_setup_clean=nonmain_setup_clean,
                cross_clock_input_setup_checks=crossings,
                worst_cross_clock_setup_ns=min(crossing_values) if crossing_values else None,
                worst_input_setup_ns=min(input_values) if input_values else None,
                worst_used_cross_clock_setup_ns=min(used_crossing_values) if used_crossing_values else None,
                worst_used_input_setup_ns=min(used_input_values) if used_input_values else None,
                unused_ddio_endpoint_proof=ddio,
                cross_clock_setup_clean=crossing_clean, input_setup_clean=input_clean,
                timing_closed_at_configured_100_mhz=closed, eligible_above_90_mhz_estimate=eligible,
                qualification_note='>90 MHz is a same-clock Fmax estimate with clean used cross-clock/input setup and other checks. Exactly16 disconnected DDR-high endpoints require a fresh routed proof; raw checks remain reported. This is not timing closure at the configured 100 MHz.')


def select(results, expected_seeds=range(1, 31)):
    expected = set(expected_seeds)
    if len(results) != len(expected) or {r['seed'] for r in results} != expected:
        raise ValueError('Do not select before every seed has a completed attempt')
    if any(r['state'] not in ('complete', 'failed') for r in results):
        raise ValueError('Unfinished seed')
    candidates = [r for r in results if r.get('valid_reports') and r.get('eligible_above_90_mhz_estimate')]
    ordered = sorted(candidates, key=lambda r: (
        r['timing_closed_at_configured_100_mhz'], r['minimum_main_restricted_fmax_mhz'],
        r['worst_main_setup_ns'], -r['resources']['ALMs']['used'], -r['seed']), reverse=True)
    return dict(all_seeds_attempted=True, eligible_seeds=[r['seed'] for r in ordered],
                selected_seed=ordered[0]['seed'] if ordered else None,
                selected_timing_closed_at_100_mhz=ordered[0]['timing_closed_at_configured_100_mhz'] if ordered else False,
                criterion='100 MHz closure first, then highest worst-corner restricted main Fmax among >90 MHz candidates with clean cross-clock/input setup and nonsetup/nonmain checks.')
