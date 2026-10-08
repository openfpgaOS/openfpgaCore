#!/usr/bin/env python3
"""Guarded private request-payload reset removal with an extracted invariant check.

This is a local validity/noninterference qualification, not whole-GPU equivalence.
The consumer audit in the emitted REVIEW.txt completes the source-level argument.
"""
from pathlib import Path
import hashlib
import json
import re
import subprocess

ROOT = Path(__file__).resolve().parents[3]
ART = ROOT / 'docs/measurements/os30-dual-20261004'
SOURCE = ART / 'validation/gpu-count-nonzero/gpu_core.v'
OUT = ART / 'fbwq-payload-reset-v1'
EXPECTED = '16059ac4b79bc6436885646e0cf6d88878bae56549dc22db1836f9a28b19dae9'


def main():
    source = SOURCE.read_text()
    assert hashlib.sha256(source.encode()).hexdigest() == EXPECTED
    candidate = source
    removals = []
    for name, value in [('addr', "{GPU_ADDR_W{1'b0}}"), ('data', "32'd0"), ('strb', "4'd0")]:
        pattern = r'^ +fbwq_req_' + name + r' <= ' + re.escape(value) + r';\n'
        matches = re.findall(pattern, source, re.M)
        assert len(matches) == 2, (name, matches)
        for statement in matches:
            removals.append(statement)
            candidate = candidate.replace(statement, '', 1)
        writes = re.findall(r'\bfbwq_req_' + name + r'\s*<=\s*([^;]+);', candidate)
        assert writes == ['fbwq_push_' + name], (name, writes)
    # The reset-less request payload still loads only on the original accept.
    load = re.search(r'            if \(fbwq_push_req && fbwq_can_push\) begin\n'
                     r'(.*?)            end else if \(fbwq_req_to_stage_now \|\| fbwq_swap_now\) begin\n'
                     r'                fbwq_req_valid <= 1\'b0;\n            end', source, re.S)[0]
    payload = '\n'.join(re.findall(r'^                fbwq_req_(?:valid|addr|data|strb)\s*<= [^;]+;', load.split('            end else if')[0], re.M))
    assert payload.count('<=') == 4
    for name in ['fbwq_req_valid', 'zw_snoop_pending', 'cbw_snoop_pending']:
        assert len(re.findall(r'^ +'+name+r' <= 1\'b0;', source, re.M)) == (3 if name == 'fbwq_req_valid' else 2)
    assert source.count('&& fbwq_push_req && fbwq_can_push;') == 2

    OUT.mkdir(exist_ok=False)
    (OUT / 'gpu_core.v').write_text(candidate)
    # Preserve every actual reference for manual consumer-boundary audit.
    references = [(i, line) for i, line in enumerate(source.splitlines(), 1)
                  if re.search(r'\bfbwq_req_(?:addr|data|strb)\b', line)]
    (OUT / 'payload-references.txt').write_text('\n'.join(f'{i}: {s}' for i, s in references)+'\n')
    # State update uses the actual payload statements above. The other inputs
    # are arbitrary cut points, including consume+replace and hard/soft reset.
    # Reference resets zero; candidate begins with deliberately different bits.
    # Arbitrary invalid payload differences must disappear before any observer
    # can legally sample them, including one-cycle delayed depth/blend snoops.
    design = '''module slot #(parameter DROP_RESET=0)(
input clk,reset_n,soft_reset,fbwq_push_req,fbwq_can_push,
input fbwq_req_to_stage_now,fbwq_swap_now,
input [25:0] fbwq_push_addr,input [31:0] fbwq_push_data,input [3:0] fbwq_push_strb,
output reg fbwq_req_valid=0,output reg [25:0] fbwq_req_addr=26'h2aaaaaa,
output reg [31:0] fbwq_req_data=32'ha55a39c6,output reg [3:0] fbwq_req_strb=4'h9,
output reg zw_snoop_pending=0,cbw_snoop_pending=0);
localparam GPU_ADDR_W=26;
always @(posedge clk)begin
if(!reset_n || soft_reset)begin
fbwq_req_valid<=0;zw_snoop_pending<=0;cbw_snoop_pending<=0;
if(!DROP_RESET)begin fbwq_req_addr<=0;fbwq_req_data<=0;fbwq_req_strb<=0;end
end else begin
if(fbwq_push_req && fbwq_can_push)begin
''' + payload + '''
end else if(fbwq_req_to_stage_now || fbwq_swap_now)fbwq_req_valid<=0;
zw_snoop_pending<=fbwq_push_req && fbwq_can_push;
cbw_snoop_pending<=fbwq_push_req && fbwq_can_push;
end end endmodule
module tb;
reg clk=0,reset_n=0,soft_reset=0,push=0,can_push=0,take=0,swap=0;
reg [25:0] addr;reg [31:0] data;reg [3:0] strb;
wire [1:0] valid,z_pending,c_pending;
wire [25:0] addr_old,addr_new;wire [31:0] data_old,data_new;wire [3:0] strb_old,strb_new;
slot #(.DROP_RESET(0)) old(clk,reset_n,soft_reset,push,can_push,take,swap,addr,data,strb,
valid[0],addr_old,data_old,strb_old,z_pending[0],c_pending[0]);
slot #(.DROP_RESET(1)) fresh(clk,reset_n,soft_reset,push,can_push,take,swap,addr,data,strb,
valid[1],addr_new,data_new,strb_new,z_pending[1],c_pending[1]);
integer observed=0,resets=0,invalid_differences=0;
initial begin
for(integer i=0;i<1000000;i=i+1)begin
reset_n=(i!=0 && (($random&255)!=0));soft_reset=(($random&127)==0);
push=$random;can_push=$random;take=$random;swap=$random;
addr=$random;data=$random;strb=$random;
#1;clk=1;#1;
if(valid[0]!==valid[1] || z_pending[0]!==z_pending[1] || c_pending[0]!==c_pending[1])
$fatal(1,"Validity mismatch");
if(valid[0]||z_pending[0]||c_pending[0])begin
observed++;
if({addr_old,data_old,strb_old}!=={addr_new,data_new,strb_new})$fatal(1,"Observed payload mismatch");
end else if({addr_old,data_old,strb_old}!=={addr_new,data_new,strb_new})invalid_differences++;
if(!reset_n||soft_reset)resets++;
clk=0;
end
if(resets<1000 || invalid_differences<1000)$fatal(1,"Insufficient reset differentiation");
$display("PASS: 1000000 request cycles, %0d valid/delayed observers, %0d resets, %0d deliberately differing invalid payload cycles",observed,resets,invalid_differences);
$finish;end endmodule
'''
    (OUT / 'tb.sv').write_text(design)
    with (OUT/'build.log').open('w') as log:
        subprocess.run(['verilator','--binary','--timing','--top-module','tb','-Wno-fatal',
                        '--Mdir',str(OUT/'obj'),'-j','2',str(OUT/'tb.sv')],check=True,stdout=log,stderr=subprocess.STDOUT)
    with (OUT/'run.log').open('w') as log:
        subprocess.run([str(OUT/'obj/Vtb')],check=True,stdout=log,stderr=subprocess.STDOUT)
    review = '''Scope: remove only six reset assignments (hard/soft reset x request
addr/data/strb). No valid, combine flag, stage payload, pending bit, data-load,
clock, cycle, depth, external interface, or RAM assignment changes.

Inductive relation: request valid and both snoop-pending bits are identical.
Whenever any is true, both payloads are equal. Reset sets all three observer
bits false. A push sets valid and both enabled snoop bits and writes all payload
bits at the same edge. A consume clears valid without changing payload. A hold
preserves both relations. Thus an unreset payload cannot become observable.
The extracted simulation deliberately allows every observer bit's enabled
snoop form, arbitrary push/backpressure/consume/replace, and midstream resets.

Actual source consumers (payload-references.txt contains every occurrence):
1. Direct fbwq_in_* data enters stage only through fbwq_in_to_stage_now,
which requires req_valid when GPU_WRITE_COMBINE=0. Tail/link expressions are
latched only on that same stage load or a req_valid-gated swap.
2. fbwq_swap_now explicitly requires req_valid. Its FIFO writes and tail/link
updates therefore see equal payload. Render-cache os30 disables swap entirely.
3. Write combiner accept=req_valid && wc_input_ready. Every direct request
payload capture, hash selection change, or RAM read requires accept. Invalid
strb may change bypass/hash_mode_change/ready combinationally; all effects are
masked by req_valid, including automatic drain and diagnostic stall. can_push
is unconditionally true for an invalid request. Independent dirty-state drain
does not use request payload unless req_valid is true.
4. Depth and blend window snoops observe addr through zw_snoop_pending and
cbw_snoop_pending, respectively. Both are reset and track the same payload
load; equality remains true even if the request has meanwhile been consumed.
5. Selective-read-wait srw_p_tag can capture invalid data, but its valid bit
is srw_q_push (swap || stage drain). Swap requires req_valid; stage drain
selects stage_addr. Invalid srw_p_tag is unobservable behind srw_p_valid.

This is a source-level noninterference argument plus extracted sequential
simulation, not a formal full-GPU equivalence proof. Whole-GPU rendering and
hard/soft-reset regressions remain desirable before selection. Area/timing
benefit is not established; removing a synchronous clear may improve packing
but the payload's existing producer mux and common accept remain unchanged.
'''
    (OUT/'REVIEW.txt').write_text(review)
    manifest = dict(reference=str(SOURCE.relative_to(ROOT)),reference_sha256=EXPECTED,
                    candidate_sha256=hashlib.sha256(candidate.encode()).hexdigest(),
                    script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                    removed_reset_assignments=6,payload_bits=62,
                    every_other_source_byte_preserved=True,
                    extracted_request_invariant_passed=True,
                    consumer_review='REVIEW.txt',whole_gpu_validation='pending',
                    measured_area_or_timing_benefit=False)
    (OUT/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    print((OUT/'run.log').read_text(),end='')
    print(json.dumps(manifest,indent=2))


if __name__ == '__main__':
    main()
