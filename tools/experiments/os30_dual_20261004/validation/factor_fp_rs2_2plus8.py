#!/usr/bin/env python3
"""Factor only the floating-point RS2 data mux into two local-priority groups."""
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
REFERENCE = ART / 'pointer-packer-combined-v1/immediate/VexiiRiscv.v'
REFERENCE_SHA = '5209f490af47c1730163b5c1b49ed475aecc116a38bdbed6c295d883e7d8bcaa'
CHECK = ROOT / 'tools/check_vexii_os20_timing.py'
CHECK_SHA = '80a0c0e9cee4d3f7532c44d4aca198becc46c2400ebc8bfba811bd61e10f6188'
EN = 'execute_lane0_bypasser_float_RS2_bypassEnables'
SEL = 'execute_lane0_bypasser_float_RS2_sel'
DEST = 'execute_ctrl1_down_float_RS2_lane0'
EARLY = 'fp_rs2_priority_early_data'
LATE = 'fp_rs2_priority_late_data'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def balanced(terms):
    if len(terms) == 1:
        return terms[0]
    split = len(terms) // 2
    return '(' + balanced(terms[:split]) + ' | ' + balanced(terms[split:]) + ')'


def run(command, log, **kwargs):
    with log.open('w') as handle:
        subprocess.run(command, check=True, stdout=handle, stderr=subprocess.STDOUT, **kwargs)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ART / 'fp-rs2-priority-2plus8-v1/immediate')
    args = parser.parse_args()
    assert sha(REFERENCE) == REFERENCE_SHA and sha(CHECK) == CHECK_SHA
    source = REFERENCE.read_text()
    data = [f'execute_ctrl{stage}_down_lane0_float_WriteBackPlugin_logic_DATA_lane0' for stage in range(4, 13)]
    data.append('execute_lane0_bypasser_float_RS2_port_data')
    old_terms = []
    for index in range(8):
        alias = '_zz_' + DEST + ('_' + str(index * 2) if index else '')
        zero = '_zz_' + DEST + '_' + str(index * 2 + 1)
        assert source.count(f'  assign {alias} = {SEL}[{index}];') == 1
        assert source.count(f"  assign {zero} = 32'h0;") == 1
        old_terms.append(f'({alias} ? {data[index]} : {zero})')
    old_terms.extend(f"({SEL}[{index}] ? {data[index]} : 32'h0)" for index in (8, 9))
    old = f'  assign {DEST} = (' + balanced(old_terms[:8]) + ' | ' + balanced(old_terms[8:]) + ');'
    assert source.count(old) == 1
    # Every live match/valid/write-enable gate remains unchanged. These guards
    # also document the exact producer priority and existing RF fallback.
    for index, stage in enumerate(range(4, 13)):
        statement = f'    {EN}[{index}] = (execute_ctrl{stage}_up_LANE_SEL_lane0 && execute_ctrl{stage}_up_RD_ENABLE_lane0) && fp_match_RS2_{stage};'
        assert source.count(statement) == 1
    assert source.count(f"    {EN}[9] = 1'b1;") == 1
    early_terms = [f"({SEL}[{i}] ? {data[i]} : 32'h0)" for i in range(2)]
    late_terms = []
    for i in range(2, 10):
        enable = f'{EN}[{i}]' if i == 2 else f'({EN}[{i}] && !(|{EN}[{i-1}:2]))'
        late_terms.append(f"({enable} ? {data[i]} : 32'h0)")
    declarations = [f'  (* keep = "true" *) wire [31:0] {name};' for name in (EARLY, LATE)]
    new = '\n'.join(declarations) + '\n' + \
        f'  assign {EARLY} = {balanced(early_terms)};\n' + \
        f'  assign {LATE} = {balanced(late_terms)};\n' + \
        f"  assign {DEST} = ({EARLY} | ((!(|{EN}[1:0])) ? {LATE} : 32'h0));"
    assert EARLY not in source and LATE not in source
    candidate = source.replace(old, new, 1)
    assert candidate.replace(new, old, 1) == source
    # The exact expressions above select/mask entire 32-bit words and OR them.
    # No data arithmetic, shift, concatenation, or bit index is introduced;
    # output bit b depends only on bit b of the ten data words and controls.
    spec = importlib.util.spec_from_file_location('actual_cones', CHECK)
    checker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checker)
    normalized = candidate
    for name, decl in zip((EARLY, LATE), declarations):
        normalized = normalized.replace(decl, f'  wire [31:0] {name};', 1)
    inputs = [EN, *data]
    outputs = [SEL, DEST]
    reference_cone, width = checker.cone(source, 'reference', inputs, outputs)
    candidate_cone, width_new = checker.cone(normalized, 'candidate', inputs, outputs)
    assert width == width_new == 42
    wrapper = '''module fp_rs2_proof(input [9:0] enables,input [319:0] data,
output [41:0] original,result);
'''
    for name, observed in [('reference', 'original'), ('candidate', 'result')]:
        wrapper += name + ' ' + ('a' if name == 'reference' else 'b') + '(enables,'
        wrapper += ','.join(f'data[{i*32+31}:{i*32}]' for i in range(10)) + ',' + observed + ');\n'
    wrapper += 'endmodule\n'
    cpp = r'''#include "Vfp_rs2_proof.h"
#include <cstdint>
#include <cstdio>
int main() {
 Vfp_rs2_proof dut;
 uint32_t words[1024][10]{};
 // Each physical bit sees all1024 independent source-bit patterns over the
 // pattern loop. Different bit positions use cyclic permutations; the source
 // guards establish bit independence, lifting this to arbitrary32-bit words.
 for(unsigned p=0;p<1024;++p) for(unsigned i=0;i<10;++i)
  for(unsigned b=0;b<32;++b) words[p][i] |= ((p>>((i+b)%10))&1u)<<b;
 unsigned cases=0;
 for(unsigned e=0;e<1024;++e) {
  unsigned selected=10;
  for(unsigned i=0;i<10;++i) if(e&(1u<<i)){selected=i;break;}
  for(unsigned p=0;p<1024;++p) {
   dut.enables=e;
   for(unsigned i=0;i<10;++i)dut.data[i]=words[p][i];
   dut.eval();
   uint64_t expected=selected==10?0:(uint64_t(1u<<selected)<<32)|words[p][selected];
   if(dut.original!=dut.result || dut.result!=expected) {
    std::printf("FAIL e=%x p=%x old=%llx new=%llx expected=%llx\n",e,p,
     (unsigned long long)dut.original,(unsigned long long)dut.result,(unsigned long long)expected);
    return 1;
   }
   ++cases;
  }
 }
 std::printf("PASS all%u actual-cone enable/data-pattern cases; every output bit independently exhausted, zero-selection and priority oracle included\n",cases);
}
'''
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    (out / 'reference.v').write_text(reference_cone)
    (out / 'candidate.v').write_text(candidate_cone)
    (out / 'proof.v').write_text(reference_cone + candidate_cone + wrapper)
    (out / 'proof.cpp').write_text(cpp)
    run(['verilator', '--cc', '--exe', '--build', '-j', '2', '-Wno-fatal',
         '--top-module', 'fp_rs2_proof', '--Mdir', str(out / 'obj'), '-CFLAGS', '-std=c++17 -O2',
         str(out / 'proof.v'), str(out / 'proof.cpp')], out / 'proof-build.log')
    assert not re.search(r'%Warning-(IMPLICIT|UNDRIVEN|LATCH)', (out / 'proof-build.log').read_text())
    run([str(out / 'obj/Vfp_rs2_proof')], out / 'proof.log')
    target = out / 'VexiiRiscv.v'
    target.write_text(candidate)
    patch = out / 'fp-rs2-priority-2plus8.patch'
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
    manifest = dict(reference=str(REFERENCE.relative_to(ROOT)), reference_sha256=REFERENCE_SHA,
        candidate_sha256=sha(target), exact_scope='Only RS2 data output expression plus two kept32-bit combinational group words.',
        groups=[[0,1],[2,3,4,5,6,7,8,9]], enables_and_original_selectors_unchanged=True,
        live_lane_and_rd_enable_qualification_unchanged=True, other_operands_unchanged=True,
        all_sequential_source_unchanged=True, new_register_bits=0, new_cycles=0,
        kept_combinational_words=2, bitwise_data_sources=10, enable_patterns=1024,
        independent_data_bit_patterns=1024, actual_cone_cases=1048576,
        output_bit_positions=32, bit_comparisons=33554432, arbitrary_zero_selection_preserved=True,
        independent_lowest_enabled_oracle=True, exact_source_guards_and_reverse_replay=True,
        full_netlist_lint=True, checker_sha256=CHECK_SHA,
        whole_cpu_validation='Pending selected composition with independent integer SRC1 grouping; no standalone CPU suite.',
        physical_limitation='Two kept word boundaries may affect area/packing; only complete-core timing qualifies benefit.')
    (out / 'manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')
    (out / 'recipe.json').write_text(json.dumps(dict(
        script_sha256=sha(out / Path(__file__).name), reference_sha256=REFERENCE_SHA,
        candidate_sha256=sha(target), patch_sha256=sha(patch),
        reference_cone_sha256=sha(out / 'reference.v'), candidate_cone_sha256=sha(out / 'candidate.v'),
        proof_sha256=sha(out / 'proof.v'), harness_sha256=sha(out / 'proof.cpp'), checker_sha256=CHECK_SHA), indent=2)+'\n')
    print((out / 'proof.log').read_text(), end='')
    print(json.dumps(manifest, indent=2))


if __name__ == '__main__':
    main()
