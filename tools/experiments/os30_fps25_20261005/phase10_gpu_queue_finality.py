#!/usr/bin/env python3
"""Private exact queue-finality factoring over GPU v14."""
import difflib
import shutil
import subprocess
import sys
from pathlib import Path
from phase10_experiment import CAMP, sha, write
P = CAMP / 'phase10'
BASE = P / 'gpu-origin-default-v14/gpu_core.v'
OUT = P / 'gpu-queue-finality-v15'


def prepare():
    assert sha(BASE) == '3c171d36bce942735040d3cdc2d96a5f9959b46b2b5c4896455d1c4763ffa4d1'
    OUT.mkdir(exist_ok=False)
    old = BASE.read_text()
    a = '''wire fbwq_head_chain_final = (fbwq_start_burst_words == 4'd8)
                          || (fbwq_count > {1'b0, fbwq_start_burst_words});'''
    b = '''// A chain is final at eight queued words, or at any broken link
// between existing entries. Do not encode its length just to test finality.
// The actual AWLEN/remaining encoder above is unchanged.
wire fbwq_head_chain_final = (fbwq_count >= 5'd8)
    || ((fbwq_count >= 5'd2) && !fbwq_link_next[fbwq_rd_ptr])
    || ((fbwq_count >= 5'd3) && !fbwq_link_next[fbwq_rd_ptr_1])
    || ((fbwq_count >= 5'd4) && !fbwq_link_next[fbwq_rd_ptr_2])
    || ((fbwq_count >= 5'd5) && !fbwq_link_next[fbwq_rd_ptr_3])
    || ((fbwq_count >= 5'd6) && !fbwq_link_next[fbwq_rd_ptr_4])
    || ((fbwq_count >= 5'd7) && !fbwq_link_next[fbwq_rd_ptr_5]);'''
    assert old.count(a) == 1
    new = old.replace(a, b)
    (OUT / 'gpu_core.v').write_text(new)
    (OUT / 'candidate.patch').write_text(''.join(difflib.unified_diff(old.splitlines(True), new.splitlines(True), fromfile='gpu-v14/gpu_core.v', tofile='gpu-v15/gpu_core.v')))
    shutil.copy2(__file__, OUT / 'recipe.py')
    write(OUT / 'prepared.json', dict(parent_sha256=sha(BASE), candidate_sha256=sha(OUT / 'gpu_core.v'), added_FF=0, added_states=0, added_cycles=0, scope='Only head-chain-final predicate; actual length encoder, writes, pointers and control consumers untouched.', deployment_ready=False))


def literal():
    work = OUT / 'literal'; work.mkdir(exist_ok=False)
    modules = []
    for name, path in [('reference', BASE), ('candidate', OUT / 'gpu_core.v')]:
        s = path.read_text()
        body = s[s.index('wire [3:0] fbwq_rd_ptr_1'):s.index('wire fbwq_aw_ahead_issue')]
        modules.append(f'''module {name}(input wire [4:0] fbwq_count,input wire [3:0] fbwq_rd_ptr,
input wire [15:0] fbwq_link_next,output wire finality,output wire [3:0] words);
{body}
assign finality=fbwq_head_chain_final;assign words=fbwq_start_burst_words;endmodule
''')
    top = '''module tb_finality(input wire [4:0] count,input wire [3:0] pointer,input wire [15:0] links,
output wire old_final,new_final,output wire [3:0] old_words,new_words);
reference r(count,pointer,links,old_final,old_words);
candidate c(count,pointer,links,new_final,new_words);
endmodule
'''
    (work / 'tb.v').write_text(top + '\n'.join(modules))
    (work / 'main.cpp').write_text('''#include "Vtb_finality.h"
#include <cstdio>
int main(){Vtb_finality t;unsigned long checks=0;
for(unsigned links=0;links<65536;++links)for(unsigned pointer=0;pointer<16;++pointer)for(unsigned count=0;count<32;++count){
t.links=links;t.pointer=pointer;t.count=count;t.eval();
if(t.old_final!=t.new_final||t.old_words!=t.new_words){std::printf("FAIL %u %u %u\\n",links,pointer,count);return 1;}++checks;
}t.final();std::printf("FINALITY_LITERAL PASS checks=%lu legal_checks=%u\\n",checks,65536u*16u*17u);return 0;}
''')
    cmd = ['verilator','--cc','--exe','--build','-j','3','-Wno-fatal','--top-module','tb_finality','--Mdir',str(work/'obj'),'-CFLAGS','-O2',str(work/'tb.v'),str(work/'main.cpp')]
    write(work/'command.json',cmd)
    with (work/'build.log').open('x') as f: subprocess.run(cmd,stdout=f,stderr=subprocess.STDOUT,check=True)
    with (work/'run.log').open('x') as f: subprocess.run([str(work/'obj/Vtb_finality')],stdout=f,stderr=subprocess.STDOUT,check=True)
    assert 'PASS checks=33554432 legal_checks=17825792' in (work/'run.log').read_text()
    write(work/'result.json',dict(passed=True,all_combinations=33554432,legal_count_combinations=17825792,counts='All 0..31; legal queue occupancy 0..16',pointers=16,link_patterns=65536,scope='Literal old/new RTL predicate and unchanged length encoder.'))


if __name__ == '__main__':
    for name in sys.argv[1:]: globals()[name]()
