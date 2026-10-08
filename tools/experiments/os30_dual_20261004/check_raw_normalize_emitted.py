#!/usr/bin/env python3
"""Independent scope, cone and bit-dependency audit of early RAW normalization."""
from pathlib import Path
import hashlib
import importlib.util
import json
import re
import subprocess

ROOT = Path(__file__).resolve().parents[3]
ART = ROOT / 'docs/measurements/os30-dual-20261004'
BASE = ART / 'match-preselect-all-v1/immediate/VexiiRiscv.v'
NEW = ART / 'raw-normalize-v1/immediate/VexiiRiscv.v'
OUT = ART / 'raw-normalize-emitted-audit-v1'
P = 'AlignerPlugin_logic_'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    base, new = BASE.read_text(), NEW.read_text()
    assert digest(BASE) == '0b4b291a6d8d13d9e4b9fe1ba3eb58a2bf2e69f73308b8d936bcfee25e05e846'
    assert digest(NEW) == '06c797895939e09a2a63a0eb921f2ae73beb8e8e55ef4ac98521c8c75f65d048'
    restored, plain = new, new
    qualifiers = list(re.finditer(r'^  \(\* keep , syn_keep \*\) wire (aligner_raw_upper_sel_(\d)_(\d)) /\* synthesis syn_keep = 1 \*/;\n  assign \1 = [^;]+;\n', new, re.M))
    assert len(qualifiers) == 13
    for match in qualifiers:
        name, lane, index = match[1], int(match[2]), int(match[3])
        assert lane in (0, 1) and lane <= index < 7
        expected = f'  assign {name} = {P}extractors_{lane}_slicesOh[{index-lane}] && {P}scanners_{index}_checker_1_required;\n'
        assert match[0].endswith(expected)
        assert f'  assign {P}scanners_{index}_checker_1_required = ({P}slices_data_{index}[1 : 0] == 2\'b11);' in base
        restored = restored.replace(match[0], '', 1)
        plain = plain.replace(match[0].splitlines()[0], f'  wire {name};', 1)
    raws, masks, selectors = [], [], []
    for lane in range(2):
        raw = f'decode_ctrls_0_up_Decode_INSTRUCTION_RAW_{lane}'
        raws.append(raw)
        masks.append(f'{P}extractors_{lane}_usableMask')
        selectors.append(f'{P}extractors_{lane}_slicesOh')
        pattern = rf'^  always @\(\*\) begin\n    {raw} = .*?^  end\n'
        old_blocks, new_blocks = re.findall(pattern, base, re.M | re.S), re.findall(pattern, new, re.M | re.S)
        assert len(old_blocks) == len(new_blocks) == 1
        restored = restored.replace(new_blocks[0], old_blocks[0], 1)
        rhs = re.search(rf'{raw} = (.*);', new_blocks[0])[1]
        suffix = f',{P}extractors_{lane}_ctx_instruction[15 : 0]}}'
        assert rhs.startswith('{') and rhs.endswith(suffix)
        upper = rhs[1:-len(suffix)]
        for index in range(lane, 7):
            term = f'(aligner_raw_upper_sel_{lane}_{index} ? {P}slices_data_{index+1} : 16\'h0)'
            assert upper.count(term) == 1
            upper = upper.replace(term, 'T')
        assert re.fullmatch(r'[()T |]+', upper)
    assert restored == base
    checker_path = ROOT / 'tools/check_vexii_os20_timing.py'
    spec = importlib.util.spec_from_file_location('checker', checker_path)
    checker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checker)
    select_cone, width = checker.cone(base, 'selector_cone', masks, selectors)
    assert width == 15
    select_tb = (NEW.parent / 'selectors/tb.sv').read_text()
    assert select_tb.startswith(select_cone + '\nmodule tb;\n')
    assert 'i<32768' in select_tb
    assert "result[14:7]-8'd1" in select_tb and "result[6:0]-7'd1" in select_tb
    data = [f'{P}slices_data_{i}' for i in range(8)]
    before, w0 = checker.cone(base, 'reference', masks + data, raws)
    after, w1 = checker.cone(plain, 'candidate', masks + data, raws)
    tb = (NEW.parent / 'data/tb.sv').read_text()
    assert w0 == w1 == 64 and tb.startswith(before + after + '\nmodule tb;\n')
    for lane in range(2):
        # Correlated selector enumeration is sufficient only because neither
        # output depends on the other lane's mask. Verify actual cone support.
        for source in [base, plain]:
            cone, width = checker.cone(source, 'lane', [masks[lane]] + data, [raws[lane]])
            assert width == 32 and masks[1-lane] not in cone
    assert 'pattern<(1<<24)' in tb and 'choice<9' in tb
    for i, word in enumerate(data):
        assert f"    {word}={{{{14{{pattern[{16+i}]}}}},pattern[{2*i+1}:{2*i}]}};" in tb
    assert f"{masks[0]}=choice==8 ? 8'h0 : (8'h1 << choice);" in tb
    assert f"{masks[1]}=(choice==0 || choice==8) ? 7'h0 : (7'h1 << (choice-1));" in tb
    OUT.mkdir(exist_ok=False)
    for kind in ['selectors', 'data']:
        assert not re.search(r'%Warning-(IMPLICIT|UNDRIVEN|LATCH)', (NEW.parent / kind / 'build.log').read_text())
        result = subprocess.run([str(NEW.parent / kind / 'obj/Vtb')], capture_output=True, text=True, check=True)
        assert 'PASS:' in result.stdout
        (OUT / (kind + '-rerun.log')).write_text(result.stdout)
    evidence = dict(reference_sha256=digest(BASE), candidate_sha256=digest(NEW),
                    exact_changed_raw_blocks=2, exact_new_qualifiers=13,
                    all_other_source_bytes_reverse_exactly=True,
                    low16_selection_unchanged=True, bitwise_upper_terms_independently_checked=True,
                    each_output_independent_of_other_lane_selector=True,
                    exact_emitted_cones_match_proof=True,
                    selector_masks_exhausted=32768, relevant_data_bit_cases=150994944,
                    zero_and_compressed_mtval_upper_halves_preserved=True,
                    proof_binaries_rerun_pass=True, added_state_bits=0, added_cycles=0,
                    qualification='CPU regressions and physical benefit require separate evidence',
                    evidence_sha256={str(p.relative_to(ROOT)): digest(p) for p in [Path(__file__),
                        NEW.parent / 'selectors/tb.sv', NEW.parent / 'data/tb.sv',
                        NEW.parent / 'selectors/proof.log', NEW.parent / 'data/proof.log']})
    (OUT / 'manifest.json').write_text(json.dumps(evidence, indent=2) + '\n')
    print(json.dumps(evidence, indent=2))


if __name__ == '__main__':
    main()
