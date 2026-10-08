#!/usr/bin/env python3
"""Keep the non-cancellation GShare training predicate as one early boundary."""
from pathlib import Path
import argparse
import difflib
import hashlib
import itertools
import json
import re
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[3]
BASE = ROOT / 'docs/measurements/os30-dual-20261004'
REFERENCE = BASE / 'lane1-rdid-constant-v1/immediate/VexiiRiscv.v'
REFERENCE_SHA = 'a78ced6dffd7c75a184481b8906f3915aa063b7132325b2ee867eaf4cc9fde1a'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=BASE / 'gshare-eligibility-v1/immediate')
    args = parser.parse_args()
    assert sha(REFERENCE) == REFERENCE_SHA
    source = REFERENCE.read_text()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    assignments = dict(re.findall(r'^  assign (\w+) = ([^;]+);', source, re.M))
    aliases = {
        'LearnPlugin_logic_buffered_0_valid': 'early0_BranchPlugin_logic_jumpLogic_learn_valid',
        'LearnPlugin_logic_arbitrated_valid': 'streamArbiter_3_io_output_valid',
        'LearnPlugin_logic_arbitrated_toFlow_valid': 'LearnPlugin_logic_arbitrated_valid',
        'LearnPlugin_logic_learn_valid': 'LearnPlugin_logic_arbitrated_toFlow_valid',
    }
    for left, right in aliases.items():
        assert assignments[left] == right
    learn = '(((execute_ctrl3_up_LANE_SEL_lane0 && execute_ctrl3_down_isReady) && (! execute_lane0_ctrls_3_upIsCancel)) && (|execute_ctrl3_down_early0_BranchPlugin_SEL_lane0))'
    assert assignments['early0_BranchPlugin_logic_jumpLogic_learn_valid'] == learn
    module = re.search(r'^module StreamArbiter_2 \(.*?^endmodule', source, re.M | re.S)[0]
    assert '  assign io_output_valid = io_inputs_0_valid;' in module
    assert not re.search(r'\balways\b|\breg\b', module)
    instance = re.search(r'^  StreamArbiter_2 streamArbiter_3 \(.*?^  \);', source, re.M | re.S)[0]
    for port, net in [('io_inputs_0_valid', 'LearnPlugin_logic_buffered_0_valid'),
                      ('io_output_valid', 'streamArbiter_3_io_output_valid')]:
        assert re.search(r'\.' + port + r'\s*\(\s*' + net + r'\s*\)', instance)
    # Validate every payload is a combinational pass-through, including fields
    # used to calculate the counter's overflow. No learning latency is changed.
    fields = re.findall(r'^  assign io_output_payload_(\w+) = io_inputs_0_payload_\1;', module, re.M)
    assert len(fields) == 14
    old = '  assign GSharePlugin_logic_mem_write_valid = ((LearnPlugin_logic_learn_valid && LearnPlugin_logic_learn_payload_isBranch) && (! GSharePlugin_logic_onLearn_overflow));'
    assert source.count(old) == 1 and 'gshare_training_eligible' not in source
    new = '''  (* keep = "true" *) wire gshare_training_eligible;
  assign gshare_training_eligible = execute_ctrl3_up_LANE_SEL_lane0 && execute_ctrl3_down_isReady && (|execute_ctrl3_down_early0_BranchPlugin_SEL_lane0) && LearnPlugin_logic_learn_payload_isBranch && !GSharePlugin_logic_onLearn_overflow;
  assign GSharePlugin_logic_mem_write_valid = gshare_training_eligible && !execute_lane0_ctrls_3_upIsCancel;'''
    candidate = source.replace(old, new, 1)
    assert candidate.replace(new, old, 1) == source
    # Exhaustive logical 0/1/X/Z semantics, with unknown = 2 after reduction.
    # This is associativity of the exact extracted conjunction, including the
    # full cancellation condition; no ISA or flush-age assumption is used.
    def land(*values):
        return 0 if 0 in values else 1 if all(v == 1 for v in values) else 2
    def lnot(value):
        return 1 - value if value < 2 else 2
    cases = 0
    for valid, ready, cancel, selected, branch, overflow in itertools.product(range(4), repeat=6):
        old_valid = land(land(land(valid, ready), lnot(cancel)), selected)
        old_write = land(land(old_valid, branch), lnot(overflow))
        new_write = land(land(valid, ready, selected, branch, lnot(overflow)), lnot(cancel))
        assert old_write == new_write
        cases += 1
    target = out / 'VexiiRiscv.v'
    target.write_text(candidate)
    (out / 'change.patch').write_text(''.join(difflib.unified_diff(source.splitlines(True), candidate.splitlines(True), fromfile='a/VexiiRiscv.v', tofile='b/VexiiRiscv.v')))
    with (out / 'lint.log').open('w') as log:
        subprocess.run(['verilator', '--lint-only', '--top-module', 'VexiiRiscv', '-Wno-fatal', str(target)], stdout=log, stderr=subprocess.STDOUT, check=True)
    assert not re.search(r'%Warning-(IMPLICIT|UNDRIVEN|LATCH|MULTIDRIVEN)', (out / 'lint.log').read_text())
    shutil.copy2(__file__, out / Path(__file__).name)
    manifest = dict(reference_sha256=REFERENCE_SHA, candidate_sha256=sha(target), script_sha256=sha(Path(__file__)),
        exact_reverse=True, exact_four_state_conjunction_cases=cases, sequential_source_unchanged=True,
        new_registers=0, new_cycles=0, all_training_address_data_and_bypass_logic_unchanged=True,
        valid_aliases=aliases, branch_learn_equation=learn, arbiter_payload_fields=fields,
        arbiter_module_sha256=hashlib.sha256(module.encode()).hexdigest(),
        arbiter_instance_sha256=hashlib.sha256(instance.encode()).hexdigest(),
        physical_result='Pending; one kept boundary does not establish improved timing.',
        independent_review='Pending', whole_cpu_validation='Pending')
    (out / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps(manifest, indent=2))


if __name__ == '__main__':
    main()
