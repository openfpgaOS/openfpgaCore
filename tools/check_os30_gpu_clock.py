#!/usr/bin/env python3
"""Fit the common GPU with os30 macros at a requested clock, without a bitstream.

Registered virtual inputs/outputs remove board pin placement from this first
feasibility check. Passing does not establish whole-core timing or performance;
failing identifies GPU paths that must change before a faster clock can work.
Feature parameters come from the common core_top.v. This legacy probe does not
resolve variants/os30.sources and does not qualify the selected os30 snapshots.
Their source-map-aware provenance is recorded by the frozen campaign's check
action in tools/experiments/os30_fps25_20261005/phase10_normal_build_endpoint_control.py.
"""
import argparse
import csv
import json
import math
from pathlib import Path
import re
import shutil
import subprocess
import time

from sweep_target_matrix import ROOT, digest, docker, inventory, save


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--mhz', type=float, default=166.0)
    parser.add_argument('--seed', type=int, default=2)
    parser.add_argument('--processors', type=int, default=4)
    parser.add_argument('--retime', action='store_true',
                        help='Allow Quartus to move registers across logic (diagnostic only)')
    args = parser.parse_args()
    if not math.isfinite(args.mhz) or not 1 <= args.mhz <= 250 or min(args.seed, args.processors) < 1:
        parser.error('Invalid clock, seed, or processor count')
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    row = next(r for r in inventory() if r['target'] == 'pocket' and r['variant'] == 'os30')
    common = out / 'common'
    shutil.copytree(ROOT / 'src/fpga/common', common)
    target = ROOT / 'src/fpga/targets/pocket'
    defs = ['+define+' + name for name in row['defs'].split()]
    pre = subprocess.check_output(['verilator', '-E', '-P', '-I' + str(target),
        '-I' + str(target / 'apf'), *defs, str(target / 'core_top.v')], text=True)
    match = re.search(r'gpu_core\s*#\((.*?)\)\s*gpu\s*\(', pre, re.S)
    if not match:
        raise ValueError('Production os30 GPU instance was not found')
    params = re.sub(r'//[^\n]*', '', match[1]).strip()
    core = subprocess.check_output(['verilator', '-E', '-P', *defs,
                                   str(common / 'gpu_core.v')], text=True)
    header = core[core.index(') (', core.index('module gpu_core')) + 3:]
    header = re.sub(r'/\*.*?\*/|//[^\n]*', '', header[:header.index(');')], flags=re.S)
    ports = re.findall(r'(input|output)\s+(?:wire|reg)\s*(\[\d+:\d+\])?\s*(\w+)', header)
    if len(ports) != header.count('input') + header.count('output'):
        raise ValueError('GPU port declaration contains an unhandled expression')
    declarations, logic, connects, pins = ['input wire clk'], [], ['.clk(clk)'], []
    for direction, width, name in ports:
        if name == 'clk':
            continue
        if name == 'gpu_enable':
            connects.append(".gpu_enable(1'b1)")
            continue
        if name.startswith('dbg_'):
            connects.append(f'.{name}()')
            continue
        pins.append(name)
        if direction == 'input':
            declarations.append(f'input wire {width} {name}')
            logic += [f'reg {width} {name}_q;',
                      f'always @(posedge clk) {name}_q <= {name};']
            connects.append(f'.{name}({name}_q)')
        else:
            declarations.append(f'output reg {width} {name}')
            logic += [f'wire {width} {name}_d;',
                      f'always @(posedge clk) {name} <= {name}_d;']
            connects.append(f'.{name}({name}_d)')
    wrapper = 'module gpu_clock_probe (\n' + ',\n'.join(declarations) + '\n);\n'
    wrapper += '\n'.join(logic) + '\ngpu_core #(\n' + params + '\n) gpu (\n'
    wrapper += ',\n'.join(connects) + '\n);\nendmodule\n'
    (out / 'gpu_clock_probe.v').write_text(wrapper)
    sources = ['gpu_clock_probe.v', 'common/gpu_core.v',
               'common/gpu_edge_walker.v', 'common/gpu_tex_cache.v']
    qsf = ['set_global_assignment -name FAMILY "Cyclone V"',
        'set_global_assignment -name DEVICE 5CEBA4F23C8',
        'set_global_assignment -name TOP_LEVEL_ENTITY gpu_clock_probe',
        'set_global_assignment -name PROJECT_OUTPUT_DIRECTORY output_files',
        'set_global_assignment -name OPTIMIZATION_MODE "HIGH PERFORMANCE EFFORT"',
        'set_global_assignment -name PHYSICAL_SYNTHESIS_REGISTER_RETIMING ' + ('ON' if args.retime else 'OFF'),
        'set_global_assignment -name PHYSICAL_SYNTHESIS_REGISTER_DUPLICATION ON',
        f'set_global_assignment -name NUM_PARALLEL_PROCESSORS {args.processors}',
        f'set_global_assignment -name SEED {args.seed}',
        'set_global_assignment -name SDC_FILE probe.sdc',
        'set_global_assignment -name RESERVE_ALL_UNUSED_PINS "AS INPUT TRI-STATED"']
    qsf += ['set_global_assignment -name VERILOG_FILE ' + src for src in sources]
    qsf += ['set_global_assignment -name VERILOG_MACRO ' + d for d in row['defs'].split()]
    qsf += ['set_instance_assignment -name VIRTUAL_PIN ON -to ' + name for name in pins]
    # The only physical input is the clock; all data goes through boundary FFs.
    qsf += ['set_instance_assignment -name IO_STANDARD "3.3-V LVTTL" -to clk']
    (out / 'probe.qsf').write_text('\n'.join(qsf) + '\n')
    # Quartus appends version and pin-reservation metadata to probe.qsf.
    # Preserve the exact input settings separately for reproducibility.
    shutil.copy2(out / 'probe.qsf', out / 'probe-input.qsf')
    (out / 'probe.qpf').write_text('PROJECT_REVISION = "probe"\n')
    input_pins = ' '.join(name + ('[*]' if width else '') for direction, width, name in ports
                          if direction == 'input' and name not in ('clk', 'gpu_enable'))
    (out / 'probe.sdc').write_text(f'create_clock -name gpu_clk -period {1000 / args.mhz:.6f} [get_ports clk]\n'
        'derive_clock_uncertainty\n'
        f'set_input_delay -clock gpu_clk -max 0 [get_ports {{{input_pins}}}]\n'
        f'set_input_delay -clock gpu_clk -min 0 [get_ports {{{input_pins}}}]\n'
        'set_output_delay -clock gpu_clk -max 0 [all_outputs]\n'
        'set_output_delay -clock gpu_clk -min 0 [all_outputs]\n')
    shutil.copy2(ROOT / 'tools/report_core_timing.tcl', out / 'report_core_timing.tcl')
    # Summarize more endpoints than the board report, so a single wide
    # arithmetic result cannot conceal other paths needing pipeline stages.
    report = out / 'report_core_timing.tcl'
    report.write_text(report.read_text().replace(
        'report_timing -$check -npaths 20 -detail full_path',
        'report_timing -$check -npaths 200 -detail full_path'))
    inputs = [out / name for name in sources] + [out / 'probe-input.qsf', out / 'probe.sdc']
    inputs += [report]
    manifest = dict(variant=row, parameters=params, mhz=args.mhz, seed=args.seed, retime=args.retime,
        scope='Isolated os30 GPU with registered virtual I/O; not a board image.',
        git_head=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
        sources={str(p.relative_to(out)): digest(p) for p in inputs})
    save(out / 'manifest.json', manifest)
    start = time.time()
    returncodes = {}
    for phase, command in [('map', ['quartus_map', 'probe']),
                           ('fit', ['quartus_fit', 'probe']),
                           ('timing', ['quartus_sta', '-t', 'report_core_timing.tcl', 'probe', 'timing'])]:
        save(out / 'status.json', dict(phase=phase, start=start))
        print('START', phase, flush=True)
        with (out / (phase + '.log')).open('w') as log:
            result = subprocess.run(docker(out, 'pocket', out, command), stdout=log, stderr=subprocess.STDOUT)
        returncodes[phase] = result.returncode
        if result.returncode and phase != 'timing':
            raise RuntimeError(f'{phase} failed; see {out / (phase + ".log")}')
    with (out / 'timing/summary.tsv').open() as stream:
        timing = list(csv.DictReader(stream, delimiter='\t'))
    checks = {'setup', 'hold', 'recovery', 'removal', 'pulse_width'}
    corners = {(r['model'], r['temperature']) for r in timing}
    if (len(timing) != 20 or len(corners) != 4
            or {(r['model'], r['temperature'], r['check']) for r in timing}
            != {(m, t, c) for m, t in corners for c in checks}):
        raise ValueError('Expected all five timing checks at four corners')
    for row in timing:
        if row['slack'] == 'no_paths':
            if row['check'] in ('setup', 'hold', 'pulse_width'):
                raise ValueError('Missing required timing paths')
        elif not math.isfinite(float(row['slack'])):
            raise ValueError('Non-finite timing slack')
    worst = {check: min(float(r['slack']) for r in timing if r['check'] == check and r['slack'] != 'no_paths')
             for check in ['setup', 'hold', 'pulse_width']}
    summary = (out / 'output_files/probe.fit.summary').read_text()
    alms = re.search(r'Logic utilization \(in ALMs\) : ([\d,]+)', summary)
    if not alms:
        raise ValueError('Missing fitter resource summary')
    result = dict(mhz=args.mhz, seed=args.seed, retime=args.retime,
                  alms=int(alms[1].replace(',', '')), worst_ns=worst, seconds=time.time()-start,
                  timing=timing, returncodes=returncodes,
                  timing_pass=all(float(r['slack']) >= 0 for r in timing if r['slack'] != 'no_paths'))
    # A complete negative-slack report intentionally makes quartus_sta fail.
    # An unexpected error must not be reported as a successful timing check.
    if returncodes['timing'] and result['timing_pass']:
        raise RuntimeError('Timing analysis failed despite nonnegative slack; inspect timing.log')
    ignored = [line for phase in ('fit', 'timing')
               for line in (out / (phase + '.log')).read_text().splitlines()
               if 'Ignored filter at probe.sdc' in line]
    if ignored:
        raise RuntimeError('Unmatched timing constraints; inspect fit.log and timing.log')
    save(out / 'results.json', result)
    save(out / 'status.json', dict(phase='complete', start=start, seconds=result['seconds']))
    print(json.dumps(result, indent=2), flush=True)


if __name__ == '__main__':
    main()
