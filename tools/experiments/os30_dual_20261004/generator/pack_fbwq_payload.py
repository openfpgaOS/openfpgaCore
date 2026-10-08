#!/usr/bin/env python3
"""Pack the same-address framebuffer write data/strobes into one 36-bit RAM."""
from pathlib import Path
import argparse
import difflib
import hashlib
import json
import re
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[4]
ART = ROOT / 'docs/measurements/os30-dual-20261004'
BASE = ART / 'texture-offer-v1/gpu_core.v'
SHA = '2d4870c5b1c687cb51a3e60aef0fa0d31372bcea4c4dd521a0c825dd8ba24ec7'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(cmd, log, **kwargs):
    with log.open('w') as f:
        subprocess.run(cmd, check=True, stdout=f, stderr=subprocess.STDOUT, **kwargs)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--output', type=Path, default=ART / 'fbwq-payload-pack-v1')
    out = ap.parse_args().output.resolve()
    assert sha(BASE) == SHA
    source = BASE.read_text()
    decl_data = 'reg [31:0] fbwq_data [0:FBWQ_DEPTH-1];'
    decl_strb = '(* ramstyle = "MLAB, no_rw_check" *) reg [3:0]  fbwq_strb [0:FBWQ_DEPTH-1];'
    assert source.count(decl_data) == source.count(decl_strb) == 1
    decl_new = '(* ramstyle = "M10K" *) reg [35:0] fbwq_payload [0:FBWQ_DEPTH-1];'
    read_old = '                m_wr_wdata   <= fbwq_data[fbwq_rd_ptr];\n                m_wr_wstrb   <= fbwq_strb[fbwq_rd_ptr];'
    read_new = '                {m_wr_wstrb, m_wr_wdata} <= fbwq_payload[fbwq_rd_ptr];'
    assert source.count(read_old) == 3
    # These exact source statements are the complete memory interface.
    uses = re.findall(r'\bfbwq_(?:data|strb)\s*\[[^\n;]+;', source)
    assert len(uses) == 12, uses  # two declarations, six reads, four writes
    for dest in ('m_wr_wdata', 'm_wr_wstrb'):
        assert len(re.findall(r'\b' + dest + r'\s*<=', source)) == 3
    writes_old, writes_new = [], []
    for kind in ('req', 'stage'):
        old = f'                fbwq_data[fbwq_wr_ptr] <= fbwq_{kind}_data;\n                fbwq_strb[fbwq_wr_ptr] <= fbwq_{kind}_strb;'
        new = f'                fbwq_payload[fbwq_wr_ptr] <= {{fbwq_{kind}_strb, fbwq_{kind}_data}};'
        assert source.count(old) == 1
        writes_old.append(old)
        writes_new.append(new)
    edits = [(decl_data + '\n' + decl_strb, decl_new), (read_old, read_new)]
    edits += list(zip(writes_old, writes_new))
    candidate = source
    for old, new in edits:
        candidate = candidate.replace(old, new)
    restored = candidate
    for old, new in reversed(edits):
        restored = restored.replace(new, old)
    assert restored == source
    assert not re.search(r'\bfbwq_(data|strb)\s*\[', candidate)
    assert len(re.findall(r'\bfbwq_payload\s*\[', candidate)) == 6

    # The proof uses the exact emitted read/write statements. Only the outer
    # unchanged control arms are reduced to their Boolean inputs. It exercises
    # all overlapping controls too, preserving the original branch priority.
    design = ''
    for name, packed in [('reference', False), ('candidate', True)]:
        declarations = decl_new if packed else decl_data + '\n' + decl_strb
        reads = [read_new] * 3 if packed else [read_old] * 3
        writes = writes_new if packed else writes_old
        init_mem = ('fbwq_payload[i] <= init_word;' if packed else
                    'fbwq_data[i] <= init_word[31:0]; fbwq_strb[i] <= init_word[35:32];')
        observe = 'fbwq_payload[g]' if packed else '{fbwq_strb[g],fbwq_data[g]}'
        design += f'''module {name}(input clk, initialize, run_enable,
input [3:0] fbwq_rd_ptr,fbwq_wr_ptr,input [2:0] reads,input [1:0] writes,
input [35:0] init_rd,init_wr,init_other,init_output,req,stage,
output [575:0] memory,output [35:0] output_word);
localparam FBWQ_DEPTH=16;
{declarations}
reg [31:0] m_wr_wdata;
reg [3:0] m_wr_wstrb;
wire [31:0] fbwq_req_data=req[31:0],fbwq_stage_data=stage[31:0];
wire [3:0] fbwq_req_strb=req[35:32],fbwq_stage_strb=stage[35:32];
wire fbwq_start_now=reads[0],fbwq_continue_now=reads[1],fbwq_w_chain_start=reads[2];
wire fbwq_swap_now=writes[0],fbwq_stage_drain_now=writes[1];
reg [35:0] init_word;
integer i;
always @(posedge clk) begin
 if(initialize) begin
  for(i=0;i<16;i=i+1) begin
   init_word=(i==fbwq_rd_ptr)?init_rd:(i==fbwq_wr_ptr)?init_wr:init_other;
   {init_mem}
  end
  {{m_wr_wstrb,m_wr_wdata}} <= init_output;
 end else if(run_enable) begin
  if(fbwq_start_now) begin
{reads[0]}
  end else if(fbwq_continue_now) begin
{reads[1]}
  end else if(fbwq_w_chain_start) begin
{reads[2]}
  end
  if(fbwq_swap_now) begin
{writes[0]}
  end else if(fbwq_stage_drain_now) begin
{writes[1]}
  end
 end
end
for(genvar g=0;g<16;g=g+1) begin
 assign memory[g*36+:36]={observe};
end
assign output_word={{m_wr_wstrb,m_wr_wdata}};
endmodule
'''
    ports = 'clk,initialize,run_enable,fbwq_rd_ptr,fbwq_wr_ptr,reads,writes,init_rd,init_wr,init_other,init_output,req,stage'
    design += '''module packing_proof(input clk,initialize,run_enable,
input [3:0] fbwq_rd_ptr,fbwq_wr_ptr,input [2:0] reads,input [1:0] writes,
input [35:0] init_rd,init_wr,init_other,init_output,req,stage,
output [575:0] old_memory,new_memory,output [35:0] old_output,new_output);
reference a(''' + ports + ''',old_memory,old_output);
candidate b(''' + ports + ''',new_memory,new_output);
endmodule
'''
    cpp = r'''#include "Vpacking_proof.h"
#include <cstdio>
#include <cstdint>
constexpr uint64_t mask=(1ULL<<36)-1;
uint64_t cases=0,collisions=0,holds=0,disabled=0;
void edge(Vpacking_proof& d){d.clk=0;d.eval();d.clk=1;d.eval();d.clk=0;d.eval();}
bool check(Vpacking_proof& d){
 d.initialize=1;edge(d);d.initialize=0;
 uint64_t expected_output=(d.run_enable && d.reads)?d.init_rd:d.init_output;
 uint64_t expected[16];
 for(unsigned i=0;i<16;++i)expected[i]=i==d.fbwq_rd_ptr?d.init_rd:i==d.fbwq_wr_ptr?d.init_wr:d.init_other;
 if(d.run_enable && d.writes)expected[d.fbwq_wr_ptr]=(d.writes&1)?d.req:d.stage;
 edge(d);
 if(d.old_output!=d.new_output || d.new_output!=expected_output)return false;
 for(unsigned i=0;i<18;++i)if(d.old_memory[i]!=d.new_memory[i])return false;
 for(unsigned i=0;i<16;++i){
  unsigned word=i*36/32,shift=i*36%32;
  uint64_t got=(uint64_t(d.new_memory[word])>>shift)|(uint64_t(d.new_memory[word+1])<<(32-shift));
  if((got&mask)!=expected[i])return false;
 }
 ++cases;collisions+=d.run_enable&&d.reads&&d.writes&&d.fbwq_rd_ptr==d.fbwq_wr_ptr;
 holds+=!d.reads;disabled+=!d.run_enable;
 return true;
}
int main(){Vpacking_proof d;
 // Per-bit induction: independently choose old read cell, old write cell,
 // prior output, request data and staged data. Other cells are unchanged.
 // All36 parallel bits are copies with no arithmetic/data-dependent control.
 for(unsigned rp=0;rp<16;++rp)for(unsigned wp=0;wp<16;++wp)
 for(unsigned r=0;r<8;++r)for(unsigned w=0;w<4;++w)for(unsigned en=0;en<2;++en)
 for(unsigned bits=0;bits<32;++bits){
  d.fbwq_rd_ptr=rp;d.fbwq_wr_ptr=wp;d.reads=r;d.writes=w;d.run_enable=en;
  d.init_rd=(bits&1)?mask:0;d.init_wr=(bits&2)?mask:0;d.init_output=(bits&4)?mask:0;
  d.req=(bits&8)?mask:0;d.stage=(bits&16)?mask:0;d.init_other=0x5a5a5a5a5;
  if(!check(d)){std::puts("FAIL control/one-bit induction");return 1;}
 }
 // Independent bit positions catch any concat/slice permutation across the
 // 32 data bits / four strobes on all read/write address/control combinations.
 d.run_enable=1;
 for(unsigned bit=0;bit<36;++bit)for(unsigned rp=0;rp<16;++rp)for(unsigned wp=0;wp<16;++wp)
 for(unsigned r=0;r<8;++r)for(unsigned w=0;w<4;++w){
  uint64_t b=1ULL<<bit;
  d.fbwq_rd_ptr=rp;d.fbwq_wr_ptr=wp;d.reads=r;d.writes=w;
  d.init_rd=b;d.init_wr=mask^b;d.init_other=0;d.init_output=mask^b;d.req=mask^b;d.stage=b;
  if(!check(d)){std::puts("FAIL independent bit-position mapping");return 1;}
 }
 std::printf("PASS %llu exact sequential transitions; collisions=%llu holds=%llu disabled=%llu; all16 cells/36 output bits, independent read-before-write oracle\n",(unsigned long long)cases,(unsigned long long)collisions,(unsigned long long)holds,(unsigned long long)disabled);
}
'''
    out.mkdir(parents=True, exist_ok=False)
    (out / 'gpu_core.v').write_text(candidate)
    (out / 'proof.v').write_text(design)
    (out / 'proof.cpp').write_text(cpp)
    (out / 'source-mapping.json').write_text(json.dumps(dict(edits=edits,reads=3,writes=2,
        data_bits='payload[31:0]',strobe_bits='payload[35:32]',unchanged_addresses_and_control=True),indent=2)+'\n')
    run(['verilator','--cc','--exe','--build','-j','2','-Wno-fatal','--top-module','packing_proof',
         '--Mdir',str(out/'obj'),'-CFLAGS','-std=c++17 -O2',str(out/'proof.v'),str(out/'proof.cpp')],out/'proof-build.log')
    assert not re.search(r'%Warning-(IMPLICIT|UNDRIVEN|LATCH|MULTIDRIVEN)',(out/'proof-build.log').read_text())
    run([str(out/'obj/Vpacking_proof')],out/'proof.log')
    patch=out/'fbwq-payload-pack.patch'
    patch.write_text(''.join(difflib.unified_diff(source.splitlines(True),candidate.splitlines(True),fromfile='a/gpu_core.v',tofile='b/gpu_core.v')))
    replay=out/'patch-replay';replay.mkdir();(replay/'gpu_core.v').write_text(candidate)
    for label,extra,expected in [('reverse',['-R'],source),('replay',[],candidate)]:
        run(['patch','--batch','--fuzz=0',*extra,'-p1','-i',str(patch)],out/(label+'.log'),cwd=replay)
        assert (replay/'gpu_core.v').read_text()==expected
    shutil.copyfile(__file__,out/Path(__file__).name)
    manifest=dict(reference_sha256=SHA,candidate_sha256=sha(out/'gpu_core.v'),exact_scope='Two RAM declarations become one36-bit RAM; three read pairs and two write pairs become concatenated assignments. Every other source byte unchanged.',
        equivalent_storage_bits=576,read_output_bits=36,new_register_bits=0,new_cycles=0,
        all_read_and_write_addresses_enables_priority_and_reset_gates_unchanged=True,
        all_read_write_controls_and_pointer_pairs_exhausted=True,disabled_and_hold_behavior_checked=True,
        same_address_simultaneous_read_write_returns_old_word=True,exhaustive_inductive_bit_cases=524288,full_bit_position_cases=294912,
        exact_reverse_replay=True,ramstyle='M10K; no no_rw_check override, preserving source old-data collision semantics',
        whole_gpu_qualification='Pending independent review and retained38x3 rendering/AXI/cycle suite.',
        physical_qualification='Expected one36-bit M10K in place of one32-bit M10K plus oneMLAB; must verify actual fitted mapping before claiming saving.')
    (out/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    (out/'recipe.json').write_text(json.dumps(dict(script_sha256=sha(out/Path(__file__).name),reference_sha256=SHA,candidate_sha256=sha(out/'gpu_core.v'),
        evidence={n:sha(out/n) for n in ['source-mapping.json','proof.v','proof.cpp','proof.log','fbwq-payload-pack.patch']}),indent=2)+'\n')
    (out/'README.txt').write_text('''Framebuffer write payload memory packing
========================================

Data32 and strobes4 share both write sites, the same write/read pointers,
and all three unreset synchronous output captures. The candidate stores
{strobe,data} in one36-bit M10K-hinted array. Existing AW address memory,
queue metadata, ordering, reset/flush behavior and every control equation
remain unchanged. No output register or latency is added. The source mapping
is exact even for simultaneous same-address reads/writes: both versions
return the previous word on that edge. No no_rw_check override is added.

The actual extracted read/write statements are exercised in an inductive
state harness:524288 independent-bit/control transitions and294912 full
bit-position transitions cover all16x16 pointer pairs, overlapping read/write
controls, global disabling, holds and collisions. Every memory cell and all36
output bits compare against both original RTL and an independent old-data
oracle. No data arithmetic or data-dependent control exists at this boundary,
so the independent-bit reduction covers arbitrary stored words. The unchanged
queue pointers provide wrap behavior; integration runs retain real queue,
AXI/cycle and rendering coverage. This is a binary proof under the ordinary
equal-memory/output initialization relation; uninitialized memories stay
unreset in both designs.

The fitted parent has one32-bit M10K and one whole MLAB for strobes. A36-bit
simple-dual-port entry can potentially share the existing block, freeing
one memory LAB. This is a mapping expectation, not a measured saving. The
existing comments in the copied parent describe its prior separate memories;
the exact private patch/manifest here identify the changed storage layout.
Reproduce using generator/pack_fbwq_payload.py --output <new-directory>.
''')
    print((out/'proof.log').read_text(),end='')
    print(json.dumps(manifest,indent=2))


if __name__=='__main__':main()
