#!/usr/bin/env python3
"""Independent emitted-source/proof audit of the local FP RS1 priority groups."""
from pathlib import Path
import hashlib
import importlib.util
import json
import re
import subprocess

ROOT = Path(__file__).resolve().parents[3]
ART = ROOT / 'docs/measurements/os30-dual-20261004'
CAND = ART / 'fp-rs1-priority-groups-v1/immediate'
OUT = ART / 'fp-rs1-priority-emitted-audit-v1'
DEST = 'execute_ctrl1_down_float_RS1_lane0'
EN = 'execute_lane0_bypasser_float_RS1_bypassEnables'
SEL = 'execute_lane0_bypasser_float_RS1_sel'
EARLY = 'fp_rs1_priority_early_data'
LATE = 'fp_rs1_priority_late_data'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def statement(source, name):
    values = re.findall(r'^  assign ' + re.escape(name) + r' = [^;]+;', source, re.M)
    assert len(values) == 1, name
    return values[0]


def balanced(terms):
    if len(terms) == 1:
        return terms[0]
    mid = len(terms)//2
    return '(' + balanced(terms[:mid]) + ' | ' + balanced(terms[mid:]) + ')'


def main():
    base = ART / 'aligner-trap-direct-v1/immediate/VexiiRiscv.v'
    target = CAND / 'VexiiRiscv.v'
    assert sha(base) == 'ec69d8b6b09f60fe60f03227bb1bad4c19ea841a28e874d8025b6970b1d639c3'
    assert sha(target) == 'a0afc808335895240d5034d5b94d26d6c9393c293b383a9cf164e8fc917ff145'
    before, after = base.read_text(), target.read_text()
    decls = [f'  (* keep = "true" *) wire [31:0] {x};' for x in [EARLY, LATE]]
    old = statement(before, DEST)
    new = '\n'.join(decls + [statement(after, x) for x in [EARLY, LATE, DEST]])
    assert before.replace(old, new, 1) == after
    assert after.replace(new, old, 1) == before
    words = [f'execute_ctrl{x}_down_lane0_float_WriteBackPlugin_logic_DATA_lane0' for x in range(4,13)]
    words += ['execute_lane0_bypasser_float_RS1_port_data']
    # Verify exact whole-word masks and producer mapping, ruling out bit mixing
    # before using the one-bit reduction in the actual extracted-source proof.
    early = [f"({SEL}[{i}] ? {words[i]} : 32'h0)" for i in range(4)]
    late = []
    for i in range(4,10):
        gate = f'{EN}[{i}]' if i == 4 else f'({EN}[{i}] && !(|{EN}[{i-1}:4]))'
        late += [f"({gate} ? {words[i]} : 32'h0)"]
    assert statement(after, EARLY) == f'  assign {EARLY} = {balanced(early)};'
    assert statement(after, LATE) == f'  assign {LATE} = {balanced(late)};'
    assert statement(after, DEST) == f"  assign {DEST} = ({EARLY} | ((!(|{EN}[3:0])) ? {LATE} : 32'h0));"
    for i, producer in enumerate(words[:8]):
        a = '_zz_' + DEST + (f'_{2*i}' if i else '')
        z = '_zz_' + DEST + f'_{2*i+1}'
        assert statement(before,a) == f'  assign {a} = {SEL}[{i}];'
        assert statement(before,z) == f"  assign {z} = 32'h0;"
        assert old.count(f'({a} ? {producer} : {z})') == 1
    for i in (8,9):
        assert old.count(f"({SEL}[{i}] ? {words[i]} : 32'h0)") == 1
    # An independent priority-index proof applies to arbitrary source words:
    # a present low group wins; otherwise the lowest present upper source wins.
    for enables in range(1024):
        old_index = next((i for i in range(10) if enables & (1<<i)),None)
        low = next((i for i in range(4) if enables & (1<<i)),None)
        high = next((i for i in range(4,10) if enables & (1<<i)),None)
        assert old_index == (low if low is not None else high)
    # Verify stored proof cones are freshly extracted from these exact emitted
    # sources. Only declaration attributes are normalized for the cone parser.
    check = CAND / 'check_vexii_os20_timing.py'
    assert sha(check) == '80a0c0e9cee4d3f7532c44d4aca198becc46c2400ebc8bfba811bd61e10f6188'
    spec = importlib.util.spec_from_file_location('fp_check', check)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    normal = after
    for name, decl in zip([EARLY,LATE],decls):
        normal = normal.replace(decl,f'  wire [31:0] {name};',1)
    for name, source in [('reference',before),('candidate',normal)]:
        cone,width = module.cone(source,name,[EN,*words],[SEL,DEST])
        assert width == 42
        assert cone == (CAND / (name+'.v')).read_text()
        assert (CAND / 'proof.v').read_text().count(cone) == 1
    OUT.mkdir(exist_ok=False)
    with (OUT / 'actual-proof-rerun.log').open('w') as log:
        subprocess.run([str(CAND / 'obj/Vfp_rs1_proof')],check=True,stdout=log,stderr=subprocess.STDOUT)
    assert 'PASS all1048576' in (OUT / 'actual-proof-rerun.log').read_text()
    result = dict(reference_sha256=sha(base),candidate_sha256=sha(target),
        exact_one_output_replacement_with_two_kept_comb_words=True,
        all_original_priority_enables_and_selectors_unchanged=True,
        all_original_match_valid_rd_enable_and_sequential_source_unchanged=True,
        zero_selection_preserved=True,source_priority_index_cases=1024,
        exact_producer_order_and_whole_word_masks_verified=True,
        bit_independence_verified_from_exact_expressions=True,
        actual_cones_freshly_match_artifacts=True,actual_proof_rerun_cases=1048576,
        actual_proof_rerun_bit_comparisons=33554432,
        kept_combinational_boundaries=2,new_registers=0,new_cycles=0,
        qualification='Combined full-CPU suite and complete-core timing remain separate.')
    (OUT / 'manifest.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))


if __name__ == '__main__':
    main()
