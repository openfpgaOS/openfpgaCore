#!/usr/bin/env python3
"""Independently bind the pointer D/XOR proof to the emitted CPU source."""
from pathlib import Path
import hashlib,itertools,json,re,shutil,subprocess
ROOT=Path(__file__).resolve().parents[4]
BASE=ROOT/'docs/measurements/os30-dual-20261004'
SOURCE=BASE/'pointer-unconditional-d-v1/immediate'
OUT=BASE/'pointer-d-emitted-audit-v1'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def main():
    OUT.mkdir(parents=True,exist_ok=False)
    manifest=json.loads((SOURCE/'manifest.json').read_text())
    before=ROOT/manifest['reference'];after=SOURCE/'VexiiRiscv.v'
    old=before.read_text();new=after.read_text()
    assert sha(before)==manifest['reference_sha256'] and sha(after)==manifest['candidate_sha256']
    old_declaration='  reg decode_pingpong_read_bank;'
    new_declaration='  (* altera_attribute = "-name AUTO_CLOCK_ENABLE_RECOGNITION OFF" *) reg decode_pingpong_read_bank;'
    old_block="""  always @(posedge clk or posedge reset) begin
    if(reset) begin
      decode_pingpong_read_bank <= 1'b0;
    end else if(decode_ctrls_1_up_valid && decode_skid_gate_down_isReady) begin
      decode_pingpong_read_bank <= !decode_pingpong_read_bank;
    end
  end"""
    new_block="""  always @(posedge clk or posedge reset) begin
    if(reset) begin
      decode_pingpong_read_bank <= 1'b0;
    end else begin
      decode_pingpong_read_bank <= decode_pingpong_read_bank ^
          (decode_ctrls_1_up_valid && decode_skid_gate_down_isReady);
    end
  end"""
    assert old.count(old_declaration)==old.count(old_block)==1
    assert new.count(new_declaration)==new.count(new_block)==1
    assert old.replace(old_declaration,new_declaration,1).replace(old_block,new_block,1)==new
    assert new.replace(new_declaration,old_declaration,1).replace(new_block,old_block,1)==old
    assert len(re.findall(r'\bdecode_pingpong_read_bank\s*<=',old))==2
    assert len(re.findall(r'\bdecode_pingpong_read_bank\s*<=',new))==2
    assert len(re.findall(r'^\s*always\s*@',old,re.M))==len(re.findall(r'^\s*always\s*@',new,re.M))
    # Check actual extracted bodies and complete proof wrappers, not merely
    # functionally similar handwritten equations.
    assert (SOURCE/'reference-block.v').read_text()==old_block+'\n'
    assert (SOURCE/'candidate-block.v').read_text()==new_block+'\n'
    proof=(SOURCE/'proof.sv').read_text()
    for name,body in [('reference',old_block),('candidate',new_block)]:
        wrapper=f'module {name}(input clk,reset,decode_ctrls_1_up_valid,decode_skid_gate_down_isReady,\noutput reg decode_pingpong_read_bank);\n{body}\nendmodule'
        assert proof.count(wrapper)==1
    for text in ['for(integer q=0;q<2;q++) for(integer r=0;r<2;r++)',
                 'for(integer h=0;h<2;h++) for(integer accept=0;accept<2;accept++)',
                 'reset=1;#1;expected=0;compare();reset=0;#1;compare();']:
        assert text in proof,text
    rows=[]
    for q,reset,valid,ready in itertools.product(range(2),repeat=4):
        original=0 if reset else (1-q if valid and ready else q)
        candidate=0 if reset else q^(valid and ready)
        assert original==candidate
        rows.append(dict(q=q,reset=reset,valid=valid,ready=ready,next_q=int(candidate)))
    (OUT/'transition-table.json').write_text(json.dumps(rows,indent=2)+'\n')
    with (OUT/'proof-rerun.log').open('w') as f:
        subprocess.run([SOURCE/'obj/Vtb'],check=True,stdout=f,stderr=subprocess.STDOUT)
    assert 'PASS all16 pointer-state/valid/ready/reset transitions and off-clock asynchronous reset' in (OUT/'proof-rerun.log').read_text()
    # Independent zero-fuzz application and reversal in fresh private storage.
    replay=OUT/'replay';replay.mkdir();shutil.copy2(before,replay/'VexiiRiscv.v')
    for label,args,expected in [('forward',[],after),('reverse',['-R'],before),('forward-again',[],after)]:
        with (OUT/(label+'.log')).open('w') as f:
            subprocess.run(['patch','--batch','--fuzz=0',*args,'-p1','-i',str(SOURCE/'pointer-d.patch')],cwd=replay,check=True,stdout=f,stderr=subprocess.STDOUT)
        assert sha(replay/'VexiiRiscv.v')==sha(expected)
    assert sha(SOURCE/'patch-replay/VexiiRiscv.v')==sha(after)
    assert not re.search(r'%Warning-(IMPLICIT|UNDRIVEN|LATCH|MULTIDRIVEN)',(SOURCE/'lint.log').read_text())
    files=[before,after,*[SOURCE/n for n in ['manifest.json','reference-block.v','candidate-block.v','proof.sv',
           'proof.cpp'] if (SOURCE/n).exists()],SOURCE/'proof.log',SOURCE/'obj/Vtb',SOURCE/'pointer-d.patch',
           SOURCE/'lint.log',SOURCE/'pointer_unconditional_d.py',Path(__file__),OUT/'transition-table.json',OUT/'proof-rerun.log']
    result=dict(reference_sha256=sha(before),candidate_sha256=sha(after),
        exact_only_original_pointer_attribute_and_next_state=True,one_existing_pointer_register=True,
        same_async_reset_sensitivity_polarity_value=True,same_toggle_predicate=True,
        all_other_source_bytes_and_consumers_identical=True,actual_proof_wrappers_bound_to_emitted=True,
        exhaustive_binary_transition_cases=16,off_clock_reset_assertion_and_release_checked=True,
        actual_compiled_proof_rerun_passed=True,independent_zero_fuzz_forward_reverse_replay=True,
        added_source_registers=0,added_pipeline_cycles=0,
        proof_boundary='Binary defined-state sequential equivalence and asynchronous reset; arbitrary four-state X propagation is not claimed.',
        physical_boundary='Attribute acceptance is separate from actual D-versus-ENA mapping, duplication and timing; those require the fitted netlist.',
        whole_cpu_tests='Generator owns one final combined-candidate suite; no standalone CPU fixture rerun here.',
        evidence_sha256={str(p.relative_to(ROOT)):sha(p) for p in files})
    (OUT/'manifest.json').write_text(json.dumps(result,indent=2)+'\n')
    (OUT/'REVIEW.txt').write_text('''Pointer D/XOR emitted-source audit: PASS

The complete candidate reconstructs exactly from the parent by only the
original read-bank declaration attribute and one next-state block. The single
existing pointer retains posedge reset, reset priority, reset value0 and the
same H&&down_ready toggle predicate. Payload banks, lane masks, occupancy,
handshakes and every consumer are byte-identical. No source FF or cycle added.

The compiled proof wrappers contain the exact emitted sequential bodies.
Independent enumeration and the rerun actual proof cover all16 binary old
state/reset/valid/ready combinations. Reset assertion between clock edges,
release without a clock, reset priority and transitions from both states pass.
Fresh zero-fuzz forward/reverse/reapply reconstruct both complete RTL files.

This establishes defined binary-state behavior after reset. It does not claim
identical arbitrary X propagation: an unknown if-condition in a four-state
simulation can hold where XOR produces X. Hardware two-state equivalence and
reset induction are the relevant qualification; unknown-control diagnostics
are not part of the claim. Actual D-versus-ENA mapping and duplicate-pointer
absence remain post-fit checks. Generator owns the combined CPU regression.
''')
    print('PASS exact pointer source,16 binary transitions, off-clock reset and independent replay; no CPU fixtures run.')
if __name__=='__main__':main()
