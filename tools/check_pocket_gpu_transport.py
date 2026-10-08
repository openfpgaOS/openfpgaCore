#!/usr/bin/env python3
"""Compare SDK command uploads on the Pocket CPU, GPU and SDRAM RTL.

Reports component workload cycles and exact pixels, not complete-game FPS.
With --no-command-dma, checks the SDK against a core with the DMA removed.

This legacy fixture reads common modules directly, without variants/*.sources.
See check_gpu_target_matrix.py for the separate selected-os30 source provenance.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
from check_gpu_recip_cache import FLAGS
from check_pocket_memory_contention import once, pocket_sdram_twin

ROOT = Path(__file__).resolve().parents[1]


def run(command, logfile, **kwargs):
    with logfile.open('w') as log:
        subprocess.run(command, stdout=log, stderr=subprocess.STDOUT,
                       check=True, **kwargs)


def sdram_parameters(config, output):
    """Read controller settings from the common top, without variant source maps."""
    def decimal(value):
        match = re.fullmatch(r"(?:\d+'[dD])?(\d+)", value.strip())
        if not match:
            raise ValueError('Non-literal SDRAM parameter: ' + value)
        return int(match[1])

    target = ROOT / 'src/fpga/targets/pocket'
    controller = re.sub(r'//[^\n]*', '', (target / 'io_sdram.v').read_text())
    defaults = controller.split(') (', 1)[0]
    params = {name: decimal(value) for name, value in
              re.findall(r'parameter\s+(\w+)\s*=\s*([^,\n]+)', defaults)}
    if config:
        if config['variant']['target'] != 'pocket':
            raise ValueError('The transport fixture requires a Pocket variant')
        command = ['verilator', '-E', '-P', '-I' + str(target),
                   '-I' + str(target / 'apf'),
                   *['+define+' + d for d in config['variant']['defs'].split()],
                   str(target / 'core_top.v')]
        pre = subprocess.run(command, capture_output=True, text=True, check=True).stdout
        (output / 'preprocessed-pocket.v').write_text(pre)
        pre = re.sub(r'//[^\n]*', '', pre)
        match = re.search(r'io_sdram\s*#\((.*?)\)\s*isr0\s*\(', pre, re.S)
        if not match:
            raise ValueError('Production SDRAM instance not found')
        overrides = re.findall(r'\.(\w+)\(\s*([^()]+)\s*\)', match[1])
        if len(overrides) != match[1].count('.'):
            raise ValueError('Unhandled SDRAM parameter expression')
        for name, value in overrides:
            if name not in params:
                raise ValueError('Unknown SDRAM parameter: ' + name)
            params[name] = decimal(value)
    return params


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--output', type=Path, required=True)
    ap.add_argument('--cpu', type=Path, default=ROOT /
                    'src/fpga/vendor/vexriscv/VexiiRiscv/VexiiRiscv_os25.v')
    ap.add_argument('--no-command-dma', action='store_true')
    ap.add_argument('--gpu-config', type=Path,
                    help='GPU manifest produced by check_gpu_target_matrix.py')
    ap.add_argument('--triangles', action='store_true',
                    help='Benchmark RGB565 triangle batches instead of column lists')
    ap.add_argument('--gpu-header', type=Path, default=ROOT / 'src/firmware/api/of_gpu.h',
                    help='SDK header to benchmark, retaining the other production API headers')
    ap.add_argument('--reuse-rtl', type=Path,
                    help='Previous output directory with an identical RTL/source manifest')
    ap.add_argument('--cflag', action='append', default=[],
                    help='Additional firmware compiler option, e.g. --cflag=-falign-loops=16')
    ap.add_argument('--jobs', type=int, default=4)
    args = ap.parse_args()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    test = ROOT / 'src/fpga/test'
    cpu = args.cpu.resolve()
    header = args.gpu_header.resolve()
    (out / 'of_gpu.h').write_bytes(header.read_bytes())
    run(['python3', str(test / 'cpu_stress/generate.py'), str(out)], out / 'generate.log')
    for name in ('start.S', 'link.ld'):
        (out / name).write_bytes((test / 'cpu_stress' / name).read_bytes())
    program = ROOT / 'tools/tests/pocket_gpu_transport.c'
    (out / 'main.c').write_bytes(program.read_bytes())
    # Only declarations are needed from libc; the freestanding fixture provides
    # memcpy/memset and UART output. The GPU API is the unmodified SDK header.
    (out / 'string.h').write_text('#include <stddef.h>\n'
        'void *memset(void *, int, size_t);\nvoid *memcpy(void *, const void *, size_t);\n')
    command = ['riscv64-unknown-elf-gcc', '-march=rv32imafc_zicsr_zifencei',
               '-mabi=ilp32f', '-O2', '-ffreestanding', '-fno-builtin',
               '-nostdlib', '-nostartfiles', '-DFIRST_MODE=' + str(int(args.no_command_dma)),
               '-DTRANSPORT_TRIANGLES=' + str(int(args.triangles)),
               '-I.', '-I' + str(ROOT / 'src/firmware/api'), '-Wl,-T,link.ld',
               '-Wl,--no-relax', *args.cflag, 'start.S', 'main.c', '-o', 'transport.elf']
    container = ['docker', 'run', '--rm', '--user', f'{os.getuid()}:{os.getgid()}',
                 '-v', f'{ROOT}:{ROOT}', '-v', f'{out}:{out}', '-w', str(out),
                 'openfpgaos-firmware']
    run([*container, *command], out / 'firmware-build.log')
    run([*container, 'riscv64-unknown-elf-objcopy', '-O', 'binary',
         'transport.elf', 'transport.bin'], out / 'objcopy.log')
    config = json.loads(args.gpu_config.read_text()) if args.gpu_config else None
    memory = sdram_parameters(config, out)
    bank_row_track = memory['BANK_ROW_TRACK']
    twin = pocket_sdram_twin(ROOT, out)
    top = (test / 'tb_system_sdram.v').read_text()
    top, count = re.subn(r'io_sdram\s+(\w+)\s*\(',
        "assign phy_ncs = 1'b0;\nio_sdram #(" +
        ','.join(f'.{key}({value})' for key, value in memory.items()) + r") \1 (", top)
    if count != 1:
        raise RuntimeError('SDRAM instance changed')
    top = once(top, '    .phy_ncs(phy_ncs),', '')
    flags = [*FLAGS, '-GINCLUDE_CPU_RING=1', '-GGPU_WRITE_COMBINE_FAST_FLUSH=1',
             '-GGPU_WRITE_GATHER=1', '-GGPU_MASKED_WRITE_BURSTS=1',
             '-GINCLUDE_COMMAND_DMA=' + str(int(not args.no_command_dma))]
    if config:
        settings = dict(config['parameters'], INCLUDE_CPU_RING=1,
                        INCLUDE_COMMAND_DMA=int(not args.no_command_dma))
        flags = ['+define+' + name for name in config['variant']['defs'].split()]
        flags += ['-G' + name + '=' + str(value) for name, value in settings.items()]
    if args.triangles and not (config and settings['INCLUDE_VERT_TRI']
                              and settings['INCLUDE_DIRECT_COLOR']):
        ap.error('--triangles requires a GPU manifest with truecolor vertex triangles')
    params = [f'.{key}({value})' for key, value in
              (flag[2:].split('=') for flag in flags if flag.startswith('-G'))]
    top = once(top, 'gpu_core gpu (', 'gpu_core #(' + ','.join(params) + ') gpu (')
    (out / 'tb_transport.v').write_text(top)
    cpp = (test / 'tb_system_sdram_main.cpp').read_text()
    # A long GPU fence wait can execute entirely from I-cache. It is not a
    # dead CPU just because there have been no CPU SDRAM requests for 1.5M cycles.
    cpp = once(cpp, 'c - last_m1_activity > 1500000', 'c - last_m1_activity > 15000000')
    cpp = once(cpp, '    uint32_t final_pc =',
        '    std::printf("\\n=== UART BEGIN ===\\n%s\\n=== UART END ===\\n", uart_buf.c_str());\n'
        '    uint32_t final_pc =')
    # Sample accepted AXI writes before the edge; chip write events are
    # registered and checked after the edge. Include all masters in the oracle.
    cpp = once(cpp, '        tick_cycle();\n        if (tb->inj_burst_rd)', '''        tick_half(false);
        const bool aw_take = tb->dbg_aw_valid && tb->dbg_aw_ready;
        const uint32_t aw_addr = tb->dbg_aw_addr, aw_len = tb->dbg_aw_len;
        const bool w_take = tb->dbg_w_valid && tb->dbg_w_ready;
        const uint32_t w_data = tb->dbg_w_data, w_strb = tb->dbg_w_strb;
        tb->vsync = (cpu_cycles % VSYNC_PERIOD == 0);
        tick_half(true); cpu_cycles++;
        if (tb->inj_burst_rd)''')
    cpp = once(cpp, 'if (tb->dbg_aw_valid && tb->dbg_aw_ready)\n'
        '            aw_fifo.push_back({tb->dbg_aw_addr >> 2, tb->dbg_aw_len});',
        'if (aw_take) aw_fifo.push_back({aw_addr >> 2, aw_len});')
    cpp = once(cpp, 'if (tb->dbg_w_valid && tb->dbg_w_ready)\n'
        '            push_w_beat(tb->dbg_w_data, tb->dbg_w_strb, c);',
        'if (w_take) push_w_beat(w_data, w_strb, c);')
    (out / 'main.cpp').write_text(cpp)
    (out / 'test.mk').write_text(f'include {test}/Makefile\n'
        f'SYSTEM_SDRAM_SRCS := $(subst tb_system_sdram.v,{out}/tb_transport.v,$(SYSTEM_SDRAM_SRCS))\n'
        f'SYSTEM_SDRAM_SRCS := $(subst io_sdram_mister_test.v,{twin},$(SYSTEM_SDRAM_SRCS))\n')
    build_command = ['make', '-f', str(out / 'test.mk'), str(out / 'obj/Vtb_system'),
         f'SYSTEM_SDRAM_DIR={out}/obj', f'SYSTEM_SDRAM_CPP={out}/main.cpp',
         f'VEXII_MISTER={cpu}', f'VERILATOR=verilator -j {args.jobs} ' +
         ' '.join(flag for flag in flags if flag.startswith('+define+'))]
    inputs = [program, cpu, header,
              ROOT / 'src/fpga/targets/pocket/io_sdram.v',
              *sorted((ROOT / 'src/fpga/common').glob('*.v'))]
    report = dict(no_command_dma=args.no_command_dma, triangles=args.triangles,
                  bank_row_track=bank_row_track, sdram_parameters=memory,
                  gpu_config=config, flags=flags, compiler=command, sources={
        str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs}, runs={})
    rtl_inputs = [cpu, ROOT / 'src/fpga/targets/pocket/io_sdram.v',
                  ROOT / 'src/fpga/targets/pocket/core_top.v',
                  *sorted((ROOT / 'src/fpga/common').glob('*.v')),
                  test / 'tb_system_sdram.v', test / 'tb_system_sdram_main.cpp',
                  test / 'sdram_model_full.v', test / 'cram0_sim_model.v', test / 'bram_model.v',
                  ROOT / 'src/fpga/targets/pocket/cram0_cdc.v',
                  ROOT / 'src/fpga/targets/pocket/snac_shifter.v',
                  ROOT / 'src/fpga/targets/pocket/apf/common.v',
                  test / 'Makefile', Path(__file__).resolve(),
                  ROOT / 'tools/check_pocket_memory_contention.py']
    report['rtl'] = dict(flags=flags, bank_row_track=bank_row_track,
                        sdram_parameters=memory,
                        inputs={str(p): hashlib.sha256(p.read_bytes()).hexdigest()
                                            for p in rtl_inputs})
    simulator = out / 'obj/Vtb_system'
    if args.reuse_rtl:
        reuse = args.reuse_rtl.resolve()
        previous = json.loads((reuse / 'results.json').read_text())
        if previous.get('rtl') != report['rtl']:
            raise RuntimeError('Cannot reuse simulator: RTL configuration or inputs changed')
        simulator = Path(previous['simulator'])
        if hashlib.sha256(simulator.read_bytes()).hexdigest() != previous['simulator_sha256']:
            raise RuntimeError('Cannot reuse simulator: executable changed')
    else:
        run(build_command, out / 'build.log', cwd=test)
    report['simulator'] = str(simulator)
    report['simulator_sha256'] = hashlib.sha256(simulator.read_bytes()).hexdigest()
    for name, scan in [('quiet', '0'), ('scanout', '1')]:
        env = dict(os.environ, SCAN_ENABLE=scan, SCAN_PERIOD='8333', SCAN_LEN='80',
                   SCAN_ACTIVE='200', SCAN_TOTAL='200', SCAN_BASE_HW='0x1800000',
                   STRIKE_SCAN_PERIOD='1000000000')
        run([str(simulator), 'transport.bin', '40000000'],
            out / (name + '.log'), cwd=out, env=env)
        log = (out / (name + '.log')).read_text()
        uart = log.split('=== UART BEGIN ===')[-1].split('=== UART END ===')[0]
        rows = [[int(x, 16) for x in line.split()]
                for line in re.findall(r'^TRANSPORT ([0-9a-f ]+)$', uart, re.M)]
        if len(rows) != (8 if args.no_command_dma else 16) or 'TRANSPORT PASS HAL init' not in uart:
            raise RuntimeError(f'{name}: missing workload results')
        if 'WCONF: mism=0 spurious=0 exp_fifo=0 aw_fifo=0 run_left=0' not in log:
            raise RuntimeError(f'{name}: AXI write delivery failed')
        if not args.no_command_dma:
            for before, after in zip(rows[::2], rows[1::2]):
                if before[:2] != after[:2] or before[-1] != after[-1]:
                    raise RuntimeError(f'{name}: workload IDs or output hashes differ')
        report['runs'][name] = rows
        print(name, uart, flush=True)
    (out / 'results.json').write_text(json.dumps(report, indent=2) + '\n')


if __name__ == '__main__':
    main()
