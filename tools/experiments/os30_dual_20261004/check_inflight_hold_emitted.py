#!/usr/bin/env python3
"""Independently check the held-trap aggregate and its load-byte composition."""
from pathlib import Path
import hashlib
import importlib.util
import json
import re
import subprocess

ROOT = Path(__file__).resolve().parents[3]
ART = ROOT / 'docs/measurements/os30-dual-20261004'
BASE = ART / 'raw-normalize-v1/immediate/VexiiRiscv.v'
HOLD = ART / 'inflight-hold-v1/immediate/VexiiRiscv.v'
LOAD = ART / 'load-byte-factor-v1/immediate/VexiiRiscv.v'
COMBINED = ART / 'raw-hold-load-v1/immediate/VexiiRiscv.v'
OUT = ART / 'inflight-hold-emitted-audit-v1'
H = 'TrapPlugin_logic_harts_0_trap_fsm_inflightTrap'
D = 'decode_logic_trapPending'
E = 'execute_lane0_logic_trapPending'
Q = 'decode_hold_buffered_trap'
F = 'LsuPlugin_logic_flushPort_valid'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    base, hold, load, combined = [p.read_text() for p in [BASE, HOLD, LOAD, COMBINED]]
    expected = ['06c797895939e09a2a63a0eb921f2ae73beb8e8e55ef4ac98521c8c75f65d048',
                '5e9affaba19a5ef2555ec3f900724e37d63f232287b729c8a853f851604e163c',
                '92b6cbb95623589ba8dff7c128e9b0fb32bf55d7ce445fa820c3747ae6465ea1',
                '1bce3882b721c09e4a5091cc3ef00360366df9b1b0b79ffb0b1938b820105569']
    assert [digest(p) for p in [BASE, HOLD, LOAD, COMBINED]] == expected
    old = re.search(r'^  assign ' + H + r' = [^;]+;', base, re.M)[0]
    new = re.search(r'^  wire ' + Q + r';\n.*?^  assign ' + H + r' = [^;]+;', hold, re.M | re.S)[0]
    assert hold.replace(new, old, 1) == base
    ordinary = re.search(r'^  assign ' + D + r'\[0\] = \((.+) \|\| decode_logic_skid_trapPending\);$', base, re.M)[1]
    expected_h = old.replace(D + '[0]', ordinary)[:-1] + ' || ' + Q + ';'
    assert new.endswith(expected_h)
    assert 'assign ' + Q + ' = decode_skid_rValid && !' in new
    assert F not in new
    for lane in range(2):
        assert f'(decode_ctrls_0_down_LANE_SEL_{lane}_s2mBuffer && decode_ctrls_0_down_TRAP_{lane}_s2mBuffer)' in new
    assert combined.replace(new, old, 1) == load
    assert load.replace(old, new, 1) == combined
    # The independently reviewed load-byte replacement is disjoint; undoing it
    # in the combined source must recover the exact held-only candidate.
    old_load = re.search(r'^  always @\(\*\) begin\n    LsuPlugin_logic_onCtrl_loadData_shifted\[7 : 0\] = .*?^  end', base, re.M | re.S)[0]
    start = load.index('  // The byte map preserves zero and bitwise OR.')
    end = load.index('\n\n  assign execute_ctrl4_down_LsuPlugin_logic_onCtrl_loadData_RESULT_lane0', start)
    new_load = load[start:end]
    assert combined.replace(new_load, old_load, 1) == hold
    checker = ROOT / 'tools/check_vexii_os20_timing.py'
    spec = importlib.util.spec_from_file_location('checker', checker)
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)

    def inputs(tb, module):
        fields = re.search(r'^module ' + module + r'\(([^)]+)\);', tb, re.M)[1].split(',')
        assert fields[-1] == 'observed'
        return fields[:-1]

    for kind, module, bus, outputs in [
            ('lsu-implication', 'execute_pending', E, [F, E]),
            ('decode-decomposition', 'decode_pending', D, [D])]:
        tb = (HOLD.parent / kind / 'tb.sv').read_text()
        scalar = base.replace('  assign ' + bus + '[0] = ', '  assign ' + bus + ' = ', 1)
        cone, width = helper.cone(scalar, module, inputs(tb, module), outputs)
        assert tb.startswith(cone + '\nmodule tb;\n')
    tb = (HOLD.parent / 'held-bypass/tb.sv').read_text()
    cutpoints = inputs(tb, 'reference')
    before, w0 = helper.cone(base, 'reference', cutpoints, ['decode_logic_skid_trapPending', '_zz_decode_logic_trapPending_4'])
    after, w1 = helper.cone(hold, 'candidate', cutpoints, [Q, '_zz_decode_logic_trapPending_4'])
    assert w0 == w1 == 3 and tb.startswith(before + after + '\nmodule tb;\n')
    assert f'if(!{F} || execute_pending)' in tb
    assert 'if(checked!=49152)' in tb
    OUT.mkdir(exist_ok=False)
    for kind in ['lsu-implication', 'decode-decomposition', 'held-bypass']:
        assert not re.search(r'%Warning-(IMPLICIT|UNDRIVEN|LATCH)', (HOLD.parent / kind / 'build.log').read_text())
        result = subprocess.run([str(HOLD.parent / kind / 'obj/Vtb')], capture_output=True, text=True, check=True)
        assert 'PASS:' in result.stdout
        (OUT / (kind + '-rerun.log')).write_text(result.stdout)
    evidence = dict(reference_sha256=expected[0], hold_sha256=expected[1], load_sha256=expected[2],
                    combined_sha256=expected[3], exact_changed_boundary=H,
                    ordinary_decode_contribution_is_exact_original_expression=True,
                    held_only_term_uses_occupancy_and_stored_payload=True,
                    actual_skid_valid_flushing_storage_lane_masks_unchanged=True,
                    implication_and_decomposition_actual_cones_verified=True,
                    actual_held_bypass_cones_verified=True,
                    all_three_proof_binaries_rerun_pass=True,
                    no_unproved_sequential_invariant=True,
                    combined_edits_disjoint_and_reverse_both_orders_exactly=True,
                    added_state_bits=0, added_cycles=0,
                    qualification='Requires separate combined whole CPU regression and physical evidence',
                    evidence_sha256={str(p.relative_to(ROOT)): digest(p) for p in [Path(__file__),
                        HOLD.parent / 'lsu-implication/tb.sv', HOLD.parent / 'decode-decomposition/tb.sv',
                        HOLD.parent / 'held-bypass/tb.sv', COMBINED.parent / 'manifest.json']})
    (OUT / 'manifest.json').write_text(json.dumps(evidence, indent=2) + '\n')
    print(json.dumps(evidence, indent=2))


if __name__ == '__main__':
    main()
