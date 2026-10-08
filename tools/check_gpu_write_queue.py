#!/usr/bin/env python3
"""Stress both GPU write-queue sizes with delayed commit and contending readers."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--jobs', type=int, default=3)
    args = parser.parse_args()
    if args.jobs < 1:
        parser.error('--jobs must be positive')
    out = args.output.resolve()
    frozen = out / 'frozen'
    frozen.mkdir(parents=True, exist_ok=False)
    sources = [ROOT / p for p in ('src/fpga/test/tb_arbiter.v',
               'src/fpga/common/axi_sdram_arbiter.v', 'tools/tests/gpu_write_queue.cpp')]
    report = {'sources': {}, 'runs': []}
    for source in sources:
        shutil.copy2(source, frozen / source.name)
        report['sources'][str(source.relative_to(ROOT))] = hashlib.sha256(source.read_bytes()).hexdigest()
    for depth in (8, 16):
        obj = out / f'q{depth}'
        cmd = ['verilator', '--cc', '--exe', '--build', '-j', str(args.jobs),
               '--top-module', 'tb_arbiter', '-Wno-fatal', '-GWRITE_RESP_DELAY=25',
               f'-GGPU_WRITE_QUEUE_DEPTH={depth}', '--Mdir', str(obj),
               *[str(frozen / source.name) for source in sources]]
        with (out / f'q{depth}-build.log').open('w') as log:
            subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT, check=True)
        result = subprocess.check_output([str(obj / 'Vtb_arbiter'), str(depth)], text=True, timeout=60)
        (out / f'q{depth}.log').write_text(result)
        assert result.startswith(f'PASS depth={depth} '), result
        report['runs'].append({'depth': depth, 'build_command': cmd, 'result': result.strip()})
        print(result, end='', flush=True)
    (out / 'results.json').write_text(json.dumps(report, indent=2) + '\n')


if __name__ == '__main__':
    main()
