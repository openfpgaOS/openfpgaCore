#!/usr/bin/env python3
"""Assess exactly the two completed v7 placements; never build or publish."""
import hashlib
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[3]
P = ROOT / 'docs/measurements/os30-fps25-20261005/phase10'
OUT = P / 'bounded-v7-placement-selection-v1'
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()


def main():
    records = []
    evidence = {}
    for seed in (3, 4):
        stage = P / f'compact-lod5-v7-seed{seed}-v1'
        final = json.loads((stage / 'final-manifest.json').read_text())
        for required in (stage / 'result.json', stage / 'prepared.json',
                         stage / f'fit-seed{seed}/complete.json'):
            assert str(required.relative_to(ROOT)) in final['evidence_sha256'], required
        for name, digest in final['evidence_sha256'].items():
            assert sha(ROOT / name) == digest, name
        result = json.loads((stage / 'result.json').read_text())
        assert result['prepared_sha256'] == sha(stage / 'prepared.json')
        assert result['effective_seed'] == result['seed'] == seed
        assert result['saved_seed'] == result['parent_seed'] == 2
        assert result['authorized_effective_seeds'] == [3, 4]
        assert result['maximum_new_fits'] == 2
        assert not result['further_seed_trials_authorized']
        assert result['exactly_one_fit'] and not result['seed_sweep']
        assert result['full_frozen_source_closure_identical']
        assert result['configured_mhz'] == 100
        fmax = result['main_clock_slow_fmax_mhz']
        complete = all(result['returncodes'].get(k) == 0 for k in ('map', 'fit', 'asm', 'sta'))
        qualifying = complete and set(fmax) == {'0', '85'} and min(fmax.values()) > 90
        qualifying = qualifying and all(result['worst_timing'][k] > 0
            for k in ('hold', 'recovery', 'removal', 'pulse_width'))
        assert bool(qualifying) == result['meets_requested_comparative_timing']
        records.append(dict(seed=seed, qualifies=bool(qualifying),
            main_clock_slow_fmax_mhz=fmax,
            minimum_slow_fmax_mhz=min(fmax.values()) if fmax else None,
            resources=result['resources'], worst_timing=result['worst_timing'],
            configured_100mhz_timing_passed=result['all_configured_timing_checks_pass']))
        for path in (stage / 'result.json', stage / 'final-manifest.json', stage / 'prepared.json',
                     stage / 'independent-review.json'):
            evidence[str(path.relative_to(ROOT))] = sha(path)
    passing = [r for r in records if r['qualifies']]
    # Select strongest worst-corner estimate; deterministic lower-seed tie break.
    selected = max(passing, key=lambda r: (r['minimum_slow_fmax_mhz'], -r['seed'])) if passing else None
    OUT.mkdir(exist_ok=False)
    shutil.copyfile(__file__, OUT / 'recipe.py')
    data = dict(passed_assessment=True, placements=records,
        selected_seed=selected['seed'] if selected else None,
        selected_for_normal_build_qualification=bool(selected),
        promotion_performed=False, deployment_ready=False,
        full_seed_sweep_performed=False, further_seed_trials_selected=False,
        configured_mhz=100, saved_production_seed_modified=False,
        selection_rule='Both slow corners >90 MHz and positive hold/recovery/removal/pulse; maximize minimum Fmax.',
        limits=['Normal-build source, selected seed and saved fingerprint must be qualified before promotion.',
                'Comparative >90 MHz does not establish setup closure at configured 100 MHz.',
                'No native full-game or Pocket FPS measurement is added by placement.'],
        evidence_sha256=evidence)
    with (OUT / 'result.json').open('x') as stream:
        json.dump(data, stream, indent=2)
        stream.write('\n')
    lines = ['Bounded unchanged-v7 placement comparison', '']
    for row in records:
        lines.append(f"Seed {row['seed']}: slow Fmax {row['main_clock_slow_fmax_mhz']} MHz; "
                     f"qualifies for the comparative target: {row['qualifies']}.")
    lines += ['', f"Selected seed for normal-build qualification: {data['selected_seed']}.",
              'Both completed results are retained. No further seed trial is selected.',
              'Configured clock remains 100 MHz; this is not a Pocket FPS result.',
              'No source, saved seed, package or SD file was changed by this assessment.']
    (OUT / 'REPORT.txt').write_text('\n'.join(lines) + '\n')
    with (OUT / 'manifest.json').open('x') as stream:
        json.dump(dict(files={p.name: sha(p) for p in sorted(OUT.iterdir())
                              if p.is_file() and p.name != 'manifest.json'},
                       evidence_sha256=evidence), stream, indent=2)
        stream.write('\n')
    print(json.dumps(data, indent=2))


if __name__ == '__main__':
    main()
