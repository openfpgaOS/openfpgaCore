#!/usr/bin/env python3
"""One seed-2 fit of the independently checked v7 MAX_FANOUT 4 map.

Reuses a copy of the completed mapped job. No synthesis rerun, sweep,
production mutation, clock change or deployment. Reports 100 MHz separately
from the comparative >90 MHz slow-corner target.
"""
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

import phase10_v7_lane_fanout as source

ROOT, OUT = source.ROOT, source.OUT
FIT = OUT / 'fit-seed2'
REVIEW = source.P / 'v7-lanefan4-independent-review-v1/review.json'
sha, save = source.sha, source.save
sys.path.insert(0, str(ROOT / 'tools'))
import sweep_target_matrix as sweep


def checked():
    prep = source.checked()
    manifest = json.loads((OUT / 'manifest.json').read_text())
    source.verify_evidence(manifest)
    mapped = json.loads((OUT / 'map-core/result.json').read_text())
    assert mapped['passed_map'] and not mapped['full_fit_attempted']
    assert mapped['assignment'] == source.ASSIGNMENT
    gate = json.loads(REVIEW.read_text())
    assert gate['passed_for_resource_fit'] and gate['exactly_one_fit']
    assert gate['effective_seed'] == gate['saved_seed'] == 2
    assert gate['prepared_sha256'] == sha(OUT / 'prepared.json')
    assert gate['evidence_sha256'][str((OUT / 'manifest.json').relative_to(ROOT))] == sha(OUT / 'manifest.json')
    source.verify_evidence(gate)
    return prep


def run():
    checked()
    FIT.mkdir(exist_ok=False)
    parent = OUT / 'map-core/job'
    before = source.inventory(parent)
    shutil.copytree(parent, FIT / 'job')
    assert source.inventory(FIT / 'job') == before
    directory = FIT / 'job/src/fpga/targets/pocket'
    assert (directory / 'ap_core.qsf').read_text() == source.BASE_QSF.read_text() + '\n' + source.ASSIGNMENT + '\n'
    save(FIT / 'prepared.json', dict(
        source_prepared_sha256=sha(OUT / 'prepared.json'),
        map_manifest_sha256=sha(OUT / 'manifest.json'),
        independent_review_sha256=sha(REVIEW),
        recipe_sha256=sha(Path(__file__)), copied_job_sha256=before,
        seed=2, configured_mhz=100, full_fit_count=1, seed_sweep=False))
    shutil.copyfile(__file__, FIT / 'recipe.py')
    result = dict(directory=str(directory), seed=2, variant='os30',
                  returncodes={'map': 0}, state='running', valid=False)
    started = time.monotonic()
    phases = [
        ('fit', ['quartus_fit', 'ap_core']),
        ('asm', ['quartus_asm', 'ap_core']),
        ('sta', ['quartus_sta', 'ap_core']),
        ('corners', ['quartus_sta', '-t', str(FIT / 'job/tools/report_core_timing.tcl'),
                     'ap_core', 'timing']),
    ]
    for name, command in phases:
        invocation = sweep.docker(FIT, 'pocket', directory, command)
        save(FIT / f'{name}-invocation.json', dict(command=invocation))
        print('START', name, flush=True)
        with (FIT / f'{name}.log').open('x') as log:
            process = subprocess.run(invocation, stdout=log, stderr=subprocess.STDOUT)
        result['returncodes'][name] = process.returncode
        print('DONE', name, process.returncode, flush=True)
        if process.returncode and name != 'corners':
            result['state'] = 'failed'
            break
    else:
        result.update(sweep.parse_result(directory, 'ap_core'))
        result.update(state='complete', valid=True)
    result['seconds'] = time.monotonic() - started
    assert source.inventory(parent) == before, 'Original mapped job changed'
    checked()
    save(FIT / 'complete.json', result)
    print(json.dumps(result, indent=2), flush=True)


def seal():
    checked()
    result = json.loads((FIT / 'complete.json').read_text())
    directory = Path(result['directory'])
    assert (directory / 'ap_core.qsf').read_text() == source.BASE_QSF.read_text() + '\n' + source.ASSIGNMENT + '\n'
    report = directory / 'output_files/ap_core.fit.rpt'
    text = report.read_text(errors='replace')
    resources = {}
    for name, pattern in {
        'alms_needed': r';\s*ALMs needed \[=A-B\+C\]\s*;\s*([\d,]+)',
        'placed_alms': r';\s*\[A\] ALMs used in final placement \[=a\+b\+c\+d\]\s*;\s*([\d,]+)',
        'labs': r';\s*Total LABs:\s*partially or completely used\s*;\s*([\d,]+)',
        'm10ks': r';\s*M10K blocks\s*;\s*([\d,]+)',
        'dsps': r';\s*Total DSP Blocks\s*;\s*([\d,]+)',
    }.items():
        match = re.search(pattern, text)
        resources[name] = int(match.group(1).replace(',', '')) if match else None
    frequencies = {}
    if result['returncodes'].get('sta') == 0:
        text = (directory / 'output_files/ap_core.sta.rpt').read_text(errors='replace')
        for temperature in (0, 85):
            section = text.split(f'; Slow 1100mV {temperature}C Model Fmax Summary', 1)[1].split('Model Setup Summary', 1)[0]
            match = re.search(r';\s*([\d.]+) MHz\s*;[^\n]+ic\|mp_ram\|[^\n]+general\[0\]', section)
            assert match, temperature
            frequencies[str(temperature)] = float(match.group(1))
    successful = all(result['returncodes'].get(phase) == 0 for phase in ('map', 'fit', 'asm', 'sta'))
    slacks = result.get('worst', {})
    physical_pass = successful and len(frequencies) == 2 and min(frequencies.values()) > 90
    physical_pass = physical_pass and all(slacks.get(check, -1) > 0 for check in ('hold', 'recovery', 'removal', 'pulse_width'))
    data = dict(whole_core_fit_successful=result['returncodes'].get('fit') == 0,
        resources=resources, main_clock_slow_fmax_mhz=frequencies,
        exceeds_90mhz=len(frequencies) == 2 and min(frequencies.values()) > 90,
        comparative_physical_gate_pass=physical_pass,
        configured_mhz=100, all_configured_timing_checks_pass=result.get('all_checks_pass', False),
        worst_timing=slacks, returncodes=result['returncodes'], seconds=result['seconds'],
        seed=2, seed_sweep=False, deployment_ready=False, production_modified=False,
        source_prepared_sha256=sha(OUT / 'prepared.json'),
        fit_prepared_sha256=sha(FIT / 'prepared.json'))
    save(OUT / 'fit-result.json', data)
    (OUT / 'FIT-REPORT.txt').write_text(
        'Private v7 MAX_FANOUT 4 seed-2 result\n\n' + json.dumps(data, indent=2) +
        '\n\nThe comparative >90 MHz target is separate from configured 100 MHz closure.\n'
        'No source, saved seed, application package or SD file was changed.\n')
    evidence = [OUT / 'prepared.json', OUT / 'manifest.json', REVIEW,
                OUT / 'fit-result.json', OUT / 'FIT-REPORT.txt', FIT / 'prepared.json',
                FIT / 'recipe.py', FIT / 'complete.json', Path(__file__),
                directory / 'ap_core.qsf', report]
    for folder in (FIT, directory / 'output_files', directory / 'timing'):
        if not folder.exists():
            continue
        evidence += [p for p in folder.iterdir() if p.is_file() and
                     p.suffix in ('.json', '.log', '.rpt', '.tsv', '.rbf', '.sof')]
    save(OUT / 'fit-manifest.json', dict(deployment_ready=False,
        evidence_sha256={str(p.relative_to(ROOT)):sha(p) for p in sorted(set(evidence))}))
    print(json.dumps(data, indent=2))


if __name__ == '__main__':
    assert sys.argv[1:] in (['run'], ['seal'])
    {'run':run, 'seal':seal}[sys.argv[1]]()
