#!/usr/bin/env python3
"""Audit completed os20/os25 fits with the retained strict four-corner policy."""
import argparse
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'tools'))
from sweep_target_matrix import docker, verify_frozen


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    out = args.output.resolve()
    if not (out / 'complete.json').is_file():
        raise ValueError('Fit driver has not completed')
    verify_frozen(out)
    recipes = ROOT / 'tools/experiments/quake2_tiny_audio_timing_20261013/recipes'
    analysis = out / 'qualification'
    analysis.mkdir(exist_ok=False)
    for name in ['results.py', 'report_sweep_timing.tcl']:
        shutil.copyfile(recipes / name, analysis / name)
    spec = importlib.util.spec_from_file_location('strict_timing', analysis / 'results.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    results = []
    for fit in json.loads((out / 'results.json').read_text()):
        record = dict(variant=fit['variant'], seed=fit['seed'], fit_valid=fit['valid'])
        directory = Path(fit['directory'])
        if fit['valid']:
            timing = analysis / ('seed-' + str(fit['seed']))
            timing.mkdir()
            command = docker(out, 'pocket', directory, ['quartus_sta', '-t',
                str(analysis / 'report_sweep_timing.tcl'), 'ap_core', str(timing)])
            with (timing / 'driver.log').open('w') as log:
                run = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, timeout=1800)
            record['sta_returncode'] = run.returncode
            try:
                record.update(module.parse(directory, timing))
                if run.returncode not in (0, 3):
                    raise ValueError('Unexpected timing tool failure: ' + str(run.returncode))
                if run.returncode == 3 and 'Whole-design timing failed' not in (timing / 'driver.log').read_text():
                    raise ValueError('Timing tool failed before its final verdict')
                record['valid_reports'] = True
            except Exception as error:
                record.update(valid_reports=False, error=str(error))
        results.append(record)
    verify_frozen(out)
    (analysis / 'results.json').write_text(json.dumps(results, indent=2) + '\n')
    for record in results:
        print(json.dumps({k: record[k] for k in ['variant', 'seed', 'valid_reports',
            'minimum_main_restricted_fmax_mhz', 'eligible_above_90_mhz_estimate',
            'worst_slack_ns', 'resources', 'error'] if k in record}), flush=True)
    if not all(r.get('valid_reports', False) for r in results):
        sys.exit(1)


if __name__ == '__main__':
    main()
