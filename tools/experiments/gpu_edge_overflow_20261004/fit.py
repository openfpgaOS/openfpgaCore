#!/usr/bin/env python3
"""Fit one private edge-walker candidate at os30's existing seed; no sweep."""
import argparse
import os
from pathlib import Path
import re
import shutil
import sys

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / 'docs/measurements/gpu-edge-overflow-20261004'
sys.path.insert(0, str(ROOT / 'tools'))
import sweep_target_matrix as fit


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('label')
    parser.add_argument('--rtl', type=Path, required=True)
    args = parser.parse_args()
    assert re.fullmatch(r'[a-z0-9_-]+', args.label)
    original_freeze = fit.freeze

    def freeze(out, rows):
        frozen = original_freeze(out, rows)
        dest = frozen / 'src/fpga/common/gpu_edge_walker.v'
        shutil.copy2(args.rtl, dest)
        fit.save(out / 'frozen-sources.json', {
            str(p.relative_to(frozen)): fit.digest(p)
            for p in sorted(frozen.rglob('*')) if p.is_file()})
        return frozen

    fit.freeze = freeze
    os.environ['OS30_GEOMETRY_WORKER'] = '1'
    os.environ['OS30_DEPTH_PREFETCH'] = '1'
    sys.argv = [sys.argv[0], '--target', 'pocket', '--variant', 'os30',
                '--seed-offsets', '0', '--jobs', '1', '--processors', '4',
                '--output', str(OUT / ('fit-' + args.label))]
    fit.main()


if __name__ == '__main__':
    main()
