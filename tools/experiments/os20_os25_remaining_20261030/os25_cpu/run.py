#!/usr/bin/env python3
"""Reproduce isolated os25 CPU candidates and bounded functional checks.

No production configuration, Quartus build, bitstream or SD card is changed.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
VENDOR = ROOT / 'src/fpga/vendor/vexriscv'
PROFILES = {
    'baseline': [],
    'candidate': ['retime_fetch_reads.pl', 'retime_aligner_pc.pl', 'factor_fetch_ready.pl'],
    'fpu-match': ['retime_fetch_reads.pl', 'retime_aligner_pc.pl', 'factor_fetch_ready.pl',
                  'retime_fpu_matches.pl'],
}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def logic_sha(path):
    # A private git-init has no commit, so Spinal's two provenance comments
    # differ. Retain every other byte, including generated implementation.
    text = '\n'.join(line for line in Path(path).read_text().split('\n')
                     if not line.startswith('// Generator :')
                     and not line.startswith('// Git hash'))
    return hashlib.sha256(text.encode()).hexdigest()


def save(path, data):
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + '\n')


def run(command, log):
    with log.open('w') as output:
        subprocess.run(list(map(str, command)), stdout=output,
                       stderr=subprocess.STDOUT, check=True)


def checked_inputs():
    pins = json.loads((HERE / 'inputs.json').read_text())
    for name, expected in pins['helpers'].items():
        if sha(VENDOR / name) != expected:
            raise ValueError('Changed generator/helper: ' + name)
    for name, expected in pins['upstream_commits'].items():
        actual = subprocess.check_output(['git', '-C', str(VENDOR / name),
                                          'rev-parse', 'HEAD'], text=True).strip()
        if actual != expected:
            raise ValueError('Changed generator checkout: ' + name)
    return pins


def source_ignore(directory, names):
    return {name for name in names if name in
            ['.git', 'target', '.bloop', '.bsp', 'simWorkspace', 'out', 'vexii_active.v']
            or (name.startswith('VexiiRiscv') and name.endswith('.v'))}


def audit(out):
    pins = checked_inputs()
    for profile in PROFILES:
        netlist = out / profile / 'VexiiRiscv_os25.v'
        if logic_sha(netlist) != pins['logic_sha256'][profile]:
            raise ValueError('Candidate implementation differs: ' + profile)
        if sha(out / profile / 'os25.cfg') != sha(HERE / (profile + '.cfg')):
            raise ValueError('Candidate configuration differs: ' + profile)
    return {profile: sha(out / profile / 'VexiiRiscv_os25.v') for profile in PROFILES}


def prepare(out):
    checked_inputs()
    out.mkdir(parents=True, exist_ok=False)
    private = out / 'generator'
    private.mkdir()
    for name in checked_inputs()['helpers']:
        shutil.copyfile(VENDOR / name, private / name)
    shutil.copytree(VENDOR / 'patches', private / 'patches')
    shutil.copytree(VENDOR / 'configs', private / 'configs')
    shutil.copyfile(HERE / 'baseline.cfg', private / 'configs/os25.cfg')
    shutil.copytree(VENDOR / 'VexiiRiscv', private / 'VexiiRiscv', ignore=source_ignore)
    subprocess.run(['git', 'init', '-q', str(private / 'VexiiRiscv')], check=True)
    # Create the writable build cache before Docker binds it. Otherwise a
    # fresh checkout may get a root-owned directory from Docker itself.
    (ROOT / 'tools/.vexii-home').mkdir(parents=True, exist_ok=True)
    command = ['docker', 'run', '--rm', '--user', f'{os.getuid()}:{os.getgid()}',
               '-v', f'{ROOT}:{ROOT}', '-v', f'{out}:{out}',
               '-v', f'{ROOT / "tools/.vexii-home"}:/vexiihome',
               '--tmpfs', '/tmp:exec', '-e', 'JAVA_TOOL_OPTIONS=-Duser.home=/vexiihome',
               '-e', 'COURSIER_CACHE=/vexiihome/.cache/coursier',
               '-w', str(private / 'VexiiRiscv'), 'openfpgaos-vexii',
               'bash', '../generate_vexii.sh', 'os25']
    save(out / 'generation-command.json', command)
    run(command, out / 'generate-baseline.log')
    generated = private / 'VexiiRiscv/VexiiRiscv_os25.v'
    for profile, transforms in PROFILES.items():
        destination = out / profile
        destination.mkdir()
        netlist = destination / 'VexiiRiscv_os25.v'
        shutil.copyfile(generated, netlist)
        shutil.copyfile(HERE / (profile + '.cfg'), destination / 'os25.cfg')
        for helper in transforms:
            run(['perl', private / helper, netlist], destination / (helper + '.log'))
        before = netlist.read_bytes()
        for helper in transforms:
            subprocess.run(['perl', str(private / helper), str(netlist)], check=True)
        if before != netlist.read_bytes():
            raise ValueError('Non-idempotent transforms: ' + profile)
    save(out / 'prepared.json', {'netlist_sha256': audit(out), 'production_modified': False})


def check(out):
    audit(out)
    checks = out / 'validation'
    checks.mkdir(exist_ok=False)
    for profile in PROFILES:
        run([sys.executable, ROOT / 'tools/check_vexii_interrupts.py',
             '--profile', 'pocket', '--bank-row-track', '1',
             '--netlist', out / profile / 'VexiiRiscv_os25.v',
             '--output', checks / ('irq-' + profile)], checks / (profile + '.log'))
    baseline = (checks / 'irq-baseline/test.log').read_bytes()
    for profile in ['candidate', 'fpu-match']:
        if baseline != (checks / ('irq-' + profile) / 'test.log').read_bytes():
            raise ValueError('Whole-CPU trace/cycle difference: ' + profile)
    for name, flags in [('fetch-reads', []), ('aligner-reads', ['--aligner'])]:
        run([sys.executable, ROOT / 'tools/check_vexii_fetch_reads.py',
             '--netlist', out / 'candidate/VexiiRiscv_os25.v',
             '--output', checks / name, *flags], checks / (name + '.log'))
    run([sys.executable, ROOT / 'tools/tests/test_fetch_ready_factor.py'],
        checks / 'fetch-ready.log')
    run([sys.executable, ROOT / 'tools/check_vexii_fpu_matches.py',
         '--reference', out / 'candidate/VexiiRiscv_os25.v',
         '--netlist', out / 'fpu-match/VexiiRiscv_os25.v',
         '--output', checks / 'fpu-matches'], checks / 'fpu-matches.log')
    save(checks / 'result.json', {'passed': True, 'whole_cpu_logs_byte_identical': True,
                                'netlist_sha256': audit(out),
                                'qualification': 'bounded functional/cycle checks only; no FPGA fit'})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare', 'audit', 'check'])
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    out = args.output.resolve()
    if not out.is_relative_to(ROOT):
        parser.error("--output must stay inside this checkout for the firmware container")
    if args.action == 'prepare':
        prepare(out)
    elif args.action == 'check':
        check(out)
    else:
        print(json.dumps(audit(out), indent=2))


if __name__ == '__main__':
    main()
