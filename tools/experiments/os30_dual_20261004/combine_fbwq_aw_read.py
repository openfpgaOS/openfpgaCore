#!/usr/bin/env python3
"""Unify two identical synchronous reads of the framebuffer write-queue address."""
from pathlib import Path
import hashlib
import json
import re
import subprocess

ROOT=Path(__file__).resolve().parents[3]
ART=ROOT/'docs/measurements/os30-dual-20261004'
BASE=ART/'validation/gpu-count-nonzero/gpu_core.v'
SHA='16059ac4b79bc6436885646e0cf6d88878bae56549dc22db1836f9a28b19dae9'
OUT=ART/'fbwq-single-read-v1'


def main():
    source=BASE.read_text()
    assert hashlib.sha256(source.encode()).hexdigest()==SHA
    stmt="                m_wr_awaddr  <= {{(32-GPU_ADDR_W){1'b0}}, fbwq_addr[fbwq_rd_ptr]};\n"
    assert source.count(stmt)==2
    assert len(re.findall(r'\bm_wr_awaddr\s*<=',source))==2
    first='            if (fbwq_start_now) begin\n                m_wr_awvalid <= 1\'b1;\n'+stmt
    assert source.count(first)==1
    second=re.search(r'            if \(fbwq_aw_ahead_issue\) begin\n.*?\n            end',source,re.S)[0]
    assert second.count(stmt)==1
    assert 'if (' not in second.split('begin\n',1)[1], 'Unexpected nested AW-ahead condition'
    replacement=('            // Both AW launches read the same queue head at this edge.\n'
                 '            // One capture expression avoids duplicating the MLAB read port.\n'
                 '            if (fbwq_start_now || fbwq_aw_ahead_issue)\n'+stmt)
    candidate=source.replace(stmt,'')
    anchor='            if (fbwq_start_now) begin\n'
    assert candidate.count(anchor)==1
    candidate=candidate.replace(anchor,replacement+'\n'+anchor,1)
    assert len(re.findall(r'\bm_wr_awaddr\s*<=',candidate))==1
    # Exact reverse proof: all unrelated bytes, reset scopes and state ordering
    # are preserved. Both conditions use wires, so moving the one assignment
    # does not observe procedural blocking temporaries.
    restored=candidate.replace(replacement+'\n','',1)
    restored=restored.replace(first.replace(stmt,''),first,1)
    restored=restored.replace(second.replace(stmt,''),second,1)
    assert restored==source
    for signal in ['fbwq_start_now','fbwq_aw_ahead_issue','fbwq_rd_ptr']:
        assert not re.search(r'\b'+signal+r'\s*=(?!=)',source) or signal!='fbwq_rd_ptr'
    OUT.mkdir(exist_ok=False)
    (OUT/'gpu_core.v').write_text(candidate)
    # Identical-data priority collapse is valid for all four condition values;
    # no assumption that start/ahead are mutually exclusive is used. Exercise
    # queue RAM writes, simultaneous read/write, hard/soft-reset inhibition and
    # arbitrary pointer positions in addition to the direct algebraic proof.
    original='if(fbwq_start_now)begin\n'+stmt+'end\nif(fbwq_aw_ahead_issue)begin\n'+stmt+'end\n'
    modified='if(fbwq_start_now || fbwq_aw_ahead_issue)begin\n'+stmt+'end\n'
    common='''input clk,reset_n,soft_reset,fbwq_start_now,fbwq_aw_ahead_issue,
input we,input [3:0] wa,fbwq_rd_ptr,input [25:0] wd,output reg [31:0] m_wr_awaddr=32'h98765432);
localparam GPU_ADDR_W=26;
reg [25:0] fbwq_addr[0:15];
initial for(integer k=0;k<16;k++)fbwq_addr[k]=k*32'h123456;
always @(posedge clk)begin
if(we)fbwq_addr[wa]<=wd;
if(reset_n && !soft_reset)begin
'''
    design='module baseline('+common+original+'end end endmodule\n'
    design+='module candidate('+common+modified+'end end endmodule\n'
    design+='''module tb;
reg clk=0,reset_n,soft_reset,start_now,ahead,we;reg [3:0] wa,rd;reg [25:0] wd;
wire [31:0] old_addr,new_addr;
baseline old(clk,reset_n,soft_reset,start_now,ahead,we,wa,rd,wd,old_addr);
candidate fresh(clk,reset_n,soft_reset,start_now,ahead,we,wa,rd,wd,new_addr);
integer captures=0,collisions=0,both=0;
initial begin
for(integer i=0;i<262144;i++)begin
{we,reset_n,soft_reset,ahead,start_now,wa,rd}=i[12:0];wd=$random;
#1;clk=1;#1;
if(old_addr!==new_addr)$fatal(1,"Address capture mismatch at %0d",i);
if(reset_n&&!soft_reset&&(start_now||ahead))begin
captures++;if(we&&wa==rd)collisions++;if(start_now&&ahead)both++;
end
clk=0;
end
$display("PASS: 262144 cycles, %0d captures, %0d read/write collisions, %0d simultaneous start/ahead assertions",captures,collisions,both);
$finish;end endmodule
'''
    (OUT/'tb.sv').write_text(design)
    with (OUT/'build.log').open('w') as log:
        subprocess.run(['verilator','--binary','--timing','--top-module','tb','-Wno-fatal',
                        '--Mdir',str(OUT/'obj'),'-j','2',str(OUT/'tb.sv')],check=True,stdout=log,stderr=subprocess.STDOUT)
    with (OUT/'run.log').open('w') as log:
        subprocess.run([str(OUT/'obj/Vtb')],check=True,stdout=log,stderr=subprocess.STDOUT)
    manifest=dict(reference=str(BASE.relative_to(ROOT)),reference_sha256=SHA,
                  candidate_sha256=hashlib.sha256(candidate.encode()).hexdigest(),
                  script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                  source_read_expressions_before=2,source_read_expressions_after=1,
                  all_other_source_bytes_preserved=True,registers_added=0,pipeline_cycles_added=0,
                  reset_assignments_changed=False,ram_storage_or_depth_changed=False,
                  extracted_equivalence_passed=True,requires_mutual_exclusion=False,
                  whole_gpu_validation='pending',physical_ram_deduplication='requires Quartus fit')
    (OUT/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    (OUT/'REVIEW.txt').write_text('''The only original assignments to m_wr_awaddr read the identical
fbwq_addr[fbwq_rd_ptr] under start_now and aw_ahead_issue. Both are in the same
nonreset main-FSM branch and occur at the same positive clock edge. Their data
and target are identical; two nonblocking assignments with identical RHS reduce
exactly to one enabled by the OR, even if both enables assert. No other writer,
reset assignment, blocking update to the read pointer, array write schedule,
FIFO capacity, or control equation changes. All other source bytes reverse
exactly to the reference. Address bit extension and AXI launch cycles stay exact.

Existing completed full-core reports contain fbwq_addr_rtl_0 and
fbwq_addr__dual_rtl_0, each reserving two MLABs (20 memory ALMs), while the
source has these two identical reads. A single expression may eliminate one
duplicate. This physical inference benefit must be verified in Quartus; it is
not claimed by the source proof or extracted simulation.
''')
    print((OUT/'run.log').read_text(),end='')
    print(json.dumps(manifest,indent=2))


if __name__=='__main__':main()
