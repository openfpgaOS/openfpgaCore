#!/usr/bin/env python3
"""Fit a private os30 A with CPU fairness threshold2, retaining A's seed2."""
import copy
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'tools'))
import sweep_target_matrix as fit

BASE = ROOT / 'docs/measurements/os30-dual-20261004/fit-single128-v1'
OUT = ROOT / 'docs/measurements/os30-fps25-20261005/phase3/arbiter-fit-seed2'
ARBITER = 'src/fpga/common/axi_sdram_arbiter.v'
CPU = 'src/fpga/vendor/vexriscv/VexiiRiscv/VexiiRiscv_os30.v'
CPU_A = '33895e9a2fc141d8da49bb75e082e06964a49e328b6d87363516ac6f9ab26119'


def inventory(**kwargs):
    rows = json.loads((BASE / 'inventory.json').read_text())
    assert len(rows) == 1
    row = rows[0]
    assert (row['target'], row['variant'], row['seed'], row['cpu_sha256']) == (
        'pocket', 'os30', 2, CPU_A)
    return copy.deepcopy(rows)


def freeze(out, rows):
    expected = json.loads((BASE / 'frozen-sources.json').read_text())
    for path, digest in expected.items():
        assert fit.digest(BASE / 'frozen' / path) == digest, path
    assert expected[CPU] == CPU_A
    dest = out / 'frozen'
    shutil.copytree(BASE / 'frozen', dest)
    source = (dest / ARBITER).read_text()
    old = "parameter [3:0] CPU_FAIR_THRESHOLD   = 4'd8,"
    new = "parameter [3:0] CPU_FAIR_THRESHOLD   = 4'd2,"
    assert source.count(old) == 1
    (dest / ARBITER).write_text(source.replace(old, new))
    actual = {str(p.relative_to(dest)): fit.digest(p)
              for p in dest.rglob('*') if p.is_file()}
    assert set(expected) == set(actual)
    changed = [p for p in expected if expected[p] != actual[p]]
    assert changed == [ARBITER]
    fit.save(out / 'inventory.json', rows)
    fit.save(out / 'frozen-sources.json', actual)
    fit.save(out / 'experiment.json', dict(
        scope=__doc__, seed=2, seed_sweep=False, configured_mhz=100,
        production_modified=False, only_changed_file=ARBITER,
        before_sha256=expected[ARBITER], after_sha256=actual[ARBITER],
        original_manifest_sha256=fit.digest(BASE / 'frozen-sources.json'),
        cpu_sha256=CPU_A, CPU_FAIR_THRESHOLD_before=8, CPU_FAIR_THRESHOLD_after=2,
        limitations=['Private implementation feasibility only; no Pocket FPS claim.',
                     'Fmax estimate above90 MHz would not close100 MHz setup.',
                     'No automatic publication or deployment.']))
    return dest


if __name__ == '__main__':
    assert sys.argv[1:] == ['run'], 'Use run for one private saved-seed fit.'
    fit.inventory = inventory
    fit.freeze = freeze
    sys.argv = [sys.argv[0], '--target', 'pocket', '--variant', 'os30',
                '--seed-offsets', '0', '--jobs', '1', '--processors', '4',
                '--output', str(OUT)]
    fit.main()
