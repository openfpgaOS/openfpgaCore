#!/usr/bin/env python3
"""Freeze the corrected walker into the measured os30 worker GPU replay."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / 'docs/measurements/gpu-edge-overflow-20261004'
BASE = ROOT / 'docs/measurements/os30-worker-20261003/replay-worker-v6'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--parallel', type=int, choices=(0, 1), default=0)
    parser.add_argument('--label', default='')
    parser.add_argument('--rtl', type=Path, default=ROOT / 'src/fpga/common/gpu_edge_walker.v')
    args = parser.parse_args()
    assert not args.label or re.fullmatch(r'[a-z0-9_-]+', args.label)
    prefix = args.label + '-' if args.label else ''
    dest = OUT / f'replay-{prefix}parallel{args.parallel}'
    manifest = json.loads((BASE / 'manifest.json').read_text())
    for rel, digest in manifest['sources'].items():
        assert sha(BASE / rel) == digest, rel
    dest.mkdir(exist_ok=False)
    shutil.copytree(BASE / 'frozen', dest / 'frozen')
    shutil.copy2(args.rtl,
                 dest / 'frozen/common/gpu_edge_walker.v')
    command = [arg.replace(str(BASE), str(dest)) for arg in manifest['command']]
    command[command.index('-GGPU_EW_PARALLEL_DIVS=0')] = f'-GGPU_EW_PARALLEL_DIVS={args.parallel}'
    manifest.update(command=command, scope='Edge arithmetic repair; GPU cycles are not game FPS',
                    sources={str(p.relative_to(dest)): sha(p)
                             for p in (dest / 'frozen').rglob('*') if p.is_file()})
    (dest / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    with (dest / 'build.log').open('w') as log:
        subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=600)
    subprocess.run(['python3', str(OUT / 'handoff/reproduce_edge_overflow.py'),
                    '--replay', str(dest / 'obj/Vtb_gpu'), '--expect-fixed',
                    '--out', str(dest / 'handoff-result')], check=True, timeout=120)
    result = json.loads((dest / 'handoff-result/result.json').read_text())
    assert all(row['extra_pixels'] == row['missing_pixels'] == 0 for row in result.values())


if __name__ == '__main__':
    main()
