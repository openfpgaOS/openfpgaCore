#!/usr/bin/env python3
"""Read-only source/proof review; writes only its new private review directory."""
from pathlib import Path
import hashlib
import itertools
import json
import re
import shutil

R = Path(__file__).resolve().parents[3]
P = R / 'docs/measurements/os30-fps25-20261005/phase10'
O = P / 'cpu-control-compose-source-review-v1'
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
evidence = {}


def bind(p):
    evidence[str(p.relative_to(R))] = sha(p)


def manifest(label, expected):
    p = P / label / 'manifest.json'
    assert sha(p) == expected, p
    d = json.loads(p.read_text())
    assert d['passed']
    bind(p)
    for name, digest in d.get('evidence_sha256', {}).items():
        q = R / name
        assert sha(q) == digest, q
        bind(q)
    for name, digest in d.get('files', {}).items():
        q = p.parent / name
        assert sha(q) == digest, q
        bind(q)
    return d


def main():
    manifest('cpu-control-compose-v1', 'f37b54462c09a97aca6df36e71d71e8f225f27c8cb58987c7afa4dbee4e19228')
    components = [
        ('cpu-btb-balanced-v2', 'c6b01ce1d6c27f3e052bae59733f69bc67b192cc79f9117445d199b88b27f9c0', 'hunk.json'),
        ('cpu-load-sign-v1', '0ac352f65916e15244afac5c34fdd07a8370f9aa1e5ff10e56fed1cecb153adb', 'hunk.json'),
        ('cpu-fp-rs3-tag-v1', '29e15fb89efbfeb71a570e4d9567988e2db346b36387cce833e8ac173a552c50', 'hunks.json'),
        ('cpu-decode-ready-v2', '0ed91703067b13620e7bdef971839580f7cd5977533b10a68b3e45c568fd2cf9', 'hunk.json'),
    ]
    for label, digest in [
        ('cpu-load-sign-source-review-v1', '1a365dbe68cf31a1761c7977a1fec9c284b13ae5f3c6ce35085bcc47a0111336'),
        ('cpu-fp-rs3-tag-source-review-v1', '2044aef951f6860a6a043db81930275bd1ea7bdcfb0e81285e83b7fe7f444274'),
        ('cpu-decode-ready-source-review-v2', 'fc32ef366b42723025792ba66ed1c9d2c3550d099da67781cd638fb9a567a8dd'),
    ]:
        manifest(label, digest)
    parent_path = P / 'cpu-load-forward-v1/VexiiRiscv.v'
    candidate_path = P / 'cpu-control-compose-v1/VexiiRiscv.v'
    assert sha(parent_path) == '82b7e440177b195cbe84f5679c7e88c44b5df2aae004f798c63b4adc3b926534'
    assert sha(candidate_path) == '878e6978d7bceb10c9c5323c983c025f32eff2fdde4e24369c4e114d7edf230e'
    parent = parent_path.read_text()
    candidate = candidate_path.read_text()
    hunks, ranges = [], []
    for label, digest, hname in components:
        manifest(label, digest)
        entries = json.loads((P / label / hname).read_text())
        if 'old' in entries:
            entries = {'only': entries}
        local = parent
        for name, h in entries.items():
            assert parent.count(h['old']) == 1
            start = parent.index(h['old'])
            ranges.append((start, start + len(h['old']), label, name))
            local = local.replace(h['old'], h['new'], 1)
            hunks.append(h)
            # These replacements introduce no edge-triggered storage or memory.
            assert not re.search(r'\b(?:posedge|negedge|initial|reg)\b', h['new'])
        assert local == (P / label / 'VexiiRiscv.v').read_text(), label
    assert len(hunks) == 5
    ordered = sorted(ranges)
    assert all(a[1] <= b[0] for a, b in zip(ordered, ordered[1:]))
    # Every application order gives the exact frozen candidate; every reverse
    # order restores the parent. This checks both insertion-name uniqueness
    # and absence of an unintended intermediate source dependency.
    for order in itertools.permutations(range(5)):
        text = parent
        for index in order:
            h = hunks[index]
            assert text.count(h['old']) == 1
            text = text.replace(h['old'], h['new'], 1)
        assert text == candidate
        for index in order:
            h = hunks[index]
            assert text.count(h['new']) == 1
            text = text.replace(h['new'], h['old'], 1)
        assert text == parent
    branch = hunks[0]['new']
    lhs = 'execute_ctrl2_down_Prediction_ALIGNED_JUMPED_PC_lane0'
    rhs = 'execute_ctrl2_down_early0_BranchPlugin_pcCalc_PC_TRUE_lane0'
    for i in range(8):
        assert f'os30_btb_target_eq4[{i}] = ({lhs}[{4*i+3}:{4*i}] == {rhs}[{4*i+3}:{4*i}]);' in branch
    assert 'os30_btb_target_eq16[0] = &os30_btb_target_eq4[3:0];' in branch
    assert 'os30_btb_target_eq16[1] = &os30_btb_target_eq4[7:4];' in branch
    assert 'BAD_TARGET_lane0 = !(&os30_btb_target_eq16);' in branch
    declarations = re.sub(r'/\*.*?\*/', '', parent, flags=re.S)
    for signal in (lhs, rhs):
        assert re.search(r'\b(?:wire|reg)\s+\[31\s*:\s*0\]\s+' + signal + r'\s*;', declarations)
    branch_result = json.loads((P / 'cpu-btb-balanced-v2/result.json').read_text())
    assert branch_result['passed'] and branch_result['checks'] == 792576
    assert branch_result['negative_control_failures'] == 33276
    lint = (P / 'cpu-control-compose-v1/lint.log').read_text()
    assert '%Error' not in lint
    bind(Path(__file__))
    result = dict(
        passed=True, source_composition_only=True,
        parent_sha256=sha(parent_path), candidate_sha256=sha(candidate_path),
        components=4, literal_hunks=5, disjoint_parent_ranges=ordered,
        all_120_forward_and_reverse_orders_exact=True,
        component_sources_and_proofs_rehashed=True,
        branch_comparison_covers_all_32_bits=True,
        branch_literal_checks=792576, branch_negative_controls=33276,
        registers_added=0, cycles_added=0,
        arithmetic_and_sequential_logic_outside_hunks_byte_identical=True,
        tests_or_CAD_repeated=False, native_integration_pending=True,
        physical_mapping_and_timing_unmeasured=True,
        deployment_ready=False, production_modified=False,
        evidence_hashes_checked=len(evidence),
        limits=[
            'This review checks source composition and existing component evidence, not final native or physical acceptance.',
            'Component proofs cover defined two-state inputs; no X/Z behavior claim is added.',
            'Combinational keep attributes may affect physical area and routing; no timing or area improvement is established here.',
        ],
    )
    O.mkdir(exist_ok=False)
    shutil.copyfile(__file__, O / 'recipe.py')
    (O / 'review.json').write_text(json.dumps(result, indent=2, sort_keys=True) + '\n')
    (O / 'REPORT.txt').write_text(
        'PASS: exact four-component, five-hunk composition over CPU82b7.\n'
        'All120 source application and removal orders reconstruct the same candidate/parent.\n'
        'The branch reduction compares all32 bits in disjoint four-bit groups; its target adder, prediction and control remain unchanged.\n'
        'The sign factor is independently reviewed by Geometry; RS3 and ready source gates are bound here.\n'
        'No new state or cycles. No duplicate simulation, build, map or fitter run.\n'
        'Combined native evidence and physical qualification remain separate pending gates.\n')
    files = {p.name: sha(p) for p in O.iterdir() if p.is_file()}
    (O / 'manifest.json').write_text(json.dumps(dict(passed=True,
        deployment_ready=False, files=files, evidence_sha256=evidence), indent=2, sort_keys=True) + '\n')
    print(json.dumps(result, indent=2))
    print('MANIFEST', sha(O / 'manifest.json'))


if __name__ == '__main__':
    main()
