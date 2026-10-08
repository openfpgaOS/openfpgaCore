#!/usr/bin/env python3
"""Freeze a two-register CE mapping experiment; preserve exact procedural holds."""
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
BASES = {
    'pair': ('pointer-packer-combined-v1/immediate/VexiiRiscv.v',
             '5209f490af47c1730163b5c1b49ed475aecc116a38bdbed6c295d883e7d8bcaa'),
    'rs12': ('integer-rs1-fp-rs2-combined-v1/immediate/VexiiRiscv.v',
              'fb18b1ca44bdb6ae500583c094d0c8859e3a7184457194fb40b738fe09337fdb'),
}
CAPTURE = '''      if(decode_skid_gate_down_isReady) begin
        decode_ctrls_1_up_LANE_SEL_0 <= decode_skid_gate_down_LANE_SEL_0;
        decode_ctrls_1_up_LANE_SEL_1 <= decode_skid_gate_down_LANE_SEL_1;
      end'''
EXPLICIT_HOLD = CAPTURE[:-3] + '''end else begin
        decode_ctrls_1_up_LANE_SEL_0 <= decode_ctrls_1_up_LANE_SEL_0;
        decode_ctrls_1_up_LANE_SEL_1 <= decode_ctrls_1_up_LANE_SEL_1;
      end'''
CANCEL = '''      if(when_DecodePipelinePlugin_l95) begin
        decode_ctrls_1_up_LANE_SEL_0 <= 1'b0;
      end
      if(when_DecodePipelinePlugin_l95_1) begin
        decode_ctrls_1_up_LANE_SEL_1 <= 1'b0;
      end'''
ATTRIBUTE = '(* altera_attribute = "-name AUTO_CLOCK_ENABLE_RECOGNITION OFF" *) '


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(command, log, **kwargs):
    with log.open('w') as f:
        subprocess.run([str(s) for s in command], check=True, stdout=f,
                       stderr=subprocess.STDOUT, **kwargs)


def transform(old):
    assert old.count(CAPTURE + '\n' + CANCEL) == 1
    new = old.replace(CAPTURE, EXPLICIT_HOLD, 1)
    for lane in range(2):
        name = f'decode_ctrls_1_up_LANE_SEL_{lane}'
        declaration = f'  reg                 {name};'
        assert old.count(declaration) == 1
        assert len(re.findall(r'\b' + name + r'\s*<=', old)) == 3
        assert len(re.findall(r"^      " + name + r" <= 1'b0;$", old, re.M)) == 1
        new = new.replace(declaration, '  ' + ATTRIBUTE + declaration[2:], 1)
    restored = new.replace(EXPLICIT_HOLD, CAPTURE, 1)
    for lane in range(2):
        declaration = f'  reg                 decode_ctrls_1_up_LANE_SEL_{lane};'
        restored = restored.replace('  ' + ATTRIBUTE + declaration[2:], declaration, 1)
    assert restored == old
    return new


def state_block(text):
    capture = EXPLICIT_HOLD if EXPLICIT_HOLD in text else CAPTURE
    assert text.count(capture + '\n' + CANCEL) == 1
    reset = '\n'.join(f"      decode_ctrls_1_up_LANE_SEL_{n} <= 1'b0;" for n in range(2))
    assert reset in text
    # All writers are exactly the original async reset, capture, and cancel,
    # plus the new explicit self-hold; the original surrounding block is shared.
    prefix = text[:text.index(reset)]
    assert prefix.rfind('always @(posedge clk or posedge reset) begin') > prefix.rfind('always @(posedge clk) begin')
    return ('  always @(posedge clk or posedge reset) begin\n'
            '    if(reset) begin\n' + reset + '\n    end else begin\n' +
            capture + '\n' + CANCEL + '\n    end\n  end\n')


def proof(original, candidate, out):
    blocks = [state_block(original), state_block(candidate)]
    modules = ''
    for name, block in zip(('reference', 'candidate'), blocks):
        (out / (name + '-block.v')).write_text(block)
        modules += ('module ' + name + '(input clk,reset,decode_skid_gate_down_isReady,\n'
                    'input decode_skid_gate_down_LANE_SEL_0,decode_skid_gate_down_LANE_SEL_1,\n'
                    'input when_DecodePipelinePlugin_l95,when_DecodePipelinePlugin_l95_1,\n'
                    'output reg decode_ctrls_1_up_LANE_SEL_0,decode_ctrls_1_up_LANE_SEL_1);\n' +
                    block + 'endmodule\n')
    tb = modules + '''module tb;
reg clk=0, reset=0, ready=0;
reg [1:0] incoming=0, cancel=0;
wire [1:0] reference_q, candidate_q;
reference a(clk,reset,ready,incoming[0],incoming[1],cancel[0],cancel[1],reference_q[0],reference_q[1]);
candidate b(clk,reset,ready,incoming[0],incoming[1],cancel[0],cancel[1],candidate_q[0],candidate_q[1]);
reg [1:0] expected;
integer cases=0;
task check;
begin
 if(reference_q !== expected || candidate_q !== expected)
  $fatal(1,"state mismatch old=%b new=%b expected=%b",reference_q,candidate_q,expected);
end
endtask
task step;
begin #1;clk=1;#1;clk=0;#1;end
endtask
initial begin
 for(integer q=0;q<4;q++) for(integer data=0;data<4;data++)
 for(integer r=0;r<2;r++) for(integer c=0;c<4;c++) for(integer rst=0;rst<2;rst++) begin
  // Restore every old state using the actual capture, without an artificial load arm.
  reset=1;ready=0;cancel=0;#1;expected=0;check();reset=0;
  ready=1;incoming=q[1:0];step();expected=q[1:0];check();
  ready=r[0];incoming=data[1:0];cancel=c[1:0];reset=rst[0];#1;
  if(rst!=0) begin expected=0;check();end
  expected=rst ? 2'b00 : ((r ? data[1:0] : q[1:0]) & ~c[1:0]);
  step();check();cases++;
 end
 reset=0;ready=1;cancel=0;incoming=3;step();expected=3;check();
 reset=1;#1;expected=0;check();reset=0;#1;check();
 $display("PASS all%0d state/input/ready/cancel/reset transitions and off-clock async reset",cases);
 $finish;
end
endmodule
'''
    (out / 'proof.sv').write_text(tb)
    run(['verilator', '--binary', '--timing', '-Wno-fatal', '--top-module', 'tb',
         '--Mdir', out / 'obj', '-j', '2', out / 'proof.sv'], out / 'proof-build.log')
    assert not re.search(r'%Warning-(IMPLICIT|UNDRIVEN|LATCH|MULTIDRIVEN)', (out / 'proof-build.log').read_text())
    run([out / 'obj/Vtb'], out / 'proof.log')
    assert 'PASS all256 state/input' in (out / 'proof.log').read_text()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--base', choices=BASES, required=True)
    ap.add_argument('--output', type=Path)
    args = ap.parse_args()
    relative, digest = BASES[args.base]
    parent = ART / relative
    assert sha(parent) == digest
    old = parent.read_text()
    new = transform(old)
    out = args.output or ART / 'decode-head-mask-d-v1' / args.base
    out = out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    target = out / 'VexiiRiscv.v'
    target.write_text(new)
    proof(old, new, out)
    patch = out / 'head-mask-d.patch'
    patch.write_text(''.join(difflib.unified_diff(old.splitlines(True), new.splitlines(True),
                     fromfile='a/VexiiRiscv.v', tofile='b/VexiiRiscv.v')))
    replay = out / 'patch-replay'
    replay.mkdir()
    (replay / 'VexiiRiscv.v').write_text(new)
    for label, flags, expected in [('reverse', ['-R'], old), ('replay', [], new)]:
        run(['patch', '--batch', '--fuzz=0', *flags, '-p1', '-i', patch],
            out / (label + '.log'), cwd=replay)
        assert (replay / 'VexiiRiscv.v').read_text() == expected
    run(['verilator', '--lint-only', '--top-module', 'VexiiRiscv', '-Wno-fatal', target], out / 'lint.log')
    assert not re.search(r'%Warning-(IMPLICIT|UNDRIVEN|LATCH|MULTIDRIVEN)', (out / 'lint.log').read_text())
    shutil.copy2(__file__, out / Path(__file__).name)
    manifest = dict(reference=str(parent.relative_to(ROOT)), reference_sha256=digest,
        candidate_sha256=sha(target), base=args.base, extra_registers=0, extra_cycles=0,
        exact_scope='Only two head lane-mask attributes and explicit self-hold else arm; original capture, cancellation priority and asynchronous reset untouched.',
        selected_registers=[f'decode_ctrls_1_up_LANE_SEL_{n}' for n in range(2)],
        actual_source_binary_transitions=256, off_clock_asynchronous_reset=True,
        four_state_semantics='Unchanged: the same procedural if samples only ready==1. Its added else assigns the previous value to itself, even for X/Z ready; cancellation still has last-assignment priority.',
        exact_reverse_replay=True, full_netlist_lint=True,
        physical_mapping='Pending fit. CE recognition OFF is a supported local option; actual D rather than ENA and resource/timing effects must be measured.',
        head_valid='Unchanged. Pair5209 already captures its measured critical input through D, so an added CE hint would not target that path.',
        whole_cpu_suite='Not run for this mapping experiment; focused proof covers the entire changed state transition.')
    for file in ['head_mask_d.py', 'head-mask-d.patch', 'proof.sv', 'proof.log', 'reference-block.v', 'candidate-block.v']:
        manifest[file.replace('.', '_') + '_sha256'] = sha(out / file)
    (out / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    (out / 'recipe.json').write_text(json.dumps(dict(script=str(Path(__file__).relative_to(ROOT)),
        script_sha256=sha(Path(__file__)), command=['python3', str(Path(__file__).relative_to(ROOT)), '--base', args.base],
        reference_sha256=digest, candidate_sha256=sha(target)), indent=2) + '\n')
    print(json.dumps(manifest, indent=2))


if __name__ == '__main__':
    main()
