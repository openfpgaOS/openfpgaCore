#!/usr/bin/env python3
"""Generate an isolated single-issue A control and late-ALU candidate."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT/'docs/measurements/os30-fps25-20261005/late-alu-v1'
VENDOR = ROOT/'src/fpga/vendor/vexriscv'
STAGE = OUT/'source'
HELPERS = ['retime_fetch_reads.pl', 'retime_aligner_pc.pl',
           'factor_fetch_ready.pl', 'retime_dcache_writes.pl']
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()


def code_sha(path):
    text = re.sub(r'//[^\n]*|/\*[\s\S]*?\*/', '', path.read_text())
    return hashlib.sha256(re.sub(r'\s+', '', text).encode()).hexdigest()


def stage():
    OUT.mkdir(exist_ok=True)
    assert not STAGE.exists(), 'Do not overwrite the frozen generator source'
    shutil.copytree(VENDOR/'VexiiRiscv', STAGE,
                    ignore=shutil.ignore_patterns('.git', 'target', '.bsp', '.metals',
                        'VexiiRiscv*.v', '*.vcd', 'simWorkspace'))
    (OUT/'helpers').mkdir()
    for name in HELPERS:
        shutil.copy2(VENDOR/name, OUT/'helpers'/name)
    baseline = ROOT/'docs/measurements/os30-dual-20261004/fit-single128-v1/frozen/src/fpga/vendor/vexriscv/VexiiRiscv/VexiiRiscv_os30.v'
    assert sha(baseline) == '33895e9a2fc141d8da49bb75e082e06964a49e328b6d87363516ac6f9ab26119'
    shutil.copy2(baseline, OUT/'frozen-A.v')
    sources = [p for p in STAGE.rglob('*') if p.is_file() and (p.suffix == '.scala' or p.name in ('build.sbt','build.properties'))]
    inputs = dict(root_modified=False, fit_started=False, baseline_cpu_sha256=sha(baseline),
        source_sha256={str(p.relative_to(STAGE)): sha(p) for p in sources},
        helper_sha256={name: sha(OUT/'helpers'/name) for name in HELPERS},
        current_config_sha256=sha(VENDOR/'configs/os30.cfg'),
        generator_script_sha256=sha(VENDOR/'generate_vexii.sh'))
    (OUT/'source-manifest.json').write_text(json.dumps(inputs, indent=2)+'\n')


def generate(label):
    assert label in ('control', 'late-alu')
    old = json.loads((ROOT/'docs/measurements/os30-dual-20261004/generator-v1/single64-manifest.json').read_text())
    flags = [f.replace('--lsu-l1-sets=512', '--lsu-l1-sets=1024') for f in old['flags']]
    assert '--fetch-l1-sets=256' in flags and '--btb-sets=1024' in flags and '--gshare-bytes=4096' in flags
    assert not any(f.startswith('--lanes=') or f.startswith('--decoders=') for f in flags)
    if label == 'late-alu': flags.append('--with-late-alu')
    command = ['docker', 'run', '--rm', '--user', f'{os.getuid()}:{os.getgid()}',
        '-v', f'{ROOT}:{ROOT}', '-v', f'{ROOT}/tools/.vexii-home:/vexiihome',
        '--tmpfs', '/tmp:exec', '-w', str(STAGE), 'openfpgaos-vexii', 'sbt',
        '-Duser.home=/vexiihome', '-Dsbt.boot.directory=/vexiihome/.sbt/boot',
        '-Dsbt.global.base=/vexiihome/.sbt', '-Dsbt.ivy.home=/vexiihome/.ivy2',
        '-Dsbt.server.forcestart=true', '--batch',
        'Test/runMain vexiiriscv.Generate '+' '.join(flags)]
    log = OUT/(label+'-generation.log')
    with log.open('w') as f:
        subprocess.run(command, stdout=f, stderr=subprocess.STDOUT, check=True)
    raw = STAGE/'VexiiRiscv.v'
    target = OUT/('VexiiRiscv_'+label+'.v')
    shutil.copy2(raw, OUT/(label+'-raw.v'))
    text = raw.read_text()
    text, count = re.subn(r'  wire +execute_freeze_valid;',
        '  (* maxfan = 16 *) wire execute_freeze_valid;', text)
    assert count == 1
    text = text.replace('= {$urandom};', '= 0;')
    assert '$urandom' not in text
    text, count = re.subn(r'^module VexiiRiscv \(',
        '(* altera_attribute = "-name AUTO_SHIFT_REGISTER_RECOGNITION OFF" *)\nmodule VexiiRiscv (', text, flags=re.M)
    assert count == 1
    target.write_text(text)
    with (OUT/(label+'-postprocess.log')).open('w') as f:
        for name in HELPERS:
            subprocess.run(['perl', str(OUT/'helpers'/name), str(target)],
                check=True, stdout=f, stderr=subprocess.STDOUT)
    report = dict(flags=flags, command=command, raw_sha256=sha(raw),
        generated_sha256=sha(target), source_manifest_sha256=sha(OUT/'source-manifest.json'),
        baseline_cpu_sha256=sha(OUT/'frozen-A.v'),
        exact_A_match=sha(target) == sha(OUT/'frozen-A.v'),
        code_A_match=code_sha(target) == code_sha(OUT/'frozen-A.v'),
        code_sha256=code_sha(target),
        only_config_difference=[] if label == 'control' else ['--with-late-alu'],
        scope='Single decoder and issue lane; I32/D128, BTB1024, GShare4KiB and all other A flags retained. No synthesis or fit performed.')
    (OUT/(label+'-manifest.json')).write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['stage', 'control', 'late-alu'])
    args = parser.parse_args()
    if args.action == 'stage': stage()
    else: generate(args.action)
