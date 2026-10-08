#!/usr/bin/env python3
"""Build a frozen candidate into private storage; never package or deploy."""
import argparse
import hashlib
import json
import shutil
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / 'docs/measurements/os30-fps25-20261005'
STAGE = OUT / 'SM64'
GFX = Path('src/sm64/sm64/src/pc/gfx')
INPUTS = {name:GFX/name for name in ('gfx_pc.c','gfx_gpu.c','gfx_gpu.h','gfx_geometry.h')}
INPUTS.update({name:Path('src/sm64/pocket')/name for name in ('perf_pocket.h','perf_pocket.c')})
INPUTS['Makefile.sm64'] = Path('src/sm64/Makefile')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('candidate')
    parser.add_argument('--diagnostic', type=int, choices=(0, 1), default=0)
    args = parser.parse_args()
    assert '/' not in args.candidate and args.candidate not in ('.', '..')
    candidate = OUT / args.candidate
    assert candidate.is_dir()
    assert any((candidate / name).is_file() for name in INPUTS), 'No candidate sources; preparation may have failed'
    label = 'diagnostic' if args.diagnostic else 'normal'
    dest = candidate / ('diagnostic.elf' if args.diagnostic else 'app.elf')
    assert not dest.exists(), f'Preserve existing build: {dest}'
    inputs = {}
    for name, rel in INPUTS.items():
        source = candidate / name
        if not source.exists():
            source = OUT / 'baseline' / name
        target = STAGE / rel
        if target.read_bytes() != source.read_bytes():
            shutil.copyfile(source, target)
        inputs[name] = {'source': str(source), 'sha256': sha(source)}
    target = STAGE / ('.obj/sm64-perfdiag/app.elf' if args.diagnostic else '.obj/sm64/app.elf')
    command = ['make', '-j4', f'PERF_DIAG={args.diagnostic}', str(target)]
    started = time.monotonic()
    with (candidate / f'build-{label}.log').open('w') as log:
        result = subprocess.run(command, cwd=STAGE / 'src/sm64', stdout=log,
                                stderr=subprocess.STDOUT)
    record = {'command': command, 'cwd': str(STAGE / 'src/sm64'),
              'returncode': result.returncode, 'diagnostic': bool(args.diagnostic),
              'elapsed_seconds': time.monotonic() - started, 'inputs': inputs}
    for name, info in inputs.items():
        assert sha(STAGE / INPUTS[name]) == info['sha256'], 'Build inputs changed'
    if result.returncode == 0:
        shutil.copyfile(target, dest)
        record.update(elf=str(dest), elf_sha256=sha(dest))
    (candidate / f'build-{label}.json').write_text(json.dumps(record, indent=2) + '\n')
    print(json.dumps(record, indent=2))
    assert result.returncode == 0


if __name__ == '__main__':
    main()
