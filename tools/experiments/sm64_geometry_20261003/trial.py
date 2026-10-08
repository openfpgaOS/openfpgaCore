#!/usr/bin/env python3
"""Isolated os30 geometry acceptance or saved-seed fit; no seed sweep."""
import argparse
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / 'docs/measurements/sm64-geometry-20261003'
sys.path.insert(0, str(ROOT / 'tools'))

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('stage', choices=['fit', 'accept'])
p.add_argument('label')
a = p.parse_args()
os.environ['OS30_GEOMETRY'] = '1'
sys.argv = [sys.argv[0], '--variant', 'os30', '--jobs', '1',
            '--output', str(OUT / (a.stage + '-' + a.label))]
if a.stage == 'fit':
    import sweep_target_matrix as runner
    sys.argv += ['--target', 'pocket', '--seed-offsets', '0', '--processors', '4']
else:
    import check_gpu_target_matrix as runner
runner.main()
