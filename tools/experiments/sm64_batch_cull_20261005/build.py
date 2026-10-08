#!/usr/bin/env python3
"""Build a private app without invoking the runtime packaging targets."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import time

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / 'docs/measurements/sm64-batch-cull-20261005'
STAGE = OUT / 'SM64'
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()


def inputs():
    return {str(p.relative_to(STAGE)): sha(p)
            for base in ('src/sdk', 'src/sm64', 'tools')
            for p in (STAGE / base).rglob('*') if p.is_file()
            and '__pycache__' not in p.parts}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--diagnostic', type=int, choices=(0, 1), required=True)
    args = parser.parse_args()
    label = 'diagnostic' if args.diagnostic else 'normal'
    target = STAGE / ('.obj/sm64-perfdiag/app.elf' if args.diagnostic else '.obj/sm64/app.elf')
    command = ['make', '-j4', f'PERF_DIAG={args.diagnostic}', str(target)]
    before = inputs()
    start = time.time()
    with (OUT / f'build-{label}.log').open('w') as log:
        result = subprocess.run(command, cwd=STAGE / 'src/sm64', stdout=log, stderr=subprocess.STDOUT)
    after = inputs()
    changed = [p for p in before.keys() | after.keys() if before.get(p) != after.get(p)]
    record = dict(command=command, cwd=str(STAGE / 'src/sm64'), returncode=result.returncode,
                  elapsed_seconds=time.time() - start, changed_inputs=changed, input_sha256=before,
                  elf=str(target), elf_sha256=sha(target) if target.is_file() else None)
    (OUT / f'build-{label}.json').write_text(json.dumps(record, indent=2) + '\n')
    assert result.returncode == 0, f'See build-{label}.log'
    assert not changed, ('Build inputs changed', changed)
    print(json.dumps({k: v for k, v in record.items() if k != 'input_sha256'}, indent=2))


if __name__ == '__main__':
    main()
