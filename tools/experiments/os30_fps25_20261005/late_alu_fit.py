#!/usr/bin/env python3
"""One private A-seed-2 full os30 fit, changing only the CPU RTL."""
import copy
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT/'tools'))
import sweep_target_matrix as fit

BASE = ROOT/'docs/measurements/os30-dual-20261004/fit-single128-v1'
OUT = ROOT/'docs/measurements/os30-fps25-20261005/late-alu-v1/fit-A-seed2'
CPU = OUT.parent/'VexiiRiscv_late-alu.v'
CPU_REL = 'src/fpga/vendor/vexriscv/VexiiRiscv/VexiiRiscv_os30.v'


def inventory(**kwargs):
    rows = json.loads((BASE/'inventory.json').read_text())
    assert len(rows) == 1 and rows[0]['target'] == 'pocket' and rows[0]['variant'] == 'os30'
    assert rows[0]['seed'] == 2
    return copy.deepcopy(rows)


def freeze(out, rows):
    expected = json.loads((BASE/'frozen-sources.json').read_text())
    for path, digest in expected.items():
        assert fit.digest(BASE/'frozen'/path) == digest, path
    assert fit.digest(CPU) == '16a9418fe09fb1192a4de7fda07be917096851a6197b5ccf4b97516b61f181cb'
    dest = out/'frozen'
    shutil.copytree(BASE/'frozen', dest)
    shutil.copy2(CPU, dest/CPU_REL)
    actual = {str(p.relative_to(dest)):fit.digest(p) for p in dest.rglob('*') if p.is_file()}
    changed = [p for p in expected if expected[p] != actual[p]]
    assert set(expected) == set(actual) and changed == [CPU_REL], changed
    rows[0]['cpu_sha256'] = fit.digest(CPU)
    rows[0]['private_cpu_source'] = str(CPU)
    fit.save(out/'inventory.json', rows)
    fit.save(out/'frozen-sources.json', actual)
    fit.save(out/'experiment.json', dict(scope=__doc__, seed=2, seed_origin='Frozen A fit',
        configured_mhz=100, production_modified=False, only_changed_file=CPU_REL,
        before_cpu_sha256=expected[CPU_REL], after_cpu_sha256=actual[CPU_REL],
        non_cpu_inputs_exact_A=True, source_manifest_sha256=fit.digest(BASE/'frozen-sources.json'),
        acceptance='At least >90 MHz clock estimate, full timing review; hardware performance remains unqualified.',
        performance_gate='Exploratory only: mixed component results include 8.3% slower stride-address loops.',
        seed_sweep=False))
    return dest


if __name__ == '__main__':
    assert sys.argv[1:] == ['run'], 'Use run only for the authorized single fit.'
    fit.inventory = inventory
    fit.freeze = freeze
    sys.argv = [sys.argv[0], '--target', 'pocket', '--variant', 'os30', '--seed-offsets', '0',
                '--jobs', '1', '--processors', '4', '--output', str(OUT)]
    fit.main()
