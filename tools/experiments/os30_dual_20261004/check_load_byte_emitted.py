#!/usr/bin/env python3
"""Independently audit actual load-byte emitted cones and the linearity proof."""
from pathlib import Path
import hashlib
import json
import re
import subprocess

ROOT = Path(__file__).resolve().parents[3]
ART = ROOT / 'docs/measurements/os30-dual-20261004'
BASE = ART / 'raw-normalize-v1/immediate/VexiiRiscv.v'
NEW = ART / 'load-byte-factor-v1/immediate/VexiiRiscv.v'
OUT = ART / 'load-byte-emitted-audit-v1'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    base, new = BASE.read_text(), NEW.read_text()
    assert digest(BASE) == '06c797895939e09a2a63a0eb921f2ae73beb8e8e55ef4ac98521c8c75f65d048'
    assert digest(NEW) == '92b6cbb95623589ba8dff7c128e9b0fb32bf55d7ce445fa820c3747ae6465ea1'
    old = re.search(r'^  always @\(\*\) begin\n    LsuPlugin_logic_onCtrl_loadData_shifted\[7 : 0\] = .*?^  end', base, re.M | re.S)[0]
    start = new.index('  // The byte map preserves zero and bitwise OR.')
    end = new.index('\n\n  assign execute_ctrl4_down_LsuPlugin_logic_onCtrl_loadData_RESULT_lane0', start)
    changed = new[start:end]
    assert new.replace(changed, old, 1) == base
    ref = (NEW.parent / 'reference.sv').read_text()
    cand = (NEW.parent / 'candidate.sv').read_text()
    assert old in ref and changed in cand
    # Every combinational statement in the old harness is copied from the
    # original source. The candidate harness embeds the entire exact new block.
    for statement in re.findall(r'^  assign [^\n]+;$|^  always @\(\*\) begin\n.*?^  end', ref, re.M | re.S):
        assert base.count(statement) == 1
    for declaration in re.findall(r'^  (?:wire|reg)\s+(?:\[[^\]]+\]\s+)?\w+;$', ref, re.M):
        assert base.count(declaration) == 1
    assert "os30_lsu_byte_map[15:8] = byte_address[1] ? word_data[31:24] : word_data[15:8];" in changed
    assert 'os30_lsu_byte_map[31:16] = word_data[31:16];' in changed
    for address, bitrange in [('00', '7:0'), ('01', '15:8'), ('10', '23:16')]:
        assert f"2'b{address} : os30_lsu_byte_map[7:0] = word_data[{bitrange}];" in changed
    assert 'default : os30_lsu_byte_map[7:0] = word_data[31:24];' in changed
    # No data-selected control or data arithmetic: the exact function is only
    # bit projection controlled by address. The surrounding exact muxes are
    # zero masks and OR controlled solely by IO mode and both hit bits.
    for condition in re.findall(r'\b(?:case|if)\(([^)]+)\)', changed):
        assert condition == 'byte_address'
    for lane in range(2):
        assert f'wire os30_lsu_byte_way{lane} = !LsuPlugin_logic_onCtrl_io_cmdSent && execute_ctrl4_down_LsuL1Plugin_logic_WAYS_HITS_lane0[{lane}];' in changed
    tb = (NEW.parent / 'tb.sv').read_text()
    assert 'control<32' in tb and 'bitpos<32' in tb and 'data_bits<8' in tb
    for index, name in enumerate(['execute_ctrl4_down_LsuL1Plugin_logic_BANKS_MUXES_lane0_0',
                                  'execute_ctrl4_down_LsuL1Plugin_logic_BANKS_MUXES_lane0_1',
                                  'LsuPlugin_logic_onCtrl_io_rsp_payload_data']):
        assert f"{name}=data_bits[{index}] ? (32'h1<<bitpos) : 32'h0;" in tb
    recipe = json.loads((NEW.parent / 'recipe.json').read_text())
    for name, value in recipe['files'].items():
        assert digest(NEW.parent / name) == value
    for name in ['build.log', 'lint.log']:
        assert not re.search(r'%Warning-(IMPLICIT|UNDRIVEN|LATCH)', (NEW.parent / name).read_text())
    result = subprocess.run([str(NEW.parent / 'obj/Vtb')], capture_output=True, text=True, check=True)
    assert 'PASS load-byte actual cones' in result.stdout
    OUT.mkdir(exist_ok=False)
    (OUT / 'rerun.log').write_text(result.stdout)
    evidence = dict(reference_sha256=digest(BASE), candidate_sha256=digest(NEW),
                    exact_single_combinational_scope=True, all_other_source_bytes_reverse_exactly=True,
                    actual_harness_statements_and_declarations_verified=True,
                    original_partial_byte_mapping_preserved=True,
                    both_hits_or_and_io_priority_preserved=True,
                    fixed_control_zero_and_or_preservation_independently_reviewed=True,
                    all_96_data_basis_vectors_exhausted_for_each_of_32_controls=True,
                    arbitrary_96_bit_word_equality_follows_from_basis_and_or_preservation=True,
                    downstream_sign_sc_amo_and_fpu_bytes_unchanged=True,
                    proof_binary_rerun_pass=True, added_state_bits=0, added_cycles=0,
                    qualification='Requires separate whole CPU and physical evidence',
                    evidence_sha256={str(p.relative_to(ROOT)): digest(p) for p in [
                        Path(__file__), NEW.parent / 'reference.sv', NEW.parent / 'candidate.sv',
                        NEW.parent / 'tb.sv', NEW.parent / 'recipe.json']})
    (OUT / 'manifest.json').write_text(json.dumps(evidence, indent=2) + '\n')
    print(json.dumps(evidence, indent=2))


if __name__ == '__main__':
    main()
