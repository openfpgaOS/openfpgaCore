#!/usr/bin/env python3
"""Run common-source os30 pixel oracles through the 100 MHz Pocket SDRAM path.

Uses the common-source GPU manifest and tb_gpu's cache/barrier wiring, replacing
only its flat SDRAM responder with arbiter -> slave -> Pocket controller ->
SDRAM chip model. CPU/scanout/audio read aggressors are optional. Textures in
these tests use SDRAM, not CRAM1. Reports workload cycles, not SM64 game FPS.

This legacy fixture does not resolve variants/os30.sources. See
check_gpu_target_matrix.py for the separate selected-os30 source provenance.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess

from check_pocket_memory_contention import once, pocket_sdram_twin
from check_pocket_gpu_transport import sdram_parameters

ROOT = Path(__file__).resolve().parents[1]


def prepare(out, manifest, common, queue_depth):
    test = ROOT / 'src/fpga/test'
    frozen = out / 'frozen'
    shutil.copytree(common, frozen / 'common')
    for name in ('tb_gpu.v', 'tb_gpu_retained_main.cpp',
                 'tb_gpu_acceptance_main.cpp', 'sdram_model_full.v',
                 'pocket_sdram_timing_checker.sv', 'altsyncram_stub.v'):
        shutil.copy2(test / name, frozen / name)
    for file in test.glob('*.h'):
        shutil.copy2(file, frozen / file.name)
    shutil.copy2(ROOT / 'src/fpga/targets/mister/synch_3.v', frozen / 'synch_3.v')
    memory = sdram_parameters(manifest, out)
    twin = pocket_sdram_twin(ROOT, frozen)
    stack = (test / 'tb_sdram_cont.v').read_text()
    stack = stack[stack.index('`default_nettype'):]
    stack = once(stack, '    // ---- Diagnostics ----', '''    input wire bd_we,
    input wire [23:0] bd_addr, bd_rd_addr,
    input wire [31:0] bd_wdata,
    output wire [31:0] bd_rd_data,
    // ---- Diagnostics ----''')
    stack = once(stack, ".bd_we(1'b0), .bd_word_addr(24'b0), .bd_wdata(32'b0)",
        '.bd_we(bd_we), .bd_word_addr(bd_addr), .bd_wdata(bd_wdata),\n'
        '    .bd_rd_word_addr(bd_rd_addr), .bd_rd_data(bd_rd_data)')
    stack = once(stack, '.BANK_ROW_TRACK(BANK_ROW_TRACK)',
        ', '.join(f'.{k}({v})' for k, v in memory.items()))
    if 'parameter GPU_WRITE_QUEUE_DEPTH' in (common / 'axi_sdram_arbiter.v').read_text():
        stack = once(stack, 'axi_sdram_arbiter sdram_arb (',
            f'axi_sdram_arbiter #(.GPU_WRITE_QUEUE_DEPTH({queue_depth})) sdram_arb (')
    else:
        assert queue_depth == 8
    stack = once(stack, '\nendmodule', '''
wire [31:0] pin_errors;
pocket_sdram_timing_checker #(.MHZ(100)) timing_check (
    .clk(phy_clk), .cke(phy_cke), .cmd({phy_ras,phy_cas,phy_we}),
    .bank(phy_ba), .address(phy_a), .errors(pin_errors));
always @(posedge clk) if(reset_n && (sdram_chip.errors || pin_errors))
    $fatal(1, "SDRAM protocol/timing error");
endmodule''')
    (frozen / 'tb_sdram_cont.v').write_text(stack)
    top = (frozen / 'tb_gpu.v').read_text()
    top = top[:top.index('// Simplified SDRAM Model')]
    # Inputs exposed by the existing memory fixture; no second controller or
    # arbitration implementation is maintained here.
    ports = ['.clk(clk)', '.reset_n(reset_n)']
    channels = {'arvalid':'rd_arvalid', 'arready':'rd_arready', 'araddr':'rd_araddr',
                'arlen':'rd_arlen', 'rvalid':'rd_rvalid', 'rdata':'rd_rdata',
                'rlast':'rd_rlast', 'awvalid':'wr_awvalid', 'awready':'wr_awready',
                'awaddr':'wr_awaddr', 'awlen':'wr_awlen', 'wvalid':'wr_wvalid',
                'wready':'wr_wready', 'wdata':'wr_wdata', 'wstrb':'wr_wstrb',
                'wlast':'wr_wlast', 'bvalid':'wr_bvalid'}
    for port, wire in channels.items():
        expr = 'gpu_' + wire
        if port in ('araddr', 'awaddr'):
            expr = "{10'd0," + expr + '[21:0]}' # same 4 MiB alias as pixel oracle
        ports.append(f'.m0_{port}({expr})')
    for master in (1, 2, 3):
        names = ['arvalid', 'arready', 'araddr', 'arlen', 'rvalid', 'rdata', 'rlast', 'rready']
        if master != 3:
            names += ['awvalid', 'awready', 'awaddr', 'awlen', 'wvalid', 'wready',
                      'wdata', 'wstrb', 'wlast', 'bvalid']
        for name in names:
            expr = ''
            if name == 'arvalid': expr = f'ag{master}_valid' if master != 2 else "1'b0"
            elif name == 'arready': expr = f'ag{master}_ready' if master != 2 else ''
            elif name == 'araddr': expr = f"32'h{0x02000000 + master * 0x400000:08x}"
            elif name == 'arlen': expr = "8'd15"
            elif name == 'rready': expr = "1'b1"
            elif name in ('awvalid', 'wvalid', 'wlast'): expr = "1'b0"
            elif name in ('awaddr', 'wdata'): expr = "32'd0"
            elif name == 'awlen': expr = "8'd0"
            elif name == 'wstrb': expr = "4'd0"
            ports.append(f'.m{master}_{name}({expr})')
    ports += ['.inj_burst_rd(scan_req)', ".inj_burst_addr(25'h1000000)",
              ".inj_burst_len(11'd160)", '.inj_burst_data_done(scan_done)',
              '.bd_we(bd_we)', ".bd_addr({4'd0,bd_addr[19:0]})", '.bd_wdata(bd_wdata)',
              ".bd_rd_addr({4'd0,bd_rd_addr[19:0]})", '.bd_rd_data(bd_rd_data)']
    top += '''// Real Pocket memory backend. Read traffic is a repeatable stress
// schedule, not a replacement for CPU instruction execution or the audio mixer.
integer traffic=0;
initial if ($value$plusargs("memory_traffic=%d", traffic)) begin end
reg [31:0] now_cycle;
reg ag1_valid, ag3_valid, scan_req, scan_active;
wire ag1_ready, ag3_ready, scan_done;
reg [31:0] next_cpu, next_audio, next_scan;
always @(posedge clk) begin
    if (!reset_n) begin
        now_cycle<=0; ag1_valid<=0; ag3_valid<=0; scan_req<=0; scan_active<=0;
        next_cpu<=30000; next_audio<=30000; next_scan<=30000;
    end else begin
        now_cycle<=now_cycle+1; scan_req<=0;
        if(traffic>=2 && !ag1_valid && now_cycle>=next_cpu) ag1_valid<=1;
        if(ag1_valid && ag1_ready) begin ag1_valid<=0; next_cpu<=now_cycle+97; end
        if(traffic>=1 && !ag3_valid && now_cycle>=next_audio) ag3_valid<=1;
        if(ag3_valid && ag3_ready) begin ag3_valid<=0; next_audio<=now_cycle+2048; end
        if(traffic>=1 && !scan_active && now_cycle>=next_scan) begin
            scan_req<=1; scan_active<=1; next_scan<=now_cycle+6361;
        end
        if(scan_done) scan_active<=0;
    end
end
'''
    top += 'tb_sdram_cont memory (\n    ' + ',\n    '.join(ports) + '\n);\n'
    top += '''reg [31:0] rd_count;
assign dbg_rd_txn_count_o=rd_count;
assign dbg_rd_last_latency_o=0;
always @(posedge clk) begin
    if(!reset_n) begin
        rd_count<=0; dbg_aw_count<=0; dbg_aw_burst_count<=0; dbg_aw_max_len<=0;
        dbg_w_beat_cycles<=0; dbg_w_busy_cycles<=0; dbg_rd_busy_cycles<=0;
        dbg_rw_overlap_cycles<=0;
    end else begin
        if(gpu_rd_arvalid && gpu_rd_arready) rd_count<=rd_count+1;
        if(gpu_wr_awvalid && gpu_wr_awready) begin
            dbg_aw_count<=dbg_aw_count+1;
            if(gpu_wr_awlen!=0) dbg_aw_burst_count<=dbg_aw_burst_count+1;
            if(gpu_wr_awlen>dbg_aw_max_len) dbg_aw_max_len<=gpu_wr_awlen;
        end
        if(gpu_wr_wvalid && gpu_wr_wready) dbg_w_beat_cycles<=dbg_w_beat_cycles+1;
    end
end
endmodule
`default_nettype wire
'''
    (frozen / 'tb_gpu.v').write_text(top)
    cpp = (frozen / 'tb_gpu_acceptance_main.cpp').read_text()
    # The chip model has power-on initialization, not a reset pin. Each
    # independent pixel case needs a fresh chip/controller pair, otherwise
    # resetting the controller alone creates an artificial refresh gap.
    cpp = once(cpp, 'static void hard_reset() {',
               'static void hard_reset() {\n    delete tb; tb = new Vtb_gpu;')
    cpp = once(cpp, 'for (int i = 0; i < 5; i++) tick();',
               'for (int i = 0; i < 30000; i++) tick();')
    # Controller initialization and shared-bus latency exceed flat-memory
    # timeouts; the timeout remains bounded and never affects passing cycles.
    cpp = cpp.replace('timeout = 400000', 'timeout = 4000000')
    (frozen / 'tb_gpu_acceptance_main.cpp').write_text(cpp)
    return frozen, memory


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--manifest', type=Path, required=True)
    p.add_argument('--common-dir', type=Path, default=ROOT / 'src/fpga/common')
    p.add_argument('--queue-depth', type=int, choices=(8,16), default=8)
    p.add_argument('--z-window', type=int, choices=(8,16), default=8)
    p.add_argument('--jobs', type=int, default=3)
    args = p.parse_args()
    if args.jobs < 1:
        p.error('--jobs must be positive')
    out = args.output.resolve(); out.mkdir(parents=True, exist_ok=False)
    manifest = json.loads(args.manifest.read_text())
    assert manifest['variant']['variant'] == 'os30'
    if set(manifest['variant']['defs'].split()) & {
            'INCLUDE_CLK90', 'INCLUDE_CLK96', 'INCLUDE_OS30_SDRAM166'}:
        p.error('This fixture requires the normal 100/100/100 MHz os30 clocks')
    frozen, memory = prepare(out, manifest, args.common_dir.resolve(), args.queue_depth)
    sources = [frozen / name for name in ['tb_gpu.v','tb_sdram_cont.v','io_sdram_pocket_test.v',
        'sdram_model_full.v','pocket_sdram_timing_checker.sv','altsyncram_stub.v','synch_3.v',
        'tb_gpu_retained_main.cpp']]
    sources += [frozen / 'common' / name for name in ['gpu_core.v','gpu_edge_walker.v',
        'gpu_tex_cache.v','gpu_color_depth_cache.sv','axi_sdram_arbiter.v','axi_sdram_slave.v','sync_fifo.v']]
    flags = [f for f in manifest['flags'] if not f.startswith('-GGPU_Z_READ_WINDOW=')]
    flags.append(f'-GGPU_Z_READ_WINDOW={args.z_window}')
    cmd = ['verilator','--cc','--exe','--build','--trace','-j',str(args.jobs),
           '-Wno-fatal','-Wno-BADVLTPRAGMA','--top-module','tb_gpu','--Mdir',str(out/'obj'),
           '-I'+str(frozen/'common'),'-CFLAGS',' '.join(manifest['cflags']),
           *['+define+'+d for d in manifest['variant']['defs'].split()], *flags, *map(str,sources)]
    report = dict(scope=__doc__, queue_depth=args.queue_depth,z_window=args.z_window,
                  sdram_parameters=memory, command=cmd, sources={
                      str(f.relative_to(out)):hashlib.sha256(f.read_bytes()).hexdigest()
                      for f in frozen.rglob('*') if f.is_file()}, runs={})
    with (out/'build.log').open('w') as log:
        subprocess.run(cmd,stdout=log,stderr=subprocess.STDOUT,check=True)
    for mode,name in enumerate(['quiet','scanout_audio','cpu_scanout_audio']):
        with (out/(name+'.log')).open('w') as log:
            subprocess.run([str(out/'obj/Vtb_gpu'),f'+memory_traffic={mode}'],cwd=out,
                           stdout=log,stderr=subprocess.STDOUT,check=True,timeout=600)
        text=(out/(name+'.log')).read_text()
        result=re.search(r'Acceptance Results: (\d+) passed, (\d+) failed',text)
        assert result and int(result[2])==0
        cycles={k:int(v) for k,v in re.findall(r'^CYCLES (\w+) (\d+)$',text,re.M)}
        assert len(cycles)==38
        report['runs'][name]=dict(passed=int(result[1]),cycles=cycles)
        (out/'results.json').write_text(json.dumps(report,indent=2)+'\n')
        print(name,result[0],sum(cycles.values()),flush=True)


if __name__ == '__main__':
    main()
