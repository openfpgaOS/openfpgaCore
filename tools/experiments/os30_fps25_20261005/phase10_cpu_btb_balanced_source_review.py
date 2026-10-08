#!/usr/bin/env python3
"""Independent source/provenance review only; no compiler or simulator."""
import hashlib
import json
from pathlib import Path
import re
import shutil

R = Path(__file__).resolve().parents[3]
P = R / 'docs/measurements/os30-fps25-20261005/phase10'
C = P / 'cpu-btb-balanced-v2'
O = P / 'cpu-btb-balanced-source-review-v1'
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()


def main():
    assert not O.exists()
    assert sha(C / 'manifest.json') == 'c6b01ce1d6c27f3e052bae59733f69bc67b192cc79f9117445d199b88b27f9c0'
    m = json.loads((C / 'manifest.json').read_text())
    for name, digest in m['evidence_sha256'].items():
        assert sha(R / name) == digest, name
    assert sha(C / 'baseline.v') == '82b7e440177b195cbe84f5679c7e88c44b5df2aae004f798c63b4adc3b926534'
    assert sha(C / 'VexiiRiscv.v') == '1a1eeb602b303cc4f517956cc2fc10be8f018b0156026357052e5e37ee73ed9c'
    a, b = (C / 'baseline.v').read_text(), (C / 'VexiiRiscv.v').read_text()
    h = json.loads((C / 'hunk.json').read_text())
    assert a.count(h['old']) == b.count(h['new']) == 1
    assert a.replace(h['old'], h['new'], 1) == b
    assert b.replace(h['new'], h['old'], 1) == a
    unchanged = lambda s: re.findall(r'^\s*(?:input|output|inout|reg|parameter|localparam)\s+.*$', s, re.M)
    assert unchanged(a) == unchanged(b)
    left = 'execute_ctrl2_down_Prediction_ALIGNED_JUMPED_PC_lane0'
    right = 'execute_ctrl2_down_early0_BranchPlugin_pcCalc_PC_TRUE_lane0'
    target = 'execute_ctrl2_down_early0_BranchPlugin_logic_alu_btb_BAD_TARGET_lane0'
    for i in range(8):
        part = f'{4*i+3}:{4*i}'
        assert h['new'].count(f'assign os30_btb_target_eq4[{i}] = ({left}[{part}] == {right}[{part}]);') == 1
    assert 'assign os30_btb_target_eq16[0] = &os30_btb_target_eq4[3:0];' in h['new']
    assert 'assign os30_btb_target_eq16[1] = &os30_btb_target_eq4[7:4];' in h['new']
    assert f'assign {target} = !(&os30_btb_target_eq16);' in h['new']
    proof = (C / 'literal/proof.v').read_text()
    for key, mod in [('old', 'btb_old'), ('new', 'btb_new')]:
        literal = h[key].replace(left, 'a').replace(right, 'b').replace(target, 'y')
        body = re.search(r'module ' + mod + r'\(.*?endmodule', proof, re.S).group()
        assert literal in body
    result = json.loads((C / 'result.json').read_text())
    assert result == json.loads((C / 'proof.log').read_text().strip().splitlines()[-1])
    assert result['passed'] and result['checks'] == 792576 and result['negative_control_failures'] == 33276
    assert '%Error' not in (C / 'lint.log').read_text()
    facts = [
        'Exactly one reversible combinational BAD_TARGET assignment changes. PC target arithmetic, control, ports, registers and sequential updates are byte-identical to CPU82b7.',
        'Eight nonoverlapping four-bit comparisons cover all32 bits exactly once. The two four-way ANDs and final inverted AND equal32-bit inequality for every binary pair; there is no alignment, canonical-address or one-hot premise.',
        'The kept declarations are ten wire bits, not state. No latency, reset, flush, branch predictor update or exception behavior changes. Physical synthesis may preserve this reduction topology; improvement is unproven until actual map/route.',
        'Literal old/new modules contain the exact corresponding RTL with port renaming only. Bound proof checks792576 cases, including every local nibble pair and every chunk mismatch pattern; omitted-high-chunk mutant fails33276. Full CPU lint passed. Tests were not repeated by this reviewer.',
        'The earlier v1 failure was the literal wrapper module name new, a parser keyword; candidate RTL stayed identical. The complete v2 proof is the selected evidence. Arbitrary X/Z equivalence is not claimed.',
        'No blocking source finding. Native integrated behavior and physical timing remain separate pending gates.'
    ]
    O.mkdir()
    shutil.copyfile(__file__, O / 'recipe.py')
    (O / 'review.json').write_text(json.dumps(dict(passed=True, source_only=True,
        candidate_sha256=sha(C / 'VexiiRiscv.v'), component_manifest_sha256=sha(C / 'manifest.json'),
        evidence_hashes_checked=len(m['evidence_sha256']), facts=facts,
        no_new_tests=True, no_cad=True, production_modified=False, deployment_ready=False), indent=2) + '\n')
    (O / 'REPORT.txt').write_text('BTB balanced equality source review: PASS\n\n' + '\n\n'.join(facts) + '\n')
    evidence = dict(m['evidence_sha256'])
    evidence[str((C / 'manifest.json').relative_to(R))] = sha(C / 'manifest.json')
    (O / 'manifest.json').write_text(json.dumps(dict(passed=True, source_only=True, deployment_ready=False,
        files={p.name: sha(p) for p in O.iterdir() if p.is_file()}, evidence_sha256=evidence), indent=2) + '\n')
    print(sha(O / 'manifest.json'))


if __name__ == '__main__':
    main()
