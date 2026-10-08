#!/usr/bin/env python3
"""Run the grouped STA audit on an already completed private whole-core fit."""
from pathlib import Path
import argparse
import hashlib
import json
import re
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[3]
CAMPAIGN = ROOT / 'docs/measurements/os30-dual-20261004'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('label')
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--hold', action='store_true', help='Report top hold paths at all corners')
    modes.add_argument('--frontend', action='store_true', help='Report RAW capture and source-restricted frontend paths')
    modes.add_argument('--pingpong', action='store_true', help='Report rotating decode-bank capture and output mux paths')
    modes.add_argument('--lsuhit', action='store_true', help='Report registered D-cache way-hit input margin')
    modes.add_argument('--mmio', action='store_true', help='Inspect measured MMIO mux predecessor and source nodes')
    modes.add_argument('--texture', action='store_true', help='Report texture RAM address and decode-bank capture margins')
    modes.add_argument('--threshold', action='store_true', help='Census register endpoints below the 90MHz-equivalent setup threshold')
    args = parser.parse_args()
    if not re.fullmatch('[a-z0-9_-]+', args.label):
        parser.error('Invalid fit label')
    fit = CAMPAIGN / ('fit-' + args.label)
    if not (fit / 'results.json').is_file():
        parser.error('The normal whole-core fit and STA flow must finish first')
    results = json.loads((fit / 'results.json').read_text())
    assert len(results) == 1 and results[0]['variant'] == 'os30'
    job = Path(results[0]['directory'])
    report = job / 'output_files/ap_core.fit.rpt'
    if 'Fitter Status : Successful' not in (job / 'output_files/ap_core.fit.summary').read_text():
        parser.error('A completed successful fitted database is required')
    suffix = '-hold' if args.hold else '-frontend' if args.frontend else '-pingpong' if args.pingpong else '-lsuhit' if args.lsuhit else '-mmio-v2' if args.mmio else '-texture' if args.texture else '-threshold' if args.threshold else ''
    out = CAMPAIGN / ('timing-audit-' + args.label + suffix)
    out.mkdir(exist_ok=False)
    tcl = out / 'audit_timing.tcl'
    template = ('audit_hold.tcl' if args.hold else 'audit_frontend.tcl' if args.frontend
                else 'audit_pingpong.tcl' if args.pingpong else 'audit_lsu_hit.tcl' if args.lsuhit else 'audit_mmio.tcl' if args.mmio else 'audit_texture.tcl' if args.texture else 'audit_threshold.tcl' if args.threshold else 'audit_timing.tcl')
    shutil.copy2(Path(__file__).with_name(template), tcl)
    old = json.loads((CAMPAIGN / 'timing-audit-fullcache-v1/manifest.json').read_text())
    command = old['command'][:]
    command[command.index('--name') + 1] = 'os30-timing-audit-' + args.label + suffix
    command[command.index('-w') + 1] = str(job)
    command[command.index('-t') + 1] = str(tcl)
    command[-1] = str(out)
    inputs = [tcl, job / 'ap_core.qsf', job / 'core_constraints.sdc', report,
              fit / 'frozen-sources.json', fit / 'experiment.json']
    hashes = {str(p): digest(p) for p in inputs}
    ignored = sorted(set(name for name in re.findall(
        r'Warning \(15706\): Node "([^"]+)"', report.read_text(errors='replace'))
        if 'isr0|' in name and any(bus in name for bus in ('burst_data[', 'word_q['))))
    manifest = dict(scope='STA only; unchanged frozen sources and timing constraints',
                    command=command, inputs=hashes,
                    experiment=json.loads((fit / 'experiment.json').read_text()),
                    ignored_receive_location_nodes=ignored,
                    location_applicability_requires_postfit_coordinate_check=True)
    (out / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    with (out / 'run.log').open('w') as log:
        result = subprocess.run(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
    unchanged = all(digest(Path(p)) == value for p, value in hashes.items())
    manifest.update(exit_code=result.returncode, input_hashes_unchanged=unchanged)
    (out / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    if result.returncode or not unchanged:
        raise SystemExit('STA failed or recorded inputs changed; inspect ' + str(out))
    print((out / 'summary.tsv').read_text())


if __name__ == '__main__':
    main()
