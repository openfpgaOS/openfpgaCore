#!/usr/bin/env python3
"""Snapshot and build one os30 candidate at seed 2. Never deploy or sweep."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[3]
BASELINE = ROOT / 'tools/experiments/quake2_native_batching_20261012/core'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inventory(directory):
    result = {}
    for path in sorted(directory.rglob('*')):
        if path.is_symlink():
            raise ValueError(f'Symlink in source snapshot: {path}')
        if path.is_file():
            result[str(path.relative_to(directory))] = sha(path)
    return result


def prepare(source, output):
    manifest = json.loads((BASELINE / 'manifest.json').read_text())
    before = inventory(source)
    # Timing claims must use the same constraints, PLLs, CPU and selected seed.
    protected = [name for name in manifest['source_sha256']
                 if name.endswith(('.sdc', '.seed')) or '/megafunctions/' in name
                 or name.endswith('/VexiiRiscv_os30.v')]
    for name in protected:
        if before.get(name) != manifest['source_sha256'][name]:
            raise ValueError(f'Protected clock/CPU/constraint input changed: {name}')
    if set(before) != set(manifest['source_sha256']):
        raise ValueError('Candidate source inventory differs; review new inputs first')
    qsf = (source / 'src/fpga/targets/pocket/ap_core.qsf').read_text()
    seeds = [line.strip() for line in qsf.splitlines()
             if line.startswith('set_global_assignment -name SEED ')]
    if seeds != ['set_global_assignment -name SEED 2']:
        raise ValueError('Candidate must retain selected seed 2')
    if output.exists() or source == output or source in output.parents:
        raise ValueError('Output must be a new directory outside the source tree')
    shutil.copytree(source, output)
    if inventory(output) != before or inventory(source) != before:
        raise ValueError('Source changed during snapshot; do not build this snapshot')
    return {
        'baseline_manifest_sha256': sha(BASELINE / 'manifest.json'),
        'source_sha256': before,
        'changes': {name: {'before': manifest['source_sha256'][name], 'after': digest}
                    for name, digest in before.items()
                    if digest != manifest['source_sha256'][name]},
        'recipe_sha256': sha(Path(__file__)),
        'seed': 2, 'configured_mhz': 100, 'deployed': False,
        'timing_passed': False, 'commands': [], 'returncodes': {},
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--prepare-only', action='store_true')
    parser.add_argument('--quartus-bin', type=Path,
                        default=Path('/home/alberto/altera_lite/25.1std/quartus/bin'))
    args = parser.parse_args()
    source, output = args.source.resolve(), args.output.resolve()
    receipt = prepare(source, output)

    def save():
        (output / 'build-receipt.json').write_text(json.dumps(receipt, indent=2) + '\n')

    save()
    if args.prepare_only:
        print(f'Prepared {len(receipt["source_sha256"])} pinned inputs at {output}')
        return 0
    version = subprocess.check_output([str(args.quartus_bin / 'quartus_sh'), '--version'], text=True)
    if 'Version 25.1std.0 Build 1129' not in version:
        raise ValueError(f'Unexpected Quartus version: {version}')
    receipt['quartus_version'] = version.strip()
    cwd = output / 'src/fpga/targets/pocket'
    steps = [(phase, [str(args.quartus_bin / ('quartus_' + phase)), 'ap_core'])
             for phase in ('map', 'fit', 'asm', 'sta')]
    steps.append(('corners', [str(args.quartus_bin / 'quartus_sta'), '-t',
                             str(output / 'tools/report_core_timing.tcl'),
                             'ap_core', str(output / 'timing')]))
    for phase, command in steps:
        print('Starting', phase, flush=True)
        entry = {'phase': phase, 'argv': command, 'cwd': str(cwd)}
        receipt['commands'].append(entry)
        save()
        before = time.monotonic()
        with (output / (phase + '.log')).open('w') as log:
            result = subprocess.run(command, cwd=cwd, stdout=log, stderr=subprocess.STDOUT)
        receipt['returncodes'][phase] = result.returncode
        entry['elapsed_seconds'] = time.monotonic() - before
        if phase == 'asm' and result.returncode == 0:
            table = bytes(int(f'{i:08b}'[::-1], 2) for i in range(256))
            artifact = output / 'os30.rbf_r'
            artifact.write_bytes((cwd / 'output_files/ap_core.rbf').read_bytes().translate(table))
            receipt['bitstream_sha256'] = sha(artifact)
        receipt['post_build_source_changes'] = {
            name: sha(output / name) for name, digest in receipt['source_sha256'].items()
            if sha(output / name) != digest}
        save()
        if result.returncode:
            print('Failed', phase, 'see', output / (phase + '.log'), flush=True)
            return result.returncode
    receipt['timing_passed'] = True
    save()
    return 0


if __name__ == '__main__':
    sys.exit(main())
