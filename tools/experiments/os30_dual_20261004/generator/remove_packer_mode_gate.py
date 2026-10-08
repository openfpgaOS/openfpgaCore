#!/usr/bin/env python3
"""Remove only the packer rounding NORMAL-mode gate; prove its observable cone."""
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
P = 'FpuPackerPlugin_logic_pip_node_1_'
S = 'FpuPackerPlugin_logic_s2_'
ROUND = P + 's1_ROUNDING_INCR'
MODE = P + 's0_VALUE_mode'
OLD = f'  assign {ROUND} = (({MODE} == FloatMode_NORMAL) && _zz_{ROUND});'
NEW = f'  assign {ROUND} = _zz_{ROUND};'
FLAGS = ['NX', 'UF', 'OF', 'DZ', 'NV']
OUTS = [S + 'fwb_value'] + ['FpuPackerPlugin_logic_flagsWb_flags_' + f for f in FLAGS]
OUTS += [f'FpuPackerPlugin_wb_at_{i}_valid' for i in (4, 1, 6, 9)]
OUTS += [S + 'fpWriter_valid', 'FpuPackerPlugin_logic_flagsWb_ats', S + 'fpWriter_payload_uopId']
COMMON = {MODE: 2, P+'s0_VALUE_sign': 1, P+'s0_VALUE_quiet': 1}
COMMON.update({P+'s0_FLAGS_'+f: 1 for f in FLAGS})
COMMON.update({P+'s0_GROUP_OH': 4, P+'valid': 1, P+'Decode_UOP_ID': 16})
FULL = dict(COMMON, **{P+'s0_VALUE_exponent': 10, S+'mr': 25,
    P+'s0_ROUNDMODE': 3, P+'s0_EXP_SUBNORMAL': 8})
CUT = dict(COMMON, **{P+'s2_EXP': 10, P+'s1_MAN_RESULT': 23,
    P+'s2_EXP_OVERFLOW': 1, P+'s2_EXP_UNDERFLOW': 1,
    'when_FpuPackerPlugin_l224': 1, 'when_FpuPackerPlugin_l241': 1})


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(command, log, **kwargs):
    with log.open('w') as handle:
        subprocess.run(command, check=True, stdout=handle, stderr=subprocess.STDOUT, **kwargs)


def closure(source):
    """Stop only at the proved data/flag interface, and reject unparsed escapes."""
    decls = list(re.finditer(r'^  (?:wire|reg)\s+(?:\[[^\n]+?\]\s+)?(\w+);$', source, re.M))
    names = {m[1] for m in decls}
    blocks = list(re.finditer(r'^  assign \w+\s*=.*?;\n|^  always @\(\*\) begin\n.*?^  end\n', source, re.M | re.S))
    lhs = [set(re.findall(r'\b(\w+)\s*(?:\[[^;\n]*?\])?\s*=(?!=)', b[0])) & names for b in blocks]
    tokens = [set(re.findall(r'\b\w+\b', b[0])) & names for b in blocks]
    terminals = {S+'fwb_value', *('FpuPackerPlugin_logic_flagsWb_flags_'+f for f in FLAGS)}
    reached, todo = set(), [ROUND]
    evidence = {}
    while todo:
        name = todo.pop()
        if name in reached:
            continue
        reached.add(name)
        if name in terminals:
            continue
        consumers = [i for i, words in enumerate(tokens) if name in words and name not in lhs[i]]
        evidence[name] = [blocks[i][0] for i in consumers]
        # Every occurrence must be covered by an actual declaration or comb
        # statement. Thus no affected internal value can feed state, an
        # instance, output, or a side effect before the proved boundary.
        spans = [m.span() for m in decls if m[1] == name]
        spans += [b.span() for i, b in enumerate(blocks) if name in tokens[i]]
        for use in re.finditer(r'\b'+re.escape(name)+r'\b', source):
            assert any(a <= use.start() < b for a, b in spans), (name, source[use.start()-40:use.end()+60])
        for i in consumers:
            todo.extend(lhs[i])
    assert len(evidence[ROUND]) == 3
    assert {next(iter(lhs[i])) for i, b in enumerate(blocks) if b[0] in evidence[ROUND]} == {
        '_zz_'+P+'s1_EXP_RESULT', P+'s1_MAN_RESULT', 'when_FpuPackerPlugin_l241'}
    assert reached & terminals == {S+'fwb_value', *('FpuPackerPlugin_logic_flagsWb_flags_'+f for f in FLAGS[:3])}
    return evidence, sorted(reached)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--output', type=Path, default=ART/'packer-round-mode-v1/immediate')
    args = ap.parse_args()
    assert sha(REF) == SHA and sha(CHECK) == CHECK_SHA
    source = REF.read_text()
    assert source.count(OLD) == 1
    changed = source.replace(OLD, NEW, 1)
    assert changed.replace(NEW, OLD, 1) == source
    assert re.findall(r'^  always @\(posedge clk.*?^  end', source, re.M | re.S) == re.findall(r'^  always @\(posedge clk.*?^  end', changed, re.M | re.S)
    evidence, reached = closure(source)
    new_evidence, new_reached = closure(changed)
    assert evidence == new_evidence and reached == new_reached
    # NORMAL is a literal gate-identity, not a sampled arithmetic argument.
    # All finite-width signed arithmetic, mantissa bits, all8 RM encodings,
    # sign/quiet and incoming flags therefore have identical full equations.
    assert OLD.replace(f'(({MODE} == FloatMode_NORMAL) && _zz_{ROUND})', '_zz_'+ROUND) == NEW
    spec = importlib.util.spec_from_file_location('actual_cones', CHECK)
    checker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checker)
    constants = '\n'.join(re.findall(r'^  localparam (?:FloatMode|FpuRoundMode)_.*?;', source, re.M))
    assert len(constants.splitlines()) == 9
    def cone(text, name, inputs):
        result, width = checker.cone(text, name, list(inputs), OUTS)
        assert width == 62
        return result.replace('\noutput wire ', '\n'+constants+'\noutput wire ', 1)
    old_full = cone(source, 'reference', FULL)
    new_full = cone(changed, 'candidate', FULL)
    assert old_full.replace('module reference(', 'module candidate(', 1).replace(OLD, NEW, 1) == new_full
    old_cut = cone(source, 'special_reference', CUT)
    new_cut = cone(changed, 'special_candidate', CUT)
    assert old_cut.replace('module special_reference(', 'module special_candidate(', 1) == new_cut
    # These independently arbitrary arithmetic payloads each occur only in
    # the initial concatenation; all other data assignments are constants.
    # No other output/control reads either cut. This proves bit independence
    # and makes the complete walking-bit basis a universal word proof.
    init = f'    {S}fwb_value[31 : 0] = {{{{{P}s0_VALUE_sign,{P}s2_EXP[7 : 0]}},{P}s1_MAN_RESULT[22 : 0]}};'
    assert old_cut.count(init) == 1
    for name in (P+'s2_EXP', P+'s1_MAN_RESULT'):
        assert len(re.findall(r'\b'+re.escape(name)+r'\b', old_cut)) == 3
    aliases = {}
    for dest in [f'FpuPackerPlugin_wb_at_{i}_payload' for i in (4, 1, 6, 9)] + [S+'fpWriter_payload_data']:
        line = f'  assign {dest} = {S}fwb_value;'
        assert source.count(line) == changed.count(line) == 1
        aliases[dest] = line
    whitebox = '  assign WhiteboxerPlugin_logic_rfWrites_ports_9_payload_data = '+S+'fpWriter_payload_data;'
    assert source.count(whitebox) == changed.count(whitebox) == 1
    aliases['whitebox'] = whitebox
    # Low22 bits: the exact five valids, four flag destination enables, and
    # uopId are unmodified and have no rounding-dependent incoming driver.
    for name in OUTS[6:]:
        block = re.findall(r'^  assign '+name+r' = [^;]+;', source, re.M)
        assert len(block) == 1 and block[0] in changed
        assert not (set(re.findall(r'\b\w+\b', block[0])) & set(reached))
    all_inputs = dict(FULL, **CUT)
    tb = old_full + new_full + old_cut + new_cut + '\nmodule tb;\n'
    tb += '\n'.join(f'reg [{w-1}:0] {n};' for n,w in all_inputs.items())+'\n'
    tb += 'wire [61:0] original,result,special_old,special_new;\n'
    for module, inputs, output in [('reference',FULL,'original'),('candidate',FULL,'result'),('special_reference',CUT,'special_old'),('special_candidate',CUT,'special_new')]:
        tb += module+' dut_'+module+'('+','.join([*inputs,output])+');\n'
    flag_concat = '{'+','.join(P+'s0_FLAGS_'+f for f in FLAGS)+'}'
    valid_concat = '{'+','.join(P+'s0_GROUP_OH['+str(i)+']' for i in range(4))+'}'
    expected_meta = '{'+valid_concat+',((|'+P+'s0_GROUP_OH)&&'+P+'valid),('+P+'valid ? '+P+'s0_GROUP_OH : 4\'b0),'+P+'Decode_UOP_ID}'
    tb += '''integer special_cases=0,raw_cases=0,metadata_cases=0;
reg [32:0] basis;
reg [31:0] expected_data;
reg [61:0] expected;
task check_raw; begin
  #1;
  if(original !== result) $fatal(1,"raw mismatch old=%h new=%h",original,result);
  raw_cases++;
end endtask
initial begin
'''
    tb += '\n'.join('  '+n+"='0;" for n in all_inputs)+'\n'
    tb += f'''  // Exhaust all mode/sign/quiet/flags and independent arithmetic-control
  // cutpoints, then every basis/complement bit of the two data cutpoints.
  for(integer mode=0;mode<3;mode++) for(integer sq=0;sq<4;sq++)
  for(integer controls=0;controls<16;controls++) for(integer flags=0;flags<32;flags++)
  for(integer bitpat=0;bitpat<68;bitpat++) begin
    {MODE}=2'(mode);
    {{{P}s0_VALUE_sign,{P}s0_VALUE_quiet}}=2'(sq);
    {{{P}s2_EXP_OVERFLOW,{P}s2_EXP_UNDERFLOW,when_FpuPackerPlugin_l224,when_FpuPackerPlugin_l241}}=4'(controls);
    {flag_concat}=5'(flags);
    basis=bitpat==0 ? 33'b0 : bitpat==1 ? {{33{{1'b1}}}} :
      bitpat<35 ? (33'b1 << (bitpat-2)) : ~(33'b1 << (bitpat-35));
    {{{P}s2_EXP,{P}s1_MAN_RESULT}}=basis;
    {P}s0_GROUP_OH=4'(flags); {P}valid=flags[4]; {P}Decode_UOP_ID=16'hac53;
    case(mode)
      0: expected_data={{{P}s0_VALUE_sign,31'b0}};
      1: expected_data={{{P}s0_VALUE_sign,8'hff,23'b0}};
      2: expected_data={{1'b0,8'hff,{P}s0_VALUE_quiet,22'b0}};
    endcase
    expected={{expected_data,{flag_concat},{expected_meta}}};
    #1;
    if(special_old !== expected || special_new !== expected)
      $fatal(1,"special mismatch mode=%d controls=%x basis=%h old=%h new=%h expected=%h",mode,controls,basis,special_old,special_new,expected);
    special_cases++;
  end
  // Exhaust all valid/group combinations and every uop bit, independent of
  // mode. These exact equations are also source-guarded as rounding-free.
  for(integer meta=0;meta<32;meta++) for(integer uid=0;uid<18;uid++) begin
    {MODE}=2'(meta); {P}s0_GROUP_OH=4'(meta); {P}valid=meta[4];
    {P}Decode_UOP_ID=uid==0 ? 16'b0 : uid==1 ? 16'hffff : (16'b1 << (uid-2));
    #1;
    if(original[24:0] !== {expected_meta} || result[24:0] !== {expected_meta}) $fatal(1,"metadata mismatch");
    metadata_cases++;
  end
  // Supplement the structural NORMAL proof with every signed10-bit exponent
  // and RM encoding, including exponent wrap and mantissa carry boundaries.
  for(integer ex=0;ex<1024;ex++) for(integer rm=0;rm<8;rm++)
  for(integer mode=0;mode<4;mode++) for(integer m=0;m<16;m++) begin
    {MODE}=2'(mode); {P}s0_VALUE_exponent=10'(ex); {P}s0_ROUNDMODE=3'(rm);
    {S}mr=m[3] ? (25'h1ffffff-25'(m&7)) : 25'(m&7);
    {P}s0_EXP_SUBNORMAL=8'(ex^m); {{{P}s0_VALUE_sign,{P}s0_VALUE_quiet}}=2'(m);
    {flag_concat}=5'(m^ex); check_raw();
  end
  for(integer i=0;i<1000000;i++) begin
'''
    tb += '\n'.join('    '+n+'=$random;' for n in FULL)+'\n'
    tb += '''    check_raw();
  end
  $display("PASS special arithmetic-independence cases=%0d metadata=%0d full actual-cone boundary/random checks=%0d",special_cases,metadata_cases,raw_cases);
  $finish;
end
endmodule
'''
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    for name,text in [('reference.v',old_full),('candidate.v',new_full),('special-reference.v',old_cut),('special-candidate.v',new_cut),('tb.sv',tb)]:
        (out/name).write_text(text)
    (out/'dependency-audit.json').write_text(json.dumps(dict(reached=reached, internal_consumers=evidence,
        visible_payload_aliases=aliases, terminal_boundary=OUTS,
        exact_three_round_consumers=True, no_unparsed_internal_escape=True),indent=2)+'\n')
    run(['verilator','--binary','--timing','--top-module','tb','-Wno-fatal','--Mdir',str(out/'obj'),'-j','2',str(out/'tb.sv')],out/'proof-build.log')
    assert not re.search(r'%Warning-(IMPLICIT|UNDRIVEN|LATCH)',(out/'proof-build.log').read_text())
    run([str(out/'obj/Vtb')],out/'proof.log')
    target=out/'VexiiRiscv.v'; target.write_text(changed)
    patch=out/'packer-round-mode.patch'
    patch.write_text(''.join(difflib.unified_diff(source.splitlines(True),changed.splitlines(True),fromfile='a/VexiiRiscv.v',tofile='b/VexiiRiscv.v')))
    replay=out/'patch-replay'; replay.mkdir(); (replay/target.name).write_text(changed)
    for label,extra,expected_source in [('reverse',['-R'],source),('replay',[],changed)]:
        run(['patch','--batch','--fuzz=0',*extra,'-p1','-i',str(patch)],out/(label+'.log'),cwd=replay)
        assert (replay/target.name).read_text()==expected_source
    run(['verilator','--lint-only','--top-module','VexiiRiscv','-Wno-fatal',str(target)],out/'lint.log')
    assert not re.search(r'%Warning-(IMPLICIT|UNDRIVEN|LATCH|MULTIDRIVEN)',(out/'lint.log').read_text())
    shutil.copyfile(__file__,out/Path(__file__).name); shutil.copyfile(CHECK,out/CHECK.name)
    manifest=dict(reference=str(REF.relative_to(ROOT)),reference_sha256=SHA,candidate_sha256=sha(target),
        exact_scope='One ROUNDING_INCR assignment: remove NORMAL qualifier only.',normal_mode_equations_identical=True,
        normal_scope='Arbitrary10-bit signed exponent,8-bit EXP_SUBNORMAL,25-bit mantissa,all8 rounding codes,sign,quiet,all flags; no arithmetic simplification.',
        special_proof='All3 special modes ×4 sign/quiet ×16 independent arithmetic controls ×32 incoming flag patterns ×68 data basis/complement patterns; bit independence source-guarded.',
        special_cases=417792,metadata_cases=576,full_actual_cone_supplemental_cases=1524288,
        packed32_data_all5_flags_all_valids_destination_uop_proved=True,three_consumer_closure_no_escape=True,
        new_registers=0,new_cycles=0,new_keep_boundaries=0,all_sequential_source_unchanged=True,
        exact_reverse_replay=True,full_netlist_lint=True,
        limitation='Boolean hardware equivalence; unknown/X mode simulation values are not claimed equivalent. Invalid startup payload has unchanged valid gating.',
        whole_cpu_qualification='Pending one final composed pointer+packer14-case run; no redundant intermediate suite.')
    (out/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    (out/'recipe.json').write_text(json.dumps(dict(script_sha256=sha(out/Path(__file__).name),checker_sha256=CHECK_SHA,
        reference_sha256=SHA,candidate_sha256=sha(target),files={p.name:sha(p) for p in out.iterdir() if p.is_file() and p.name not in ('recipe.json','manifest.json')}),indent=2)+'\n')
    (out/'README.txt').write_text('''Packer rounding mode gate removal
=================================

Only ROUNDING_INCR loses its NORMAL-mode qualifier. Raw rounding, all finite
width arithmetic, clamping, flags, valid/metadata logic and every FF stay exact.
The dependency audit proves exactly three consumers, all terminating at the
packed word or generated NX/UF/OF flags. All five visible payload aliases,
five final flags, valid/group destinations and uopId are covered.

NORMAL has literally identical driver equations after the mode predicate is
one. This universally covers every exponent/mantissa/RM encoding, reserved RM
codes and signed wrapping; simulation sampling is not the basis of that proof.
For ZERO/INF/NAN the extracted final cone is independent of arbitrary EXP,
MAN_RESULT, overflow/underflow and rounding-control outcomes.417792 cases cover
all independent controls/incoming flags/sign/quiet and a complete bit basis.
The data cutpoints appear only in the initial word concatenation, followed by
constant bit overwrites, establishing the bitwise lift to all word values.
Special data preserves this core's exact existing NaN/sign/quiet behavior.
576 metadata cases and1524288 full-cone arithmetic boundary/random checks pass.

No register, cycle or KEEP is added. Four-state unknown mode equivalence is not
claimed; actual valid hardware modes are all four Boolean encodings, and all
startup valid gating is unchanged. Reverse/replay/lint and exact source guards
pass. Physical improvement requires fit. Final combined CPU tests are recorded
separately after composition with the disjoint pointer-D edit.

Reproduce with generator/remove_packer_mode_gate.py --output <new-directory>.
''')
    print((out/'proof.log').read_text(),end=''); print(json.dumps(manifest,indent=2))


if __name__ == '__main__':
    main()
