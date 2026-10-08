#!/usr/bin/env python3
"""Private exact delay-counter clear factoring; no command/state changes."""
import difflib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
import phase10_sdram_address_mux as inherited
from phase10_experiment import CAMP, ROOT, sha, write

P = CAMP / 'phase10'
OUT = P / 'sdram-counter-factor-v2'
BASE = P / 'sdram-address-mux-v1/io_sdram.v'

CLEAR = '''
// Every legacy override clears the same counter. Group the independent
// state predicates instead of synthesizing a priority chain of zero writes.
wire dc_clear =
       (state == ST_WRITE_0) || (state == ST_WRITE_2)
    || (state == ST_WRITE_3) || (state == ST_READ_0)
    || (state == ST_READ_6) || (state == ST_REFRESH_0)
    || ((state == ST_BOOT_0) && (delay_boot == BOOT_CYCLES))
    || (((state == ST_BOOT_1) || (state == ST_WRITE_4_NR_PRECHG))
        && (dc == TIMING_PRECHARGE-1))
    || (((state == ST_BOOT_2) || (state == ST_BOOT_3))
        && (dc == TIMING_AUTOREFRESH-1))
    || ((state == ST_BOOT_4) && (dc == TIMING_LMR-1))
    || ((state == ST_IDLE) && (refresh_pending != 3'd0) && (row_open_v != 4'd0))
    || (((state == ST_REQ_READ) || (state == ST_REQ_WRITE)
         || (state == ST_REQ_BURST_READ)) && !req_row_hit && req_need_prechg)
    || ((state == ST_REQ_BURST_WRITE) && req_need_prechg)
    || (((state == ST_WRITE_1) || (state == ST_WRITE_4_NR_ACT)
         || (state == ST_READ_1)) && (dc == TIMING_ACT_RW-1))
    || ((state == ST_WRITE_4_NEWROW) && (dc == TIMING_WRITE-1+1))
    || (FAST_166 && ((state == ST_BURSTWR_4)
        || ((state == ST_BURSTWR_5) && (dc >= 4'd3))));

'''


def prepare():
    assert sha(BASE) == '402fbc16b1b6957001f13b5c0ba957ce4e2253d586ddb3b965464e5c017ac2fc'
    OUT.mkdir(exist_ok=False)
    old = BASE.read_text()
    pattern = r'^\s*(?:if \(FAST_166\) )?dc <= 0;[^\n]*\n'
    removed = re.findall(pattern, old, re.M)
    assert len(removed) == 23
    new = re.sub(pattern, '\n', old, flags=re.M)
    assert new.count("    dc <= dc + 1'b1;") == 1
    new = new.replace("    dc <= dc + 1'b1;", "    dc <= dc_clear ? 4'd0 : dc + 1'b1;")
    marker = 'always @(posedge controller_clk) begin\n    phy_dq_oe <= 0;'
    assert new.count(marker) == 1
    new = new.replace(marker, CLEAR + marker)
    (OUT / 'baseline.v').write_text(old)
    (OUT / 'io_sdram.v').write_text(new)
    (OUT / 'candidate.patch').write_text(''.join(difflib.unified_diff(old.splitlines(True), new.splitlines(True), fromfile=str(BASE.name), tofile='candidate/io_sdram.v')))
    shutil.copy2(__file__, OUT / 'recipe.py')
    write(OUT / 'prepared.json', dict(parent_sha256=sha(BASE), candidate_sha256=sha(OUT / 'io_sdram.v'), removed_clear_writes=len(removed), added_FF=0, added_states=0, added_cycles=0, deployment_ready=False))


def literal():
    work = OUT / 'literal'; work.mkdir(exist_ok=False)
    old = inherited.twin((OUT / 'baseline.v').read_text(), 'io_sdram_reference')
    new = inherited.twin((OUT / 'io_sdram.v').read_text(), 'io_sdram_candidate')
    header = new.split(');', 1)[0] + ');\n'
    ports = re.findall(r'^(input|output)\s+(?:wire|reg)\s*(\[[^\]]+\])?\s*(\w+)', header, re.M)
    text = 'module tb_counter(input wire clk,input wire reset_n);\n'
    for fast in (0, 1):
        for bank in (0, 1):
            for flavor in ('candidate', 'reference'):
                inst = f'{flavor}_{fast}_{bank}'
                connections = []
                for direction, width, name in ports:
                    if direction == 'output': connections.append(f'.{name}()')
                    else:
                        val = 'clk' if name in ('controller_clk', 'chip_clk', 'clk_90') else 'reset_n' if name == 'reset_n' else "'0"
                        connections.append(f'.{name}({val})')
                text += f'io_sdram_{flavor} #(.FAST_166({fast}),.BANK_ROW_TRACK({bank})) {inst} (' + ','.join(connections) + ');\n'
    text += 'endmodule\n' + old + new
    (work / 'tb.v').write_text(text)
    cpp = '''#include "Vtb_counter.h"
#include "Vtb_counter___024root.h"
#include <cstdio>
#include <cstdlib>
int main(){ Vtb_counter t; t.clk=0;t.reset_n=1;t.eval();unsigned long checks=0;
for(unsigned state=0;state<64;++state)for(unsigned dc=0;dc<16;++dc)for(unsigned mask=0;mask<64;++mask){
t.clk=0;t.reset_n=(mask>>5)&1;t.eval();
'''
    for fast in (0, 1):
        for bank in (0, 1):
            for flavor in ('candidate', 'reference'):
                obj = f't.rootp->tb_counter__DOT__{flavor}_{fast}_{bank}__DOT__'
                cpp += f'{obj}state=state;{obj}dc=dc;{obj}req_row_hit=mask&1;{obj}req_need_prechg=(mask>>1)&1;'
                cpp += f'{obj}refresh_pending=(mask&4)?7:0;{obj}row_open_v=(mask&8)?15:0;{obj}delay_boot=(mask&16)?{40000 if fast else 30000}:0;\n'
    cpp += 't.eval();t.clk=1;t.eval();\n'
    for fast in (0, 1):
        for bank in (0, 1):
            a = f't.rootp->tb_counter__DOT__candidate_{fast}_{bank}__DOT__dc'
            b = f't.rootp->tb_counter__DOT__reference_{fast}_{bank}__DOT__dc'
            cpp += f'if({a}!={b}){{std::printf("FAIL fast={fast} bank={bank} state=%u dc=%u mask=%u new=%u old=%u\\n",state,dc,mask,unsigned({a}),unsigned({b}));return 1;}}++checks;\n'
    cpp += '}t.final();std::printf("COUNTER_LITERAL PASS checks=%lu\\n",checks);return 0;}\n'
    (work / 'main.cpp').write_text(cpp)
    shutil.copy2(ROOT / 'src/fpga/targets/mister/synch_3.v', work / 'synch_3.v')
    command = ['verilator', '--cc', '--exe', '--build', '-j', '3', '--public-flat-rw', '-Wno-fatal', '--top-module', 'tb_counter', '--Mdir', str(work / 'obj'), '-CFLAGS', '-O2', str(work / 'tb.v'), str(work / 'synch_3.v'), str(work / 'main.cpp')]
    write(work / 'command.json', command)
    with (work / 'build.log').open('x') as f: subprocess.run(command, stdout=f, stderr=subprocess.STDOUT, check=True)
    with (work / 'run.log').open('x') as f: subprocess.run([str(work / 'obj/Vtb_counter')], stdout=f, stderr=subprocess.STDOUT, check=True)
    assert 'PASS checks=262144' in (work / 'run.log').read_text()
    write(work / 'result.json', dict(passed=True, actual_RTL_next_counter_comparisons=262144, states=64, counter_values=16, control_masks=64, configurations=4, scope='Actual baseline/candidate full module; synthetic exhaustive counter-control forcing, including reset input and unencoded states. BANK_ROW_TRACK and FAST_166 each 0/1. No claim these artificial states are complete transaction histories.'))


def native():
    # Snapshot the established native SDRAM miter into this new proof. The only
    # changes are input roots and stronger exact-address/counter assertions.
    old = Path(inherited.__file__).read_text()
    new = old.replace('ROOT = Path(__file__).resolve().parents[3]', f'ROOT = Path({str(ROOT)!r})')
    new = new.replace("BASE = P / 'compact-lod5-core-v3/frozen'", "BASE = P / 'compact-lod5-core-v6/frozen'")
    new = new.replace("OUT = P / 'sdram-address-mux-v1'", "OUT = P / 'sdram-counter-factor-v2'")
    needle = "    wrapper += '''\nif (phy_cke) begin"
    assert new.count(needle) == 1
    new = new.replace(needle, "    wrapper += '''\nif (dut.dc !== ref_dut.dc) $fatal(1, \"counter value differs\");\nif (phy_a !== ref_phy_a) $fatal(1, \"all address pins differ\");\nif (phy_cke) begin")
    helper = OUT / 'native-recipe.py'; helper.write_text(new)
    subprocess.run([sys.executable, str(helper), 'banks'], check=True)


if __name__ == '__main__':
    for action in sys.argv[1:]: globals()[action]()
