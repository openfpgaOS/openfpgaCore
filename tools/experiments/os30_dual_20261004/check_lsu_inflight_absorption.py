#!/usr/bin/env python3
"""Prove LSU flush is absorbed by execute pending at inflightTrap only."""
from pathlib import Path
import hashlib
import importlib.util
import json
import re
import subprocess

ROOT = Path(__file__).resolve().parents[3]
ART = ROOT / 'docs/measurements/os30-dual-20261004'
SRC = ART / 'match-preselect-all-v1/immediate/VexiiRiscv.v'
OUT = ART / 'lsu-inflight-absorption-audit-v1'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    assert digest(SRC) == '0b4b291a6d8d13d9e4b9fe1ba3eb58a2bf2e69f73308b8d936bcfee25e05e846'
    src = SRC.read_text()
    checker = ROOT / 'tools/check_vexii_os20_timing.py'
    spec = importlib.util.spec_from_file_location('checker', checker)
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    inputs = ['execute_ctrl4_up_LANE_SEL_lane0', 'execute_ctrl4_down_AguPlugin_SEL_lane0',
              'LsuPlugin_logic_onCtrl_lsuTrap', 'execute_ctrl4_up_TRAP_lane0']
    for stage in range(1, 4):
        inputs += [f'execute_ctrl{stage}_up_LANE_SEL_lane0', f'execute_ctrl{stage}_down_TRAP_lane0']
    other_flush = ['DecoderPlugin_logic_laneLogic_1_flushPort_valid',
                   'DecoderPlugin_logic_laneLogic_0_flushPort_valid',
                   'early0_EnvPlugin_logic_flushPort_valid', 'CsrAccessPlugin_logic_flushPort_valid',
                   'early0_BranchPlugin_logic_flushPort_valid']
    inputs += other_flush
    inputs += ['decode_skid_gate_up_valid', 'decode_skid_gate_up_LANE_SEL_0',
               'decode_skid_gate_up_LANE_SEL_1', 'decode_skid_gate_up_TRAP_0', 'decode_skid_gate_up_TRAP_1']
    outputs = ['LsuPlugin_logic_flushPort_valid', 'execute_lane0_logic_trapPending', 'decode_logic_skid_trapPending']
    # The shared extractor accepts whole-vector assignment syntax. This
    # one-bit vector index normalization changes no expression or width.
    bit_assign = '  assign execute_lane0_logic_trapPending[0] = '
    assert src.count(bit_assign) == 1
    normalized = src.replace(bit_assign, '  assign execute_lane0_logic_trapPending = ', 1)
    cone, width = helper.cone(normalized, 'actual', inputs, outputs)
    assert len(inputs) == 20 and width == 3
    assert 'execute_ctrl4_up_LANE_SEL_lane0 && 1\'b1) && execute_ctrl4_down_TRAP_lane0' in cone
    tb = cone + '\nmodule tb;\n'
    tb += '\n'.join('reg ' + name + ';' for name in inputs) + '\nreg other_pending;\nwire [2:0] actual_out;\n'
    tb += 'actual dut(' + ','.join(inputs + ['actual_out']) + ');\n'
    tb += 'wire relaxed_skid = decode_skid_gate_up_valid && !(' + ' || '.join(other_flush) + ') && ((decode_skid_gate_up_LANE_SEL_0 && decode_skid_gate_up_TRAP_0) || (decode_skid_gate_up_LANE_SEL_1 && decode_skid_gate_up_TRAP_1));\n'
    tb += 'wire original_inflight = actual_out[1] || other_pending || actual_out[0];\n'
    tb += 'wire proposed_inflight = actual_out[1] || other_pending || relaxed_skid;\n'
    tb += 'initial begin\n for(integer bits=0;bits<(1<<21);bits++) begin\n'
    tb += '{' + ','.join(inputs + ['other_pending']) + "}=21'(bits);\n"
    tb += '''#1;
  if(actual_out[2] && !actual_out[1]) $fatal(1,"LSU flush does not imply execute pending: %x",bits);
  if(original_inflight !== proposed_inflight) $fatal(1,"Aggregate mismatch: %x",bits);
 end
 $display("PASS: actual LSU/execute/skid cones, all2097152 control combinations; F=>E and aggregate absorption exact");
 $finish;
end
endmodule
'''
    OUT.mkdir(exist_ok=False)
    (OUT / 'tb.sv').write_text(tb)
    with (OUT / 'build.log').open('w') as log:
        subprocess.run(['verilator', '--binary', '--timing', '--top-module', 'tb',
                        '-Wno-fatal', '--Mdir', str(OUT / 'obj'), '-j', '2', str(OUT / 'tb.sv')],
                       check=True, stdout=log, stderr=subprocess.STDOUT)
    assert not re.search(r'%Warning-(IMPLICIT|UNDRIVEN|LATCH)', (OUT / 'build.log').read_text())
    with (OUT / 'proof.log').open('w') as log:
        subprocess.run([str(OUT / 'obj/Vtb')], check=True, stdout=log, stderr=subprocess.STDOUT)
    evidence = dict(reference_sha256=digest(SRC), source_path=str(SRC.relative_to(ROOT)),
                    script_sha256=digest(Path(__file__)), exact_extracted_cone_sha256=digest(OUT / 'tb.sv'),
                    input_cutpoints=inputs, exhaustive_states=2097152,
                    lsu_flush_implies_execute_pending=True,
                    removing_lsu_cancel_only_at_combined_inflight_trap_is_equivalent=True,
                    proof_scope='Combinational aggregate identity; no candidate emitted',
                    required_boundary='Keep actual skid valid, lane masks, flushing and separate pending consumers unchanged.',
                    structural_caveat='Skid UP lane values still include original flush on bypass; do not claim complete cone removal without fitted analysis.')
    (OUT / 'manifest.json').write_text(json.dumps(evidence, indent=2) + '\n')
    print((OUT / 'proof.log').read_text(), end='')


if __name__ == '__main__':
    main()
