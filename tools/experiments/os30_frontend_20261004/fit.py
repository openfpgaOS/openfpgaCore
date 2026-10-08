#!/usr/bin/env python3
"""Fit the raw-attribute worker at os30's existing seed; no sweep."""
import argparse
import os
from pathlib import Path
import re
import shutil
import sys

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / 'docs/measurements/os30-frontend-20261004'
sys.path.insert(0, str(ROOT / 'tools'))
import sweep_target_matrix as fit


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('label')
    parser.add_argument('--rtl', type=Path, default=OUT/'rtl')
    parser.add_argument('--physical', action='store_true', help='Opt-in os30 physical optimization/retiming')
    args = parser.parse_args()
    assert re.fullmatch(r'[a-z0-9_-]+', args.label)
    original_freeze = fit.freeze

    def freeze(out, rows):
        frozen = original_freeze(out, rows)
        for name in ('gpu_geometry_worker.v','gpu_core.v','gpu_edge_walker.v','audio_mixer.v'):
            shutil.copy2(args.rtl/name, frozen/'src/fpga/common'/name)
        if args.physical:
            qsf=frozen/'src/fpga/targets/pocket/variants/os30.qsf'
            assert not qsf.exists(), 'Unexpected existing os30 fitter overrides'
            qsf.write_text((Path(__file__).parent/'physical.qsf').read_text())
        fit.save(out/'frontend-settings.json',dict(physical=args.physical))
        fit.save(out / 'frozen-sources.json', {
            str(p.relative_to(frozen)): fit.digest(p)
            for p in sorted(frozen.rglob('*')) if p.is_file()})
        return frozen

    fit.freeze = freeze
    original_inventory=fit.inventory
    def inventory(**kwargs):
        rows=original_inventory(**kwargs)
        for row in rows:
            if row['variant']=='os30':
                row['defs']+=' INCLUDE_GPU_VERTEX_ATTRIBUTES INCLUDE_MIXER_RAMP_PIPE'
        return rows
    fit.inventory=inventory
    os.environ['OS30_GEOMETRY_WORKER'] = '1'
    os.environ['OS30_DEPTH_PREFETCH'] = '1'
    sys.argv = [sys.argv[0], '--target', 'pocket', '--variant', 'os30',
                '--seed-offsets', '0', '--jobs', '1', '--processors', '4',
                '--output', str(OUT / ('fit-' + args.label))]
    fit.main()


if __name__ == '__main__':
    main()
