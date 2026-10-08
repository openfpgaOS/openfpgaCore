#!/usr/bin/env python3
"""Independent final-fetch-only source and existing evidence review; no simulation."""
import ast
import hashlib
import json
from pathlib import Path
import re
import shutil

ROOT = Path(__file__).resolve().parents[3]
P = ROOT / 'docs/measurements/os30-fps25-20261005/phase10'
BASE = P / 'cpu-rvc-plane-v7'
CAND = P / 'cpu-fetch-v7-v12'
OUT = P / 'cpu-fetch-v7-source-review-v12'
T = ROOT / 'tools/experiments/os30_fps25_20261005'
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
read = lambda p: json.loads(p.read_text())


def main():
    assert not OUT.exists()
    parent = (BASE / 'VexiiRiscv.v').read_text()
    candidate = (CAND / 'VexiiRiscv.v').read_text()
    assert sha(BASE / 'VexiiRiscv.v') == '8fca28712f731dab4b8c81c3e57ddf2529c6bf4222c292f120e4108c758689f1'
    assert sha(CAND / 'VexiiRiscv.v') == 'ca6a2cb822978bf3ad6223397bc5622f0149a550237fb8096178a5087bc76d40'
    recipe = CAND / 'recipe.py'
    literals = {}
    for n in ast.parse(recipe.read_text()).body:
        if isinstance(n, ast.Assign):
            for target in n.targets:
                if isinstance(target, ast.Name) and target.id in ('OLD', 'NEW'):
                    literals[target.id] = ast.literal_eval(n.value)
    old, new = literals['OLD'], literals['NEW']
    assert parent.count(old) == candidate.count(new) == 1
    assert parent.replace(old, new) == candidate
    assert candidate.replace(new, old) == parent
    assert 'aligner_mask_last_update' not in candidate
    assert 'os30_wb_copy_byte' not in candidate

    def statement(text, start):
        i = text.index(start)
        return text[i:text.index(';', i) + 1]

    unchanged = [
        '(* keep *) wire fetch_ready_allow', '(* keep *) wire fetch_ready_empty',
        '(* keep *) wire dispatch_ready_early_can', 'assign when_DispatchPlugin_l373',
        'assign when_AlignerPlugin_l256',
    ]
    for start in unchanged:
        assert statement(parent, start) == statement(candidate, start)
    assert parent.count("decode_ctrls_2_down_ready = 1'b1;") == 1
    assert parent.count("decode_ctrls_2_down_ready = 1'b0;") == 1

    component = read(CAND / 'manifest.json')
    assert component['candidate_sha256'] == sha(CAND / 'VexiiRiscv.v')
    assert component['parent_manifest_sha256'] == sha(BASE / 'manifest.json')
    for name, digest in component['files'].items():
        assert sha(CAND / name) == digest, name
    prep = read(CAND / 'prepared.json')
    assert prep['candidate_sha256'] == component['candidate_sha256']
    assert prep['only_final_fetch_ready_equation_changed']
    assert prep['original_dispatch_ready_fire_issue_hazards_reservation_and_registers_unchanged']
    assert prep['added_state'] == prep['added_cycles'] == 0
    proof = read(CAND / 'proof/result.json')
    assert proof['passed'] and proof['all32768_independent_controls']
    assert proof['original_literal_early_stall_decode_ready_and_new_fetch_ready']
    assert proof['negative_missing_RS_hazard_mismatches'] == 7
    probe = (CAND / 'proof/probe.sv').read_text()
    assert 'reg decode_ctrls_2_down_ready;' in probe
    assert 'output old_ready,new_ready,negative_ready' in probe
    assert '(fetch_ready_check_rs && !(|bits[14:12]))' in probe

    result = read(CAND / 'result.json')
    assert result['passed'] and result['all10_direct8fca_UART_cycles_exact']
    assert len(result['runs']) == 10
    expected_coverage = dict(result['live_checks'])
    actual_coverage = dict.fromkeys(expected_coverage, 0)
    stalled = rs3 = 0
    evidence = [CAND / 'manifest.json', CAND / 'prepared.json', CAND / 'result.json',
                CAND / 'candidate.patch', CAND / 'VexiiRiscv.v', recipe,
                CAND / 'proof/probe.sv', CAND / 'proof/main.cpp', CAND / 'proof/result.json',
                CAND / 'native/result.json', CAND / 'native/extra/result.json',
                CAND / 'native/prepared.json', BASE / 'manifest.json', BASE / 'VexiiRiscv.v',
                P / 'compact-timing-audit-v7/manifest.json', P / 'compact-timing-audit-v7/families.json',
                Path(__file__)]
    for row in result['runs']:
        a = CAND / row['candidate_log']
        b = ROOT / row['reference_log']
        assert b.is_relative_to(BASE)
        assert sha(a) == row['candidate_sha256']
        assert sha(b) == row['reference_sha256']
        at, bt = a.read_text(), b.read_text()
        uart = lambda s: s.split('=== UART BEGIN ===')[1].split('=== UART END ===')[0]
        cycles = lambda s: int(re.search(r'cycles=(\d+) PC=', s)[1])
        assert uart(at) == uart(bt)
        assert cycles(at) == cycles(bt) == row['cycles']
        for name in actual_coverage:
            actual_coverage[name] += int(re.search(name + r'_COVER checks=(\d+)', at)[1])
        counts = re.search(r'FETCH_HAZARD_COVER checks=\d+ stalled=(\d+) rs3=(\d+)', at)
        stalled += int(counts[1])
        rs3 += int(counts[2])
        evidence += [a, b]
    assert actual_coverage == expected_coverage
    assert stalled == result['live_fetch_stall_cycles'] > 0
    assert rs3 == result['live_RS3_hazard_cycles'] > 0
    assert actual_coverage['FETCH_HAZARD'] == 14519017
    monitor_cpu = CAND / 'native/frozen/src/fpga/vendor/vexriscv/VexiiRiscv/VexiiRiscv_os30.v'
    native_prep = read(CAND / 'native/prepared.json')
    assert sha(monitor_cpu) == native_prep['sources_sha256'][str(monitor_cpu.relative_to(CAND / 'native'))]
    monitor = monitor_cpu.read_text()
    assert 'assert(fetch_logic_ctrls_3_down_ready==fetch_hazard_reference);' in monitor
    assert 'assert(decode_ctrls_2_down_ready==' in monitor
    evidence.append(monitor_cpu)

    OUT.mkdir()
    shutil.copyfile(__file__, OUT / 'recipe.py')
    review = dict(
        passed=True, candidate_sha256=sha(CAND / 'VexiiRiscv.v'), parent_sha256=sha(BASE / 'VexiiRiscv.v'),
        exact_v7_plus_final_fetch_only=True,
        original_dispatch_and_sequential_statements_byte_identical=True,
        aligner_enable_reservation_and_writeback_revisions_excluded=True,
        arbitrary_boolean_equivalence=True, literal_control_combinations=32768,
        negative_control_mismatches=7, all10_direct_v7_uart_and_cycle_comparisons_exact=True,
        live_checks=actual_coverage, live_fetch_stall_cycles=stalled, live_RS3_hazard_cycles=rs3,
        added_state=0, added_cycles=0, no_extra_simulations=True,
        timing_qualification_separate=True, generation_qualification_separate=True,
        deployment_ready=False)
    (OUT / 'review.json').write_text(json.dumps(review, indent=2) + '\n')
    (OUT / 'REPORT.txt').write_text('''Independent v7 final-fetch CPU review: PASS

The candidate ca6a2cb8 differs from exact v7 CPU8fca in one source region:
the final fetch-ready assignment gains two kept combinational early terms.
Reversing that single hunk reproduces every parent byte. No later aligner
enable, reservation, writeback DATA or other accumulated revision is present.
Every register update, reset/flush priority, data/trap capture, dispatch
ready/fire/issue equation, reservation and operand-hazard definition remains
byte-identical. No extra cycle or state is introduced.

Let V=fetch valid, A=unchanged allow, B=unchanged empty term,
G=lane selected and not sent, E=unchanged early dispatch permission, and
H=OR of the three source hazards. The sole decode-ready writer defines
D=!G||(E&&!H). Distributing F=!V||A&&(B||D) gives
(!V||A&&(B||!G))||(A&&E&&!H), exactly the new expression. The G qualification
can be omitted in the second term because the first already permits !G.
This holds for arbitrary binary inputs, including reservation, flush, fence,
freeze and halt; it assumes neither reachable-state correlations nor one-hot
controls. Four-state unknown propagation is outside the proof claim.

The literal RTL probe covers all32768 fifteen-control combinations. Its
missing-hazard negative control fails7 combinations. Ten existing actual-CPU
logs were rehashed and independently compared directly against v7 UART and
cycle counts. Live original fetch/decode-ready assertions and inherited
ready/arithmetic/decoder/forwarding checks remain enabled; fetch checks total
14,519,017 cycles with both actual fetch stalls and RS3 hazards exercised.

The v7 physical need is only three hot aligner mask/last enable endpoints:
LANE_SEL through reservation, early_can, l373 and shared fetch/capture logic.
The neighboring RS3 dependency also has little margin. The new expression
bypasses the l373 qualification for final fetch without changing its other
consumers, but routed improvement is not established by functional equality.
The exact selected CPU must still pass real generator/normal-build closure
and a separately authorized routed fit. No simulation, mapping, fitting,
production edit or device operation was performed for this review.
''')
    manifest = dict(files={str(p.relative_to(OUT)): sha(p) for p in sorted(OUT.iterdir()) if p.is_file()},
                    evidence_sha256={str(p.relative_to(ROOT)): sha(p) for p in evidence})
    (OUT / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps(dict(manifest_sha256=sha(OUT / 'manifest.json'), review_sha256=sha(OUT / 'review.json'))))


if __name__ == '__main__':
    main()
