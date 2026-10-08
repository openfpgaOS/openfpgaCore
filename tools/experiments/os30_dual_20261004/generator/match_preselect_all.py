#!/usr/bin/env python3
"""Move all remaining registered FP/integer matches before source selection."""
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
REF = ART / 'src1-balance-v1/immediate/VexiiRiscv.v'
SHA = '6077cfd99d013f3d1e21892e00f3782c14f35385747f1da6cc66a6eec0e2d80c'
CHECK = ROOT / 'tools/check_vexii_os20_timing.py'
CHECK_SHA = '80a0c0e9cee4d3f7532c44d4aca198becc46c2400ebc8bfba811bd61e10f6188'
S0 = '_zz_execute_ctrl0_up_LANE_AGE_lane0'
S1 = '_zz_execute_ctrl0_up_LANE_AGE_lane0_1'
PRIOR = {('RS1', 10), ('RS2', 4), ('RS2', 7), ('RS2', 8), ('RS2', 10),
         ('RS3', 6), ('RS3', 10), ('RS3', 11)}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def prove(out, name, tb):
    directory = out / name
    directory.mkdir()
    (directory / 'tb.sv').write_text(tb)
    with (directory / 'build.log').open('w') as log:
        subprocess.run(['verilator', '--binary', '--timing', '--top-module', 'tb',
                        '-Wno-fatal', '--Mdir', str(directory / 'obj'), '-j', '2',
                        str(directory / 'tb.sv')], check=True, stdout=log, stderr=subprocess.STDOUT)
    assert not re.search(r'%Warning-(IMPLICIT|UNDRIVEN|LATCH)', (directory / 'build.log').read_text())
    with (directory / 'proof.log').open('w') as log:
        subprocess.run([str(directory / 'obj/Vtb')], check=True, stdout=log, stderr=subprocess.STDOUT)
    print((directory / 'proof.log').read_text(), end='')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ART / 'match-preselect-all-v1/immediate')
    args = parser.parse_args()
    src = REF.read_text()
    assert digest(src.encode()) == SHA
    assert digest(CHECK.read_bytes()) == CHECK_SHA

    def once(text):
        assert src.count(text) == 1, (text, src.count(text))

    aliases, muxes = [], []
    for index, name in enumerate((S0, S1)):
        line = f'  assign {name} = DispatchPlugin_logic_inserter_0_oh[{index}];'
        once(line)
        aliases.append(line)
    for rs in ('RS1', 'RS2', 'RS3'):
        for field, zero in [('RFID', "1'b0"), ('PHYS', "5'h0")]:
            up = f'execute_ctrl0_up_{rs}_{field}_lane0'
            line = f'  assign {up} = (({S0} ? DispatchPlugin_logic_candidates_0_ctx_hm_{rs}_{field} : {zero}) | ({S1} ? DispatchPlugin_logic_candidates_1_ctx_hm_{rs}_{field} : {zero}));'
            once(line)
            muxes.append(line)
            line = f'  assign execute_ctrl0_down_{rs}_{field}_lane0 = {up};'
            once(line)
            muxes.append(line)

    existing, added, changes, entries = [], [], [], []
    all_equalities = {}

    def add_equalities(prefix, rs, stage, lane, is_existing=False):
        names = []
        for candidate in range(2):
            name = f'{prefix}_candidate{candidate}'
            declaration = f'  (* keep , syn_keep *) wire {name} /* synthesis syn_keep = 1 */;'
            line = f'  assign {name} = (execute_ctrl{stage}_down_RD_PHYS_lane{lane} == DispatchPlugin_logic_candidates_{candidate}_ctx_hm_{rs}_PHYS) && (execute_ctrl{stage}_down_RD_RFID_lane{lane} == DispatchPlugin_logic_candidates_{candidate}_ctx_hm_{rs}_RFID);'
            if is_existing:
                once(declaration)
                once(line)
                existing.extend([declaration, line])
            else:
                assert not re.search(r'\b' + name + r'\b', src)
                added.extend([declaration, line])
            all_equalities[name] = line
            names.append(name)
        return names

    def selected_match(eq, stage, lane):
        producer = f'execute_ctrl{stage}_down_RD'
        return f'({S0} && {eq[0]}) || ({S1} && {eq[1]}) || ((!{S0} && !{S1}) && ({producer}_PHYS_lane{lane} == 5\'h0) && ({producer}_RFID_lane{lane} == 1\'b0))'

    def entry(name, rs, stage, lane, old, new, integer=False, unchanged=False):
        block = f'  reg {name};\n  always @(posedge clk) if(execute_ctrl0_down_isReady)\n    {name} <= {old};'
        once(block)
        assert len(re.findall(r'\b' + name + r'\s*<=', src)) == 1
        if not unchanged:
            changes.append((block, block.replace(old, new)))
        entries.append(dict(name=name, rs=rs, stage=stage, lane=lane, old=old, new=new, integer=integer))

    # Complete the FP family, preserving the already-qualified eight endpoints.
    for rs in ('RS1', 'RS2', 'RS3'):
        for stage in range(4, 13):
            prefix = f'fp_preselect_{rs}_{stage}'
            eq = add_equalities(prefix, rs, stage-1, 0, (rs, stage) in PRIOR)
            new = selected_match(eq, stage-1, 0)
            old = new if (rs, stage) in PRIOR else f'(execute_ctrl{stage-1}_down_RD_PHYS_lane0 == execute_ctrl0_down_{rs}_PHYS_lane0) && (execute_ctrl{stage-1}_down_RD_RFID_lane0 == execute_ctrl0_down_{rs}_RFID_lane0)'
            entry(f'fp_match_{rs}_{stage}', rs, stage-1, 0, old, new, unchanged=(rs, stage) in PRIOR)
            once(f'    execute_lane0_bypasser_float_{rs}_bypassEnables[{stage-4}] = (execute_ctrl{stage}_up_LANE_SEL_lane0 && execute_ctrl{stage}_up_RD_ENABLE_lane0) && fp_match_{rs}_{stage};')

    # Integer stage4/5 lane0 equality is exactly FP RS2_4/5 equality. Reuse the
    # kept wires, but never share/replace the integer previous DOWN_RD_ENABLE.
    shared = []
    for stage in range(2, 6):
        for lane in range(2):
            name = f'int_rs2_write_match_{stage}_{lane}'
            if lane == 0 and stage in (4, 5):
                eq = [f'fp_preselect_RS2_{stage}_candidate{index}' for index in range(2)]
                assert all(n in all_equalities for n in eq)
                shared.append(dict(integer_match=name, equality_wires=eq))
            else:
                eq = add_equalities(f'int_rs2_preselect_{stage}_{lane}', 'RS2', stage-1, lane)
            enable = f'execute_ctrl{stage-1}_down_RD_ENABLE_lane{lane}'
            old = f'{enable} && (execute_ctrl{stage-1}_down_RD_PHYS_lane{lane} == execute_ctrl0_down_RS2_PHYS_lane0) && (execute_ctrl{stage-1}_down_RD_RFID_lane{lane} == execute_ctrl0_down_RS2_RFID_lane0)'
            new = f'{enable} && ({selected_match(eq, stage-1, lane)})'
            entry(name, 'RS2', stage-1, lane, old, new, integer=True)
            once(f'    execute_lane0_bypasser_integer_RS2_bypassEnables[{(stage-2)*2+lane}] = execute_ctrl{stage}_up_LANE_SEL_lane{lane} && {name};')

    assert len(entries) == 35 and len(changes) == 27
    assert len(existing) == 32 and len(added) == 100 and len(all_equalities) == 66
    added_text = '\n'.join(added) + '\n'
    anchor = '  reg int_rs2_write_match_2_0;\n'
    once(anchor)
    candidate = src.replace(anchor, added_text + anchor, 1)
    for old, new in changes:
        candidate = candidate.replace(old, new, 1)
    reverse = candidate.replace(added_text, '', 1)
    for old, new in changes:
        reverse = reverse.replace(new, old, 1)
    assert reverse == src
    assert len(re.findall(r'^  reg fp_match_', candidate, re.M)) == 27
    assert len(re.findall(r'^  reg int_rs2_write_match_', candidate, re.M)) == 8

    # Audit actual expression leaves before driving the parallel exhaustive
    # harness. Sharing test values across endpoints is sound only after checking
    # every endpoint refers to exactly its own producer/candidate/enable inputs.
    drivers = {}
    for line in muxes + list(all_equalities.values()):
        match = re.fullmatch(r'  assign (\w+) = (.*);', line)
        assert match and match[1] not in drivers
        drivers[match[1]] = match[2]

    def leaves(expression, expected, active=None):
        active = set() if active is None else active
        tokens = set(re.findall(r'\b[A-Za-z_]\w*\b', re.sub(r"\d+'[bdh][0-9a-fA-F_xXzZ]+", '', expression)))
        found = set()
        for token in tokens:
            if token in expected:
                found.add(token)
            else:
                assert token in drivers and token not in active, token
                found |= leaves(drivers[token], expected, active | {token})
        return found

    audit = []
    for e in entries:
        expected = {S0, S1}
        expected |= {f'execute_ctrl{e["stage"]}_down_RD_{field}_lane{e["lane"]}' for field in ('PHYS', 'RFID')}
        expected |= {f'DispatchPlugin_logic_candidates_{index}_ctx_hm_{e["rs"]}_{field}' for index in range(2) for field in ('PHYS', 'RFID')}
        if e['integer']:
            expected.add(f'execute_ctrl{e["stage"]}_down_RD_ENABLE_lane{e["lane"]}')
        for text, field in ((src, 'old'), (candidate, 'new')):
            actual = re.findall(r'^    ' + re.escape(e['name']) + r' <= ([^;]+);$', text, re.M)
            assert actual == [e[field]]
            assert leaves(actual[0], expected) == expected, (e['name'], field)
        audit.append(dict(endpoint=e['name'], exact_leaves=sorted(expected)))

    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    spec = importlib.util.spec_from_file_location('dispatch_checker', CHECK)
    checker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checker)
    freeze = '  (* maxfan = 16 *) wire execute_freeze_valid;'
    once(freeze)
    plain = src.replace(freeze, '  wire execute_freeze_valid;', 1)
    prove(out, 'dispatch', checker.dispatch_tb(plain, plain))

    tb = 'module tb;\nreg [5:0] rd, c0, c1; reg rd_enable; reg [1:0] DispatchPlugin_logic_inserter_0_oh;\n'
    tb += f'wire {S0}, {S1};\n' + '\n'.join(aliases) + '\n'
    for rs in ('RS1', 'RS2', 'RS3'):
        for field, width, bit in [('PHYS', '[4:0]', '[4:0]'), ('RFID', '', '[5]')]:
            for index in range(2):
                tb += f'wire {width} DispatchPlugin_logic_candidates_{index}_ctx_hm_{rs}_{field}=c{index}{bit};\n'
            for side in ('up', 'down'):
                tb += f'wire {width} execute_ctrl0_{side}_{rs}_{field}_lane0;\n'
    for stage, lane in sorted({(e['stage'], e['lane']) for e in entries}):
        tb += f'wire [4:0] execute_ctrl{stage}_down_RD_PHYS_lane{lane}=rd[4:0];\n'
        tb += f'wire execute_ctrl{stage}_down_RD_RFID_lane{lane}=rd[5];\n'
        if stage < 5:
            tb += f'wire execute_ctrl{stage}_down_RD_ENABLE_lane{lane}=rd_enable;\n'
    tb += '\n'.join(muxes + existing + added) + '\nwire [34:0] original, result;\n'
    for index, e in enumerate(entries):
        tb += f'assign original[{index}]={e["old"]};\nassign result[{index}]={e["new"]};\n'
    tb += '''initial begin
  for(integer ids=0;ids<(1<<18);ids++) begin
    {rd,c0,c1}=18'(ids);
    for(integer control=0;control<6;control++) begin
      DispatchPlugin_logic_inserter_0_oh=2'(control/2);
      rd_enable=control[0];
      #1;
      if(original !== result) $fatal(1,"Preselect-all mismatch IDs=%x control=%x old=%x new=%x",ids,control,original,result);
    end
  end
  $display("PASS: all35 actual match equations, 1572864 cases per endpoint (all six-bit producer/candidate IDs, zero/one-hot selection, both RD_ENABLE states)");
  $finish;
end
endmodule
'''
    prove(out, 'matches', tb)
    (out / 'VexiiRiscv.v').write_text(candidate)
    shutil.copy2(__file__, out / 'match_preselect_all.py')
    shutil.copy2(CHECK, out / 'check_vexii_os20_timing.py')
    (out / 'preselect-all.patch').write_text(''.join(difflib.unified_diff(src.splitlines(True), candidate.splitlines(True), fromfile='a/VexiiRiscv.v', tofile='b/VexiiRiscv.v')))
    (out / 'endpoint-input-audit.json').write_text(json.dumps(audit, indent=2) + '\n')
    manifest = dict(reference=str(REF.relative_to(ROOT)), reference_sha256=SHA,
                    candidate_sha256=digest(candidate.encode()), script_sha256=digest(Path(__file__).read_bytes()),
                    checker_sha256=CHECK_SHA, completed_fp_matches=27, integer_rs2_matches=8,
                    changed_fp_matches=19, changed_integer_matches=8, existing_eight_fp_rhs_unchanged=True,
                    new_register_bits=0, pipeline_cycles_added=0, new_early_equality_wires=50,
                    total_early_equality_wires=66, shared_integer_fp_equalities=shared,
                    dispatch_states_exhausted=1<<21, cases_per_endpoint=6*(1<<18),
                    exact_endpoint_input_dependencies_checked=True, previous_down_rd_enable_preserved=True,
                    all_current_valid_write_enable_gates_unchanged=True, all_ff_enables_unchanged=True,
                    all_other_rtl_exactly_unchanged=True, whole_cpu_validation='pending')
    (out / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps(manifest, indent=2))


if __name__ == '__main__':
    main()
