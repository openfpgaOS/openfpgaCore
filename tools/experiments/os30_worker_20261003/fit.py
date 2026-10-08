#!/usr/bin/env python3
"""Saved-seed worker budget experiments; never publishes or sweeps seeds."""
import argparse
import copy
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / 'docs/measurements/os30-worker-20261003'
sys.path.insert(0, str(ROOT / 'tools'))
import sweep_target_matrix as fit


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('candidate', choices=['dc64', 'dc64_queues', 'dc64_queues_shifts', 'worker'])
    p.add_argument('--label')
    p.add_argument('--dc128',action='store_true')
    p.add_argument('--mlab-queues',action='store_true')
    a = p.parse_args()
    os.environ['OS30_GEOMETRY'] = '1'
    os.environ['OS30_DEPTH_PREFETCH'] = '1'
    os.environ['OS30_GEOMETRY_WORKER'] = '0'
    row = next(r for r in fit.inventory() if r['variant'] == 'os30')
    assert row['seed'] == 2
    row['original_defs'] = row['defs']
    if a.candidate != 'dc64' and not a.mlab_queues:
        row['defs'] = ' '.join(x for x in row['defs'].split() if x != 'INCLUDE_TEX_QUEUE_MLAB')
    if a.candidate == 'worker':
        row['defs'] += ' EXCLUDE_GPU_XFORM_MAC INCLUDE_GPU_GEOMETRY_WORKER'
    row['experiment'] = a.candidate
    fit.inventory = lambda **kwargs: [copy.deepcopy(row)]
    original_freeze = fit.freeze

    def freeze(out, rows):
        frozen = original_freeze(out, rows)
        cpu = ROOT / ('src/fpga/vendor/vexriscv/VexiiRiscv/VexiiRiscv_os30'
                      + ('' if a.dc128 else '_worker') + '.v')
        dest = frozen / 'src/fpga/vendor/vexriscv/VexiiRiscv/VexiiRiscv_os30.v'
        text = cpu.read_text()
        if a.candidate == 'dc64_queues_shifts':
            marker = '(* altera_attribute = "-name AUTO_SHIFT_REGISTER_RECOGNITION OFF" *)\n'
            assert text.count(marker) == 1
            text = text.replace(marker, '')
        dest.write_text(text)
        rows[0]['cpu_sha256'] = fit.digest(dest)
        rows[0]['generated_cpu_sha256'] = fit.digest(cpu)
        fit.save(out / 'inventory.json', rows)
        fit.save(out / 'frozen-sources.json', {
            str(p.relative_to(frozen)): fit.digest(p)
            for p in sorted(frozen.rglob('*')) if p.is_file()})
        return frozen

    fit.freeze = freeze
    sys.argv = [sys.argv[0], '--variant', 'os30', '--target', 'pocket',
                '--seed-offsets', '0', '--processors', '4', '--jobs', '1',
                '--output', str(OUT / ('fit-' + (a.label or a.candidate)))]
    fit.main()


if __name__ == '__main__':
    main()
