#!/usr/bin/env python3
"""Privately move eight measured FP ID comparisons before dispatch selection."""
from pathlib import Path
import argparse
import difflib
import hashlib
import importlib.util
import json
import re
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[4]
ART = ROOT / 'docs/measurements/os30-dual-20261004'
REF = ART / 'decode-ready-v1/immediate/VexiiRiscv.v'
REF_SHA = 'ff0bd8cf20e0fc0fd85ec91c70b5122a5fabd8d9a185a33b12cc40e9be1baf55'
CHECK = ROOT / 'tools/check_vexii_os20_timing.py'
CHECK_SHA = '80a0c0e9cee4d3f7532c44d4aca198becc46c2400ebc8bfba811bd61e10f6188'
TARGETS = [('RS1', 10), ('RS2', 4), ('RS2', 7), ('RS2', 8),
           ('RS2', 10), ('RS3', 6), ('RS3', 10), ('RS3', 11)]
S0 = '_zz_execute_ctrl0_up_LANE_AGE_lane0'
S1 = '_zz_execute_ctrl0_up_LANE_AGE_lane0_1'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def prove(out, name, tb):
    directory = out / name
    directory.mkdir()
    (directory / 'tb.sv').write_text(tb)
    with (directory / 'build.log').open('w') as log:
        subprocess.run(['verilator', '--binary', '--timing', '--top-module', 'tb',
                        '-Wno-fatal', '--Mdir', str(directory / 'obj'), '-j', '2',
                        str(directory / 'tb.sv')], check=True, stdout=log,
                       stderr=subprocess.STDOUT)
    # An incomplete extracted cone must not pass with implicit zero inputs.
    warnings = (directory / 'build.log').read_text()
    assert not re.search(r'%Warning-(IMPLICIT|UNDRIVEN|LATCH)', warnings), warnings
    with (directory / 'proof.log').open('w') as log:
        subprocess.run([str(directory / 'obj/Vtb')], check=True, stdout=log,
                       stderr=subprocess.STDOUT)
    print((directory / 'proof.log').read_text(), end='')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ART / 'fp-preselect-v1/immediate')
    args = parser.parse_args()
    src = REF.read_text()
    assert sha(src.encode()) == REF_SHA
    assert sha(CHECK.read_bytes()) == CHECK_SHA
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)

    def once(text):
        assert src.count(text) == 1, (text, src.count(text))

    # Pin the exact selected-ID representation, including the defined zero
    # result on no dispatch. The scheduler invariant is proved below, not assumed.
    aliases = []
    for index, name in enumerate((S0, S1)):
        line = f'  assign {name} = DispatchPlugin_logic_inserter_0_oh[{index}];'
        once(line)
        aliases.append(line)
    muxes = []
    for rs in ('RS1', 'RS2', 'RS3'):
        for field, zero in [('RFID', "1'b0"), ('PHYS', "5'h0")]:
            name = f'execute_ctrl0_up_{rs}_{field}_lane0'
            line = f'  assign {name} = (({S0} ? DispatchPlugin_logic_candidates_0_ctx_hm_{rs}_{field} : {zero}) | ({S1} ? DispatchPlugin_logic_candidates_1_ctx_hm_{rs}_{field} : {zero}));'
            once(line)
            muxes.append(line)
            line = f'  assign execute_ctrl0_down_{rs}_{field}_lane0 = {name};'
            once(line)
            muxes.append(line)

    changes, early, originals, replacements = [], [], [], []
    for rs, stage in TARGETS:
        name = f'fp_match_{rs}_{stage}'
        producer = f'execute_ctrl{stage-1}_down_RD'
        old = f'({producer}_PHYS_lane0 == execute_ctrl0_down_{rs}_PHYS_lane0) && ({producer}_RFID_lane0 == execute_ctrl0_down_{rs}_RFID_lane0)'
        block = f'  reg {name};\n  always @(posedge clk) if(execute_ctrl0_down_isReady)\n    {name} <= {old};'
        once(block)
        assert len(re.findall(r'\b' + name + r'\s*<=', src)) == 1
        equality = []
        for lane in range(2):
            eq = f'fp_preselect_{rs}_{stage}_candidate{lane}'
            candidate = f'DispatchPlugin_logic_candidates_{lane}_ctx_hm_{rs}'
            early += [f'  (* keep , syn_keep *) wire {eq} /* synthesis syn_keep = 1 */;',
                      f'  assign {eq} = ({producer}_PHYS_lane0 == {candidate}_PHYS) && ({producer}_RFID_lane0 == {candidate}_RFID);']
            equality.append(eq)
        # All match bits retain their original value, even on invalid/no-issue
        # cycles. Current bypass LANE_SEL/RD_ENABLE qualification is untouched.
        new = f'({S0} && {equality[0]}) || ({S1} && {equality[1]}) || ((!{S0} && !{S1}) && ({producer}_PHYS_lane0 == 5\'h0) && ({producer}_RFID_lane0 == 1\'b0))'
        changed = block.replace(old, new)
        changes.append((block, changed))
        originals.append(old)
        replacements.append(new)

    added = '\n'.join(early) + '\n'
    anchor = '  reg fp_match_RS1_4;\n'
    once(anchor)
    candidate = src.replace(anchor, added + anchor, 1)
    for old, new in changes:
        candidate = candidate.replace(old, new, 1)
    restored = candidate.replace(added, '', 1)
    for old, new in changes:
        restored = restored.replace(new, old, 1)
    assert restored == src
    assert len(re.findall(r'^  reg fp_match_', candidate, re.M)) == 27

    spec = importlib.util.spec_from_file_location('dispatch_proof', CHECK)
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    # Extraction only: this attributed wire is otherwise omitted by the shared
    # checker's declaration parser. No RTL or functional expression is changed.
    freeze = '  (* maxfan = 16 *) wire execute_freeze_valid;'
    once(freeze)
    reference_for_proof = src.replace(freeze, '  wire execute_freeze_valid;', 1)
    prove(out, 'dispatch', helper.dispatch_tb(reference_for_proof, reference_for_proof))

    # Copy the real emitted selected-ID drivers, original match RHS, and new
    # early comparators/RHS into the harness. The 6-bit physical+RF IDs are
    # independent arbitrary inputs. Feeding each endpoint the same exhaustive
    # values covers every endpoint's complete local truth table.
    tb = 'module tb;\nreg [5:0] rd, c0, c1; reg [1:0] DispatchPlugin_logic_inserter_0_oh;\n'
    tb += f'wire {S0}, {S1};\n' + '\n'.join(aliases) + '\n'
    for rs in ('RS1', 'RS2', 'RS3'):
        for field, width, select in [('PHYS', '[4:0]', '[4:0]'), ('RFID', '', '[5]')]:
            for lane in range(2):
                tb += f'wire {width} DispatchPlugin_logic_candidates_{lane}_ctx_hm_{rs}_{field} = c{lane}{select};\n'
            for side in ('up', 'down'):
                tb += f'wire {width} execute_ctrl0_{side}_{rs}_{field}_lane0;\n'
    for stage in sorted({stage - 1 for rs, stage in TARGETS}):
        tb += f'wire [4:0] execute_ctrl{stage}_down_RD_PHYS_lane0 = rd[4:0];\n'
        tb += f'wire execute_ctrl{stage}_down_RD_RFID_lane0 = rd[5];\n'
    tb += '\n'.join(muxes) + '\n' + added
    tb += 'wire [7:0] original, result;\n'
    for index, (old, new) in enumerate(zip(originals, replacements)):
        tb += f'assign original[{index}] = {old};\nassign result[{index}] = {new};\n'
    tb += '''initial begin
  for(integer ids=0;ids<(1<<18);ids++) begin
    {rd,c0,c1}=18'(ids);
    for(integer selector=0;selector<3;selector++) begin
      DispatchPlugin_logic_inserter_0_oh=2'(selector);
      #1;
      if(original !== result) $fatal(1,"FP preselect mismatch IDs=%x selector=%b old=%x new=%x",ids,DispatchPlugin_logic_inserter_0_oh,original,result);
    end
  end
  $display("PASS: all eight emitted match equations, 786432 cases each (all producer/candidate six-bit IDs, zero/one-hot selectors)");
  $finish;
end
endmodule
'''
    prove(out, 'matches', tb)
    (out / 'VexiiRiscv.v').write_text(candidate)
    shutil.copy2(__file__, out / 'fp_preselect.py')
    shutil.copy2(CHECK, out / 'check_vexii_os20_timing.py')
    (out / 'preselect.patch').write_text(''.join(difflib.unified_diff(src.splitlines(True), candidate.splitlines(True), fromfile='a/VexiiRiscv.v', tofile='b/VexiiRiscv.v')))
    manifest = dict(reference=str(REF.relative_to(ROOT)), reference_sha256=REF_SHA,
                    candidate_sha256=sha(candidate.encode()), script_sha256=sha(Path(__file__).read_bytes()),
                    checker_sha256=CHECK_SHA, endpoints=[f'{rs}_{stage}' for rs, stage in TARGETS],
                    new_register_bits=0, pipeline_cycles_added=0, preserved_early_comparison_wires=16,
                    dispatch_states_exhausted=1 << 21, match_cases_per_endpoint=3 * (1 << 18),
                    all_zero_one_hot_cases_exact=True, match_ff_enables_unchanged=True,
                    current_valid_write_enable_gating_unchanged=True, other_nineteen_matches_unchanged=True,
                    all_other_rtl_exactly_unchanged=True, whole_cpu_validation='pending')
    (out / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps(manifest, indent=2))


if __name__ == '__main__':
    main()
