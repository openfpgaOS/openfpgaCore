#!/usr/bin/env python3
"""Fit a private CPU in complete ordinary os30, one explicit placement at a time."""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import sys

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / 'docs/measurements/os30-dual-20261004'
sys.path.insert(0, str(ROOT / 'tools'))
import sweep_target_matrix as fit


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('label')
    parser.add_argument('--placement-seed', type=int,
                        help='One explicit placement seed; default is the saved os30 seed')
    parser.add_argument('--cpu', type=Path, required=True)
    parser.add_argument('--gpu', type=Path,
                        help='Optional private GPU RTL; production source remains unchanged')
    parser.add_argument('--render-cache', type=Path,
                        help='Optional private color/depth cache RTL')
    parser.add_argument('--texture-cache', type=Path,
                        help='Optional private texture cache RTL')
    parser.add_argument('--audio-mixer', type=Path,
                        help='Optional private audio mixer RTL')
    parser.add_argument('--cpu-system', type=Path,
                        help='Optional private CPU system/interconnect RTL')
    parser.add_argument('--peripheral-slave', type=Path,
                        help='Optional private peripheral read/write slave RTL')
    parser.add_argument('--target-port-per', type=Path,
                        help='Optional private uncached CRAM target port RTL')
    parser.add_argument('--cpu-speed', action='store_true',
                        help='Use speed synthesis only in the CPU hierarchy; verify applied scope in map report')
    parser.add_argument('--area', action='store_true')
    parser.add_argument('--optimization-mode', choices=('BALANCED', 'HIGH PERFORMANCE EFFORT'),
                        help='Override the mode after the area preset, retaining explicit synthesis/retiming settings')
    parser.add_argument('--combo-area', action='store_true',
                        help='Enable Quartus combinational physical synthesis for area')
    parser.add_argument('--combo-speed', action='store_true',
                        help='Enable Quartus combinational physical synthesis for timing')
    parser.add_argument('--physical-effort', choices=('NORMAL', 'EXTRA'),
                        help='Physical synthesis effort; unchanged seed and timing constraints')
    parser.add_argument('--register-duplication', action='store_true',
                        help='Allow physical register duplication after the area preset')
    parser.add_argument('--register-packing', choices=('MINIMIZE_AREA', 'MINIMIZE_AREA_WITH_CHAINS'),
                        help='Explicit Quartus register packing mode; still requires full timing checks')
    parser.add_argument('--placement-effort', type=float, choices=(1.0, 2.0, 3.0),
                        help='Quartus placement effort multiplier; no seed or timing constraint changes')
    parser.add_argument('--router-effort', type=float, choices=(1.0, 2.0, 4.0),
                        help='Cyclone V router effort multiplier; verify physical result rather than parser acceptance')
    parser.add_argument('--register-retiming', action='store_true',
                        help='Enable supported Quartus physical register retiming; inspect actual applied optimizations')
    parser.add_argument('--decode-ready-fanout', action='store_true',
                        help='Keep the decode1 load-control cell and request fanout at most 64')
    parser.add_argument('--decode-ready-fanout-limit', type=int, choices=(16, 32, 64), default=64,
                        help='Maximum fanout for --decode-ready-fanout (default: 64)')
    parser.add_argument('--stage2-ready-fanout-limit', type=int, choices=(16, 32, 64),
                        help='Buffer the measured decode_ctrls_1_down_ready capture-enable driver')
    parser.add_argument('--stage2-ready-global', action='store_true',
                        help='Route the stage2 capture-enable driver on an FPGA global network')
    parser.add_argument('--sdram-rx-labs', action='store_true',
                        help='Place SDRAM receive registers in eight existing north-edge LABs')
    parser.add_argument('--texture-queues-bram', action='store_true',
                        help='Use existing synchronous block-RAM texture FIFO implementation')
    parser.add_argument('--write-buffer-bram', action='store_true',
                        help='Map synchronous SDRAM burst buffer to M10K instead of MLAB')
    args = parser.parse_args()
    if args.placement_seed is not None and args.placement_seed < 1:
        parser.error('--placement-seed must be positive')
    if args.stage2_ready_global and args.stage2_ready_fanout_limit is not None:
        parser.error('Choose either a global ready route or local fanout buffering')
    if not args.decode_ready_fanout and args.decode_ready_fanout_limit != 64:
        parser.error('--decode-ready-fanout-limit requires --decode-ready-fanout')
    if not re.fullmatch(r'[a-z0-9_-]+', args.label):
        parser.error('label must contain lowercase letters, digits, underscores or hyphens')
    cpu = args.cpu.resolve(strict=True)
    gpu = args.gpu.resolve(strict=True) if args.gpu else None
    render_cache = args.render_cache.resolve(strict=True) if args.render_cache else None
    texture_cache = args.texture_cache.resolve(strict=True) if args.texture_cache else None
    audio_mixer = args.audio_mixer.resolve(strict=True) if args.audio_mixer else None
    cpu_system = args.cpu_system.resolve(strict=True) if args.cpu_system else None
    peripheral_slave = args.peripheral_slave.resolve(strict=True) if args.peripheral_slave else None
    target_port_per = args.target_port_per.resolve(strict=True) if args.target_port_per else None
    output = OUT / ('fit-' + args.label)
    for key in ('OS30_GEOMETRY', 'OS30_GEOMETRY_WORKER', 'OS30_DEPTH_PREFETCH',
                'OS30_SDRAM166'):
        os.environ.pop(key, None)
    original_inventory = fit.inventory

    def inventory(**kwargs):
        rows = original_inventory(**kwargs)
        for row in rows:
            if row['target'] != 'pocket' or row['variant'] != 'os30':
                continue
            row['saved_seed'] = row['seed']
            if args.placement_seed is not None:
                row['seed'] = args.placement_seed
            defs = row['defs'].split()
            assert {'INCLUDE_VERT_TRI', 'INCLUDE_XFORM', 'INCLUDE_TEX_MEM'} <= set(defs)
            assert not {'INCLUDE_GPU_GEOMETRY_WORKER', 'INCLUDE_GPU_PLANE_INPUT',
                        'INCLUDE_OS30_SDRAM166'} & set(defs)
            if args.texture_queues_bram:
                defs.remove('INCLUDE_TEX_QUEUE_MLAB')
            row['defs'] = ' '.join(defs)
        return rows

    original_freeze = fit.freeze

    def freeze(out, rows):
        frozen = original_freeze(out, rows)
        assert len(rows) == 1 and rows[0]['variant'] == 'os30'
        dest = frozen / 'src/fpga/vendor/vexriscv/VexiiRiscv/VexiiRiscv_os30.v'
        shutil.copy2(cpu, dest)
        rows[0]['cpu_sha256'] = fit.digest(dest)
        rows[0]['private_cpu_source'] = str(cpu)
        if gpu:
            shutil.copy2(gpu, frozen / 'src/fpga/common/gpu_core.v')
        if render_cache:
            shutil.copy2(render_cache, frozen / 'src/fpga/common/gpu_color_depth_cache.sv')
        if texture_cache:
            shutil.copy2(texture_cache, frozen / 'src/fpga/common/gpu_tex_cache.v')
        if audio_mixer:
            shutil.copy2(audio_mixer, frozen / 'src/fpga/common/audio_mixer.v')
        if cpu_system:
            shutil.copy2(cpu_system, frozen / 'src/fpga/common/cpu_system.v')
        if peripheral_slave:
            shutil.copy2(peripheral_slave, frozen / 'src/fpga/common/axi_periph_slave.v')
        if target_port_per:
            shutil.copy2(target_port_per, frozen / 'src/fpga/common/cpu_target_port_per.v')
        qsf = frozen / 'src/fpga/targets/pocket/ap_core.qsf'
        if args.write_buffer_bram:
            old = 'set_instance_assignment -name RAMSTYLE_ATTRIBUTE MLAB -to *sdram_axi_slave|wbuf*'
            data = qsf.read_text()
            assert data.count(old) == 1
            qsf.write_text(data.replace(old, old.replace('MLAB', 'M10K')))
        if args.area:
            with qsf.open('a') as settings:
                settings.write('\nset_global_assignment -name OPTIMIZATION_MODE BALANCED\n'
                               'set_global_assignment -name OPTIMIZATION_TECHNIQUE AREA\n'
                               'set_global_assignment -name PHYSICAL_SYNTHESIS_REGISTER_DUPLICATION OFF\n')
        if args.combo_area:
            with qsf.open('a') as settings:
                settings.write('\nset_global_assignment -name PHYSICAL_SYNTHESIS_COMBO_LOGIC_FOR_AREA ON\n')
        if args.optimization_mode:
            with qsf.open('a') as settings:
                settings.write('\nset_global_assignment -name OPTIMIZATION_MODE "'
                               + args.optimization_mode + '"\n')
        if args.combo_speed:
            with qsf.open('a') as settings:
                settings.write('\nset_global_assignment -name PHYSICAL_SYNTHESIS_COMBO_LOGIC ON\n')
        if args.physical_effort:
            with qsf.open('a') as settings:
                settings.write('\nset_global_assignment -name PHYSICAL_SYNTHESIS_EFFORT '
                               + args.physical_effort + '\n')
        if args.register_duplication:
            with qsf.open('a') as settings:
                settings.write('\nset_global_assignment -name PHYSICAL_SYNTHESIS_REGISTER_DUPLICATION ON\n')
        if args.cpu_speed:
            with qsf.open('a') as settings:
                settings.write('\nset_instance_assignment -name OPTIMIZATION_TECHNIQUE SPEED -to ic|cpu|cpu\n')
        if args.register_packing:
            with qsf.open('a') as settings:
                settings.write('\nset_global_assignment -name AUTO_PACKED_REGISTERS_STRATIXII '
                               + args.register_packing + '\n')
        if args.placement_effort is not None:
            with qsf.open('a') as settings:
                settings.write('\nset_global_assignment -name PLACEMENT_EFFORT_MULTIPLIER '
                               + str(args.placement_effort) + '\n')
        if args.router_effort is not None:
            with qsf.open('a') as settings:
                settings.write('\nset_global_assignment -name ROUTER_EFFORT_MULTIPLIER '
                               + str(args.router_effort) + '\n')
        if args.register_retiming:
            with qsf.open('a') as settings:
                settings.write('\nset_global_assignment -name PHYSICAL_SYNTHESIS_REGISTER_RETIMING ON\n')
        if args.decode_ready_fanout:
            fanout = Path(__file__).with_name('decode-ready-fanout.qsf')
            data = fanout.read_text()
            assert data.count('set_instance_assignment ') == 2
            assert data.count('-to ic|cpu|cpu|decode_skid_gate_down_ready') == 2
            old_limit = '-name MAX_FANOUT 64 '
            assert data.count(old_limit) == 1
            data = data.replace(old_limit, '-name MAX_FANOUT '
                                + str(args.decode_ready_fanout_limit) + ' ', 1)
            with qsf.open('a') as settings:
                settings.write('\n' + data)
            (out / fanout.name).write_text(data)
        if args.stage2_ready_fanout_limit is not None:
            stage2_fanout = out / 'stage2-ready-fanout.qsf'
            node = 'ic|cpu|cpu|decode_ctrls_1_down_ready'
            data = ('set_instance_assignment -name IMPLEMENT_AS_OUTPUT_OF_LOGIC_CELL ON -to ' + node + '\n'
                    'set_instance_assignment -name MAX_FANOUT ' + str(args.stage2_ready_fanout_limit)
                    + ' -to ' + node + '\n')
            stage2_fanout.write_text(data)
            with qsf.open('a') as settings:
                settings.write('\n' + data)
        if args.stage2_ready_global:
            stage2_global = out / 'stage2-ready-global.qsf'
            node = 'ic|cpu|cpu|decode_ctrls_1_down_ready'
            data = ('set_instance_assignment -name IMPLEMENT_AS_OUTPUT_OF_LOGIC_CELL ON -to ' + node + '\n'
                    'set_instance_assignment -name GLOBAL_SIGNAL "GLOBAL CLOCK" -to ' + node + '\n')
            stage2_global.write_text(data)
            with qsf.open('a') as settings:
                settings.write('\n' + data)
        placement = Path(__file__).with_name('sdram_rx_labs.qsf')
        if args.sdram_rx_labs:
            # These are existing rising-edge receive registers, downstream of
            # the hardened falling-to-rising DDIO capture. No cycle, pin or
            # timing exception changes. LAB location assignments work without
            # the subscription-only LogicLock feature in this Quartus edition.
            data = placement.read_text()
            assert data.count('set_location_assignment ') == 64
            # Quartus' direct QSF reader preserves Tcl braces literally here.
            # Require the canonical exact instance form verified by project-open.
            locations = re.findall(r'^set_location_assignment LAB_X\d+_Y\d+ -to '
                                   r'(ic\|isr0\|(?:burst_data|word_q)\[\d+\])$',
                                   data, re.M)
            expected = {f'ic|isr0|{bus}[{bit}]'
                        for bus in ('burst_data', 'word_q') for bit in range(32)}
            assert len(locations) == 64 and set(locations) == expected
            with qsf.open('a') as settings:
                settings.write('\n' + data)
            shutil.copy2(placement, out / 'sdram_rx_labs.qsf')
        fit.save(out / 'inventory.json', rows)
        fit.save(out / 'experiment.json', dict(cpu_source=str(cpu), cpu_sha256=fit.digest(dest),
                 gpu_source=str(gpu) if gpu else None,
                 gpu_sha256=fit.digest(gpu) if gpu else None,
                 render_cache_source=str(render_cache) if render_cache else None,
                 render_cache_sha256=fit.digest(render_cache) if render_cache else None,
                 texture_cache_source=str(texture_cache) if texture_cache else None,
                 texture_cache_sha256=fit.digest(texture_cache) if texture_cache else None,
                 audio_mixer_source=str(audio_mixer) if audio_mixer else None,
                 audio_mixer_sha256=fit.digest(audio_mixer) if audio_mixer else None,
                 cpu_system_source=str(cpu_system) if cpu_system else None,
                 cpu_system_sha256=fit.digest(cpu_system) if cpu_system else None,
                 peripheral_slave_source=str(peripheral_slave) if peripheral_slave else None,
                 peripheral_slave_sha256=fit.digest(peripheral_slave) if peripheral_slave else None,
                 target_port_per_source=str(target_port_per) if target_port_per else None,
                 target_port_per_sha256=fit.digest(target_port_per) if target_port_per else None,
                 cpu_speed=args.cpu_speed,
                 area=args.area, combo_area=args.combo_area,
                 optimization_mode=args.optimization_mode,
                 combo_speed=args.combo_speed, physical_effort=args.physical_effort,
                 register_duplication=args.register_duplication,
                 register_packing=args.register_packing,
                 placement_effort=args.placement_effort,
                 router_effort=args.router_effort,
                 register_retiming=args.register_retiming,
                 decode_ready_fanout=args.decode_ready_fanout,
                 decode_ready_fanout_limit=args.decode_ready_fanout_limit if args.decode_ready_fanout else None,
                 decode_ready_fanout_sha256=fit.digest(out / fanout.name) if args.decode_ready_fanout else None,
                 stage2_ready_fanout_limit=args.stage2_ready_fanout_limit,
                 stage2_ready_fanout_sha256=fit.digest(stage2_fanout) if args.stage2_ready_fanout_limit is not None else None,
                 stage2_ready_global=args.stage2_ready_global,
                 stage2_ready_global_sha256=fit.digest(stage2_global) if args.stage2_ready_global else None,
                 sdram_rx_labs=args.sdram_rx_labs,
                 sdram_rx_labs_sha256=fit.digest(placement) if args.sdram_rx_labs else None,
                 texture_queues_bram=args.texture_queues_bram,
                 write_buffer_bram=args.write_buffer_bram,
                 full_triangle_gpu=True, texture_memory=True, geometry_worker=False,
                 clock_mhz=100, production_modified=False, seed_sweep=False))
        experiment = json.loads((out / 'experiment.json').read_text())
        experiment.update(saved_seed=rows[0]['saved_seed'], placement_seed=rows[0]['seed'],
                          explicit_placement_comparison=args.placement_seed is not None)
        fit.save(out / 'experiment.json', experiment)
        fit.save(out / 'frozen-sources.json', {
            str(p.relative_to(frozen)): fit.digest(p)
            for p in sorted(frozen.rglob('*')) if p.is_file()})
        (out / 'NOT_DEPLOYABLE.txt').write_text(
            'Private CPU feasibility experiment. Fit success alone does not establish '
            'timing closure, application performance or device qualification.\n')
        return frozen

    fit.inventory = inventory
    fit.freeze = freeze
    sys.argv = [sys.argv[0], '--target', 'pocket', '--variant', 'os30',
                '--seed-offsets', '0', '--jobs', '1', '--processors', '4',
                '--output', str(output)]
    fit.main()
    for result in json.loads((output / 'results.json').read_text()):
        directory = Path(result.get('directory', output))
        report = directory / 'output_files/ap_core.fit.rpt'
        if not report.is_file():
            continue
        data = report.read_text(errors='replace')
        counts = {}
        for key, pattern in {
            'alms': r';\s*Logic utilization \(in ALMs\)\s*;\s*([\d,.]+)\s*/\s*([\d,]+)',
            'labs': r';\s*(?:Total LABs:\s*partially or completely used|Logic Array Blocks)\s*;\s*([\d,]+)\s*/\s*([\d,]+)',
            'm10ks': r';\s*M10K blocks\s*;\s*([\d,]+)\s*/\s*([\d,]+)',
        }.items():
            match = re.search(pattern, data)
            if match:
                counts[key] = [float(v.replace(',', '')) for v in match.groups()]
        counts.update(valid=result['valid'], error=result.get('error'),
                      timing=result.get('worst'), fit_report=str(report))
        fit.save(output / 'resource-summary.json', counts)
        print(json.dumps(counts, indent=2))


if __name__ == '__main__':
    main()
