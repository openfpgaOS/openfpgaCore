#!/usr/bin/env python3
"""Check full coordinate range in both GPU edge dividers and step decoders."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rtl', type=Path, default=ROOT / 'src/fpga/common/gpu_edge_walker.v')
    parser.add_argument('--output', type=Path, default=ROOT / 'build/tests/gpu-edge-range')
    args = parser.parse_args()
    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=True)
    results = []
    for parallel in (0, 1):
        for predecode in (False, True):
            name = f'parallel{parallel}-predecode{int(predecode)}'
            dest = args.output / name
            dest.mkdir(exist_ok=True)
            command = ['verilator', '--cc', '--exe', '--build', '-j', '4',
                       '-Wno-fatal', '--top-module', 'gpu_edge_walker',
                       f'-GEW_PARALLEL_DIVS={parallel}', '--Mdir', str(dest / 'obj'),
                       '-CFLAGS', '-std=c++17 -O2']
            if predecode:
                command += ['+define+INCLUDE_EW_STEP_PREDECODE']
            command += [str(args.rtl.resolve()),
                        str(ROOT / 'src/fpga/test/tb_gpu_edge_range.cpp')]
            with (dest / 'build.log').open('w') as log:
                subprocess.run(command, stdout=log, stderr=subprocess.STDOUT,
                               check=True, timeout=180)
            result = subprocess.run([str(dest / 'obj/Vgpu_edge_walker')],
                                    text=True, capture_output=True, timeout=120)
            (dest / 'test.log').write_text(result.stdout + result.stderr)
            print(name, result.stdout.strip(), result.stderr.strip(), flush=True)
            results.append(dict(parallel=parallel, predecode=predecode,
                                returncode=result.returncode, output=result.stdout,
                                rtl_sha256=hashlib.sha256(args.rtl.read_bytes()).hexdigest()))
            (args.output / 'results.json').write_text(json.dumps(results, indent=2) + '\n')
    assert all(row['returncode'] == 0 for row in results), 'Edge range regression'


if __name__ == '__main__':
    main()
