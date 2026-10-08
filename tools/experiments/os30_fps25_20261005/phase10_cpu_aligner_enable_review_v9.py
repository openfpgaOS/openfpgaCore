#!/usr/bin/env python3
"""Independent source and existing-proof review of CPU v9; no simulation."""
import ast
import json
import re
from pathlib import Path
from phase10_experiment import CAMP, ROOT, sha, write

P = CAMP / 'phase10'
BASE = P / 'cpu-rvc-plane-v7'
CAND = P / 'cpu-aligner-enable-v9'
OUT = P / 'cpu-aligner-enable-review-v9'


def main():
    parent = (BASE / 'VexiiRiscv.v').read_text()
    candidate = (CAND / 'VexiiRiscv.v').read_text()
    assert sha(CAND / 'VexiiRiscv.v') == 'b511dba179677c9f2b3fe433caeb0c5399a4fe1dc688e5d85587f6888d9cbeab'
    recipe = Path(__file__).with_name('phase10_cpu_aligner_enable.py')
    constants = {}
    for node in ast.parse(recipe.read_text()).body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in ('ANCHOR', 'ADDITION', 'OLD', 'NEW'):
                    constants[target.id] = ast.literal_eval(node.value)
    assert parent.count(constants['ANCHOR']) == parent.count(constants['OLD']) == 1
    expected = parent.replace(constants['ANCHOR'], constants['ANCHOR'] + constants['ADDITION'])
    expected = expected.replace(constants['OLD'], constants['NEW'])
    assert expected == candidate == (P / 'cpu-aligner-enable-v1/VexiiRiscv.v').read_text()
    for key in ('(* keep *) wire dispatch_ready_early_can', 'assign when_DispatchPlugin_l373'):
        def block(text):
            start = text.index(key)
            return text[start:text.index(';', start) + 1]
        assert block(parent) == block(candidate)
    assert '!DispatchPlugin_logic_candidates_0_reservationHazards[0]' in block(candidate) or (
        '!DispatchPlugin_logic_candidates_0_reservationHazards[0]' in
        candidate[candidate.index('(* keep *) wire dispatch_ready_early_can'):candidate.index('assign when_DispatchPlugin_l373')])

    component = json.loads((CAND / 'manifest.json').read_text())
    assert component['candidate_sha256'] == sha(CAND / 'VexiiRiscv.v')
    assert component['parent_manifest_sha256'] == sha(BASE / 'manifest.json')
    for name, digest in component['files'].items():
        assert sha(CAND / name) == digest, name
    proof = json.loads((CAND / 'proof/result.json').read_text())
    assert proof['passed'] and proof['all_binary_state_and_control_combinations'] == 262144
    assert proof['negative_missing_halt_mismatches'] == 8192
    native = json.loads((CAND / 'native/v7-direct-comparison.json').read_text())
    assert native['passed'] and native['all10_direct_v7_comparisons']
    assert len(native['runs']) == 10
    coverage = dict(checks=0, empty_capture=0, flush=0)
    evidence = [CAND / 'manifest.json', CAND / 'prepared.json', CAND / 'result.json',
                CAND / 'proof/result.json', CAND / 'native/v7-direct-comparison.json',
                BASE / 'manifest.json', P / 'cpu-aligner-ready-second-review-v8/manifest.json',
                P / 'cpu-aligner-enable-v1/proof/result.json', recipe, Path(__file__)]
    for run in native['runs']:
        a, b = CAND / run['candidate_log'], ROOT / run['reference_log']
        assert sha(a) == run['candidate_log_sha256']
        assert sha(b) == run['reference_log_sha256']
        assert b.is_relative_to(BASE)
        at, bt = a.read_text(), b.read_text()
        uart = lambda text: text.split('=== UART BEGIN ===')[1].split('=== UART END ===')[0]
        cycles = lambda text: int(re.search(r'cycles=(\d+) PC=', text)[1])
        assert uart(at) == uart(bt)
        assert cycles(at) == cycles(bt) == run['cycles']
        row = re.search(r'ALIGNER_UPDATE_COVER checks=(\d+) empty_capture=(\d+) flush=(\d+)', at)
        assert row
        for key, value in zip(coverage, row.groups()):
            coverage[key] += int(value)
        evidence += [a, b]
    assert coverage == dict(checks=14519017, empty_capture=143788, flush=212375)
    OUT.mkdir(exist_ok=False)
    review = dict(passed=True, candidate_sha256=sha(CAND / 'VexiiRiscv.v'),
                  parent_sha256=sha(BASE / 'VexiiRiscv.v'),
                  exact_v7_plus_enable_only=True, reservation_rewrite_selected=False,
                  original_reservation_and_dispatch_equations_unchanged=True,
                  all262144_next_mask_last_states_exact=True,
                  negative_control_mismatches=8192,
                  all10_direct_v7_uart_and_cycle_comparisons_exact=True,
                  live_coverage=coverage, added_state=0, added_cycles=0,
                  no_extra_simulations=True, timing_qualification_separate=True,
                  generation_qualification_separate=True, deployment_ready=False)
    write(OUT / 'review.json', review)
    (OUT / 'REPORT.txt').write_text('''Independent CPU v9 enable-only review: PASS

The exact b511dba candidate is byte-identical to original CPU v7 plus the
previous isolated mask/last enable patch. There are exactly two source delta
regions: the kept combinational enable and grouping of the existing saved
mask/last writes. The v8 reservation bypass is absent. Both dispatch early_can
and l373, including the original reservation qualification, equal v7 exactly.

For this generated CPU, fetch/down cancellation is constant zero. Original
capture is fetchValid && allow && (emptyMask || downFire), and emptyMask
implies allow = !haltIt. Thus downFire || capture equals the new local
downFire || (fetchValid && !haltIt && emptyMask) enable. In the grouped
enabled-but-not-capturing branch, downFire must be true. Capture still wins
over consumption; later flush zeroing still wins over both. Reset is unchanged.
Trap, data and PC retain their original capture conditions. No registers,
pipeline cycles, cache/ISA features or architectural control were added.

The existing exhaustive probe covers all 262,144 arbitrary controls and saved,
incoming and used fields, including reset/flush. Removing halt qualification
causes 8,192 negative-control mismatches. The identical source binding permits
reuse of the isolated proof, and the v9 component also binds its own proof.

All ten existing v9 native outputs and total cycles were independently compared
directly with the actual v7 logs: frontend, stress, lighting, halfword and
hazards, each quiet and nominal. No transitive reference is needed here.
Original-control and mask/last shadow checks cover 14,519,017 cycles, including
143,788 captures without downFire and 212,375 flush cycles. Every component
manifest file and both sides of every native comparison were rehashed.

No blocking source or control finding. No extra simulation, CPU source edit,
mapping or fitting was performed for this review. Real generator reproduction,
normal-build closure and routed timing remain separate gates; no timing gain
or deployment readiness follows from this source proof.
''')
    write(OUT / 'manifest.json', dict(
        files={str(p.relative_to(OUT)): sha(p) for p in sorted(OUT.iterdir()) if p.is_file()},
        evidence_sha256={str(p.relative_to(ROOT)): sha(p) for p in evidence}))
    print(sha(OUT / 'manifest.json'))


if __name__ == '__main__':
    main()
