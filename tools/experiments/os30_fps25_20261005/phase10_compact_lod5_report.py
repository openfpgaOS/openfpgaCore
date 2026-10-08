#!/usr/bin/env python3
"""Seal one completed private compact-LOD5 fit without overstating timing."""
import argparse
import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
P = ROOT / 'docs/measurements/os30-fps25-20261005/phase10'
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()


def save(p, value):
    with p.open('x') as f:
        json.dump(value, f, indent=2)
        f.write('\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('label')
    args = parser.parse_args()
    assert re.fullmatch(r'compact-lod5-core-v[0-9]+', args.label)
    out = P / args.label
    fit = out / 'fit-seed2'
    assert (fit / 'complete.json').exists()
    prep = json.loads((out / 'prepared.json').read_text())
    assert prep['seed'] == 2 and not prep['seed_sweep']
    for n, h in prep['source_sha256'].items():
        assert sha(out / 'frozen' / n) == sha(fit / 'frozen' / n) == h, n
    results = json.loads((fit / 'results.json').read_text())
    assert len(results) == 1
    result = results[0]
    assert result['seed'] == 2 and result['variant'] == 'os30'
    directory = Path(result['directory'])
    qsf = directory / 'ap_core.qsf'
    assert re.findall(r'^set_global_assignment -name SEED (\d+)$', qsf.read_text(), re.M) == ['2']
    report = directory / 'output_files/ap_core.fit.rpt'
    text = report.read_text(errors='replace')

    def number(pattern):
        return int(re.search(pattern, text).group(1).replace(',', ''))

    resources = dict(
        alms_needed=number(r';\s*ALMs needed \[=A-B\+C\]\s*;\s*([\d,]+)'),
        placed_alms=number(r';\s*\[A\] ALMs used in final placement \[=a\+b\+c\+d\]\s*;\s*([\d,]+)'),
        labs=number(r';\s*Total LABs:\s*partially or completely used\s*;\s*([\d,]+)'),
        m10ks=number(r';\s*M10K blocks\s*;\s*([\d,]+)'),
        dsps=number(r';\s*Total DSP Blocks\s*;\s*([\d,]+)'))
    fitted = result['returncodes'].get('fit') == 0
    frequencies = {}
    sta = directory / 'output_files/ap_core.sta.rpt'
    if fitted and sta.exists():
        timing_text = sta.read_text(errors='replace')
        for temperature in (0, 85):
            title = f'; Slow 1100mV {temperature}C Model Fmax Summary'
            section = timing_text.split(title, 1)[1].split('Model Setup Summary', 1)[0]
            match = re.search(r';\s*([\d.]+) MHz\s*;[^\n]+ic\|mp_ram\|[^\n]+general\[0\]', section)
            assert match, temperature
            frequencies[str(temperature)] = float(match.group(1))
    data = dict(whole_core_fit_successful=fitted, resources=resources,
        main_clock_slow_fmax_mhz=frequencies,
        exceeds_90mhz=bool(frequencies) and min(frequencies.values()) > 90,
        configured_mhz=100, all_configured_timing_checks_pass=result.get('all_checks_pass'),
        worst_timing=result.get('worst'), seed=2, seed_sweep=False,
        returncodes=result['returncodes'], private_only=True, deployment_ready=False,
        whole_core_native_game_measured=False, pocket_fps_measured=False,
        prepared_sha256=sha(out / 'prepared.json'), fit_report_sha256=sha(report))
    save(out / 'result.json', data)
    lines = [f'Private {args.label}: ' + ('fit passes' if fitted else 'fit fails'), '',
        f"Resources: {resources['alms_needed']} needed ALMs, {resources['placed_alms']} placed ALMs, "
        f"{resources['labs']}/1848 LABs, {resources['m10ks']}/308 M10Ks, {resources['dsps']}/66 DSPs.",
        'One seed-2 AREA build. Configured clock remains 100 MHz.',
        f'Main-clock slow-corner Fmax estimates: {frequencies or "unavailable"}.',
        f'All configured timing checks pass: {result.get("all_checks_pass", "unavailable")}.',
        'The native component and coupled modeled-CPU checks are bound by independent-review.json.',
        'Those checks do not establish full native-game performance or Pocket FPS.',
        'No production, sibling application, saved seed, clock or SD files were changed.',
        'This is not a deployment recommendation.']
    (out / 'REPORT.txt').write_text('\n'.join(lines) + '\n')
    evidence = {}
    for p in out.iterdir():
        if p.is_file() and p.name != 'final-manifest.json':
            evidence[str(p.relative_to(ROOT))] = sha(p)
    for p in (fit / 'results').rglob('*'):
        if p.is_file():
            evidence[str(p.relative_to(ROOT))] = sha(p)
    for p in (qsf, report, fit / 'results.json', fit / 'inventory.json',
              fit / 'frozen-sources.json', Path(__file__)):
        evidence[str(p.relative_to(ROOT))] = sha(p)
    save(out / 'final-manifest.json', dict(whole_core_fit_successful=fitted,
        deployment_ready=False, evidence_sha256=evidence))
    print(json.dumps(data, indent=2))


if __name__ == '__main__':
    main()
