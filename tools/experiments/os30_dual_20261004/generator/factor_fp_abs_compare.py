#!/usr/bin/env python3
"""Prove a local FP absolute-comparison special-class Boolean factorization."""
from pathlib import Path
import argparse
import difflib
import hashlib
import importlib.util
import json
import re
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[4]
ART = ROOT / 'docs/measurements/os30-dual-20261004'
REF = ART / 'fp-last-combined-v1/immediate/VexiiRiscv.v'
SHA = '9faabaeecba4c514cc7f76739218ff36a402d016617fb49c859ad57c6eaeb377'
CHECK = ROOT / 'tools/check_vexii_os20_timing.py'
CHECK_SHA = '80a0c0e9cee4d3f7532c44d4aca198becc46c2400ebc8bfba811bd61e10f6188'
DEST = 'FpuCmpPlugin_logic_onCmp_rs1AbsSmaller'
PREFIX = 'execute_ctrl2_down_FpuCmpPlugin_logic_onCmp_'
EXP_LESS = PREFIX + 'rs1ExpSmaller_lane0'
EXP_EQ = PREFIX + 'expEqual_lane0'
MANT_LESS = PREFIX + 'rs1MantissaSmaller_lane0'
FLAGS = ['when_FpuCmpPlugin_l' + str(i) for i in range(110, 114)]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(command, log, **kwargs):
    with log.open('w') as handle:
        subprocess.run(command, check=True, stdout=handle, stderr=subprocess.STDOUT, **kwargs)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ART / 'fp-cmp-abs-factor-v1/immediate')
    args = parser.parse_args()
    assert sha(REF) == SHA and sha(CHECK) == CHECK_SHA
    source = REF.read_text()
    base = f'({EXP_LESS} || ({EXP_EQ} && {MANT_LESS}))'
    old = f'''  always @(*) begin
    {DEST} = {base};
    if({FLAGS[0]}) begin
      {DEST} = 1'b1;
    end
    if({FLAGS[1]}) begin
      {DEST} = 1'b1;
    end
    if({FLAGS[2]}) begin
      {DEST} = 1'b0;
    end
    if({FLAGS[3]}) begin
      {DEST} = 1'b0;
    end
  end
'''
    assert source.count(old) == 1
    new = f'''  always @(*) begin
    {DEST} = (!( {FLAGS[3]} || {FLAGS[2]})) &&
        ({FLAGS[0]} || {FLAGS[1]} || {base});
  end
'''
    candidate = source.replace(old, new, 1)
    assert candidate.replace(new, old, 1) == source
    # Class decoding is not rewritten and no mutually-exclusive-class
    # assumption is used. Even all four overrides asserted must preserve
    # the final zero override of the original ordered assignments.
    for flag, rs, mode in zip(FLAGS, (2,1,2,1), ('INF','ZERO','ZERO','INF')):
        statement = f'  assign {flag} = (execute_ctrl2_down_FpuUnpack_RS{rs}_RS_lane0_mode == FloatMode_{mode});'
        assert source.count(statement) == candidate.count(statement) == 1
    seq_old = re.findall(r'^  always @\(posedge clk.*?^  end', source, re.M | re.S)
    seq_new = re.findall(r'^  always @\(posedge clk.*?^  end', candidate, re.M | re.S)
    assert seq_old == seq_new
    # NaN signaling, equality, signed comparison, final CMP_RESULT, min/max,
    # flags and all consumers remain literally unchanged outside this block.
    spec = importlib.util.spec_from_file_location('actual_cones', CHECK)
    checker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checker)
    inputs = [EXP_LESS, EXP_EQ, MANT_LESS, *FLAGS]
    reference, width = checker.cone(source, 'reference', inputs, [DEST])
    changed, new_width = checker.cone(candidate, 'candidate', inputs, [DEST])
    assert width == new_width == 1
    tb = reference + changed + '\nmodule tb;\n'
    tb += '\n'.join('reg ' + name + ';' for name in inputs) + '\nwire original,result;\n'
    tb += 'reference a(' + ','.join(inputs + ['original']) + ');\n'
    tb += 'candidate b(' + ','.join(inputs + ['result']) + ');\n'
    tb += '''integer cases=0;
initial begin
  // Exactly32 independent class/base conditions, with numericAbsLess=v[0].
  for(integer v=0;v<32;v++) begin
'''
    tb += f'    {EXP_LESS}=v[0]; {EXP_EQ}=0; {MANT_LESS}=0;\n'
    for bit, name in enumerate(FLAGS, 1):
        tb += f'    {name}=v[{bit}];\n'
    tb += '''    #1;
    if(original !== result) $fatal(1,"class/base mismatch v=%x old=%x new=%x",v,original,result);
    cases++;
  end
  // Also exhaust the exact emitted three-input numeric comparison term,
  // alongside every independent combination of the four class predicates.
  for(integer v=0;v<128;v++) begin
'''
    for bit, name in enumerate(inputs):
        tb += f'    {name}=v[{bit}];\n'
    tb += '''    #1;
    if(original !== result) $fatal(1,"actual-cone mismatch v=%x old=%x new=%x",v,original,result);
    cases++;
  end
  $display("PASS all32 independent class/base conditions and all128 actual seven-input cone conditions (%0d total checks)",cases);
  $finish;
end
endmodule
'''
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    (out / 'reference.v').write_text(reference)
    (out / 'candidate.v').write_text(changed)
    (out / 'tb.sv').write_text(tb)
    run(['verilator', '--binary', '--timing', '--top-module', 'tb', '-Wno-fatal',
         '--Mdir', str(out / 'obj'), '-j', '2', str(out / 'tb.sv')], out / 'proof-build.log')
    assert not re.search(r'%Warning-(IMPLICIT|UNDRIVEN|LATCH)', (out / 'proof-build.log').read_text())
    run([str(out / 'obj/Vtb')], out / 'proof.log')
    target = out / 'VexiiRiscv.v'
    target.write_text(candidate)
    patch = out / 'fp-abs-factor.patch'
    patch.write_text(''.join(difflib.unified_diff(source.splitlines(True), candidate.splitlines(True),
                    fromfile='a/VexiiRiscv.v', tofile='b/VexiiRiscv.v')))
    replay = out / 'patch-replay'
    replay.mkdir()
    (replay / target.name).write_text(candidate)
    for label, extra, expected in [('reverse', ['-R'], source), ('replay', [], candidate)]:
        run(['patch', '--batch', '--fuzz=0', *extra, '-p1', '-i', str(patch)], out / (label+'.log'), cwd=replay)
        assert (replay / target.name).read_text() == expected
    run(['verilator', '--lint-only', '--top-module', 'VexiiRiscv', '-Wno-fatal', str(target)], out / 'lint.log')
    assert not re.search(r'%Warning-(IMPLICIT|UNDRIVEN|LATCH|MULTIDRIVEN)', (out / 'lint.log').read_text())
    shutil.copyfile(__file__, out / Path(__file__).name)
    shutil.copyfile(CHECK, out / CHECK.name)
    manifest = dict(reference=str(REF.relative_to(ROOT)), reference_sha256=SHA,
        candidate_sha256=sha(target), exact_scope='Only the one rs1AbsSmaller combinational block; four ordered overrides factored into a Boolean expression.',
        class_decoding_unchanged=True, numeric_comparison_term_unchanged=True,
        nan_equality_sign_final_result_flags_consumers_unchanged=True,
        all_sequential_source_unchanged=True, no_class_reachability_assumptions=True,
        independent_class_base_cases=32, actual_seven_input_cases=128, total_actual_cone_checks=160,
        new_registers=0, new_cycles=0, new_keep_boundaries=0,
        exact_guarded_scope_and_reverse_replay=True, full_netlist_lint=True,
        whole_cpu_qualification='NOT RUN: proof-only fallback pending current complete-core timing; parent explicitly holds the14-case suite and fit.',
        physical_limitation='Compiler may already derive the same Boolean network; this source proof establishes behavior, not an expected timing improvement.')
    (out / 'manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')
    (out / 'recipe.json').write_text(json.dumps(dict(script_sha256=sha(out / Path(__file__).name),
        checker_sha256=CHECK_SHA, reference_sha256=SHA, candidate_sha256=sha(target),
        patch_sha256=sha(patch), reference_cone_sha256=sha(out / 'reference.v'),
        candidate_cone_sha256=sha(out / 'candidate.v'), proof_sha256=sha(out / 'tb.sv')),indent=2)+'\n')
    (out / 'README.txt').write_text('''Proof-only FP comparison fallback
=================================

This changes only the rs1AbsSmaller special-class override block on qualified
9faabaee. The original numeric comparison and class predicates are retained.
The final two zero overrides are factored as a common veto; the first two one
overrides OR with the numeric result. This preserves all32 independent class/
base combinations, including impossible simultaneous class predicates, without
assuming any class correlation. The actual seven-input emitted cone also
passes all128 combinations. Nothing in NaN handling, equality, sign ordering,
min/max selection, flags, final result or any sequential block is changed.

No FFs, cycles or KEEP boundaries are added. Source guards, actual-cone proof,
external zero-fuzz reverse/replay and full-netlist lint pass. Whole-CPU tests
and fitting are intentionally held until the parent selects this fallback.
The compiler may already infer the same network; timing benefit is unproven.

Reproduce with generator/factor_fp_abs_compare.py --output <new-directory>.
Production is unchanged. recipe.json pins all reproduction/proof inputs.
''')
    print((out / 'proof.log').read_text(), end='')
    print(json.dumps(manifest, indent=2))


if __name__ == '__main__':
    main()
