#!/usr/bin/env python3
"""Check existing RGB565 lighting against a CPU oracle on the frozen fit inputs."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / 'docs/measurements/os30-geometry-fit-20261002'


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('candidate', choices=['matrix_light_ram', 'matrix_light_regs'])
    args = p.parse_args()
    acceptance = OUT / ('accept-' + args.candidate)
    fitted = OUT / ('fit-' + args.candidate)
    manifest = json.loads((acceptance / 'os30/manifest.json').read_text())
    assert manifest['parameters']['INCLUDE_GPU_LIGHT'] == 1
    assert manifest['parameters']['INCLUDE_GPU_XFORM_MAC'] == 1
    sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
    for path in (acceptance / 'frozen/src/fpga/common').glob('*'):
        if path.is_file():
            assert sha(path) == sha(fitted / 'frozen/src/fpga/common' / path.name)
    fit_defs = json.loads((fitted / 'inventory.json').read_text())[0]['defs']
    assert set(fit_defs.split()) == set(manifest['variant']['defs'].split())
    dest = OUT / ('lighting-' + args.candidate)
    dest.mkdir(exist_ok=False)
    cpp = dest / 'lighting.cpp'
    shutil.copy2(Path(__file__).with_suffix('.cpp'), cpp)
    obj = acceptance / 'os30/obj'
    vr = Path(re.search(r'^VERILATOR_ROOT = (.+)$',
                       (obj / 'Vtb_gpu.mk').read_text(), re.M)[1])
    libraries = [obj / 'Vtb_gpu__ALL.a', *sorted(obj.glob('verilated*.o'))]
    cmd = ['g++', *manifest['cflags'], '-DVM_TRACE=1', '-DVM_TRACE_VCD=1',
           '-DVM_TRACE_FST=0', '-I' + str(obj), '-I' + str(vr / 'include'),
           '-I' + str(vr / 'include/vltstd'),
           '-I' + str(acceptance / 'frozen/src/fpga/test'), str(cpp),
           *map(str, libraries), '-pthread', '-latomic', '-o', str(dest / 'check')]
    (dest / 'manifest.json').write_text(json.dumps(dict(
        command=cmd, acceptance=str(acceptance), fitted=str(fitted),
        inputs={str(f): sha(f) for f in [cpp, *libraries,
                                       acceptance / 'os30/manifest.json']}), indent=2) + '\n')
    with (dest / 'build.log').open('w') as log:
        subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT, check=True)
    results = {}
    for label, flags in [('quiet', []), ('delayed', ['+gpu_rd_latency=24',
            '+gpu_rd_latency_var=1', '+gpu_wr_latency=17']), ('gapped',
            ['+gpu_rd_latency=24', '+gpu_rd_latency_var=1',
             '+gpu_wr_latency=17', '+gpu_rd_beat_gap=7'])]:
        with (dest / (label + '.log')).open('w') as log:
            run = subprocess.run([str(dest / 'check'), *flags], cwd=dest,
                                 stdout=log, stderr=subprocess.STDOUT, timeout=120)
        match = re.search(r'Acceptance Results: (\d+) passed, (\d+) failed',
                          (dest / (label + '.log')).read_text())
        assert match is not None, label
        results[label] = dict(exit_code=run.returncode,
                              passed=int(match[1]), failed=int(match[2]))
        (dest / 'results.json').write_text(json.dumps(results, indent=2) + '\n')
        assert run.returncode == 0 and int(match[2]) == 0, label
    print(json.dumps(results, indent=2))


if __name__ == '__main__':
    main()
