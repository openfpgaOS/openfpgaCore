#!/usr/bin/env python3
"""Cross-check the completed bounded selection against actual reports; no STA."""
import hashlib
import json
from pathlib import Path
import re
import shutil

ROOT = Path(__file__).resolve().parents[3]
P = ROOT / 'docs/measurements/os30-fps25-20261005/phase10'
OUT = P / 'bounded-v7-selection-check-v1'
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
read = lambda p: json.loads(p.read_text())


def main():
    assert not OUT.exists()
    evidence, checked, comparisons = {}, 0, []
    selection_dir = P / 'bounded-v7-placement-selection-v1'
    selection = read(selection_dir / 'result.json')
    sm = read(selection_dir / 'manifest.json')
    for base, values in ((selection_dir, sm['files']), (ROOT, sm['evidence_sha256'])):
        for name, digest in values.items():
            assert sha(base / name) == digest, name
            checked += 1
    for seed in (3, 4):
        stage = P / f'compact-lod5-v7-seed{seed}-v1'
        manifest = read(stage / 'final-manifest.json')
        for name, digest in manifest['evidence_sha256'].items():
            assert sha(ROOT / name) == digest, name
            checked += 1
        result = read(stage / 'result.json')
        assert result['prepared_sha256'] == sha(stage / 'prepared.json')
        actual = read(stage / f'fit-seed{seed}/results.json')
        assert len(actual) == 1 and actual[0]['seed'] == seed
        assert actual[0]['returncodes'] == result['returncodes']
        assert actual[0]['worst'] == result['worst_timing']
        job = Path(actual[0]['directory'])
        qsf = job / 'ap_core.qsf'
        parent_qsf = P / 'compact-lod5-core-v7/fit-seed2/jobs/pocket-os30-s2/src/fpga/targets/pocket/ap_core.qsf'
        assert qsf.read_text() == parent_qsf.read_text().replace('set_global_assignment -name SEED 2\n', f'set_global_assignment -name SEED {seed}\n')
        sta = job / 'output_files/ap_core.sta.rpt'
        frequencies = {}
        for temperature in (0, 85):
            part = sta.read_text().split(f'; Slow 1100mV {temperature}C Model Fmax Summary', 1)[1].split('Model Setup Summary', 1)[0]
            match = re.search(r';\s*([\d.]+) MHz\s*;[^\n]+ic\|mp_ram\|[^\n]+general\[0\]', part)
            assert match
            frequencies[str(temperature)] = float(match[1])
        assert frequencies == result['main_clock_slow_fmax_mhz']
        qualified = all(result['returncodes'].get(k) == 0 for k in ('map', 'fit', 'asm', 'sta'))
        qualified = qualified and min(frequencies.values()) > 90
        qualified = qualified and all(result['worst_timing'][k] > 0 for k in ('hold', 'recovery', 'removal', 'pulse_width'))
        assert not qualified and not result['meets_requested_comparative_timing']
        row = next(r for r in selection['placements'] if r['seed'] == seed)
        assert row['qualifies'] == qualified
        assert row['main_clock_slow_fmax_mhz'] == frequencies
        assert row['minimum_slow_fmax_mhz'] == min(frequencies.values())
        assert row['resources'] == result['resources'] and row['worst_timing'] == result['worst_timing']
        comparisons.append(dict(seed=seed, frequencies=frequencies, qualifies=False,
                                all_configured_checks_pass=result['all_configured_timing_checks_pass']))
        for path in (stage / 'final-manifest.json', stage / 'result.json', stage / 'prepared.json',
                     stage / f'fit-seed{seed}/results.json', sta, qsf):
            evidence[str(path.relative_to(ROOT))] = sha(path)
    assert selection['selected_seed'] is None
    assert not selection['selected_for_normal_build_qualification']
    assert not selection['further_seed_trials_selected'] and not selection['deployment_ready']
    for path in (selection_dir / 'manifest.json', selection_dir / 'result.json',
                 P / 'bounded-v7-selection-review-v1/manifest.json', Path(__file__)):
        evidence[str(path.relative_to(ROOT))] = sha(path)
    OUT.mkdir()
    shutil.copyfile(__file__, OUT / 'recipe.py')
    result = dict(passed_independent_selection_check=True, selected_seed=None,
                  comparisons=comparisons, rehashed_files=checked,
                  further_seed_trials_authorized=False, deployment_ready=False,
                  sta_or_fit_invoked=False, source_or_device_writes=0,
                  evidence_sha256=evidence)
    (OUT / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
    (OUT / 'REPORT.txt').write_text('''Independent bounded selection check: PASS; no candidate selected.

Actual STA reports confirm seed 3 at 88.73 MHz hot / 89.20 MHz cold and
seed 4 at 81.90 / 84.02 MHz. Neither passes the strict both-corners >90 MHz
condition. Other positive timing checks cannot compensate for this failure.
Both also fail configured 100 MHz setup closure. The recorded null selection,
retained failed results and no-further-seed decision are correct.

Both final manifests, actual return codes/slacks, source-preparation hashes,
selected QSFs and raw STA frequency tables were independently rechecked.
This check read existing reports only; no STA, fit, source change, selection
rerun, promotion or device write occurred.
''')
    (OUT / 'manifest.json').write_text(json.dumps(dict(
        files={p.name: sha(p) for p in OUT.iterdir() if p.is_file()},
        evidence_sha256=evidence), indent=2) + '\n')
    print(json.dumps(dict(manifest_sha256=sha(OUT / 'manifest.json'), checked_files=checked)))


if __name__ == '__main__':
    main()
