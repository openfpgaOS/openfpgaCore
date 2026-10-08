#!/usr/bin/env python3
"""Independent literal/source audit; does not compile or simulate the candidate."""
from pathlib import Path
import difflib
import hashlib
import json
import re
import shutil

R = Path(__file__).resolve().parents[3]
P = R / 'docs/measurements/os30-fps25-20261005/phase10'
C = P / 'cpu-load-forward-v1'
O = P / 'cpu-load-forward-source-review-v1'
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()


def main():
    assert not O.exists()
    parent = C / 'baseline.v'
    candidate = C / 'VexiiRiscv.v'
    assert sha(parent) == 'aecbd7d8f63675333909df756c072b767bab269d29188190f80cfb3cde376295'
    assert sha(candidate) == '82b7e440177b195cbe84f5679c7e88c44b5df2aae004f798c63b4adc3b926534'
    manifest = json.loads((C / 'manifest.json').read_text())
    for name, digest in manifest['evidence_sha256'].items():
        assert sha(R / name) == digest, name
    old, new = parent.read_text(), candidate.read_text()
    hunks = json.loads((C / 'hunks.json').read_text())
    assert set(hunks) == {'RS1', 'RS2'}
    made = old
    reverse = new
    proof = (C / 'literal/proof.v').read_text()
    for rs in ('RS1', 'RS2'):
        a, b = hunks[rs]['old'], hunks[rs]['new']
        assert made.count(a) == reverse.count(b) == 1
        made = made.replace(a, b, 1)
        reverse = reverse.replace(b, a, 1)
        for kind, block in (('old', a), ('new', b)):
            extracted = re.search(r'module ' + kind + '_' + rs + r'\(.*?endmodule', proof, re.S).group()
            assert extracted.count(block) == 1
        when = 'when_ExecuteLanePlugin_l196' + ('_1' if rs == 'RS2' else '')
        selector = '_zz_execute_ctrl1_down_integer_' + rs + '_lane0'
        assert f'assign {when} = execute_lane0_bypasser_integer_{rs}_sel[0];' in old
        assert f'assign {selector} = execute_lane0_bypasser_integer_{rs}_sel[4 : 1];' in old
        assert f'(!{when}) && {selector}[1]' in b
        assert 'execute_ctrl4_down_lane0_integer_WriteBackPlugin_logic_DATA_lane0' not in b
        assert b.count('execute_ctrl4_up_lane0_integer_WriteBackPlugin_logic_DATA_lane0') == 1
        assert b.count('lane0_integer_WriteBackPlugin_logic_stages_2_muxed') == 1
    assert made == new and reverse == old
    assert ''.join(difflib.unified_diff(old.splitlines(True), new.splitlines(True),
        fromfile='aecb/VexiiRiscv.v', tofile='load-forward/VexiiRiscv.v')) == (C / 'candidate.patch').read_text()
    merge = [
        'assign lane0_integer_WriteBackPlugin_logic_stages_2_merged = (execute_ctrl4_up_lane0_integer_WriteBackPlugin_logic_DATA_lane0 | lane0_integer_WriteBackPlugin_logic_stages_2_muxed);',
        'assign execute_ctrl4_lane0_integer_WriteBackPlugin_logic_DATA_lane0_bypass = lane0_integer_WriteBackPlugin_logic_stages_2_merged;',
        'assign execute_ctrl4_down_lane0_integer_WriteBackPlugin_logic_DATA_lane0 = execute_ctrl4_lane0_integer_WriteBackPlugin_logic_DATA_lane0_bypass;'
    ]
    for line in merge:
        assert old.count(line) == new.count(line) == 1
    declarations = lambda s: re.findall(r'^\s*(?:input|output|inout|reg|parameter|localparam)\s+.*$', s, re.M)
    assert declarations(old) == declarations(new)
    seq = lambda s: re.findall(r'(?m)^\s*always\s*@\([^\n]*(?:posedge|negedge)[^\n]*\)', s)
    assert seq(old) == seq(new)
    result = json.loads((C / 'result.json').read_text())
    literal = json.loads((C / 'literal/result.json').read_text())
    assert result['passed'] and result['literal'] == literal
    assert literal['vectors'] == 276480 and literal['negative_control_failures'] == 65915
    assert literal['exhaustive_one_bit_vectors'] == 32 * 64
    assert literal['full_width_basis_complement_vectors'] == 32 * 192 * 2
    assert not result['passed_for_map_or_fit'] and result['native_integration_pending']
    assert '%Error' not in (C / 'lint.log').read_text()
    facts = [
        'Candidate is exactly two independently reversible RS1/RS2 forwarding-expression replacements over aecb. Original state, ports, parameters, all sequential blocks, selector generation, load formatting and architectural writeback are unchanged.',
        'The source definition of ctrl4_down DATA is exactly P4|F4 through two direct aliases. P4 is ctrl4_up DATA; F4 is the existing stage2_muxed formatter contribution. No valid or one-hot premise is needed.',
        'For each bit, let Y be the youngest override, S the ctrl4 select and O the OR of other selected contributors. Original is Y?D2:(O|(S&(P4|F4))). Candidate is (Y?D2:(O|(S&P4)))|(!Y&S&F4). Y=1 suppresses the late term; Y=0 follows Boolean distribution. This covers every binary selector and payload combination, including simultaneous selectors.',
        'The four kept declarations are wires with continuous combinational initializers, not registers. Fully assigned combinational output blocks introduce no latch, new state or cycle. Reset, freeze, flush, redirect, hazard and issue control remain byte-identical.',
        'Both old/new RS literal modules contain their exact corresponding source blocks. Existing proof covers all 2048 one-bit Boolean cases, 12288 bit-basis/complement vectors and 262144 random 32-bit vectors, checking both outputs. The deliberate missing !Y qualifier fails 65915 vectors.',
        'The exhaustive binary per-bit identity establishes full-width Boolean equivalence because these data expressions use only bitwise OR/AND and scalar selection; there is no cross-bit arithmetic. This is not a claim about arbitrary X/Z propagation.',
        'No blocking source issue. Existing literal proof and full-CPU lint bindings were rehashed without repeating execution. Native integration, synthesis resource attribution and routed timing remain separate gates; physical improvement is unproven.'
    ]
    O.mkdir()
    shutil.copyfile(__file__, O / 'recipe.py')
    review = dict(passed=True, source_only=True, exact_two_hunks=True,
        candidate_sha256=sha(candidate), parent_sha256=sha(parent),
        component_manifest_sha256=sha(C / 'manifest.json'),
        evidence_hashes_checked=len(manifest['evidence_sha256']), facts=facts,
        new_simulation=False, compilation_invoked=False, quartus_invoked=False,
        production_modified=False, native_integration_pending=True,
        deployment_ready=False)
    (O / 'review.json').write_text(json.dumps(review, indent=2) + '\n')
    (O / 'REPORT.txt').write_text('CPU load-forward independent source review: PASS\n\n' + '\n\n'.join(facts) + '\n')
    evidence = {str((C / n).relative_to(R)): sha(C / n) for n in [
        'manifest.json', 'result.json', 'prepared.json', 'candidate.patch',
        'hunks.json', 'VexiiRiscv.v', 'baseline.v', 'literal/proof.v',
        'literal/main.cpp', 'literal/result.json', 'lint.log']}
    (O / 'manifest.json').write_text(json.dumps(dict(passed=True, source_only=True,
        deployment_ready=False, files={p.name: sha(p) for p in O.iterdir() if p.is_file()},
        evidence_sha256=evidence), indent=2) + '\n')
    print(sha(O / 'manifest.json'))


if __name__ == '__main__':
    main()
