#!/usr/bin/env python3
"""Build F8 from reviewed private inputs without touching a release package."""
import argparse
import importlib.util
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / 'docs/measurements/os30-fps25-20261005/phase6'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['normal', 'diagnostic'])
    args = parser.parse_args()
    baseline = json.loads((OUT / 'baseline.json').read_text())
    spec = importlib.util.spec_from_file_location('f8_builder', Path(__file__).with_name('build.py'))
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    builder.OUT, builder.STAGE, builder.INPUTS = OUT, OUT / 'SM64', baseline['inputs']
    diagnostic = args.mode == 'diagnostic'
    elf = OUT / 'candidate' / ('diagnostic.elf' if diagnostic else 'app.elf')
    assert not elf.exists(), 'Preserve an existing completed candidate build'
    # Copied dependency files can name the old staging tree. Rebuild every
    # object in this private mode directory to avoid a stale header dependency.
    objects = builder.STAGE / '.obj' / ('sm64-perfdiag' if diagnostic else 'sm64')
    assert objects.parent == OUT / 'SM64/.obj' and not objects.is_symlink()
    if objects.exists():
        shutil.rmtree(objects)
    sys.argv = [str(Path(__file__)), 'candidate', '--diagnostic', str(int(diagnostic))]
    builder.main()
    if not diagnostic:
        assert builder.sha(elf) == baseline['normal_elf_sha256'], 'Normal executable changed'


if __name__ == '__main__':
    main()
