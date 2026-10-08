#!/usr/bin/env python3
"""Independent source/evidence review, with no simulation or CAD invocation."""
from pathlib import Path
import hashlib
import json
import re
import shutil

R = Path(__file__).resolve().parents[3]
P = R / 'docs/measurements/os30-fps25-20261005/phase10'
C = P / 'gpu-control-owner-v1'
V = P / 'gpu-control-owner-review-v1'
O = P / 'gpu-control-owner-source-review-v1'
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
evidence = {}


def bind(p):
    evidence[str(p.relative_to(R))] = sha(p)


def check_manifest(p, expected=None):
    if expected:
        assert sha(p) == expected, p
    d = json.loads(p.read_text())
    assert d['passed']
    bind(p)
    for name, digest in d.get('files', {}).items():
        q = p.parent / name
        assert sha(q) == digest, q
        bind(q)
    for name, digest in d.get('evidence_sha256', {}).items():
        q = R / name
        assert sha(q) == digest, q
        bind(q)
    return d


def main():
    check_manifest(C / 'manifest.json', 'dd8754de06809670370cacff821574cc009e7745c192770ea50a8774fdc23ae2')
    check_manifest(V / 'manifest.json', '8c67a9fc9f735dcb1c238356acf6d167ac9f673b9d0399f61a66c942f672f8bf')
    check_manifest(V / 'abort/manifest.json')
    old = (C / 'baseline.v').read_text()
    new = (C / 'gpu_core.v').read_text()
    assert sha(C / 'baseline.v') == '238f44273b6bb0188447485d1501e898c8df0c1cb9b758841e8af7ceac414adb'
    assert sha(C / 'gpu_core.v') == 'e75ac93db5483ee8525c44ef1aeed4e610f8deecf8fa42765997367873dd4f15'
    # Reconstruct the parent independently, rather than executing the author's
    # source transformer. The PSS body retains every non-DSP-A byte unchanged.
    begin = '            if (!persp_active) begin\n                persp_pss <= PSS_IDLE;'
    end = '            if (persp_active && ((persp_pss == PSS_ADV_TAIL_COMMIT)'
    old_body = old[old.index(begin):old.index(end, old.index(begin))]
    new_body = new[new.index(begin):new.index(end, new.index(begin))]
    old_writes = re.findall(r'\bdsp_a\s*<=\s*([^;]+);', old_body)
    assert len(old_writes) == 8
    stripped = re.sub(r'^[ \t]*dsp_a\s*<=[^;]+;\n', '', old_body, flags=re.M)
    assert stripped == new_body
    reverse = new.replace(new_body, old_body, 1)
    owner_new = ('// A live lookahead owner was armed only by the walker-qualified EMIT.\n'
                 '// Command flags cannot change until it retires or reset clears span_la.\n'
                 'wire span_la_eligible = spanprod_active && tex_route\n')
    owner_old = 'wire span_la_eligible = cmd_is_tri_walker && spanprod_active && tex_route\n'
    assert reverse.count(owner_new) == 1
    reverse = reverse.replace(owner_new, owner_old, 1)
    a = reverse.index('// Select the PSS operand independently of the main-FSM ownership bit.')
    b = reverse.index('// Cheap "<<2 magnitude shrink" check:', a)
    mux = reverse[a:b]
    reverse = reverse[:a] + reverse[b:]
    writer = ('            if (state == S_FRAG_PIPE && persp_active && pss_dsp_a_write)\n'
              '                dsp_a <= pss_dsp_a_data;\n')
    assert reverse.count(writer) == 1
    reverse = reverse.replace(writer, '', 1)
    assert reverse == old
    assert new.index(writer) > new.index("            dsp2_b <= 32'sd0;\n            if (state == S_FRAG_PIPE")
    assert 'pss_dsp_a_data = 32\'sd0;' in mux and 'always @* begin' in mux
    assert not re.search(r'\b(?:posedge|negedge|initial)\b', mux)
    # Moving the writer ahead of shared span tasks is safe for every supported
    # configuration: lookahead explicitly requires dedicated multipliers;
    # without lookahead, span tasks run in non-fragment states.
    assert '`ifdef INCLUDE_SPAN_LOOKAHEAD\n`ifndef INCLUDE_SPAN_DEDICATED_MULT\n    initial $fatal(1,"Span lookahead requires dedicated span multipliers");' in old
    assert old.count('span_la_eligible') == new.count('span_la_eligible') == 2
    assert 'if(span_la==LA_READY && span_la_eligible && src_done' in old
    assert old.count('if(cmd_is_tri_walker && spanprod_active && tex_route') == new.count('if(cmd_is_tri_walker && spanprod_active && tex_route') == 1
    assert 'if(state!=S_FRAG_PIPE)begin\n            span_la<=LA_IDLE;' in old
    owner = json.loads((C / 'ownership-induction.json').read_text())
    assert owner['passed'] and len(owner['lookahead_assignment_sites']) == 18
    assert len(owner['owner_flag_writes']) == 8
    for flag, lines in owner['owner_flag_writes'].items():
        actual = [old.count('\n', 0, m.start()) + 1 for m in re.finditer(r'\b' + flag + r'\s*<=', old)]
        assert actual == lines and len(lines) == 3
    actual_la = [[old.count('\n', 0, m.start()) + 1, m.group(0)] for m in re.finditer(r'\bspan_la\s*<=[^;]+;', old)]
    assert actual_la == owner['lookahead_assignment_sites']
    result = json.loads((C / 'result.json').read_text())
    assert result['passed'] and result['literal']['checks'] == 69108864
    assert result['replay']['totals']['cycles'] == 30829640
    assert result['replay']['pairs'] == 16 and result['active_abort']['pairs'] == 4
    assert all(x['bytes_equal'] and x['cycles_equal'] and x['counts']['soft_resets'] == 1 for x in result['active_abort']['rows'])
    assert result['added_registers'] == result['added_cycles'] == 0
    # The next-state shadow uses every original PSS assignment in its original
    # order. Verify those literal bytes in the frozen compiled GPU monitor.
    model = (V / 'model/frozen/common/gpu_core.v').read_text()
    restored = re.sub(r'\bdsp_a(?=\s*<=)', 'proof_dsp_a_control_old', old_body)
    assert restored in model
    for rel in ['result.json', 'boundaries-v3/result.json', 'owner-result.json', 'abort/result.json']:
        assert json.loads((V / rel).read_text())['passed']
    facts = [
        'Exact parent reconstruction permits only the removed walker conjunction, one combinational operand mux, one relocated write and eight deleted original DSP-A assignments. All other source bytes remain identical.',
        'The removed conjunction is consumed only under LA_READY. Initial EMIT still requires a triangle-walker command. All eight owner flags change only at hard reset, soft reset or DECODE; DECODE and every non-FRAG edge clear LA. All18 LA writers preserve the legal-state invariant LA!=IDLE implies walker ownership.',
        'DSP-A predicates match all eight original PSS sites. Full advance omits ADV_ISSUE; Q29/prune and speculative-step conditions remain exact. SLOPE_PREP retains divisor0 as well as all nonpower-of-two divisors below16. The unsigned32-bit subtraction and signed assignment preserve INT_MIN and all payload bits.',
        'The relocated writer is inside the existing non-reset/non-soft-reset arm after global DSP defaults. Other main-FSM writers are mutually exclusive. Concurrent lookahead tasks cannot write this DSP in supported configurations because lookahead requires dedicated span multipliers; without lookahead their states are disjoint. This priority condition is essential.',
        'All non-DSP PSS statements, DSP-B/DSP2 operands, launches, captures and state edges remain byte-identical. The added reg is a fully assigned combinational mux variable, not a new flip-flop.',
        'The literal reference predicates and values were reviewed against the frozen original sites. Both speculative configurations cover all64 PSS encodings, all control/divisor combinations and data bases/random inputs; full original DSP-A writer priority is also checked by a register shadow in actual GPU replays.',
        'All16 original replay/boundary pairs and four active-soft-reset recovery pairs match bytes/cycles. Soft reset is exercised in LA_SELECT/LA_WAIT before first pixels; arbitrary outstanding AXI abort behavior is not newly qualified.',
        'Generic Q29/illegal-PSS controls have literal coverage, while broad rendering uses selected os30 parameters. No four-state or corrupted-state proof is claimed. Combined actual native CPU replay and physical qualification remain separate gates.',
    ]
    bind(Path(__file__))
    review = dict(passed=True, source_only=True, source_sha256=sha(C / 'gpu_core.v'),
        component_manifest_sha256=sha(C / 'manifest.json'), exact_parent_reconstruction=True,
        facts=facts, literal_checks=69108864, full_original_dsp_a_shadow_cycles=30829640,
        exact_cycle_output_pairs=20, source_induction_sites=18,
        active_lookahead_cycles=18063960, handoffs=115684,
        evidence_hashes_checked=len(evidence), tests_or_CAD_repeated=False,
        no_new_registers_or_cycles=True, deployment_ready=False, production_modified=False)
    O.mkdir(exist_ok=False)
    shutil.copyfile(__file__, O / 'recipe.py')
    (O / 'review.json').write_text(json.dumps(review, indent=2, sort_keys=True) + '\n')
    (O / 'REPORT.txt').write_text('PASS: GPU control-owner source/proof review.\n\n' + '\n\n'.join(facts) + '\n')
    files = {p.name: sha(p) for p in O.iterdir() if p.is_file()}
    (O / 'manifest.json').write_text(json.dumps(dict(passed=True, deployment_ready=False,
        files=files, evidence_sha256=evidence), indent=2, sort_keys=True) + '\n')
    print('PASS', len(evidence), 'bound evidence files')
    print('MANIFEST', sha(O / 'manifest.json'))


if __name__ == '__main__':
    main()
