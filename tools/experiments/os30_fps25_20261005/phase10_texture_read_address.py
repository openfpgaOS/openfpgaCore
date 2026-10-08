#!/usr/bin/env python3
"""Private texture-cache port-A address simplification and literal proof."""
import difflib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from phase10_experiment import CAMP, ROOT, sha, write

P = CAMP / 'phase10'
OUT = P / 'texture-read-address-v1'
PARENT = P / 'compact-lod5-core-v11/frozen/src/fpga/common/gpu_tex_cache.v'


def run(cmd, log):
    with log.open('x') as f:
        subprocess.run(list(map(str, cmd)), stdout=f, stderr=subprocess.STDOUT, check=True)


def prepare():
    OUT.mkdir(exist_ok=False)
    old = PARENT.read_text()
    before = '''wire [SET_BITS-1:0] read_set_a  = prime_a ? lat_set  : addr_set_a;
wire [1:0]          read_word_a = prime_a ? lat_word : addr_word_a;'''
    after = '''// On an enabled read, accept means req_valid=1, while prime_a means
// FILL_OUT/A/no flush and therefore req_ready=1, req_valid=0. Select the
// owned address directly; differing idle addresses are never consumed.
wire [SET_BITS-1:0] read_set_a  = req_valid ? addr_set_a  : lat_set;
wire [1:0]          read_word_a = req_valid ? addr_word_a : lat_word;'''
    assert old.count(before) == 1
    new = old.replace(before, after)
    (OUT / 'gpu_tex_cache.v').write_text(new)
    shutil.copy2(PARENT, OUT / 'baseline.v')
    shutil.copy2(__file__, OUT / 'recipe.py')
    (OUT / 'candidate.patch').write_text(''.join(difflib.unified_diff(
        old.splitlines(True), new.splitlines(True), fromfile='baseline.v', tofile='gpu_tex_cache.v')))
    write(OUT / 'prepared.json', dict(parent_path=str(PARENT.relative_to(ROOT)),
        parent_sha256=sha(PARENT), candidate_sha256=sha(OUT / 'gpu_tex_cache.v'),
        changed_assignments=2, added_registers=0, added_cycles=0,
        changed_scope='Port-A read set/word muxes only; read/write enables and all other source unchanged.'))


def literal():
    d = OUT / 'literal'; d.mkdir()
    old = (OUT / 'baseline.v').read_text(); new = (OUT / 'gpu_tex_cache.v').read_text()
    defs = []
    for name in ['req_ready', 'prime_a', 'read_en_a', 'data_write', 'tag_write',
                 'tag_address_a', 'data_address_a']:
        pattern = (r'assign\s+' if name == 'req_ready' else r'wire\s+(?:\[[^\]]+\]\s*)?') + name + r'\s*=.*?;'
        match = re.search(pattern, old, re.S); assert match, name
        defs.append(match.group(0))
    # Literal old/new address expressions are extracted independently.
    for src, prefix in [(old, 'old_'), (new, 'new_')]:
        for name in ['read_set_a', 'read_word_a']:
            match = re.search(r'wire\s+\[[^\]]+\]\s*' + name + r'\s*=.*?;', src, re.S)
            assert match
            defs.append(match.group(0).replace(name, prefix + name))
    defs = [x.replace('= read_set_a', '= old_read_set_a') for x in defs]
    # Address expressions use the old aliases; an independent candidate copy
    # substitutes only its two mux outputs under the unchanged writer priority.
    tb = '''module tb_address;
localparam SET_BITS=10,S_PIPE=0,S_FILL_DATA=2,S_FILL_OUT=3,S_INIT=4;
reg [2:0] state; reg reset_n,flush,flush_pending,lat_port,req_valid,pipe_miss_a,axi_rvalid,axi_rlast;
reg [9:0] lat_set,addr_set_a,init_counter; reg [1:0] lat_word,addr_word_a,fill_beat;
wire state_pipe_ready=(state==S_PIPE),state_fillout_ready=(state==S_FILL_OUT),state_fillout_prime=(state==S_FILL_OUT);
wire flush_block=flush||flush_pending;wire req_ready;
wire [9:0] read_set_a=old_read_set_a;wire[1:0]read_word_a=old_read_word_a;
''' + '\n'.join(defs) + '''
wire [9:0] new_tag_address_a=tag_write?((state==S_INIT)?init_counter:lat_set):new_read_set_a;
wire [11:0] new_data_address_a=data_write?{lat_set,fill_beat}:{new_read_set_a,new_read_word_a};
integer s,c,k;reg[31:0]rnd=32'h71a953cd;integer checks=0,reads=0,writes=0,idle_diff=0,negative=0;
initial begin
 for(s=0;s<8;s=s+1)for(c=0;c<256;c=c+1)for(k=0;k<64;k=k+1)begin
  state=s;{reset_n,flush,flush_pending,lat_port,req_valid,pipe_miss_a,axi_rvalid,axi_rlast}=c;
  rnd=(rnd^(rnd<<13));rnd=(rnd^(rnd>>17));rnd=(rnd^(rnd<<5));
  {lat_set,lat_word}=k<12?(12'b1<<k):rnd[11:0];
  {addr_set_a,addr_word_a}=k<12?~(12'b1<<k):rnd[23:12];init_counter=rnd[25:16];fill_beat=rnd[31:30];
  #1;checks=checks+1;
  if(read_en_a)begin
   reads=reads+1;
   if(old_read_set_a!==new_read_set_a||old_read_word_a!==new_read_word_a)$fatal(1,"owned read address changed");
   if(prime_a&&{lat_set,lat_word}!={addr_set_a,addr_word_a})negative=negative+1;
  end else if(old_read_set_a!==new_read_set_a||old_read_word_a!==new_read_word_a)idle_diff=idle_diff+1;
  if(tag_write||read_en_a)if(tag_address_a!==new_tag_address_a)$fatal(1,"tag port address changed");
  if(data_write||read_en_a)if(data_address_a!==new_data_address_a)$fatal(1,"data port address changed");
  if(tag_write||data_write)writes=writes+1;
 end
 if(reads==0||writes==0||idle_diff==0||negative==0)$fatal(1,"missing coverage");
 $display("PASS checks=%0d reads=%0d writes=%0d idle_diff=%0d negative=%0d",checks,reads,writes,idle_diff,negative);$finish;
end
endmodule
'''
    (d / 'tb.v').write_text(tb)
    cmd = ['verilator', '--binary', '--timing', '--assert', '-Wno-fatal', '-j', '2',
           '--top-module', 'tb_address', '--Mdir', d / 'obj', d / 'tb.v']
    write(d / 'command.json', list(map(str, cmd)))
    run(cmd, d / 'build.log'); run([d / 'obj/Vtb_address'], d / 'run.log')
    text = (d / 'run.log').read_text(); assert 'PASS checks=131072 ' in text
    counts = {k: int(v) for k,v in re.findall(r'(\w+)=(\d+)', text)}
    write(d / 'result.json', dict(passed=True, counts=counts,
        candidate_sha256=sha(OUT / 'gpu_tex_cache.v'),
        scope='All eight state encodings, all 256 control combinations and 64 address bases/random patterns; exact literal enabled read/write address equivalence.'))


if __name__ == '__main__':
    for action in sys.argv[1:]:
        globals()[action]()
