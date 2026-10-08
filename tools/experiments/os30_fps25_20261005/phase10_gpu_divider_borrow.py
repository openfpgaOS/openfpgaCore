#!/usr/bin/env python3
"""Share restoring-divider comparison and subtraction, without a new cycle."""
import difflib
import hashlib
import json
import subprocess
from pathlib import Path
from phase10_gpu_request_mux import P, ROOT

BASE = P / 'gpu-request-mux-v9/gpu_core.v'
OUT = P / 'gpu-request-mux-v10'
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()


def prepare():
    original = BASE.read_text()
    start = original.index('wire [35:0] rdet_try  =')
    end = original.index('\n', original.index('wire [34:0] rdet_next =', start))
    old = original[start:end]
    assert old == '''wire [35:0] rdet_try  = {rdet_rem, rdet_dq[44]};
wire        rdet_ge   = rdet_try >= {1'b0, rdet_divisor};
wire [44:0] rdet_dq_next = {rdet_dq[43:0], rdet_ge};
// When rdet_ge, rdet_try - divisor < divisor < 2^35, so the 35-bit subtract is
// exact; when !rdet_ge, rdet_try < 2^35 already.
wire [34:0] rdet_next = rdet_ge ? (rdet_try[34:0] - rdet_divisor) : rdet_try[34:0];'''
    new = '''wire [35:0] rdet_try  = {rdet_rem, rdet_dq[44]};
// Both operands are unsigned. A 37-bit difference represents every possible
// 36-bit trial minus 35-bit divisor; its high bit is the borrow/sign. Share
// this carry chain instead of a separate comparator and subtractor.
wire [36:0] rdet_difference = {1'b0, rdet_try} - {2'b0, rdet_divisor};
wire        rdet_ge = !rdet_difference[36];
wire [44:0] rdet_dq_next = {rdet_dq[43:0], rdet_ge};
wire [34:0] rdet_next = rdet_ge ? rdet_difference[34:0] : rdet_try[34:0];'''
    OUT.mkdir(exist_ok=False)
    (OUT / 'gpu_core.v').write_text(original[:start] + new + original[end:])
    (OUT / 'candidate.patch').write_text(''.join(difflib.unified_diff(
        original.splitlines(True), (original[:start] + new + original[end:]).splitlines(True),
        fromfile='gpu-v9/gpu_core.v', tofile='gpu-v10/gpu_core.v')))
    (OUT / 'recipe.py').write_text(Path(__file__).read_text())
    for name, source in [('baseline', old), ('candidate', new)]:
        text = '''module divider_''' + name + '''(
input [34:0] rdet_rem, input [44:0] rdet_dq, input [34:0] rdet_divisor,
output [44:0] quotient, output [34:0] remainder, output ge);
''' + source + '''
assign quotient=rdet_dq_next;
assign remainder=rdet_next;
assign ge=rdet_ge;
endmodule
'''
        (OUT / f'{name}.sv').write_text(text)
    (OUT / 'tb.sv').write_text('''module tb;
reg [34:0] remv, divv;
reg [44:0] dq;
wire [44:0] qa,qb;
wire [34:0] ra,rb;
wire ga,gb;
divider_baseline a(remv,dq,divv,qa,ra,ga);
divider_candidate b(remv,dq,divv,qb,rb,gb);
integer checks=0;
integer i,j,k,delta;
reg [63:0] rng=64'hbca0763463173241;
reg [35:0] trial;
task check;
begin
 #1;
 if({qa,ra,ga}!=={qb,rb,gb})$fatal(1,"divider mismatch");
 if(ga!==( {remv,dq[44]} >= {1'b0,divv}))$fatal(1,"compare mismatch");
 checks=checks+1;
end
endtask
task trial_check(input [35:0] t,input [34:0] d);
begin
 remv=t[35:1];divv=d;dq={t[0],44'had123bc7901};check;
end
endtask
initial begin
 // Every reduced-width input combination, including illegal divider states.
 for(i=0;i<256;i=i+1)for(j=0;j<256;j=j+1)trial_check(36'(i),35'(j));
 // Full-width sign/borrow boundaries, bit transitions and zero divisors.
 for(i=0;i<36;i=i+1)for(j=0;j<35;j=j+1)
  for(delta=-1;delta<=1;delta=delta+1)begin
   trial_check((36'd1<<i)+36'(delta),35'd1<<j);
   trial_check((36'd1<<i)+36'(delta),(35'd1<<j)-35'd1);
   trial_check((36'd1<<i)+36'(delta),(35'd1<<j)+35'd1);
  end
 for(k=0;k<1048576;k=k+1)begin
  rng=rng^(rng<<13);rng=rng^(rng>>7);rng=rng^(rng<<17);
  trial=rng[35:0];
  rng=rng^(rng<<13);rng=rng^(rng>>7);rng=rng^(rng<<17);
  divv=rng[34:0];remv=trial[35:1];dq={trial[0],rng[43:0]};check;
  if(k<65536)begin
   trial_check({1'b0,divv},divv);
   trial_check({1'b0,divv}-36'd1,divv);
   trial_check({1'b0,divv}+36'd1,divv);
  end
 end
 $display("DIVIDER_EQUIVALENCE PASS checks=%0d",checks);$finish;
end
endmodule
''')
    (OUT / 'prepared.json').write_text(json.dumps(dict(
        parent_sha256=sha(BASE), candidate_sha256=sha(OUT / 'gpu_core.v'),
        recipe_sha256=sha(Path(__file__)), new_state_bits=0, changed_cycles=0,
        proof='For 0<=trial<2^36 and 0<=divisor<2^35, their difference lies '
              'strictly between -2^35 and 2^36. In 37-bit two\'s complement, '
              'bit36 is exactly trial<divisor; low35 difference equals the '
              'original modulo2^35 subtraction for all inputs. Appending the '
              'same comparison bit preserves all45 quotient bits, including '
              'overflow and the 33/39/45-iteration projection modes.',
        timing_gain_unproven=True
    ), indent=2) + '\n')


def test():
    command = ['verilator', '--binary', '--timing', '-Wno-fatal', '--top-module', 'tb',
               '--Mdir', str(OUT / 'obj'), '-j', '4'] + [str(OUT / n) for n in ['baseline.sv', 'candidate.sv', 'tb.sv']]
    with (OUT / 'build.log').open('x') as f:
        subprocess.run(command, stdout=f, stderr=subprocess.STDOUT, check=True)
    with (OUT / 'run.log').open('x') as f:
        subprocess.run([str(OUT / 'obj/Vtb')], stdout=f, stderr=subprocess.STDOUT, check=True)
    assert 'DIVIDER_EQUIVALENCE PASS' in (OUT / 'run.log').read_text()
    files = [BASE, OUT / 'gpu_core.v', OUT / 'recipe.py', OUT / 'prepared.json',
             OUT / 'baseline.sv', OUT / 'candidate.sv', OUT / 'tb.sv',
             OUT / 'build.log', OUT / 'run.log']
    (OUT / 'divider-proof.json').write_text(json.dumps(dict(
        passed=True, command=command, actual_rtl_expressions=True,
        evidence_sha256={str(p.relative_to(ROOT)):sha(p) for p in files},
        scope='Arithmetic equivalence only; request payload ownership and full GPU replay remain separate.'
    ), indent=2) + '\n')
    print((OUT / 'run.log').read_text())


if __name__ == '__main__':
    import sys
    for action in sys.argv[1:]:
        {'prepare': prepare, 'test': test}[action]()
