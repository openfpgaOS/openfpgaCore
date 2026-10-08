#!/usr/bin/env python3
"""Seal exact v16 ring equality and paired native GPU replay evidence."""
import json
import shutil
from pathlib import Path
from phase10_experiment import CAMP, ROOT, sha, write

P = CAMP / 'phase10'
OUT = P / 'gpu-ring-equality-review-v16'
SOURCE = P / 'gpu-ring-equality-v16'
OLD = P / 'gpu-origin-review-v14'


def main():
    assert not (OUT / 'manifest.json').exists()
    assert not (SOURCE / 'manifest.json').exists()
    cand = SOURCE / 'gpu_core.v'
    parent = P / 'compact-lod5-core-v10/frozen/src/fpga/common/gpu_core.v'
    assert sha(cand) == '32de956cdfc6a85e627aed6772c805494c4475a92d9193ece5673d7650aff177'
    assert sha(parent) == '3c171d36bce942735040d3cdc2d96a5f9959b46b2b5c4896455d1c4763ffa4d1'
    names = ['result.json', 'chunk-result.json', 'resume-result.json',
             'queue-result.json', 'request-result.json', 'paths-result.json',
             'attribute-result.json', 'origin-result.json', 'ring-result.json',
             'boundaries-v3/result.json', 'boundaries-v3/resume-result.json']
    results = {n: json.loads((OUT / n).read_text()) for n in names}
    assert all(j['passed'] for j in results.values())
    assert len(results['resume-result.json']['exact_cycle_and_output_pairs']) == 12
    assert len(results['boundaries-v3/resume-result.json']['pairs']) == 4
    literal = json.loads((SOURCE / 'literal/result.json').read_text())
    assert literal['passed'] and literal['all_pointer_pairs'] == 2**24
    assert literal['equal_pairs'] == 4096
    assert literal['missing_high_group_negative_mismatches'] == 28672
    inputs = json.loads((OUT / 'origin-inputs.json').read_text())
    assert sha(OUT / 'model/frozen/common/gpu_core.v') == inputs['model_gpu_sha256']
    assert sha(cand) == inputs['candidate_sha256']
    assert sha(OLD / 'manifest.json') == inputs['baseline_manifest_sha256']
    counts = results['ring-result.json']['totals']
    q = results['queue-result.json']['totals']
    report = f'''Private GPU v16: parallel ring-pointer equality

PASS: the sole source substitution replaces a 12-bit pointer equality with
four kept three-bit equalities followed by a reduction AND. RING_ADDR_BITS is
fixed at 12. The group intervals are disjoint and cover all pointer bits, so
the result is exactly the original equality for every pair of two-state
pointer values. No pointer, consumer, reset, MMIO, core-top mux, register or
FSM logic changes. No functional flip-flop, state or cycle is added.

The literal RTL test extracts the actual candidate equations and the actual
parent equality. All {literal['all_pointer_pairs']:,} pointer pairs pass;
exactly {literal['equal_pairs']:,} are equal. Omitting the high group gives
{literal['missing_high_group_negative_mismatches']:,} mismatches, proving the
negative control is exercised. All wrap values are covered by enumeration.
This is a two-state equivalence proof, not an X-propagation claim.

Twelve native replay pairs and four adversarial boundary pairs match the
sealed actual GPU v14 model in color, depth and fence bytes, total/busy cycles
and batch counts. Quiet, nominal and contended memory schedules, both texture
routes, row/column, affine/perspective, blend, depth and combine cases are
covered. All inherited arithmetic, span, request and queue assertions pass.
  Live original-equality comparisons: {counts['cycles']:,}
  Empty / nonempty observations: {counts['empty']:,} / {counts['nonempty']:,}
  STATUS busy/empty output checks: {counts['status']:,}
  Pointer wrap observations: {counts['wraps']:,}
  Reset ownership observations: {counts['resets']:,}
  Queue enqueue / consume checks: {q['pushes']:,} / {q['reads']:,}
  AW ordering checks: {q['aw']:,}
  Partial-strobe records: {q['partial']:,}

The functional candidate and instrumented model are intentionally distinct.
The latter adds inherited simulation-only operand shadows and passive ring
equality/status checks; monitor state never feeds functional logic. Both
sources and their derivation recipes are bound. Reset coverage initializes
empty queues; no new active-reset protocol is claimed. The selected live
configuration retains GPU_WRITE_GATHER=0 and the prior Q29 setting. The
combinational equality proof itself is independent of those configurations.

The routed parent has one sub-90 MHz endpoint per slow corner, from the GPU
ring pointer through STATUS to the peripheral read register. Its mapped
equality cone has four serial LUTs. The keep-marked grouping aims to expose
parallel six-input comparisons; only a subsequent synthesis and routed result
can establish an improvement. No map, fitter, device operation or production
promotion was performed by this proof. Production and SD files are unchanged.
'''
    (OUT / 'REPORT.txt').write_text(report)
    shutil.copy2(__file__, OUT / 'seal-recipe.py')
    evidence = [p for p in OUT.rglob('*') if p.is_file() and 'obj' not in p.parts]
    evidence += [p for p in SOURCE.rglob('*') if p.is_file() and 'obj' not in p.parts]
    evidence += [OUT / 'model/obj/Vtb_gpu', SOURCE / 'literal/obj/Vtb_equality',
                 OLD / 'manifest.json', parent,
                 P / 'compact-lod5-core-v10/final-manifest.json',
                 P / 'compact-timing-audit-v10/manifest.json']
    helpers = Path(__file__).resolve().parent
    evidence += [helpers / n for n in [
        'phase10_gpu_ring_equality.py', 'phase10_gpu_ring_acceptance.py',
        'phase10_gpu_ring_seal.py', 'phase10_gpu_origin_acceptance.py',
        'phase10_gpu_attribute_acceptance.py', 'phase10_gpu_paths_acceptance.py',
        'phase10_gpu_request_acceptance.py', 'phase10_gpu_queue_acceptance.py',
        'phase10_span_resume_acceptance.py', 'phase10_span_chunk_acceptance.py',
        'phase10_span_acceptance.py']]
    assert all(p.is_file() for p in evidence)
    write(OUT / 'manifest.json', dict(
        passed=True, passed_for_resource_screen=True, deployment_ready=False,
        candidate_sha256=sha(cand), parent_sha256=sha(parent),
        exact_cycle_pixel_pairs=16, literal_pointer_pairs=2**24,
        live_ring_checks=counts, added_flip_flops=0, added_states=0, added_cycles=0,
        evidence_sha256={str(p.relative_to(ROOT)): sha(p) for p in sorted(set(evidence))}))
    write(SOURCE / 'result.json', dict(
        passed=True, candidate_sha256=sha(cand), parent_sha256=sha(parent),
        literal_pointer_pairs=2**24, exact_cycle_pixel_pairs=16,
        exact_pixels_depth_fence_and_cycles=True, deployment_ready=False,
        proof_manifest=str((OUT / 'manifest.json').relative_to(ROOT)),
        proof_manifest_sha256=sha(OUT / 'manifest.json')))
    top_files = [p for p in SOURCE.rglob('*') if p.is_file() and 'obj' not in p.parts]
    top_files += [OUT / 'manifest.json', helpers / 'phase10_gpu_ring_seal.py']
    write(SOURCE / 'manifest.json', dict(
        passed=True, candidate_sha256=sha(cand), parent_sha256=sha(parent),
        passed_for_resource_screen=True, deployment_ready=False,
        evidence_sha256={str(p.relative_to(ROOT)): sha(p) for p in sorted(set(top_files))}))
    print(json.dumps(dict(proof_manifest_sha256=sha(OUT / 'manifest.json'),
                          component_manifest_sha256=sha(SOURCE / 'manifest.json'))))


if __name__ == '__main__':
    main()
