#!/usr/bin/env python3
"""Compare Pocket SDRAM bank tracking with actual mixer RTL and fixed transfers.

Requires Verilator and a C++ compiler. Checks read/write data, SDRAM protocol,
48 kHz service and exact PCM equality against both the baseline and a quiet
reference. Throughput is a synthetic mixed-bank workload, not game FPS.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import re
import subprocess


def once(text, old, new):
    if text.count(old) != 1:
        raise RuntimeError(f"Unexpected fixture/source structure: {old!r}")
    return text.replace(old, new)


def pocket_sdram_twin(root, output):
    source = root / 'src/fpga/targets/pocket/io_sdram.v'
    text = source.read_text()
    text = once(text, 'inout   wire    [15:0]  phy_dq,',
                'input wire [15:0] phy_dq_in,\noutput wire [15:0] phy_dq_out_port,\noutput wire phy_dq_oe_port,')
    text = once(text, "assign          phy_dq = phy_dq_oe ? phy_dq_out : 16'bZZZZZZZZZZZZZZZZ;",
                'assign phy_dq_out_port = phy_dq_out;\nassign phy_dq_oe_port = phy_dq_oe;')
    text = re.sub(r'\bphy_dq\b', 'phy_dq_in', text)
    twin = output / 'io_sdram_pocket_test.v'
    twin.write_text(text)
    return twin


def fixture(root, output):
    twin = pocket_sdram_twin(root, output)
    text = (root / 'src/fpga/test/tb_sdram_cont.v').read_text()
    # Reuse the production arbiter/slave/controller wiring, replacing only
    # the external M3 aggressor with the actual mixer and exposing test taps.
    text = '// Pocket memory contention fixture, generated for testing.\n' + text[text.index('`default_nettype'):]
    text = once(text, 'parameter BANK_ROW_TRACK = 1',
                'parameter BANK_ROW_TRACK = 0, parameter REFRESH_CYCLES = 736')
    text = once(text, '.BANK_ROW_TRACK(BANK_ROW_TRACK)',
                '.BANK_ROW_TRACK(BANK_ROW_TRACK), .REFRESH_INTERVAL(REFRESH_CYCLES)')
    text = once(text, 'axi_sdram_arbiter sdram_arb (',
                'axi_sdram_arbiter #(.CPU_FAIR_THRESHOLD(8)) sdram_arb (')
    for name in ('m3_arvalid', 'm3_araddr', 'm3_arlen', 'm3_rready'):
        text, count = re.subn(r'input(\s+wire[^\n]*\b' + name + r'\b)', r'output\1', text)
        if count != 1:
            raise RuntimeError(f'Unexpected M3 port: {name}')
    text = once(text, '    // ---- Diagnostics ----', '''    input wire voice_wr,
    output wire voice_wr_ready,
    input wire [3:0] voice_field,
    input wire [4:0] voice_sel,
    input wire [31:0] voice_wdata,
    input wire mixer_enable,
    input wire [9:0] fifo_level,
    output wire sample_wr,
    output wire [31:0] sample_data,
    input wire bd_we,
    input wire [23:0] bd_word_addr,
    input wire [31:0] bd_wdata,
    output wire [31:0] bd_rdata,
    output wire [31:0] protocol_errors,
    output wire [3:0] memory_command,
    // ---- Diagnostics ----''')
    text = once(text, ".bd_we(1'b0), .bd_word_addr(24'b0), .bd_wdata(32'b0)",
                '.bd_we(bd_we), .bd_word_addr(bd_word_addr), .bd_wdata(bd_wdata),\n    .bd_rd_word_addr(bd_word_addr), .bd_rd_data(bd_rdata)')
    text = once(text, 'endmodule', '''
assign protocol_errors = sdram_chip.errors;
assign memory_command = {phy_cke, phy_ras, phy_cas, phy_we};
audio_mixer mixer (
    .clk(clk), .reset_n(reset_n), .mixer_enable(mixer_enable),
    .voice_wr(voice_wr), .voice_wr_ready(voice_wr_ready),
    .voice_field(voice_field), .voice_sel(voice_sel), .voice_sel_rd(5'd0),
    .voice_wdata(voice_wdata), .master_vol(8'hff),
    .group_vol_0(8'hff), .group_vol_1(8'hff), .group_vol_2(8'hff), .group_vol_3(8'hff),
    .voice_group_packed(64'd0), .m_arvalid(m3_arvalid), .m_arready(m3_arready),
    .m_araddr(m3_araddr), .m_arlen(m3_arlen), .m_rvalid(m3_rvalid),
    .m_rdata(m3_rdata), .m_rresp(2'd0), .m_rlast(m3_rlast), .m_rready(m3_rready),
    .sample_wr(sample_wr), .sample_data(sample_data), .fifo_level(fifo_level),
    .pos_readback(), .irq_clear_wr(1'b0), .irq_clear(32'd0),
    .voice_end_pending(), .voice_end_irq(), .voice_active_mask()
);
endmodule''')
    top = output / 'tb_pocket_audio.v'
    top.write_text(text)
    return [top, twin, root / 'src/fpga/test/sdram_model_full.v',
            *[root / 'src/fpga/common' / name for name in
              ('axi_sdram_arbiter.v', 'sync_fifo.v', 'axi_sdram_slave.v', 'audio_mixer.v')],
            root / 'src/fpga/targets/mister/synch_3.v',
            root / 'tools/tests/pocket_memory_contention.cpp']


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=root / 'build/pocket-memory-contention')
    parser.add_argument('--mhz', nargs='+', type=int, choices=[90, 100], default=[90, 100])
    parser.add_argument('--voices', nargs='+', type=int, choices=range(1, 33), default=[9, 20])
    parser.add_argument('--jobs', type=int, default=4)
    parser.add_argument('--queue-depth', type=int, choices=(8, 16), default=8,
                        help='GPU posted-write queue capacity in words')
    args = parser.parse_args()
    if args.jobs < 1:
        parser.error('--jobs must be positive')
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    sources = fixture(root, output)
    if args.queue_depth != 8:
        top = sources[0]
        top.write_text(once(top.read_text(), '.CPU_FAIR_THRESHOLD(8)',
                           f'.CPU_FAIR_THRESHOLD(8), .GPU_WRITE_QUEUE_DEPTH({args.queue_depth})'))
    inputs = [*sources, root / 'src/fpga/targets/pocket/io_sdram.v',
              root / 'src/fpga/test/tb_sdram_cont.v']
    report = dict(scope=__doc__, queue_depth=args.queue_depth, sources={str(s):
                  hashlib.sha256(s.read_bytes()).hexdigest() for s in inputs}, runs=[])
    for mhz in args.mhz:
        for bank in (0, 1):
            job = output / f'row{bank}-{mhz}'
            cmd = ['verilator', '--cc', '--exe', '--build', '-j', str(args.jobs),
                   '--top-module', 'tb_sdram_cont', '--Mdir', str(job),
                   '-Wno-fatal', '-Wno-BADVLTPRAGMA', '--public-flat-rw', '-CFLAGS', '-std=c++17 -O2',
                   f'-GBANK_ROW_TRACK={bank}', f'-GREFRESH_CYCLES={660 if mhz == 90 else 736}',
                   *map(str, sources)]
            with (output / f'row{bank}-{mhz}-build.log').open('w') as log:
                subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT, check=True)
            print(f'Built row{bank} at {mhz} MHz refresh cadence', flush=True)

        for voices in args.voices:
            def run(case):
                bank, quiet = case
                stem = f'row{bank}-{mhz}-voices{voices}-' + ('quiet' if quiet else 'stress')
                raw = output / (stem + '.raw')
                cmd = [str(output / f'row{bank}-{mhz}/Vtb_sdram_cont'), str(raw), str(voices), str(mhz)]
                if quiet:
                    cmd.append('quiet')
                with (output / (stem + '.log')).open('w') as log:
                    subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=300)
                text = (output / (stem + '.log')).read_text()
                lines = [line for line in text.splitlines() if line.startswith('PASS ')]
                if len(lines) != 1:
                    raise RuntimeError(f'Missing verdict: {stem}')
                stats = {k: int(v) for k, v in re.findall(r'(\w+)=(\d+)', lines[0])}
                if stats['underruns'] or stats['protocol_errors']:
                    raise RuntimeError(f'Failed service/data check: {stem}')
                print(stem, lines[0], flush=True)
                pcm = raw.read_bytes()
                if len(pcm) != 8192 * 4 or len(set(pcm[i:i+4] for i in range(0, len(pcm), 4))) < 100:
                    raise RuntimeError(f'Missing/vacuous audio output: {stem}')
                return dict(bank=bank, stats=stats, pcm_sha256=hashlib.sha256(pcm).hexdigest())

            with ThreadPoolExecutor(max_workers=2) as pool:
                cases = list(pool.map(run, [(0, True), (0, False), (1, False)]))
            if len({case['pcm_sha256'] for case in cases}) != 1:
                raise RuntimeError(f'PCM changed at {mhz} MHz / {voices} voices')
            report['runs'].extend(cases)
            print(f'PASS exact 8192-sample PCM comparison: {mhz} MHz, {voices} voices', flush=True)
    (output / 'results.json').write_text(json.dumps(report, indent=2) + '\n')


if __name__ == '__main__':
    main()
