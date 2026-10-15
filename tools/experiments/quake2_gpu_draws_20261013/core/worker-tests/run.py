#!/usr/bin/env python3
"""Rerun the selected exact worker gates in a new directory, without Quartus."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import package


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('output', type=Path)
    a = p.parse_args()
    package.verify()
    root = Path(__file__).resolve().parent
    worker = root / 'gpu_geometry_worker.v'
    out = a.output.resolve()
    if package.HERE == out or package.HERE in out.parents:
        raise ValueError('Output must be outside the immutable package')
    out.mkdir(parents=True, exist_ok=False)
    baseline = json.loads((package.HERE / 'evidence/worker/receipt.json').read_text())
    receipt = dict(worker_sha256=package.sha(worker),
                   version=subprocess.check_output(['verilator', '--version'], text=True).strip(),
                   commands=[], scope=baseline['scope'], passed=False)
    for mode in ('enabled', 'legacy-disabled'):
        testroot = root if mode == 'enabled' else root / mode
        dest = out / mode
        dest.mkdir()
        receipt[mode] = {'sources': {name: package.sha(testroot / name)
                                    for name in ('geometry_worker_tb.v', 'geometry_worker.cpp')}}
        cmd = ['verilator', '--cc', '--exe', '--build', '-j', '4', '-Wno-fatal',
               '--top-module', 'geometry_worker_tb', '--Mdir', str(dest / 'obj'),
               str(testroot / 'geometry_worker_tb.v'), str(worker), str(testroot / 'geometry_worker.cpp')]
        receipt['commands'].append(cmd)
        with (dest / 'build.log').open('w') as log:
            subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT, check=True)
        cmd = [str(dest / 'obj/Vgeometry_worker_tb')]
        receipt['commands'].append(cmd)
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        receipt[mode]['result'] = result.stdout + result.stderr
        (dest / 'result.txt').write_text(receipt[mode]['result'])
        print(result.stdout, end='')
        if receipt[mode]['result'] != baseline[mode]['result']:
            raise ValueError('Gate transcript/cycle totals differ: ' + mode)
    receipt['passed'] = True
    (out / 'receipt.json').write_text(json.dumps(receipt, indent=2) + '\n')


if __name__ == '__main__':
    main()
