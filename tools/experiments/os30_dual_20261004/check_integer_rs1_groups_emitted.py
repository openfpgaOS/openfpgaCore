#!/usr/bin/env python3
"""Independently check exact pair-based integer RS1 local-priority emission."""
from pathlib import Path
import hashlib
import importlib.util
import json
import re
import subprocess

ROOT = Path(__file__).resolve().parents[3]
ART = ROOT / 'docs/measurements/os30-dual-20261004'
CAND = ART / 'integer-rs1-priority-groups-v2/immediate'
OUT = ART / 'integer-rs1-priority-emitted-audit-v2'
EN = 'execute_lane0_bypasser_integer_RS1_bypassEnables'
SEL = 'execute_lane0_bypasser_integer_RS1_sel'
DEST = '_zz_execute_ctrl1_down_integer_RS1_lane0_1'
EARLY = 'integer_rs1_priority_early_data'
LATE = 'integer_rs1_priority_late_data'
KEEP = '  (* keep , syn_keep *) reg        [31:0]   ' + DEST + ' /* synthesis syn_keep = 1 */ ;'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def balanced(terms):
    if len(terms) == 1:
        return terms[0]
    mid = len(terms)//2
    return '(' + balanced(terms[:mid]) + ' | ' + balanced(terms[mid:]) + ')'


def main():
    base = ART / 'pointer-packer-combined-v1/immediate/VexiiRiscv.v'
    target = CAND / 'VexiiRiscv.v'
    assert sha(base) == '5209f490af47c1730163b5c1b49ed475aecc116a38bdbed6c295d883e7d8bcaa'
    assert sha(target) == 'b182970b0f7a7e24dbafc5a6f7dc4c4008b14b62daf215dcb8b0e67578481dbf'
    before, after = base.read_text(), target.read_text()
    words = [f'execute_ctrl{s}_down_lane{l}_integer_WriteBackPlugin_logic_DATA_lane{l}'
             for s in range(2, 6) for l in range(2)]
    words += ['execute_lane0_bypasser_integer_RS1_port_data']
    old = '  always @(*) begin\n    ' + DEST + ' = ' + balanced([
        f"({SEL}[{i}] ? {word} : 32'h0)" for i, word in enumerate(words)]) + ';\n  end\n'
    decls = [f'  (* keep = "true" *) wire [31:0] {x};' for x in [EARLY, LATE]]
    early = [f"({SEL}[{i}] ? {words[i]} : 32'h0)" for i in range(2)]
    late = []
    for i in range(2, 9):
        gate = f'{EN}[{i}]' if i == 2 else f'({EN}[{i}] && !(|{EN}[{i-1}:2]))'
        late.append(f"({gate} ? {words[i]} : 32'h0)")
    new = '\n'.join(decls) + '\n'
    new += f'  assign {EARLY} = {balanced(early)};\n'
    new += f'  assign {LATE} = {balanced(late)};\n'
    new += f"  always @(*) begin\n    {DEST} = ({EARLY} | ((!(|{EN}[1:0])) ? {LATE} : 32'h0));\n  end\n"
    assert before.count(old) == after.count(new) == 1
    assert before.replace(old, new, 1) == after
    assert after.replace(new, old, 1) == before
    assert before.count(KEEP) == after.count(KEEP) == 1
    # Exact source reconstruction retains every comparator and sequential block;
    # independently inspect the old kept data output's complete consumer census.
    uses = [line for line in before.splitlines() if re.search(r'\b' + DEST + r'\b', line)]
    assert len(uses) == 3 and KEEP in uses
    assert f'  assign execute_ctrl1_down_integer_RS1_lane0 = {DEST};' in uses
    for enables in range(512):
        original = next((i for i in range(9) if enables & (1 << i)), None)
        early_index = next((i for i in range(2) if enables & (1 << i)), None)
        late_index = next((i for i in range(2, 9) if enables & (1 << i)), None)
        assert original == (early_index if early_index is not None else late_index)
    check = CAND / 'check_vexii_os20_timing.py'
    assert sha(check) == '80a0c0e9cee4d3f7532c44d4aca198becc46c2400ebc8bfba811bd61e10f6188'
    spec = importlib.util.spec_from_file_location('integer_check', check)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for name, source in [('reference', before), ('candidate', after)]:
        normal = source.replace(KEEP, '  reg [31:0] ' + DEST + ';', 1)
        for word, decl in zip([EARLY, LATE], decls):
            normal = normal.replace(decl, f'  wire [31:0] {word};', 1)
        cone, width = module.cone(normal, name, [EN, *words], [SEL, DEST])
        assert width == 41 and cone == (CAND / (name + '.v')).read_text()
        assert (CAND / 'proof.v').read_text().count(cone) == 1
        selector, width = module.cone(normal, 'selector', [EN], [SEL])
        assert width == 9 and selector == (CAND / 'selector.v').read_text()
    # The C++ wrapper explores all independent nine-source patterns for every
    # physical bit. Whole-word masks and OR above establish no cross-bit mixing.
    harness = (CAND / 'proof.cpp').read_text()
    assert 'for(unsigned e=0;e<512;++e)' in harness
    assert 'for(unsigned p=0;p<512;++p)' in harness
    assert 'words[p][i]|=((p>>((i+b)%9))&1u)<<b;' in harness
    assert 'dut.original!=dut.result || dut.result!=expected' in harness
    OUT.mkdir(exist_ok=False)
    with (OUT / 'actual-proof-rerun.log').open('w') as log:
        subprocess.run([str(CAND / 'obj/Vinteger_rs1_proof')], check=True,
                       stdout=log, stderr=subprocess.STDOUT)
    assert 'PASS all262144' in (OUT / 'actual-proof-rerun.log').read_text()
    result = dict(reference_sha256=sha(base), candidate_sha256=sha(target),
        exact_one_data_block_replacement_with_two_kept_comb_words=True,
        original_output_keep_and_sole_operand_consumer_unchanged=True,
        original_live_comparators_gates_and_selector_cone_unchanged=True,
        all_other_source_including_sequential_source_identical=True,
        exact_producer_order_and_bitwise_masks_verified=True,
        zero_selection_preserved_without_rf_fallback_assumption=True,
        source_priority_index_cases=512, actual_cones_freshly_match_artifacts=True,
        actual_proof_rerun_cases=262144, actual_proof_bit_comparisons=8388608,
        new_registers=0, new_cycles=0,
        qualification='Full CPU and physical fit qualification remain separate.')
    (OUT / 'manifest.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
