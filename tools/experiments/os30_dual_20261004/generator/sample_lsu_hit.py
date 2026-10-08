#!/usr/bin/env python3
"""Sample the LSU hit reduction beside the unchanged stage4 hit vector."""
from pathlib import Path
import argparse
import difflib
import hashlib
import itertools
import json
import re
import subprocess

ROOT = Path(__file__).resolve().parents[4]
ART = ROOT/'docs/measurements/os30-dual-20261004'
REF = ART/'decode-pingpong-v1/immediate/VexiiRiscv.v'
SHA = 'd471c5da61b3f5f67ce3c78eb0bf34d98cb828407ba0da15b63ac1044ed50f86'
UP = 'execute_ctrl4_up_LsuL1Plugin_logic_WAYS_HITS_lane0'
DOWN = 'execute_ctrl4_down_LsuL1Plugin_logic_WAYS_HITS_lane0'
IN = 'execute_ctrl3_down_LsuL1Plugin_logic_WAYS_HITS_lane0'
HIT = 'execute_ctrl4_down_LsuL1Plugin_logic_WAYS_HIT_lane0'
SHADOW = 'execute_ctrl4_up_LsuL1Plugin_logic_WAYS_HIT_lane0'
EN = 'execute_ctrl3_down_isReady'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def run(args, path, **kwargs):
    with path.open('w') as log:
        subprocess.run(args,stdout=log,stderr=subprocess.STDOUT,check=True,**kwargs)


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--output',type=Path,default=ART/'lsu-hit-sampled-v1/immediate')
    args=ap.parse_args()
    src=REF.read_text()
    assert sha(src.encode())==SHA
    decl=re.findall(r'^  reg\s+\[1:0\]\s+'+UP+r';$',src,re.M)
    assert len(decl)==1
    decl=decl[0]
    old=f'  assign {HIT} = (|{DOWN});'
    alias=f'  assign {DOWN} = {UP};'
    capture=f'      {UP} <= {IN};'
    for statement in [old,alias,capture]:
        assert src.count(statement)==1
    assert len(re.findall(r'\b'+UP+r'\s*<=',src))==1
    assert not re.search(r'\b'+SHADOW+r'\b',src)
    blocks=re.findall(r'    if\('+EN+r'\) begin\n((?:      [^\n]*\n)+)    end',src)
    matched=[b for b in blocks if capture in b]
    assert len(matched)==1 and all(re.fullmatch(r'      \w+ <= [^;]+;',s) for s in matched[0].splitlines())
    # The payload writer is in the unreset synchronous always block, unlike
    # the separate resettable lane-valid/SEL writers at the same stage.
    pos=src.index(capture)
    sync=src.rfind('  always @(posedge clk) begin\n',0,pos)
    async_reset=src.rfind('  always @(posedge clk or posedge reset) begin\n',0,pos)
    assert sync>async_reset>=0
    assert 'if(reset)' not in src[sync:pos]
    assert src.count('      execute_ctrl4_up_LsuL1_SEL_lane0 <= 1\'b0;')==1
    for bit in range(2):
        expression=f'    {IN}[{bit}] = (execute_ctrl3_down_LsuL1Plugin_logic_WAYS_TAGS_lane0_{bit}_loaded && (execute_ctrl3_down_LsuL1Plugin_logic_WAYS_TAGS_lane0_{bit}_address == execute_ctrl3_down_LsuL1_PHYSICAL_ADDRESS_lane0[31 : 16]));'
        assert src.count(expression)==1
    consumers=[line for line in src.splitlines() if re.search(r'\b'+HIT+r'\b',line) and line!=old and not re.search(r'^  wire\s+',line)]
    expected_consumers={
        'execute_ctrl4_down_LsuL1_MISS_lane0',
        'execute_ctrl4_down_LsuL1_FAULT_lane0',
        'execute_ctrl4_down_LsuL1_MISS_UNIQUE_lane0',
        'LsuL1Plugin_logic_lsu_ctrl_askCbm',
        'LsuL1Plugin_logic_lsu_ctrl_doWrite',
    }
    assert {re.search(r'assign (\w+) =',line)[1] for line in consumers}==expected_consumers
    assert len(consumers)==5
    new_decl='  reg                 '+SHADOW+';\n'
    new_capture=f'      {SHADOW} <= (|{IN});\n'
    new=f'  assign {HIT} = {SHADOW};'
    changes=[(decl,new_decl+decl),(capture,new_capture+capture),(old,new)]
    candidate=src
    for a,b in changes:
        candidate=candidate.replace(a,b,1)
    reverse=candidate
    for a,b in reversed(changes):
        assert reverse.count(b)==1
        reverse=reverse.replace(b,a,1)
    assert reverse==src
    assert all(candidate.count(line)==1 for line in consumers)
    assert candidate.count(alias)==1 and candidate.count(capture)==1
    # Complete four-state induction, including X/Z source and enables. Verilog
    # procedural if enters only for a known1. Both unchanged vector capture and
    # new reduction FF use the identical if, so X/Z enables hold both states.
    values=('0','1','X','Z')
    def reduction(bits):
        if '1' in bits:return '1'
        if all(bit=='0' for bit in bits):return '0'
        return 'X'
    checked=0
    for previous in itertools.product(values,repeat=2):
        scalar=reduction(previous)
        for incoming in itertools.product(values,repeat=2):
            for enable in values:
                next_vector=incoming if enable=='1' else previous
                next_scalar=reduction(incoming) if enable=='1' else scalar
                assert next_scalar==reduction(next_vector)
                checked+=1
    assert checked==1024 and reduction(('X','X'))=='X'
    out=args.output.resolve()
    out.mkdir(parents=True,exist_ok=False)
    (out/'four-state-induction.json').write_text(json.dumps(dict(passed=True,cases=checked,
        values=values,initial_vector=['X','X'],initial_scalar='X',reset='Both payload registers are unreset; reset adds no independent assignment.',
        invariant='sampled_hit === (|sampled_vector)',
        guarantee='Preserved across arbitrary source-vector/enable sequences, freezes, cancellation and reset transitions; first enabled capture establishes the invariant for any prior physical power-up values.'),indent=2)+'\n')
    # Actual emitted capture/alias/reduction statements form this independent
    # sequential fixture. All32 Boolean induction steps are then exhausted.
    tb=f'''module tb;
 reg clk=0;
 reg {EN};
 reg [1:0] {IN};
 reg [1:0] {UP};
 wire [1:0] {DOWN};
 reg {SHADOW};
 wire {HIT};
 wire reference_hit;
{alias}
{new}
 assign reference_hit=(|{DOWN});
 always @(posedge clk) if({EN}) begin
{capture}
{new_capture.rstrip()}
 end
 integer cases=0;
 initial begin
  for(integer old_state=0;old_state<4;old_state++)
   for(integer incoming=0;incoming<4;incoming++)
    for(integer advance=0;advance<2;advance++) begin
     clk=0;
     {UP}=2'(old_state);
     {SHADOW}=|2'(old_state);
     {IN}=2'(incoming);
     {EN}=1'(advance);
     #1;clk=1;#1;
     if({HIT}!==reference_hit) $fatal(1,"sampled aggregate differs: old=%x in=%x en=%x",old_state,incoming,advance);
     cases++;
    end
  $display("PASS: all%0d Boolean induction transitions from actual capture statements; freezes included",cases);
  $finish;
 end
endmodule
'''
    (out/'tb.sv').write_text(tb)
    run(['verilator','--binary','--timing','--top-module','tb','-Wno-fatal',
         '--Mdir',str(out/'obj'),'-j','2',str(out/'tb.sv')],out/'proof-build.log')
    assert not re.search(r'%Warning-(IMPLICIT|UNDRIVEN|LATCH)',(out/'proof-build.log').read_text())
    run([str(out/'obj/Vtb')],out/'proof.log')
    (out/'VexiiRiscv.v').write_text(candidate)
    patch=''.join(difflib.unified_diff(src.splitlines(True),candidate.splitlines(True),
                    fromfile='a/VexiiRiscv.v',tofile='b/VexiiRiscv.v'))
    (out/'sample-hit.patch').write_text(patch)
    replay=out/'patch-replay'
    replay.mkdir()
    (replay/'VexiiRiscv.v').write_text(candidate)
    run(['patch','--batch','--fuzz=0','-R','-p1','-i',str(out/'sample-hit.patch')],out/'reverse.log',cwd=replay)
    assert (replay/'VexiiRiscv.v').read_text()==src
    run(['patch','--batch','--fuzz=0','-p1','-i',str(out/'sample-hit.patch')],out/'replay.log',cwd=replay)
    assert (replay/'VexiiRiscv.v').read_text()==candidate
    run(['verilator','--lint-only','--top-module','VexiiRiscv','-Wno-fatal',str(out/'VexiiRiscv.v')],out/'lint.log')
    assert not re.search(r'%Warning-(IMPLICIT|UNDRIVEN|LATCH|MULTIDRIVEN)',(out/'lint.log').read_text())
    manifest=dict(reference=str(REF.relative_to(ROOT)),reference_sha256=SHA,
        candidate_sha256=sha(candidate.encode()),added_register_bits=1,extra_pipeline_cycles=0,
        capture_enable=EN,original_vector_unchanged=True,consumer_expressions_unchanged=consumers,
        unreset_payload_semantics_preserved=True,single_unreset_vector_writer_guarded=True,
        four_state_induction_cases=1024,actual_capture_induction_cases=32,
        exact_reverse_and_replay=True,full_netlist_lint_passed=True,whole_cpu_validation='pending',
        timing_scope='Moves only existing two-way OR before stage4 capture; leaves all tag comparisons and two-bit hit-vector consumers unchanged.',
        upstream_timing_evidence='timing-audit-rvc-skid-pingpong-v1-lsuhit: +0.386ns slow0, +0.584ns slow85 before new OR')
    (out/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    (out/'sample_lsu_hit.py').write_text(Path(__file__).read_text())
    print((out/'proof.log').read_text(),end='')
    print('READY',out/'VexiiRiscv.v','SHA256',manifest['candidate_sha256'],flush=True)


if __name__=='__main__':main()
