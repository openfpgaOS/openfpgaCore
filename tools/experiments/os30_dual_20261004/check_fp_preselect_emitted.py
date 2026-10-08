#!/usr/bin/env python3
"""Independently check the emitted eight-match rewrite and its extracted proof."""
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[3]
ART = ROOT / 'docs/measurements/os30-dual-20261004'
BASE = ART / 'decode-ready-v1/immediate/VexiiRiscv.v'
NEW = ART / 'fp-preselect-v1/immediate/VexiiRiscv.v'
OUT = ART / 'fp-preselect-emitted-audit-v1'
TARGETS = [('RS1', 10), ('RS2', 4), ('RS2', 7), ('RS2', 8),
           ('RS2', 10), ('RS3', 6), ('RS3', 10), ('RS3', 11)]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    base, new = BASE.read_text(), NEW.read_text()
    assert digest(BASE) == 'ff0bd8cf20e0fc0fd85ec91c70b5122a5fabd8d9a185a33b12cc40e9be1baf55'
    assert digest(NEW) == 'a85cdf3621b3e0257f7fd2ceeb5288c554cf50bc76f1b7bf728006ca726370ad'
    proof = NEW.parent / 'matches/tb.sv'
    harness = proof.read_text()
    restored = new
    early_count = 0
    for index, (rs, stage) in enumerate(TARGETS):
        name = f'fp_match_{rs}_{stage}'
        pattern = rf'  reg {name};\n  always @\(posedge clk\) if\(execute_ctrl0_down_isReady\)\n    {name} <= ([^;]+);'
        old = re.findall(pattern, base)
        changed = re.findall(pattern, new)
        assert len(old) == len(changed) == 1
        assert f'assign original[{index}] = {old[0]};' in harness
        assert f'assign result[{index}] = {changed[0]};' in harness
        assert len(re.findall(rf'\b{name}\s*<=', new)) == 1
        restored = restored.replace(f'{name} <= {changed[0]};', f'{name} <= {old[0]};', 1)
        producer = f'execute_ctrl{stage-1}_down_RD'
        for lane in range(2):
            eq = f'fp_preselect_{rs}_{stage}_candidate{lane}'
            candidate = f'DispatchPlugin_logic_candidates_{lane}_ctx_hm_{rs}'
            declaration = f'  (* keep , syn_keep *) wire {eq} /* synthesis syn_keep = 1 */;\n'
            assignment = f'  assign {eq} = ({producer}_PHYS_lane0 == {candidate}_PHYS) && ({producer}_RFID_lane0 == {candidate}_RFID);\n'
            assert new.count(declaration + assignment) == 1
            assert declaration + assignment in harness
            restored = restored.replace(declaration + assignment, '', 1)
            early_count += 1
        assert f'({producer}_PHYS_lane0 == 5\'h0) && ({producer}_RFID_lane0 == 1\'b0)' in changed[0]
    assert restored == base, 'Additional RTL difference outside eight exact match equations'
    assert len(re.findall(r'^  reg fp_match_', new, re.M)) == 27

    helper_path = ROOT / 'tools/check_vexii_os20_timing.py'
    spec = importlib.util.spec_from_file_location('proof_helper', helper_path)
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    # The attributed input is normalized only for the declaration extractor.
    before = '  (* maxfan = 16 *) wire execute_freeze_valid;'
    assert base.count(before) == new.count(before) == 1
    normalized_base = base.replace(before, '  wire execute_freeze_valid;')
    normalized_new = new.replace(before, '  wire execute_freeze_valid;')
    dispatch_tb = helper.dispatch_tb(normalized_base, normalized_new)
    assert dispatch_tb == (NEW.parent / 'dispatch/tb.sv').read_text()
    assert 'DispatchPlugin_logic_inserter_0_oh,DispatchPlugin_logic_inserter_1_oh};' in dispatch_tb
    assert "if(result[3:2] == 2'b11 || result[1:0] == 2'b11)" in dispatch_tb
    for proof_kind in ['dispatch', 'matches']:
        warnings = (NEW.parent / proof_kind / 'build.log').read_text()
        assert not re.search(r'%Warning-(IMPLICIT|UNDRIVEN|LATCH)', warnings)
        result = subprocess.run([str(NEW.parent / proof_kind / 'obj/Vtb')],
                                capture_output=True, text=True, check=True)
        assert 'PASS:' in result.stdout

    OUT.mkdir(exist_ok=False)
    status = dict(base_sha256=digest(BASE), candidate_sha256=digest(NEW),
                  targets=[f'{rs}_{stage}' for rs, stage in TARGETS],
                  sixteen_emitted_equality_wires_match_proof=(early_count == 16),
                  eight_emitted_rhs_match_proof=True,
                  all_other_source_bytes_reverse_exactly=True,
                  all_27_registers_and_enables_preserved=True,
                  actual_dispatch_extracted_from_candidate_matches_proof=True,
                  scheduler_zero_one_hot_exhaustive_states=2097152,
                  local_equivalence_cases_per_endpoint=786432,
                  explicit_no_selection_rd_zero_case_preserved=True,
                  proof_binaries_rerun_pass=True,
                  no_new_state_or_architectural_cycle=True,
                  whole_cpu_qualification='separate regression evidence required',
                  actual_fit_timing_and_area='separate full fit evidence required',
                  evidence_sha256={str(p.relative_to(ROOT)): digest(p) for p in [
                      Path(__file__), helper_path, proof,
                      NEW.parent / 'dispatch/tb.sv', NEW.parent / 'dispatch/proof.log',
                      NEW.parent / 'matches/proof.log']})
    (OUT / 'manifest.json').write_text(json.dumps(status, indent=2) + '\n')
    print(json.dumps(status, indent=2))


if __name__ == '__main__':
    main()
