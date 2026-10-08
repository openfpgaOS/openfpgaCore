#!/usr/bin/env python3
"""Independent audit of the emitted remaining match preselection changes."""
from pathlib import Path
import hashlib
import importlib.util
import json
import re
import subprocess

ROOT = Path(__file__).resolve().parents[3]
ART = ROOT / 'docs/measurements/os30-dual-20261004'
BASE = ART / 'src1-balance-v1/immediate/VexiiRiscv.v'
NEW = ART / 'match-preselect-all-v1/immediate/VexiiRiscv.v'
OUT = ART / 'all-match-emitted-audit-v1'
S0 = '_zz_execute_ctrl0_up_LANE_AGE_lane0'
S1 = S0 + '_1'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    base, new = BASE.read_text(), NEW.read_text()
    assert digest(BASE) == '6077cfd99d013f3d1e21892e00f3782c14f35385747f1da6cc66a6eec0e2d80c'
    assert digest(NEW) == '0b4b291a6d8d13d9e4b9fe1ba3eb58a2bf2e69f73308b8d936bcfee25e05e846'
    pattern = r'^  \(\* keep , syn_keep \*\) wire ((?:fp_preselect|int_rs2_preselect)_\w+) /\* synthesis syn_keep = 1 \*/;\n  assign \1 = [^;]+;\n'
    original = {m[1]: m[0] for m in re.finditer(pattern, base, re.M)}
    current = {m[1]: m[0] for m in re.finditer(pattern, new, re.M)}
    assert len(original) == 16 and len(current) == 66
    assert all(current[name] == block for name, block in original.items())
    proof = NEW.parent / 'matches/tb.sv'
    tb = proof.read_text()
    restored = new
    for name, block in current.items():
        assert block.rstrip() in tb
        if name not in original:
            restored = restored.replace(block, '', 1)
    names = [f'fp_match_RS{rs}_{stage}' for rs in range(1, 4) for stage in range(4, 13)]
    names += [f'int_rs2_write_match_{stage}_{lane}' for stage in range(2, 6) for lane in range(2)]
    drivers = {m[1]: m[2] for m in re.finditer(r'^  assign (\w+) = ([^;]+);$', new, re.M)}

    def leaves(expression, expected, active=()):
        tokens = set(re.findall(r'\b[A-Za-z_]\w*\b', re.sub(r"\d+'[bdh][0-9a-fA-F_xXzZ]+", '', expression)))
        result = set()
        for token in tokens:
            if token in expected:
                result.add(token)
            else:
                assert token in drivers and token not in active, token
                result |= leaves(drivers[token], expected, active + (token,))
        return result

    changed_fp = changed_int = 0
    for index, name in enumerate(names):
        pat = rf'  reg {name};\n  always @\(posedge clk\) if\(execute_ctrl0_down_isReady\)\n    {name} <= ([^;]+);'
        before, after = re.search(pat, base), re.search(pat, new)
        assert before and after
        assert len(re.findall(rf'\b{name}\s*<=', new)) == 1
        assert f'assign original[{index}]={before[1]};' in tb
        assert f'assign result[{index}]={after[1]};' in tb
        restored = restored.replace(after[0], before[0], 1)
        integer = name.startswith('int_')
        if integer:
            stage, lane = map(int, name.rsplit('_', 2)[1:])
            rs = 'RS2'
            enable = f'execute_ctrl{stage-1}_down_RD_ENABLE_lane{lane}'
            assert after[1].startswith(enable + ' && (')
            changed_int += before[0] != after[0]
        else:
            rs, stage_text = name.rsplit('_', 2)[1:]
            stage, lane = int(stage_text), 0
            changed_fp += before[0] != after[0]
        expected = {S0, S1}
        expected |= {f'execute_ctrl{stage-1}_down_RD_{field}_lane{lane}' for field in ['PHYS', 'RFID']}
        expected |= {f'DispatchPlugin_logic_candidates_{c}_ctx_hm_{rs}_{field}' for c in range(2) for field in ['PHYS', 'RFID']}
        if integer:
            expected.add(enable)
        assert leaves(after[1], expected) == expected, name
    assert restored == base
    assert (changed_fp, changed_int) == (19, 8)
    assert len(re.findall(r'^  reg fp_match_', new, re.M)) == 27
    assert len(re.findall(r'^  reg int_rs2_write_match_', new, re.M)) == 8
    checker = ROOT / 'tools/check_vexii_os20_timing.py'
    spec = importlib.util.spec_from_file_location('checker', checker)
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    freeze = '  (* maxfan = 16 *) wire execute_freeze_valid;'
    assert base.count(freeze) == new.count(freeze) == 1
    dispatch = helper.dispatch_tb(base.replace(freeze, '  wire execute_freeze_valid;'),
                                  new.replace(freeze, '  wire execute_freeze_valid;'))
    assert dispatch == (NEW.parent / 'dispatch/tb.sv').read_text()
    for kind in ['dispatch', 'matches']:
        assert not re.search(r'%Warning-(IMPLICIT|UNDRIVEN|LATCH)', (NEW.parent / kind / 'build.log').read_text())
        result = subprocess.run([str(NEW.parent / kind / 'obj/Vtb')], capture_output=True, text=True, check=True)
        assert 'PASS:' in result.stdout
    OUT.mkdir(exist_ok=False)
    evidence = dict(base_sha256=digest(BASE), candidate_sha256=digest(NEW),
                    exact_modified_fp_rhs=19, exact_modified_integer_rhs=8,
                    exact_added_comparison_wires=50, preexisting_eight_fp_edits_preserved=True,
                    all_other_source_bytes_reverse_exactly=True,
                    all_35_actual_rhs_match_exhaustive_harness=True,
                    actual_endpoint_inputs_checked_independently=True,
                    previous_down_rd_enable_preserved=True,
                    all_ff_enables_and_current_gating_unchanged=True,
                    actual_candidate_dispatch_cone_matches_onehot_proof=True,
                    dispatch_cases=2097152, match_cases_per_endpoint=1572864,
                    proof_binaries_rerun_pass=True,
                    added_state_bits=0, added_cycles=0,
                    whole_cpu_validation='requires separate regression evidence',
                    fitted_area_timing='requires independent full fit',
                    evidence_sha256={str(p.relative_to(ROOT)): digest(p) for p in [
                        Path(__file__), proof, NEW.parent / 'dispatch/tb.sv',
                        NEW.parent / 'matches/proof.log', NEW.parent / 'dispatch/proof.log']})
    (OUT / 'manifest.json').write_text(json.dumps(evidence, indent=2) + '\n')
    print(json.dumps(evidence, indent=2))


if __name__ == '__main__':
    main()
