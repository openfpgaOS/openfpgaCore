#!/usr/bin/env python3
"""Freeze a cycle-equivalent single-pointer D/XOR mapping experiment."""
from pathlib import Path
import difflib
import hashlib
import json
import re
import shutil
import subprocess

ROOT=Path(__file__).resolve().parents[3]
ART=ROOT/'docs/measurements/os30-dual-20261004'
BASE=ART/'fp-last-combined-v1/immediate/VexiiRiscv.v'
OUT=ART/'pointer-unconditional-d-v1/immediate'
SHA='9faabaeecba4c514cc7f76739218ff36a402d016617fb49c859ad57c6eaeb377'


def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()


def run(cmd,log,**kw):
    with log.open('w') as f:subprocess.run(list(map(str,cmd)),check=True,stdout=f,stderr=subprocess.STDOUT,**kw)


def main():
    assert sha(BASE)==SHA
    old=BASE.read_text()
    declaration='  reg decode_pingpong_read_bank;'
    attribute='  (* altera_attribute = "-name AUTO_CLOCK_ENABLE_RECOGNITION OFF" *) reg decode_pingpong_read_bank;'
    before='''  always @(posedge clk or posedge reset) begin
    if(reset) begin
      decode_pingpong_read_bank <= 1'b0;
    end else if(decode_ctrls_1_up_valid && decode_skid_gate_down_isReady) begin
      decode_pingpong_read_bank <= !decode_pingpong_read_bank;
    end
  end'''
    after='''  always @(posedge clk or posedge reset) begin
    if(reset) begin
      decode_pingpong_read_bank <= 1'b0;
    end else begin
      decode_pingpong_read_bank <= decode_pingpong_read_bank ^
          (decode_ctrls_1_up_valid && decode_skid_gate_down_isReady);
    end
  end'''
    assert old.count(declaration)==old.count(before)==1
    assert len(re.findall(r'decode_pingpong_read_bank\s*<=',old))==2
    new=old.replace(declaration,attribute,1).replace(before,after,1)
    assert new.replace(after,before,1).replace(attribute,declaration,1)==old
    OUT.mkdir(parents=True,exist_ok=False)
    modules=''
    for name,body in [('reference',before),('candidate',after)]:
        modules+=f'''module {name}(input clk,reset,decode_ctrls_1_up_valid,decode_skid_gate_down_isReady,
output reg decode_pingpong_read_bank);
{body}
endmodule
'''
    tb=modules+'''module tb;
reg clk=0,reset=0,valid=0,ready=0;
wire original,result;
reference a(clk,reset,valid,ready,original);
candidate b(clk,reset,valid,ready,result);
integer cases=0;
reg expected;
task step;
begin
  #1; clk=1; #1; clk=0; #1;
end
endtask
task compare;
begin
  if(original !== expected || result !== expected)
    $fatal(1,"pointer mismatch expected=%b old=%b new=%b",expected,original,result);
end
endtask
initial begin
 for(integer q=0;q<2;q++) for(integer r=0;r<2;r++)
 for(integer h=0;h<2;h++) for(integer accept=0;accept<2;accept++) begin
  // Restore a defined old state without adding a proof-only RTL load arm.
  reset=1;valid=0;ready=0;#1;expected=0;compare();reset=0;
  if(q!=0) begin valid=1;ready=1;step();end
  expected=q;compare();
  valid=h;ready=accept;reset=r;#1;
  if(r!=0) begin expected=0;compare();end
  expected=r?0:(q^(h&&accept));step();compare();
  cases++;
 end
 // Reset assertion between clock edges clears both implementations immediately.
 reset=0;valid=1;ready=1;step();expected=1;compare();
 reset=1;#1;expected=0;compare();reset=0;#1;compare();
 $display("PASS all%0d pointer-state/valid/ready/reset transitions and off-clock asynchronous reset",cases);
 $finish;
end
endmodule
'''
    (OUT/'reference-block.v').write_text(before+'\n')
    (OUT/'candidate-block.v').write_text(after+'\n')
    (OUT/'proof.sv').write_text(tb)
    run(['verilator','--binary','--timing','-Wno-fatal','--top-module','tb','--Mdir',OUT/'obj','-j','2',OUT/'proof.sv'],OUT/'proof-build.log')
    assert not re.search(r'%Warning-(IMPLICIT|UNDRIVEN|LATCH|MULTIDRIVEN)',(OUT/'proof-build.log').read_text())
    run([OUT/'obj/Vtb'],OUT/'proof.log')
    assert 'PASS all16 pointer-state' in (OUT/'proof.log').read_text()
    target=OUT/'VexiiRiscv.v';target.write_text(new)
    patch=OUT/'pointer-d.patch'
    patch.write_text(''.join(difflib.unified_diff(old.splitlines(True),new.splitlines(True),fromfile='a/VexiiRiscv.v',tofile='b/VexiiRiscv.v')))
    replay=OUT/'patch-replay';replay.mkdir();(replay/'VexiiRiscv.v').write_text(new)
    for name,flags,expected in [('reverse',['-R'],old),('replay',[],new)]:
        run(['patch','--batch','--fuzz=0',*flags,'-p1','-i',patch],OUT/(name+'.log'),cwd=replay)
        assert (replay/'VexiiRiscv.v').read_text()==expected
    run(['verilator','--lint-only','--top-module','VexiiRiscv','-Wno-fatal',target],OUT/'lint.log')
    assert not re.search(r'%Warning-(IMPLICIT|UNDRIVEN|LATCH|MULTIDRIVEN)',(OUT/'lint.log').read_text())
    shutil.copy2(__file__,OUT/Path(__file__).name)
    manifest=dict(reference=str(BASE.relative_to(ROOT)),reference_sha256=SHA,candidate_sha256=sha(target),
        exact_scope='Only original read-bank register attribute and equivalent next-state expression.',
        same_async_reset_polarity_and_value=True,same_toggle_predicate=True,
        no_other_payload_metadata_occupancy_ready_or_state_edits=True,
        actual_source_transition_cases=16,off_clock_reset_checked=True,
        extra_registers=0,extra_cycles=0,existing_pointer_registers=1,
        clock_enable_option='AUTO_CLOCK_ENABLE_RECOGNITION OFF on decode_pingpong_read_bank only',
        option_parser_probe=str((ART/'pointer-ce-option-probe-v1').relative_to(ROOT)),
        parser_support='Lite25.1 accepts exact per-instance option with no warning; actual mapping still needs confirmation.',
        mapping_qualification='Must verify the critical arrival uses D, not ENA, and that no duplicate pointer or extra bank state is introduced.',
        exact_reverse_replay=True,full_netlist_lint=True,
        full_cpu_qualification='Pending final composition; retain existing pointer/skid monitors in14-case suite.')
    (OUT/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    print((OUT/'proof.log').read_text(),end='');print(json.dumps(manifest,indent=2))


if __name__=='__main__':main()
