#!/usr/bin/env python3
"""Compute the second instruction's usable positions without the first selector."""
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
BASE = ART / 'all-operand-groups-v1/immediate/VexiiRiscv.v'
BASE_SHA = 'ffed6f6e299b8e7d3275b781d55d7094ae4d6c086b9f57d6553f794cd545082c'
CHECKER = ROOT / 'tools/check_vexii_os20_timing.py'
CHECKER_SHA = '80a0c0e9cee4d3f7532c44d4aca198becc46c2400ebc8bfba811bd61e10f6188'
DEST = 'AlignerPlugin_logic_extractors_1_usableMask'
V = 'AlignerPlugin_logic_extractors_0_usableMask'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def statement(source, name):
    found = re.findall(r'^  assign ' + re.escape(name) + r' =\s*[^;]+;', source, re.M)
    assert len(found) == 1, name
    return found[0]


def run(command, logfile, **kwargs):
    with logfile.open('w') as log:
        subprocess.run(command, check=True, stdout=log, stderr=subprocess.STDOUT, **kwargs)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--output', type=Path, default=ART / 'second-usable-direct-v1/immediate')
    args = ap.parse_args()
    assert sha(BASE) == BASE_SHA
    assert sha(CHECKER) == CHECKER_SHA
    source = BASE.read_text()
    assert statement(source, 'AlignerPlugin_logic_usedMask_0') == "  assign AlignerPlugin_logic_usedMask_0 = 8'h0;"
    contracts = {}
    for i in range(8):
        prefix = f'AlignerPlugin_logic_scanners_{i}'
        block = re.findall(r'  always @\(\*\) begin\n    ' + prefix + r"_usageMask = 8'h0;.*?\n  end", source, re.S)
        assert len(block) == 1
        expected = [f"    {prefix}_usageMask = 8'h0;", f'    {prefix}_usageMask[{i}] = {prefix}_checker_0_required;']
        if i < 7:
            expected.append(f'    {prefix}_usageMask[{i+1}] = {prefix}_checker_1_required;')
        assert block[0] == '  always @(*) begin\n' + '\n'.join(expected) + '\n  end'
        assert statement(source, prefix + '_checker_0_required') == f"  assign {prefix}_checker_0_required = 1'b1;"
        contracts[prefix] = block[0]
    old = statement(source, DEST)
    terms = []
    for j in range(7, 0, -1):
        adjacent = f'({V}[{j-1}] && !AlignerPlugin_logic_scanners_{j-1}_checker_1_required)'
        earlier = f'((|{V}[{j-2}:0]) || {adjacent})' if j > 1 else adjacent
        terms.append(f'({V}[{j}] && {earlier})')
    new = '  assign ' + DEST + ' = {\n      ' + ',\n      '.join(terms) + '\n  };'
    assert source.count(old) == 1
    candidate = source.replace(old, new, 1)
    assert candidate.replace(new, old, 1) == source
    sequential = lambda s: re.findall(r'^  always @\(posedge clk.*?^  end', s, re.M | re.S)
    assert sequential(source) == sequential(candidate)

    # Cut both old and new cones at the same actual scanner leaves. In
    # particular V is NOT an independent input: the exact old zero-used-mask
    # mapping from scanner valid bits is included in both proofs.
    spec = importlib.util.spec_from_file_location('actual_cones', CHECKER)
    checker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checker)
    inputs = [f'AlignerPlugin_logic_scanners_{i}_valid' for i in range(8)]
    inputs += [f'AlignerPlugin_logic_scanners_{i}_checker_1_required' for i in range(8)]
    outputs = [V, DEST, 'AlignerPlugin_logic_extractors_1_slicesOh',
               'AlignerPlugin_logic_usedMask_2', 'AlignerPlugin_logic_buffer_readers_1_lastFromBuffer']
    reference, width = checker.cone(source, 'reference', inputs, outputs)
    proposed, new_width = checker.cone(candidate, 'candidate', inputs, outputs)
    assert width == new_width == 31
    ports = [f'valids[{i}]' for i in range(8)] + [f'requireds[{i}]' for i in range(8)]
    wrapper = '''module second_usable_proof(input [7:0] valids, requireds,
output [30:0] old_observed, new_observed);
reference a(''' + ','.join(ports + ['old_observed']) + ''');
candidate b(''' + ','.join(ports + ['new_observed']) + ''');
endmodule
'''
    cpp = '''#include "Vsecond_usable_proof.h"
#include <cstdio>
int main() {
 Vsecond_usable_proof d;
 unsigned cases=0,empty=0,sparse=0;
 for(unsigned v=0;v<256;++v) for(unsigned r=0;r<256;++r) {
  d.valids=v;d.requireds=r;d.eval();
  unsigned first=8,second=8,used=0;
  for(unsigned i=0;i<8;++i) if(v&(1u<<i)){first=i;break;}
  if(first<8) used=((1u<<first)|((r&(1u<<first))?(1u<<(first+1)):0))&255;
  unsigned remaining=v&~used, w=remaining>>1, selected=0;
  for(unsigned i=1;i<8;++i) if(remaining&(1u<<i)){second=i;selected=1u<<(i-1);break;}
  if(second<8) used|=((1u<<second)|((r&(1u<<second))?(1u<<(second+1)):0))&255;
  unsigned last=second==8 || (second<4 && !(second==3 && (r&8)));
  unsigned oracle=(v<<23)|(w<<16)|(selected<<9)|(used<<1)|last;
  if(d.old_observed!=d.new_observed || d.new_observed!=oracle) {
   std::printf("FAIL V=%02x R=%02x old=%08x new=%08x oracle=%08x\\n",v,r,d.old_observed,d.new_observed,oracle);return 1;
  }
  ++cases;empty+=w==0;
  unsigned compact=v;while(compact && !(compact&1))compact>>=1;
  sparse+=compact && ((compact&(compact+1))!=0);
 }
 std::printf("PASS %u actual-cone cases; all 31 output bits and independent priority/remove oracle; empty-second=%u sparse-mask=%u\\n",cases,empty,sparse);
}
'''
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    for name, content in [('reference.v', reference), ('candidate.v', proposed),
                          ('second_usable_proof.v', reference + proposed + wrapper),
                          ('second_usable_proof.cpp', cpp), ('VexiiRiscv.v', candidate)]:
        (out / name).write_text(content)
    (out / 'source-contracts.json').write_text(json.dumps(contracts, indent=2) + '\n')
    run(['verilator', '--cc', '--exe', '--build', '-j', '2', '-Wno-fatal',
         '--top-module', 'second_usable_proof', '--Mdir', str(out / 'obj'),
         '-CFLAGS', '-std=c++17 -O2', str(out / 'second_usable_proof.v'),
         str(out / 'second_usable_proof.cpp')], out / 'proof-build.log')
    assert not re.search(r'%Warning-(IMPLICIT|UNDRIVEN|LATCH|MULTIDRIVEN)', (out / 'proof-build.log').read_text())
    run([str(out / 'obj/Vsecond_usable_proof')], out / 'proof.log')
    patch = out / 'second-usable-direct.patch'
    patch.write_text(''.join(difflib.unified_diff(source.splitlines(True), candidate.splitlines(True),
        fromfile='a/VexiiRiscv.v', tofile='b/VexiiRiscv.v')))
    replay = out / 'patch-replay'
    replay.mkdir()
    (replay / 'VexiiRiscv.v').write_text(candidate)
    for label, extra, expected in [('reverse', ['-R'], source), ('replay', [], candidate)]:
        run(['patch', '--batch', '--fuzz=0', *extra, '-p1', '-i', str(patch)], out / (label + '.log'), cwd=replay)
        assert (replay / 'VexiiRiscv.v').read_text() == expected
    run(['verilator', '--lint-only', '--top-module', 'VexiiRiscv', '-Wno-fatal', str(out / 'VexiiRiscv.v')], out / 'lint.log')
    assert not re.search(r'%Warning-(IMPLICIT|UNDRIVEN|LATCH|MULTIDRIVEN)', (out / 'lint.log').read_text())
    shutil.copyfile(__file__, out / Path(__file__).name)
    shutil.copyfile(CHECKER, out / CHECKER.name)
    manifest = dict(reference=str(BASE.relative_to(ROOT)), reference_sha256=BASE_SHA,
        candidate_sha256=sha(out / 'VexiiRiscv.v'), exact_scope='Only extractor1 usableMask vector assignment; all other RTL bytes unchanged.',
        cutpoints=inputs, observed_outputs=outputs, observed_bits=31, exhaustive_cases=65536,
        arbitrary_valid_and_required_patterns=True, no_contiguity_reachability_or_valid_control_assumption=True,
        original_shared_scanner_to_first_usable_cone_included=True, independent_first_and_second_priority_oracle=True,
        no_selection_last_true_preserved=True, all_sequential_source_unchanged=True,
        current_lastFromBuffer_and_trap_predicates_unchanged=True, extra_registers=0, extra_cycles=0,
        exact_reverse_replay=True, full_netlist_lint=True,
        whole_cpu_qualification='Pending independent review and retained14 fixtures.',
        physical_qualification='Requires complete-core fit; no timing or area gain assumed.')
    (out / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    recipe = dict(script_sha256=sha(out / Path(__file__).name), reference_sha256=BASE_SHA,
        cone_extractor_sha256=CHECKER_SHA, candidate_sha256=sha(out / 'VexiiRiscv.v'),
        evidence={name:sha(out / name) for name in ['reference.v','candidate.v','second_usable_proof.v',
            'second_usable_proof.cpp','proof.log','source-contracts.json','second-usable-direct.patch']})
    (out / 'recipe.json').write_text(json.dumps(recipe, indent=2) + '\n')
    (out / 'README.txt').write_text('''Direct second-instruction usable positions
==========================================

The previous second-position mask depended on selecting the first instruction,
selecting its one/two-slice usage mask, then excluding that mask. For position j,
the same answer is V[j] && (any earlier V below j-1 || (V[j-1] && !R[j-1])).
The second extractor never selects position0. This removes the first selector
and usage mux from the second-mask dependency, with no new state or cycle.

Only the seven-bit usableMask1 assignment changes. Its existing priority
selection, current lastFromBuffer and trap equations, masks, data paths and
all sequential source remain exact. No invalid-input or contiguous-mask
assumption is made; consumption is not qualified by redo, halt or output valid.

The proof extracts both actual cones from all eight scanner valid leaves and
all eight required-length leaves, including the original zero-usedMask0 to
usableMask0 mapping. All65536 combinations compare31 output bits: usable0,
usable1, selector1, usedMask2 and lastFromBuffer1. An independent sequential
first/second priority-removal oracle checks each result, including holes,
empty selection, adjacent32-bit candidates and truncated final-slice usage.
Equality of the complete substituted mask preserves every downstream consumer.

The focused proof is binary combinational equivalence. Full CPU regression
and physical timing are separate qualifications. Exact patch reversal/replay
and full-netlist lint pass. Reproduce with generator/factor_second_usable.py
--output <fresh-directory>; source/checker hashes are guarded in the script.
''')
    print((out / 'proof.log').read_text(), end='')
    print(json.dumps(manifest, indent=2))


if __name__ == '__main__':
    main()
