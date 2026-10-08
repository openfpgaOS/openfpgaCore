#!/usr/bin/env python3
"""Saved-seed standalone CPU area/internal-timing probe; never deploys a core.

External CPU interfaces are virtual and unconstrained. Results measure internal
register-to-register paths only, not os30 integration or board timing closure.
"""
import argparse
import csv
import fcntl
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / 'docs/measurements/os30-dual-20261004'
sys.path.insert(0, str(ROOT / 'tools'))
import sweep_target_matrix as fit

QSF = '''set_global_assignment -name FAMILY "Cyclone V"
set_global_assignment -name DEVICE 5CEBA4F23C8
set_global_assignment -name TOP_LEVEL_ENTITY VexiiRiscv
set_global_assignment -name PROJECT_OUTPUT_DIRECTORY output_files
set_global_assignment -name NUM_PARALLEL_PROCESSORS 2
set_global_assignment -name OPTIMIZATION_MODE "HIGH PERFORMANCE EFFORT"
set_global_assignment -name OPTIMIZATION_TECHNIQUE SPEED
set_global_assignment -name PHYSICAL_SYNTHESIS_COMBO_LOGIC OFF
set_global_assignment -name PHYSICAL_SYNTHESIS_REGISTER_DUPLICATION ON
set_global_assignment -name PHYSICAL_SYNTHESIS_REGISTER_RETIMING OFF
set_global_assignment -name PHYSICAL_SYNTHESIS_ASYNCHRONOUS_SIGNAL_PIPELINING OFF
set_global_assignment -name ROUTER_LCELL_INSERTION_AND_LOGIC_DUPLICATION ON
set_global_assignment -name ROUTER_TIMING_OPTIMIZATION_LEVEL MAXIMUM
set_global_assignment -name PHYSICAL_SYNTHESIS_EFFORT NORMAL
set_global_assignment -name FITTER_EFFORT "STANDARD FIT"
set_global_assignment -name SEED 2
set_global_assignment -name VERILOG_FILE VexiiRiscv.v
set_global_assignment -name SDC_FILE timing.sdc
set_instance_assignment -name VIRTUAL_PIN ON -to *
set_instance_assignment -name VIRTUAL_PIN OFF -to clk
set_instance_assignment -name VIRTUAL_PIN OFF -to reset
'''
SDC = '''create_clock -period 10.000 -name clk [get_ports {clk}]
derive_clock_uncertainty
set_false_path -from [get_ports {reset}]
'''


def summarize(out, result):
    directory = out / 'fit'
    report = directory / 'output_files/ap_core.fit.rpt'
    if report.is_file():
        data = report.read_text(errors='replace')
        patterns = {
            'alms': r';\s*Logic utilization \(ALMs needed / total ALMs on device\)\s*;\s*([\d,.]+)',
            'labs': r';\s*Total LABs:\s*partially or completely used\s*;\s*([\d,]+)',
            'm10ks': r';\s*M10K blocks\s*;\s*([\d,]+)',
            'dsp_blocks': r';\s*Total DSP Blocks\s*;\s*([\d,]+)',
            'registers': r';\s*Dedicated logic registers\s*;\s*([\d,]+)',
            'placed_alms': r';\s*\[A\] ALMs used in final placement \[=a\+b\+c\+d\]\s*;\s*([\d,.]+)',
        }
        for name, pattern in patterns.items():
            match = re.search(pattern, data)
            if match:
                result[name] = float(match[1].replace(',', ''))
        start = data.find('; Fitter Resource Utilization by Entity')
        if start >= 0:
            end = data.find('\n\n', start)
            (out / 'entity-resources.txt').write_text(data[start:end] + '\n')
    timing = directory / 'timing/summary.tsv'
    if timing.is_file():
        with timing.open() as stream:
            rows = list(csv.DictReader(stream, delimiter='\t'))
        result['timing'] = rows
        result['worst_internal_slack_ns'] = {}
        for check in ('setup', 'hold', 'recovery', 'removal', 'pulse_width'):
            values = [float(r['slack']) for r in rows
                      if r['check'] == check and r['slack'] != 'no_paths']
            result['worst_internal_slack_ns'][check] = min(values) if values else None
        complete = len(rows) == 20 and all(
            result['worst_internal_slack_ns'][key] is not None
            for key in ('setup', 'hold', 'pulse_width'))
        result['internal_checks_pass'] = complete and all(
            v is None or v >= 0 for v in result['worst_internal_slack_ns'].values())
    sta = directory / 'output_files/ap_core.sta.rpt'
    if sta.is_file():
        matches = re.findall(r';\s*([\d.]+) MHz\s*;\s*[\d.]+ MHz\s*;\s*clk\s*;',
                             sta.read_text(errors='replace'))
        if matches:
            result['min_internal_fmax_mhz'] = min(map(float, matches))
    fit.save(out / 'result.json', result)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('label')
    parser.add_argument('--cpu', type=Path, required=True)
    args = parser.parse_args()
    if not re.fullmatch(r'[a-z0-9_-]+', args.label):
        parser.error('label must contain lowercase letters, digits, underscores or hyphens')
    cpu = args.cpu.resolve(strict=True)
    OUT.mkdir(parents=True, exist_ok=True)
    lock = (OUT / '.cpu-fit.lock').open('w')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    out = OUT / ('cpu-fit-' + args.label)
    out.mkdir()
    directory = out / 'fit'
    directory.mkdir()
    shutil.copy2(cpu, directory / 'VexiiRiscv.v')
    shutil.copy2(ROOT / 'tools/report_core_timing.tcl', directory / 'report_core_timing.tcl')
    (directory / 'ap_core.qpf').write_text('PROJECT_REVISION = "ap_core"\n')
    (directory / 'ap_core.qsf').write_text(QSF)
    (directory / 'timing.sdc').write_text(SDC)
    manifest = dict(cpu_source=str(cpu), cpu_sha256=fit.digest(cpu), device='5CEBA4F23C8',
                    configured_clock_mhz=100, seed=2, processors=2,
                    scope='Standalone CPU; virtual unconstrained external ports; internal timing only',
                    production_modified=False, full_core_fit=False, deployable=False,
                    tool_source_sha256=fit.digest(Path(__file__)),
                    frozen_sources={str(p.relative_to(out)): fit.digest(p)
                                    for p in directory.iterdir() if p.is_file()})
    # Quartus may append its version to the working QSF. Retain byte-exact
    # original inputs separately so the manifest remains reproducible.
    shutil.copytree(directory, out / 'inputs')
    shutil.copy2(Path(__file__), out / 'fit_cpu.py')
    manifest['immutable_inputs'] = {
        str(p.relative_to(out)): fit.digest(p)
        for p in (out / 'inputs').iterdir() if p.is_file()}
    fit.save(out / 'manifest.json', manifest)
    result = dict(label=args.label, scope=manifest['scope'], cpu_sha256=manifest['cpu_sha256'],
                  returncodes={}, full_core_timing_qualified=False)
    started = time.monotonic()
    stages = [('map', ['quartus_map', 'ap_core']),
              ('fit', ['quartus_fit', 'ap_core']),
              ('sta', ['quartus_sta', 'ap_core']),
              ('corners', ['quartus_sta', '-t', 'report_core_timing.tcl', 'ap_core', 'timing'])]
    try:
        for stage, command in stages:
            invocation = fit.docker(out, 'pocket', directory, command)
            fit.save(out / (stage + '-command.json'), invocation)
            print(f'{args.label}: {stage}', flush=True)
            with (out / (stage + '.log')).open('w') as log:
                code = subprocess.run(invocation, stdout=log, stderr=subprocess.STDOUT).returncode
            result['returncodes'][stage] = code
            summarize(out, result)
            if code:
                break
    finally:
        result['elapsed_seconds'] = time.monotonic() - started
        summarize(out, result)
    print(json.dumps(result, indent=2), flush=True)
    if any(code for stage, code in result['returncodes'].items() if stage != 'corners'):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
