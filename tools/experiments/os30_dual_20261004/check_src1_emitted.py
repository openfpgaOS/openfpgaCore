#!/usr/bin/env python3
"""Independent emitted-source/proof audit for the private SRC1 mux rewrite."""
from pathlib import Path
import hashlib
import importlib.util
import json
import re
import subprocess

ROOT = Path(__file__).resolve().parents[3]
ART = ROOT / 'docs/measurements/os30-dual-20261004'
BASE = ART / 'fp-preselect-v1/immediate/VexiiRiscv.v'
NEW = ART / 'src1-balance-v1/immediate/VexiiRiscv.v'
OUT = ART / 'src1-balance-emitted-audit-v1'
DEST = '_zz_execute_ctrl1_down_integer_RS1_lane0_1'
SEL = 'execute_lane0_bypasser_integer_RS1_sel'
ENABLES = 'execute_lane0_bypasser_integer_RS1_bypassEnables'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    base, new = BASE.read_text(), NEW.read_text()
    assert digest(BASE) == 'a85cdf3621b3e0257f7fd2ceeb5288c554cf50bc76f1b7bf728006ca726370ad'
    assert digest(NEW) == '6077cfd99d013f3d1e21892e00f3782c14f35385747f1da6cc66a6eec0e2d80c'
    pattern = rf'^  always @\(\*\) begin\n    {DEST} = .*?^  end\n'
    old_blocks = re.findall(pattern, base, re.M | re.S)
    new_blocks = re.findall(pattern, new, re.M | re.S)
    assert len(old_blocks) == len(new_blocks) == 1
    assert new.replace(new_blocks[0], old_blocks[0], 1) == base
    data = [f'execute_ctrl{s}_down_lane{lane}_integer_WriteBackPlugin_logic_DATA_lane{lane}'
            for s in range(2, 6) for lane in range(2)]
    data.append('execute_lane0_bypasser_integer_RS1_port_data')
    # Every candidate term selects one entire word or zero, with no bit
    # arithmetic, mixing, truncation or selector dependence on data values.
    rhs = re.search(rf'{DEST} = (.*);', new_blocks[0])[1]
    for bit, value in enumerate(data):
        term = f"({SEL}[{bit}] ? {value} : 32'h0)"
        assert rhs.count(term) == 1
        rhs = rhs.replace(term, 'T')
    assert re.fullmatch(r'[T() |]+', rhs)
    keep = '  (* keep , syn_keep *) reg        [31:0]   ' + DEST + ' /* synthesis syn_keep = 1 */ ;'
    assert base.count(keep) == new.count(keep) == 1
    ordinary = '  reg [31:0] ' + DEST + ';'
    checker_path = ROOT / 'tools/check_vexii_os20_timing.py'
    spec = importlib.util.spec_from_file_location('checker', checker_path)
    checker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checker)
    inputs = [ENABLES] + data
    before, width = checker.cone(base.replace(keep, ordinary), 'reference', inputs, [SEL, DEST])
    after, new_width = checker.cone(new.replace(keep, ordinary), 'candidate', inputs, [SEL, DEST])
    assert width == new_width == 41
    tb_path = NEW.parent / 'tb.sv'
    tb = tb_path.read_text()
    assert tb.startswith(before + after + '\nmodule tb;\n')
    selector_old, selector_width = checker.cone(base, 'priority', [ENABLES], [SEL])
    selector_new, new_selector_width = checker.cone(new, 'priority', [ENABLES], [SEL])
    assert selector_old == selector_new and selector_width == new_selector_width == 9
    assert not any(name in selector_new for name in data)
    assert 'original[40:32]-9\'d1' in tb
    assert 'enables<512' in tb and 'values<512' in tb and 'bitpos<32' in tb
    warnings = (NEW.parent / 'build.log').read_text()
    assert not re.search(r'%Warning-(IMPLICIT|UNDRIVEN|LATCH)', warnings)
    checked = subprocess.run([str(NEW.parent / 'obj/Vtb')], capture_output=True, text=True, check=True)
    assert 'PASS:' in checked.stdout
    OUT.mkdir(exist_ok=False)
    evidence = dict(base_sha256=digest(BASE), candidate_sha256=digest(NEW),
                    exact_changed_scope=DEST, reverse_source_equality=True,
                    original_priority_selector_and_keep_unchanged=True,
                    selector_has_no_data_dependencies=True,
                    exact_emitted_cones_match_proof=True,
                    exhaustive_cases=8388608, proof_binary_rerun_pass=True,
                    bitwise_extension_to_arbitrary_simultaneous_words=True,
                    added_state_bits=0, added_pipeline_cycles=0,
                    whole_cpu_validation='requires separate regression evidence',
                    fitted_area_and_timing='requires independent full fit',
                    evidence_sha256={str(p.relative_to(ROOT)): digest(p) for p in [
                        Path(__file__), tb_path, NEW.parent / 'proof.log', checker_path]})
    (OUT / 'manifest.json').write_text(json.dumps(evidence, indent=2) + '\n')
    print(json.dumps(evidence, indent=2))


if __name__ == '__main__':
    main()
