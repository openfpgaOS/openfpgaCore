#!/usr/bin/env python3
"""Seal the single exact-v7 seed-4 fit, including failures and 100-MHz limits."""
import json
import re
from pathlib import Path

import phase10_compact_v7_seed4_fit as candidate

ROOT, BASE, OUT, FIT = candidate.ROOT, candidate.BASE, candidate.OUT, candidate.FIT
sha, save = candidate.sha, candidate.save


def main():
    prep = candidate.checked_candidate()
    assert not (OUT / 'result.json').exists() and not (OUT / 'final-manifest.json').exists()
    assert (FIT / 'complete.json').exists()
    assert candidate.source_inventory(FIT / 'frozen') == prep['source_sha256']
    assert json.loads((FIT / 'frozen-sources.json').read_text()) == prep['source_sha256']
    assert json.loads((FIT / 'inventory.json').read_text()) == json.loads((BASE / 'fit-seed2/inventory.json').read_text())
    config = json.loads((FIT / 'config.json').read_text())
    assert config == dict(jobs=1, processors=4, seed_offsets=[2], seeds=None, variants=1, candidates=1)
    results = json.loads((FIT / 'results.json').read_text())
    assert len(results) == 1
    result = results[0]
    assert result['seed'] == 4 and result['variant'] == 'os30' and result['target'] == 'pocket'
    expected_directory = FIT / 'jobs/pocket-os30-s4/src/fpga/targets/pocket'
    directory = Path(result.get('directory', expected_directory))
    assert directory == expected_directory
    qsf = directory / 'ap_core.qsf'
    assert re.findall(r'^set_global_assignment -name SEED (\d+)$', qsf.read_text(), re.M) == ['4']
    parent_qsf = BASE / 'fit-seed2/jobs/pocket-os30-s2/src/fpga/targets/pocket/ap_core.qsf'
    parent_text = parent_qsf.read_text()
    assert parent_text.count('set_global_assignment -name SEED 2\n') == 1
    assert qsf.read_text() == parent_text.replace('set_global_assignment -name SEED 2\n',
                                                 'set_global_assignment -name SEED 4\n')
    report = directory / 'output_files/ap_core.fit.rpt'
    report_text = report.read_text(errors='replace') if report.exists() else ''

    def number(pattern):
        match = re.search(pattern, report_text)
        return int(match.group(1).replace(',', '')) if match else None

    resources = dict(
        alms_needed=number(r';\s*ALMs needed \[=A-B\+C\]\s*;\s*([\d,]+)'),
        placed_alms=number(r';\s*\[A\] ALMs used in final placement \[=a\+b\+c\+d\]\s*;\s*([\d,]+)'),
        labs=number(r';\s*Total LABs:\s*partially or completely used\s*;\s*([\d,]+)'),
        labs_required=number(r';\s*Logic Array Blocks\s*;\s*([\d,]+)'),
        m10ks=number(r';\s*M10K blocks\s*;\s*([\d,]+)'),
        dsps=number(r';\s*Total DSP Blocks\s*;\s*([\d,]+)'))
    codes = result['returncodes']
    fitted = codes.get('fit') == 0
    complete = all(codes.get(phase) == 0 for phase in ('map', 'fit', 'asm', 'sta'))
    frequencies = {}
    sta = directory / 'output_files/ap_core.sta.rpt'
    if complete and sta.exists():
        timing_text = sta.read_text(errors='replace')
        for temperature in (0, 85):
            title = f'; Slow 1100mV {temperature}C Model Fmax Summary'
            section = timing_text.split(title, 1)[1].split('Model Setup Summary', 1)[0]
            match = re.search(r';\s*([\d.]+) MHz\s*;[^\n]+ic\|mp_ram\|[^\n]+general\[0\]', section)
            assert match, temperature
            frequencies[str(temperature)] = float(match.group(1))
    worst = result.get('worst')
    exceeds = len(frequencies) == 2 and min(frequencies.values()) > 90
    positive_other_checks = bool(worst) and all(
        isinstance(worst.get(check), (int, float)) and worst[check] > 0
        for check in ('hold', 'recovery', 'removal', 'pulse_width'))
    data = dict(
        whole_core_fit_successful=complete, fitter_succeeded=fitted, resources=resources,
        main_clock_slow_fmax_mhz=frequencies, exceeds_90mhz=exceeds,
        meets_requested_comparative_timing=complete and exceeds and positive_other_checks,
        configured_mhz=100, all_configured_timing_checks_pass=result.get('all_checks_pass', False),
        worst_timing=worst, seed=4, effective_seed=4, saved_seed=2, parent_seed=2,
        seed_sweep=False, exactly_one_fit=True, authorized_effective_seeds=[3, 4],
        maximum_new_fits=2, further_seed_trials_authorized=False,
        full_frozen_source_closure_identical=True,
        generated_qsf_only_difference='Effective SEED 2 -> 4', returncodes=codes,
        private_only=True, deployment_ready=False,
        normal_build_seed4_closure_required_before_promotion=True,
        whole_core_native_game_measured=False, pocket_fps_measured=False,
        prepared_sha256=sha(OUT / 'prepared.json'),
        fit_report_sha256=sha(report) if report.exists() else None)
    save(OUT / 'result.json', data)
    lines = [
        'Private compact-v7: single seed-4 alternate placement', '',
        f'Complete map/fit/assembly/STA success: {complete}; return codes: {codes}.',
        f'Resources: {resources}.',
        'All frozen source bytes match compact-v7. Effective job seed is 4; saved/source seed remains 2.',
        'Exactly one AREA fit; CPU SPEED override and other physical settings unchanged. No seed sweep.',
        'Only two newly authorized trials, seeds 3 and 4; no further seed trials authorized.',
        'Configured clock remains 100 MHz.',
        f'Main-clock slow-corner Fmax estimates: {frequencies or "unavailable"}.',
        f'Both slow corners exceed 90 MHz: {exceeds}. Other timing checks positive: {positive_other_checks}.',
        f'All configured 100-MHz timing checks pass: {data["all_configured_timing_checks_pass"]}.',
        f'Worst timing slacks: {worst}.',
        'Existing component/native/coupled checks are inherited only through independent exact-source binding.',
        'No new native-game measurement or Pocket FPS claim is made. The 25-FPS goal remains unproven.',
        'This result alone is not deployment-ready: normal build must reproduce effective seed 4 first.',
        'No production, sibling application, saved-seed, configured-clock or SD file was changed.'
    ]
    with (OUT / 'REPORT.txt').open('x') as stream:
        stream.write('\n'.join(lines) + '\n')
    evidence = {str(p.relative_to(ROOT)): sha(p) for p in OUT.iterdir()
                if p.is_file() and p.name != 'final-manifest.json'}
    for p in (FIT / 'results').rglob('*'):
        if p.is_file():
            evidence[str(p.relative_to(ROOT))] = sha(p)
    for p in (qsf, report, sta, FIT / 'complete.json', FIT / 'results.json',
              FIT / 'inventory.json', FIT / 'config.json', FIT / 'experiment.json',
              FIT / 'frozen-sources.json', Path(__file__)):
        if p.exists():
            evidence[str(p.relative_to(ROOT))] = sha(p)
    save(OUT / 'final-manifest.json', dict(
        whole_core_fit_successful=complete, deployment_ready=False, effective_seed=4,
        saved_seed=2, seed_sweep=False, exactly_one_fit=True,
        authorized_effective_seeds=[3, 4], maximum_new_fits=2,
        further_seed_trials_authorized=False, evidence_sha256=evidence))
    print(json.dumps(data, indent=2))


if __name__ == '__main__':
    main()
