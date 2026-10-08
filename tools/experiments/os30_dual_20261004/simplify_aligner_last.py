#!/usr/bin/env python3
"""Classify selected-instruction buffer ownership before its priority mux."""
from pathlib import Path
import difflib
import hashlib
import json
import re
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[3]
ART = ROOT / 'docs/measurements/os30-dual-20261004'
BASE = ART / 'aligner-trap-direct-v1/immediate/VexiiRiscv.v'
OUT = ART / 'aligner-last-direct-v1/immediate'
SHA = 'ec69d8b6b09f60fe60f03227bb1bad4c19ea841a28e874d8025b6970b1d639c3'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def statement(source, name):
    found = re.findall(r'^  assign ' + re.escape(name) + r' =\s*[^;]+;', source, re.M)
    assert len(found) == 1, name
    return found[0]


def run(command, logfile, **kwargs):
    with logfile.open('w') as log:
        subprocess.run(command, check=True, stdout=log, stderr=subprocess.STDOUT, **kwargs)


def main():
    assert digest(BASE) == SHA
    source = BASE.read_text()
    changes = []
    for lane, boundary, top in [(0, 3, 7), (1, 2, 6)]:
        name = f'AlignerPlugin_logic_buffer_readers_{lane}_lastFromBuffer'
        usable = f'AlignerPlugin_logic_extractors_{lane}_usableMask'
        usage = f'AlignerPlugin_logic_extractors_{lane}_usageMask'
        old = statement(source, name)
        expected = f"  assign {name} = ({{{usage}[7],{{{usage}[6],{{{usage}[5],{usage}[4]}}}}}} == 4'b0000);"
        assert old == expected, (old, expected)
        new = f'''  // The first usable instruction stays in the buffer unless it starts
  // beyond it or its boundary slice requires one slice from fetch.
  assign {name} =
      !(!(|{usable}[{boundary-1}:0]) &&
        (({usable}[{boundary}] && AlignerPlugin_logic_scanners_3_checker_1_required) ||
         (!{usable}[{boundary}] && (|{usable}[{top}:{boundary+1}]))));'''
        changes.append((old, new))
    candidate = source
    for old, new in changes:
        assert candidate.count(old) == 1
        candidate = candidate.replace(old, new, 1)
    reversed_source = candidate
    for old, new in reversed(changes):
        reversed_source = reversed_source.replace(new, old, 1)
    assert reversed_source == source

    # Extract the actual priority selector, exact per-position usage masks,
    # original first/last classification and already-direct trap expressions. The cutpoints
    # are arbitrary usable masks, all eight required-second-slice bits, and
    # independent buffer/fetch fault bits. No reachability assumption is used.
    blocks = []
    for index in range(8):
        prefix = f'AlignerPlugin_logic_scanners_{index}'
        found = re.findall(r'  always @\(\*\) begin\n    ' + prefix + r'_usageMask = 8\'h0;.*?\n  end', source, re.S)
        assert len(found) == 1
        block = found[0]
        expected = [f"    {prefix}_usageMask = 8'h0;",
                    f'    {prefix}_usageMask[{index}] = {prefix}_checker_0_required;']
        if index < 7:
            expected.append(f'    {prefix}_usageMask[{index+1}] = {prefix}_checker_1_required;')
        assert block == '  always @(*) begin\n' + '\n'.join(expected) + '\n  end'
        blocks.append(block)
        required0 = statement(source, prefix + '_checker_0_required')
        assert required0 == f"  assign {prefix}_checker_0_required = 1'b1;"
        blocks.append(required0)
    for lane, width in [(0, 8), (1, 7)]:
        prefix = f'AlignerPlugin_logic_extractors_{lane}'
        for line in source.splitlines():
            if re.match(r'  assign (?:_zz_)?' + prefix + r'_usableMask_(?:bools_\d+|range_\d+_to_\d+) =', line):
                blocks.append(line)
        selector = re.findall(r'  always @\(\*\) begin\n    _zz_' + prefix + r'_slicesOh\[0\] =.*?\n  end', source, re.S)
        assert len(selector) == 1
        assert len(re.findall(r'_slicesOh\[\d+\] =', selector[0])) == width
        blocks.append(selector[0])
        blocks.append(statement(source, prefix + '_slicesOh'))
        for bit in range(width):
            alias = '_zz_' + prefix + '_redo' + (f'_{bit}' if bit else '')
            actual = statement(source, alias)
            assert actual == f'  assign {alias} = {prefix}_slicesOh[{bit}];'
            blocks.append(actual)
        blocks.append(statement(source, prefix + '_usageMask'))
        blocks.append(statement(source, f'AlignerPlugin_logic_buffer_readers_{lane}_firstFromBuffer'))
        blocks.append(changes[lane][0])
        blocks.append(statement(source, prefix + '_ctx_trap'))
    reference_logic = '\n'.join(blocks)
    assert len(blocks) == 69
    candidate_logic = reference_logic
    for old, new in changes:
        candidate_logic = candidate_logic.replace(old, new, 1)
    names = set(re.findall(r'\b(?:_zz_AlignerPlugin\w*|AlignerPlugin\w*|fetch_logic_ctrls_3_down_TRAP)\b', reference_logic))
    declarations = []
    for name in sorted(names):
        decls = re.findall(r'^  (?:wire|reg)\s+(?:\[[\d :]+\]\s+)?' + re.escape(name) + r';$', source, re.M)
        assert len(decls) == 1, name
        declarations.append(decls[0])
    cutpoints = '''  assign AlignerPlugin_logic_extractors_0_usableMask = usable0;
  assign AlignerPlugin_logic_extractors_1_usableMask = usable1;
  assign AlignerPlugin_logic_buffer_trap = buffer_trap;
  assign fetch_logic_ctrls_3_down_TRAP = fetch_trap;
'''
    for index in range(8):
        name = f'AlignerPlugin_logic_scanners_{index}_checker_1_required'
        if name in names:
            cutpoints += f'  assign {name} = requireds[{index}];\n'
    design = ''
    for label, logic in [('reference', reference_logic), ('candidate', candidate_logic)]:
        design += f'''module {label}(input [7:0] usable0,input [6:0] usable1,
input [7:0] requireds,input buffer_trap,fetch_trap,output [1:0] lasts,output [1:0] traps);
''' + '\n'.join(declarations) + '\n' + cutpoints + logic + '''
  assign lasts = {AlignerPlugin_logic_buffer_readers_1_lastFromBuffer,AlignerPlugin_logic_buffer_readers_0_lastFromBuffer};
  assign traps = {AlignerPlugin_logic_extractors_1_ctx_trap,AlignerPlugin_logic_extractors_0_ctx_trap};
endmodule
'''
    design += '''module last_proof(input [7:0] usable0,input [6:0] usable1,
input [7:0] requireds,input buffer_trap,fetch_trap,output [1:0] old_lasts,new_lasts,old_traps,new_traps);
reference a(usable0,usable1,requireds,buffer_trap,fetch_trap,old_lasts,old_traps);
candidate b(usable0,usable1,requireds,buffer_trap,fetch_trap,new_lasts,new_traps);
endmodule
'''
    cpp = '''#include "Vlast_proof.h"
#include <cstdio>
int main() {
 Vlast_proof dut;
 unsigned cases=0;
 for(unsigned lane=0;lane<2;++lane) for(unsigned usable=0;usable<(lane?128u:256u);++usable)
  for(unsigned required=0;required<256;++required) for(unsigned faults=0;faults<4;++faults) {
   dut.usable0=lane?0:usable;dut.usable1=lane?usable:0;
   dut.requireds=required;dut.buffer_trap=faults&1;dut.fetch_trap=(faults>>1)&1;dut.eval();
   unsigned chosen=8;
   for(unsigned i=0;i<(lane?7u:8u);++i) if(usable&(1u<<i)){chosen=i+lane;break;}
   bool expected_last=chosen==8 || (chosen<4 && !(chosen==3 && (required&8)));
   bool expected=chosen<8 && ((chosen<4 && (faults&1)) ||
     ((chosen>=4 || (chosen==3 && (required&8))) && (faults&2)));
   if(dut.old_lasts!=dut.new_lasts || ((dut.new_lasts>>lane)&1)!=expected_last ||
      dut.old_traps!=dut.new_traps || ((dut.new_traps>>lane)&1)!=expected) {
    std::printf("FAIL lane=%u usable=%x required=%x faults=%x old=%x new=%x oracle=%x\\n",
     lane,usable,required,faults,dut.old_traps,dut.new_traps,expected);return 1;
   }
   ++cases;
  }
 std::printf("PASS all%u independent-lane cases: actual priority/usage-mask/lastFromBuffer cones and direct classification match the first-slice oracle; existing trap outputs unchanged\\n",cases);
}
'''
    OUT.mkdir(parents=True, exist_ok=False)
    (OUT / 'last_proof.v').write_text(design)
    (OUT / 'last_proof.cpp').write_text(cpp)
    (OUT / 'extracted-blocks.json').write_text(json.dumps(blocks, indent=2)+'\n')
    run(['verilator', '--cc', '--exe', '--build', '-j', '2', '-Wno-fatal',
         '--top-module', 'last_proof', '--Mdir', str(OUT / 'obj'), '-CFLAGS', '-std=c++17 -O2',
         str(OUT / 'last_proof.v'), str(OUT / 'last_proof.cpp')], OUT / 'proof-build.log')
    assert not re.search(r'%Warning-(IMPLICIT|UNDRIVEN|LATCH)', (OUT / 'proof-build.log').read_text())
    run([str(OUT / 'obj/Vlast_proof')], OUT / 'proof.log')
    (OUT / 'VexiiRiscv.v').write_text(candidate)
    (OUT / 'last-direct.patch').write_text(''.join(difflib.unified_diff(
        source.splitlines(True), candidate.splitlines(True), fromfile='a/VexiiRiscv.v', tofile='b/VexiiRiscv.v')))
    replay = OUT / 'patch-replay'
    replay.mkdir()
    (replay / 'VexiiRiscv.v').write_text(candidate)
    for label, extra in [('reverse', ['-R']), ('replay', [])]:
        run(['patch', '--batch', '--fuzz=0', *extra, '-p1', '-i', str(OUT / 'last-direct.patch')], OUT / (label+'.log'), cwd=replay)
        assert (replay / 'VexiiRiscv.v').read_text() == (source if label == 'reverse' else candidate)
    run(['verilator', '--lint-only', '--top-module', 'VexiiRiscv', '-Wno-fatal', str(OUT / 'VexiiRiscv.v')], OUT / 'lint.log')
    assert not re.search(r'%Warning-(IMPLICIT|UNDRIVEN|LATCH|MULTIDRIVEN)', (OUT / 'lint.log').read_text())
    shutil.copy2(__file__, OUT / 'simplify_aligner_last.py')
    manifest = dict(reference=str(BASE.relative_to(ROOT)), reference_sha256=SHA,
                    candidate_sha256=digest(OUT / 'VexiiRiscv.v'),
                    exact_scope='Only two aligner lastFromBuffer assignments; all usage masks, trap equations, prediction payload expressions and sequential source remain byte-identical.',
                    independent_lane_cases=393216, lane0_masks=256, lane1_masks=128,
                    all_required_vectors=256, all_buffer_fetch_trap_pairs=4,
                    no_selection_and_arbitrary_usable_patterns=True,
                    sequential_or_cross_lane_invariants_assumed=False,
                    proof_reduction='Each actual lane cone depends only on its own usable mask, eight required bits, and shared two fault bits. Exhausting them separately covers their Cartesian product.',
                    actual_extracted_source_blocks=len(blocks), independent_first_slice_oracle=True,
                    exact_reverse_replay=True, full_netlist_lint=True,
                    extra_registers=0, extra_cycles=0,
                    whole_cpu_qualification='Pending independent 14-case regression.', physical_benefit='Requires full-core fit.')
    (OUT / 'manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')
    print((OUT / 'proof.log').read_text(), end='')
    print(json.dumps(manifest, indent=2))


if __name__ == '__main__':
    main()
