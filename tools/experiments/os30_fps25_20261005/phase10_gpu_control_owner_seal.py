#!/usr/bin/env python3
"""Seal exact GPU control component tests and source ownership induction."""
import hashlib
import json
import re
import shutil
from pathlib import Path
import phase10_gpu_control_owner as component

R, P, O = component.ROOT, component.P, component.OUT
V = P / 'gpu-control-owner-review-v1'
sha = component.sha
save = component.save
candidate = O / 'gpu_core.v'
prepared = json.loads((O / 'prepared.json').read_text())
assert sha(component.BASE) == component.BASE_SHA
assert sha(candidate) == prepared['candidate_sha256']
old = component.BASE.read_text()
new = candidate.read_text()
literal = json.loads((O / 'literal/result.json').read_text())
assert literal['passed'] and literal['checks'] == 69108864
names = ['result.json', 'resume-result.json', 'owner-result.json', 'recip-result.json',
         'origin-result.json', 'attribute-result.json', 'paths-result.json',
         'request-result.json', 'queue-result.json', 'boundaries-v3/result.json',
         'boundaries-v3/resume-result.json', 'abort/result.json']
results = {n: json.loads((V / n).read_text()) for n in names}
assert all(x['passed'] for x in results.values())
abort = json.loads((V / 'abort/manifest.json').read_text())
for path, digest in abort['files'].items():
    assert sha(V / 'abort' / path) == digest, path
for path, digest in abort['evidence_sha256'].items():
    assert sha(R / path) == digest, path

# Every owner flag has exactly reset, soft-reset and S_DECODE writers.
flags = ['cmd_is_draw_' + x for x in ('param_tri', 'vert_tri', 'param_tri_recs',
          'xform_tri', 'vert_tri_rgb', 'xform_tri_rgb', 'indexed_tri', 'clip_tri')]
main = old.index('always @(posedge clk) begin : main_fsm')
hard = old.index('    if (!reset_n) begin', main)
hard_end = old.index('    end else begin\n        // Ring reset:', hard)
soft = old.index('        if (soft_reset) begin', hard_end)
soft_end = old.index('        end else begin\n            // ------------------------------------------------------------', soft)
decode = old.index('        S_DECODE: begin', soft_end)
decode_end = old.index('        S_PAY_WAIT: begin', decode)
flag_writes = {}
for flag in flags:
    matches = list(re.finditer(r'\b' + flag + r'\s*<=', old))
    assert len(matches) == 3
    assert hard <= matches[0].start() < hard_end
    assert soft <= matches[1].start() < soft_end
    assert decode <= matches[2].start() < decode_end
    flag_writes[flag] = [old.count('\n', 0, m.start()) + 1 for m in matches]
la_writes = [(old.count('\n', 0, m.start()) + 1, m.group(0))
             for m in re.finditer(r'\bspan_la\s*<=[^;]+;', old)]
assert len(la_writes) == 18
assert old.count('wire span_la_eligible = cmd_is_tri_walker && spanprod_active && tex_route') == 1
assert new.count('wire span_la_eligible = spanprod_active && tex_route') == 1
assert old.count('if(cmd_is_tri_walker && spanprod_active && tex_route') == 1
assert new.count('if(cmd_is_tri_walker && spanprod_active && tex_route') == 1
# Exhaust the abstract induction step, preserving legal scalar transition
# categories established by the source-site census and reviewed state cases.
steps = 0
for live in (False, True):
    for owner in (False, True):
        if live and not owner:
            continue
        for state in ('fragment', 'emit', 'decode', 'other'):
            for reset in ('hard', 'soft', 'none'):
                for control in range(8):
                    if reset != 'none':
                        next_live = next_owner = False
                    elif state == 'decode':
                        next_live, next_owner = False, bool(control & 1)
                    elif state == 'emit':
                        next_live, next_owner = owner and bool(control & 1), owner
                    elif state == 'fragment':
                        next_live, next_owner = live and bool(control & 1), owner
                    else:
                        next_live, next_owner = False, owner
                    assert not next_live or next_owner
                    steps += 1
save(O / 'ownership-induction.json', dict(passed=True, source_sha256=sha(component.BASE),
    owner_flag_writes=flag_writes, lookahead_assignment_sites=la_writes,
    abstract_transition_cases=steps,
    invariant='span_la != LA_IDLE implies cmd_is_tri_walker for every legally initialized state',
    argument=[
        'Hard and soft reset clear both lookahead state and all eight command-owner flags.',
        'All eight command flags have no writers outside those resets and S_DECODE. The global state!=S_FRAG_PIPE assignment clears lookahead on the same edge as S_DECODE changes them.',
        'The only transition from idle lookahead to a live phase is the S_SPANPROD_EMIT arming block, which still requires cmd_is_tri_walker. State EMIT cannot concurrently execute S_DECODE.',
        'Inside S_FRAG_PIPE, LA_FILL/SELECT/SETUP/WAIT/CAPTURE and the LA_READY handoff can advance only an already live lookahead owner. LA_IDLE default never arms. Command flags hold throughout these edges.',
        'Other states clear lookahead. Arbitrary main-state transitions preserve the argument because the owner flags hold until the non-fragment S_DECODE edge clears lookahead.',
        'Consequently the removed tri-owner factor is true whenever the sole consumer tests LA_READY; unsupported/wrong-size commands cannot arm lookahead and retain their original fallback.'],
    limits=['The abstract transition checker formalizes this manually reviewed source induction; it is not a general RTL formal proof or a proof for corrupted/uninitialized state. Live assertions and actual MMIO abort/recovery are separate evidence.']))

(O / 'REPORT.txt').write_text('''GPU control owner component: PASS for composition/native validation.
Source e75ac93d is based only on238f4427. No register state, cycle, transaction,
clock or parameter change. Remove only the redundant walker-owner conjunction
inside lookahead eligibility; initial arming still requires it. Source induction
accounts for every lookahead and owner-flag writer. Every active-owner and
handoff edge is checked against the original predicate in the live GPU model.

DSP-A data is selected before final fragment/perspective ownership. All eight
original write predicates and expressions, existing defaults, hard/soft reset,
and other main-FSM writers remain exact. 69,108,864 literal checks cover all64
PSS codes, all control/divisor combinations,64 payload bases and random full
data for both speculative-step configurations. A simulation-only original
DSP-A shadow reconstructs every original assignment and its actual priority;
30,829,640 replay cycles agree exactly, including the unchanged default.

The16 original replay/boundary pairs preserve every output and cycle. They
check18,063,960 active-lookahead cycles,115,684 handoffs,1,206,228 PSS writes
and25,797 flush cycles. Four additional baseline/candidate pairs issue real
MMIO soft reset in active LA_SELECT/LA_WAIT, then unsupported/wrong-size
commands and a valid draw/fence; all output bytes and cycle counts agree.
Hard reset is exercised by all runs; arbitrary in-flight AXI abort semantics
are not claimed. Generic Q29/illegal-PSS behavior is literal-tested, while
rendering tests use the selected os30 features. No native CPU composition,
mapping, fit, timing improvement or deployment is qualified by this component.
''')
save(O / 'result.json', dict(passed=True, candidate_sha256=sha(candidate),
    added_registers=0, added_cycles=0, literal=literal,
    replay=results['owner-result.json'], active_abort=results['abort/result.json'],
    exact_cycle_output_pairs=20, resource_map_qualified=False, timing_qualified=False,
    requires_combined_native=True, production_modified=False))
shutil.copy2(__file__, O / 'seal-recipe.py')
refs = [Path(__file__), Path(component.__file__), component.BASE,
        R / 'tools/experiments/os30_fps25_20261005/phase10_gpu_control_owner_acceptance.py',
        R / 'tools/experiments/os30_fps25_20261005/phase10_gpu_control_owner_abort.py',
        P / 'gpu-recip-write-v1/manifest.json',
        P / 'gpu-recip-write-review-v2/manifest.json',
        P / 'gpu-origin-review-v14/manifest.json']
for name in ('gpu_recip_acceptance', 'gpu_origin_acceptance', 'gpu_attribute_acceptance',
             'gpu_paths_acceptance', 'gpu_request_acceptance', 'gpu_queue_acceptance',
             'span_resume_acceptance', 'span_chunk_acceptance', 'span_acceptance'):
    refs.append(R / ('tools/experiments/os30_fps25_20261005/phase10_' + name + '.py'))
save(V / 'manifest.json', dict(passed=True,
    files={str(p.relative_to(V)): sha(p) for p in V.rglob('*') if p.is_file() and 'obj' not in p.parts},
    evidence_sha256={str(p.relative_to(R)): sha(p) for p in refs + [V / 'model/obj/Vtb_gpu']}))
save(O / 'manifest.json', dict(passed=True,
    files={str(p.relative_to(O)): sha(p) for p in O.rglob('*') if p.is_file() and 'obj' not in p.parts},
    evidence_sha256={str((V / 'manifest.json').relative_to(R)): sha(V / 'manifest.json')}))
print('GPU', sha(candidate))
print('MANIFEST', sha(O / 'manifest.json'))
