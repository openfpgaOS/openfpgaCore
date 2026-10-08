#!/usr/bin/env python3
"""Check 83/83/166 os30 memory correctness and synthetic service performance.

Real arbiter, clock bridge, buffered SDRAM slave, controller and audio mixer.
The chip data model checks data while a separate monitor checks 166 MHz
command timing. Pin flight times and PLL phase still require board STA.
Numbers are a synthetic contention workload, not SM64 FPS.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import re
import subprocess
from check_pocket_memory_contention import fixture, once, pocket_sdram_twin

ROOT = Path(__file__).resolve().parents[1]


def controller_regression(out, jobs):
    """Reuse the byte-strobe/row-crossing/burst-length regression at 166 MHz."""
    top = (ROOT/'src/fpga/test/tb_sdram.v').read_text()
    top = once(top, '.BANK_ROW_TRACK(BANK_ROW_TRACK)',
               '.BANK_ROW_TRACK(BANK_ROW_TRACK), .FAST_166(1), .REFRESH_INTERVAL(1222)')
    top = once(top, 'sdram_model sdram_chip', 'sdram_model_full sdram_chip')
    top = once(top, 'endmodule', '''
pocket_sdram_timing_checker #(.MHZ(166)) timing_check (
    .clk(phy_clk), .cke(phy_cke), .cmd({phy_ras,phy_cas,phy_we}),
    .bank(phy_ba), .address(phy_a), .errors());
always @(posedge clk) if(sdram_chip.errors) $fatal(1, "SDRAM data model error");
endmodule''')
    top_path = out/'tb_sdram.v'; top_path.write_text(top)
    cpp = (ROOT/'src/fpga/test/tb_sdram_main.cpp').read_text()
    cpp = once(cpp, 'i < 31000', 'i < 41000')
    cpp_path = out/'controller.cpp'; cpp_path.write_text(cpp)
    sources = [top_path, pocket_sdram_twin(ROOT,out),
        ROOT/'src/fpga/test/sdram_model_full.v',
        ROOT/'src/fpga/test/pocket_sdram_timing_checker.sv',
        ROOT/'src/fpga/common/axi_sdram_slave.v', ROOT/'src/fpga/common/sync_fifo.v',
        ROOT/'src/fpga/targets/mister/synch_3.v', cpp_path]
    command = ['verilator', '--cc', '--exe', '--build', '--trace', '-j', str(jobs),
        '--top-module', 'tb_sdram', '--Mdir', str(out/'controller'),
        '-Wno-fatal', '-Wno-BADVLTPRAGMA', '-GWCONT_TIE=0', *map(str,sources)]
    with (out/'controller-build.log').open('w') as log:
        subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,check=True)
    with (out/'controller.log').open('w') as log:
        subprocess.run([str(out/'controller/Vtb_sdram')],cwd=out,
                       stdout=log,stderr=subprocess.STDOUT,check=True)
    print((out/'controller.log').read_text().splitlines()[-1],flush=True)


def prepare(out):
    sources = fixture(ROOT, out)
    top = sources[0]
    s = top.read_text()
    s = once(s, 'parameter BANK_ROW_TRACK = 0, parameter REFRESH_CYCLES = 736',
             'parameter BANK_ROW_TRACK = 1, parameter REFRESH_CYCLES = 736, parameter FAST_166 = 0')
    s = once(s, 'input  wire        clk,', 'input wire clk, clk_ram,')
    s = once(s, '.CPU_FAIR_THRESHOLD(8)', '.CPU_FAIR_THRESHOLD(8), .BUFFERED_SLAVE(FAST_166)')
    s = once(s, '.REFRESH_INTERVAL(REFRESH_CYCLES)',
             '.REFRESH_INTERVAL(REFRESH_CYCLES), .FAST_166(FAST_166)')
    core = (ROOT/'src/fpga/targets/pocket/core_top.v').read_text()
    a = core.index('    // Experimental 83 MHz fabric')
    b = core.index('    axi_sdram_slave #(', a)
    # Extract the actual production crossing and bypass, not a second wiring.
    bridge = core[a:b].replace('clk_cpu', 'clk').replace('clk_ram_controller', 'clk_ram')
    bridge = bridge.replace('pll_ram_locked', 'reset_n')
    s = once(s, 'axi_sdram_slave #(', bridge + 'axi_sdram_slave #(')
    a = s.index('axi_sdram_slave #('); b = s.index('\n);', a)
    block = s[a:b].replace('arb_s_', 'mem_s_').replace('.clk(clk)', '.clk(clk_ram)')
    block = block.replace('.reset_n(reset_n)', '.reset_n(FAST_166 ? sdram_fast_reset_n : reset_n)')
    block = block.replace(".s_axi_bready(1'b1)", '.s_axi_bready(mem_s_bready)')
    s = s[:a]+block+s[b:]
    s = once(s, 'always @(posedge clk or negedge reset_n) begin',
             'always @(posedge clk_ram or negedge reset_n) begin')
    s = once(s, '.controller_clk(clk), .chip_clk(clk), .clk_90(clk)',
             '.controller_clk(clk_ram), .chip_clk(clk_ram), .clk_90(clk_ram)')
    a = s.index('axi_sdram_arbiter #('); b = s.index('\n);', a)
    block = s[a:b].replace('.reset_n(reset_n)', '.reset_n(FAST_166 ? sdram_cpu_reset_n : reset_n)')
    s = s[:a]+block+s[b:]
    s = once(s, '.clk_90(clk_ram), .reset_n(reset_n)',
             '.clk_90(clk_ram), .reset_n(FAST_166 ? sdram_fast_reset_n : reset_n)')
    s = once(s, 'assign protocol_errors = sdram_chip.errors;', '''wire [31:0] pin_timing_errors;
pocket_sdram_timing_checker #(.MHZ(FAST_166 ? 166 : 100)) timing_check (
    .clk(phy_clk), .cke(phy_cke), .cmd({phy_ras,phy_cas,phy_we}),
    .bank(phy_ba), .address(phy_a), .errors(pin_timing_errors));
assign protocol_errors = sdram_chip.errors + pin_timing_errors;''')
    top.write_text(s)
    cpp = (ROOT/'tools/tests/pocket_memory_contention.cpp').read_text()
    cpp = once(cpp, 'static bool running, playing;', 'static bool running, playing, scan_seen;')
    cpp = once(cpp, 'static void low(){d.clk=0;d.fifo_level=level;d.eval();}',
               'static void low(){d.clk=0;d.clk_ram=0;d.fifo_level=level;d.eval();}')
    cpp = once(cpp, '  if(d.memory_command==11)acts++;\n  if(d.memory_command==10)pres++;', '')
    cpp = once(cpp, ' d.clk=1;d.eval();cycles++;', '''
 // One CPU/GPU edge and two memory edges in the 83/166 case.
 scan_seen |= d.inj_burst_data_done;
 if(running){acts+=d.memory_command==11;pres+=d.memory_command==10;}
 d.clk=1;d.clk_ram=1;d.eval();
 d.inj_burst_rd=0; // scanout request is one MEMORY-cycle pulse
 d.clk_ram=0;d.eval();
 if(mhz==83){
  scan_seen |= d.inj_burst_data_done;
  if(running){acts+=d.memory_command==11;pres+=d.memory_command==10;}
  d.clk_ram=1;d.eval();d.clk_ram=0;d.eval();
 }
 cycles++;''')
    cpp = once(cpp, 'bool scan_done=d.inj_burst_data_done;', 'bool scan_done=scan_seen;scan_seen=false;')
    # Keep traffic identical in bytes; report wall time using each CPU clock.
    cpp = once(cpp, 'total=100000;', 'total=20000;')
    cpp = once(cpp, 'n<11000', 'n<45000')
    # Exercise CPU R backpressure and W gaps, preserving VALID until accepted.
    cpp = once(cpp, 'd.m1_rready=1;', 'd.m1_rready=(cycles%97)>=23;')
    cpp = once(cpp, 'bool rv=id==0?d.m0_rvalid:d.m1_rvalid;',
               'bool rv=id==0?d.m0_rvalid:(d.m1_rvalid&&d.m1_rready);')
    cpp = once(cpp, 'd.m1_wvalid=w&&!wd;',
               'd.m1_wvalid=w&&!wd && ((cycles%71)>=13 || held_wvalid);')
    cpp = once(cpp, 'unsigned id,job=0,stage=0,beat=0; bool aw=false,wd=false;',
               'unsigned id,job=0,stage=0,beat=0; bool aw=false,wd=false,held_wvalid=false;')
    cpp = once(cpp, 'if(ah)aw=true;', 'if(id==1)held_wvalid=d.m1_wvalid&&!d.m1_wready;\n  if(ah)aw=true;')
    dest = out/'memory.cpp'; dest.write_text(cpp)
    return [*sources[:-1], ROOT/'src/fpga/common/axi_sdram_clock_bridge.v',
            ROOT/'src/fpga/test/pocket_sdram_timing_checker.sv', dest]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--jobs', type=int, default=4)
    p.add_argument('--controller-only', action='store_true')
    args = p.parse_args()
    if args.jobs < 1: p.error('--jobs must be positive')
    out = args.output.resolve(); out.mkdir(parents=True, exist_ok=False)
    if args.controller_only:
        controller_regression(out,args.jobs)
        return
    sources = prepare(out)
    result = dict(scope=__doc__, sources={str(s):hashlib.sha256(s.read_bytes()).hexdigest() for s in sources}, runs={})

    def build(mhz):
        command = ['verilator', '--cc', '--exe', '--build', '-j', str(args.jobs),
                   '--top-module', 'tb_sdram_cont', '--Mdir', str(out/f'obj{mhz}'),
                   '-Wno-fatal', '-Wno-BADVLTPRAGMA', '-CFLAGS', '-std=c++17 -O2',
                   f'-GFAST_166={int(mhz==83)}', f'-GREFRESH_CYCLES={1222 if mhz==83 else 736}']
        if mhz==83: command += ['+define+INCLUDE_OS30_SDRAM166']
        with (out/f'build{mhz}.log').open('w') as log:
            subprocess.run([*command,*map(str,sources)],stdout=log,stderr=subprocess.STDOUT,check=True)
        print('Built', mhz, flush=True)

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(build,[100,83]))
    for voices in [9,20]:
        for mhz,quiet in [(100,True),(100,False),(83,False)]:
            name=f'{mhz}-voices{voices}-' + ('quiet' if quiet else 'stress')
            raw=out/(name+'.raw')
            command=[str(out/f'obj{mhz}/Vtb_sdram_cont'),str(raw),str(voices),str(mhz)]
            if quiet: command+=['quiet']
            with (out/(name+'.log')).open('w') as log:
                subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=600)
            verdict=[s for s in (out/(name+'.log')).read_text().splitlines() if s.startswith('PASS ')]
            if len(verdict)!=1: raise RuntimeError('Missing verdict: '+name)
            stats={k:int(v) for k,v in re.findall(r'(\w+)=(\d+)',verdict[0])}
            assert stats['underruns']==stats['protocol_errors']==0
            stats['gpu_us']=stats['gpu_cycles']/mhz
            stats['cpu_us']=stats['cpu_cycles']/mhz
            stats['pcm_sha256']=hashlib.sha256(raw.read_bytes()).hexdigest()
            result['runs'][name]=stats
            print(name,stats,flush=True)
            (out/'results.json').write_text(json.dumps(result,indent=2)+'\n')
        hashes={v['pcm_sha256'] for k,v in result['runs'].items() if f'-voices{voices}-' in k}
        if len(hashes)!=1: raise RuntimeError('Audio differs across clock configurations')
    print('PASS memory data, command timing, audio equality and 48 kHz service',flush=True)


if __name__=='__main__': main()
