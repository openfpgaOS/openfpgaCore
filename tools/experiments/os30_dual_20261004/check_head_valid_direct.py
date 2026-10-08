#!/usr/bin/env python3
"""Independent emitted-state and ready-invariant review of head-valid D."""
from pathlib import Path
import hashlib,json,re,subprocess
ROOT=Path(__file__).resolve().parents[3];ART=ROOT/'docs/measurements/os30-dual-20261004'
BASE=ART/'pointer-packer-combined-v1/immediate/VexiiRiscv.v'
SRC=ART/'decode-head-valid-direct-v3/immediate';OUT=ART/'decode-head-valid-direct-emitted-audit-v1'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def main():
    assert sha(BASE)=='5209f490af47c1730163b5c1b49ed475aecc116a38bdbed6c295d883e7d8bcaa'
    assert sha(SRC/'VexiiRiscv.v')=='c4e3515079d5c7e0584982be72ccffcc692abfcda4107e6908d80c096709af41'
    old=BASE.read_text();new=(SRC/'VexiiRiscv.v').read_text()
    h='decode_ctrls_1_up_valid';r='decode_skid_gate_down_isReady';v='decode_skid_gate_down_isValid'
    before=f'      if({r}) begin\n        {h} <= {v};\n      end'
    after=f"      {h} <= 1'b1;\n"+before
    assert old.count(before)==new.count(after)==1 and old.replace(before,after,1)==new
    assert re.findall(r'\b'+h+r'\s*<=\s*([^;]+);',old)==["1'b0",v]
    assert re.findall(r'\b'+h+r'\s*<=\s*([^;]+);',new)==["1'b0","1'b1",v]
    start=old.index('  wire decode_load_pending0;');end=old.index('\n  assign when_StageLink_l71_2',start)
    cone=old[start:end]
    assert cone in new and cone in (SRC/'actual-ready.v').read_text()
    assert 'assign decode_load_without_lane0 = !decode_ctrls_1_up_valid ||' in cone
    assert 'decode_skid_gate_down_ready = decode_load_without_lane0 || (dispatch_available0[1] && decode_load_needs_lane0);' in cone
    assert f'  assign {r} = decode_skid_gate_down_ready;' in old
    assert f'  assign {v} = decode_skid_gate_down_valid;' in old
    for side,cap in [('reference',before),('candidate',after)]:
        block=(SRC/(side+'-state.v')).read_text()
        expected=f"  always @(posedge clk or posedge reset) begin\n    if(reset) begin\n      {h} <= 1'b0;\n    end else begin\n{cap}\n    end\n  end\n"
        assert block==expected and block in (SRC/'proof.sv').read_text()
    binary=0;four=0
    for H in [0,1]:
      for rest in [0,1]:
       R=int((not H) or rest)
       for V in [0,1]:
        for reset in [0,1]:
         a=0 if reset else V if R else H
         b=0 if reset else V if R else 1
         assert a==b and a in (0,1);binary+=1
    for H in ['0','1']:
      for rest in ['0','1','x','z']:
       R='1' if H=='0' or rest=='1' else '0' if rest=='0' else 'x'
       for V in ['0','1','x','z']:
        for reset in ['0','1','x','z']:
         a='0' if reset=='1' else V if R=='1' else H
         b='0' if reset=='1' else V if R=='1' else '1'
         assert a==b;four+=1
    m=json.loads((SRC/'manifest.json').read_text());x=json.loads((SRC/'four-state-scope.json').read_text())
    assert not m['unrestricted_unknown_head_equivalence'] and x['counterexample']['old_next']!=x['counterexample']['new_next']
    OUT.mkdir(exist_ok=False)
    with (OUT/'proof-rerun.log').open('w') as log:subprocess.run([str(SRC/'obj/Vtb')],stdout=log,stderr=subprocess.STDOUT,check=True)
    result=dict(passed=True,parent_sha256=sha(BASE),candidate_sha256=sha(SRC/'VexiiRiscv.v'),
      exact_only_constant_default_before_existing_capture=True,reset_and_sole_writer_scope_verified=True,
      actual_ready_cone_and_aliases_bound=True,actual_131072_pattern_binary_rerun_pass=True,
      independent_binary_reset_closed_transitions=binary,known_head_four_state_cases=four,
      all_other_register_control_payload_masks_and_flush_equations_identical=True,
      unrestricted_unknown_head_equivalence=False,
      qualification_scope='Equivalent from reset-established binary valid state with binary protocol inputs; arbitrary unknown-head state is explicitly excluded.',
      no_extra_registers_or_cycles=True,whole_cpu_qualification='Pending separately.')
    (OUT/'manifest.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))
if __name__=='__main__':main()
