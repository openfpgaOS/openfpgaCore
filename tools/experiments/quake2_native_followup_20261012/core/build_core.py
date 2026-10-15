#!/usr/bin/env python3
"""Build the frozen experiment in a new directory; never deploy or sweep seeds.

A negative timing slack is a failed check even when assembly succeeds. This
portable recipe is equivalent to the recorded tool sequence; its staging is
validated separately without repeating the physical fit.
"""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time
sys.dont_write_bytecode = True
import package


def prepare(source, output):
    m = package.verify()
    package.check_stage(source, m)
    if output.exists():
        raise ValueError('Output must not exist')
    shutil.copytree(source, output)
    package.check_stage(output, m)
    return m


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('source', type=Path)
    p.add_argument('output', type=Path)
    p.add_argument('--quartus-bin', type=Path,
                   default=Path('/home/alberto/altera_lite/25.1std/quartus/bin'))
    args = p.parse_args()
    source, output = args.source.resolve(), args.output.resolve()
    m = prepare(source, output)
    version = subprocess.check_output([str(args.quartus_bin / 'quartus_sh'), '--version'], text=True)
    if m['quartus'] not in version:
        raise ValueError('Unexpected Quartus version: ' + version)
    receipt = dict(source_sha256=m['source_sha256'], package_manifest_sha256=package.sha(package.HERE/'manifest.json'),
                   recipe_sha256=package.sha(Path(__file__)), seed=2, configured_mhz=100,
                   silent_audio=True, mixer=False, deployed=False, timing_passed=False,
                   quartus_version=version.strip(), commands=[], returncodes={})
    def save():
        (output/'build-receipt.json').write_text(json.dumps(receipt, indent=2)+'\n')
    cwd = output/'src/fpga/targets/pocket'
    steps = [(phase, [str(args.quartus_bin/('quartus_'+phase)), 'ap_core'])
             for phase in ('map', 'fit', 'asm', 'sta')]
    steps.append(('corners', [str(args.quartus_bin/'quartus_sta'), '-t',
        str(output/'tools/report_core_timing.tcl'), 'ap_core', str(output/'timing')]))
    save()
    for phase, command in steps:
        print('Starting', phase, flush=True)
        entry = dict(phase=phase, argv=command, cwd=str(cwd))
        receipt['commands'].append(entry)
        save()
        before = time.monotonic()
        with (output/(phase+'.log')).open('w') as log:
            result = subprocess.run(command, cwd=cwd, stdout=log, stderr=subprocess.STDOUT)
        receipt['returncodes'][phase] = result.returncode
        entry['elapsed_seconds'] = time.monotonic()-before
        if phase == 'asm' and result.returncode == 0:
            table = bytes(int(f'{i:08b}'[::-1], 2) for i in range(256))
            artifact = output/'os30.rbf_r'
            artifact.write_bytes((cwd/'output_files/ap_core.rbf').read_bytes().translate(table))
            receipt['bitstream_sha256'] = package.sha(artifact)
        save()
        if result.returncode:
            print('Failed', phase, 'see', output/(phase+'.log'), flush=True)
            return result.returncode
    receipt['timing_passed'] = True
    save()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
