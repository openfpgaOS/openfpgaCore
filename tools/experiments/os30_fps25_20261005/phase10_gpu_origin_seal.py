#!/usr/bin/env python3
"""Seal the private v14 zero-product invariant and paired GPU evidence."""
import json
import re
import shutil
from pathlib import Path
from phase10_experiment import CAMP, ROOT, sha, write

P = CAMP / 'phase10'
OUT = P / 'gpu-origin-review-v14'
CAND = P / 'gpu-origin-default-v14/gpu_core.v'
OLD = P / 'gpu-attribute-v13/gpu_core.v'


def main():
    assert not (OUT / 'manifest.json').exists()
    assert sha(CAND) == '3c171d36bce942735040d3cdc2d96a5f9959b46b2b5c4896455d1c4763ffa4d1'
    assert sha(OLD) == '61f4776cada85fb3d209204b82444027054f18ced59eb46e970458c783110888'
    results = {}
    for name in ['result.json', 'chunk-result.json', 'resume-result.json',
                 'queue-result.json', 'request-result.json', 'paths-result.json',
                 'attribute-result.json', 'origin-result.json',
                 'boundaries-v3/result.json', 'boundaries-v3/resume-result.json']:
        results[name] = json.loads((OUT / name).read_text())
        assert results[name]['passed'], name
    assert len(results['resume-result.json']['exact_cycle_and_output_pairs']) == 12
    assert len(results['boundaries-v3/resume-result.json']['pairs']) == 4
    inputs = json.loads((OUT / 'origin-inputs.json').read_text())
    assert sha(OUT / 'model/frozen/common/gpu_core.v') == inputs['model_gpu_sha256']
    assert sha(CAND) == inputs['candidate_sha256']
    assert sha(P / 'gpu-attribute-review-v13/manifest.json') == inputs['baseline_manifest_sha256']
    cmd = json.loads((OUT / 'model/command.json').read_text())
    assert '+define+INCLUDE_SPAN_DEDICATED_MULT' in cmd
    assert '+define+INCLUDE_SPAN_OPERAND_DEFAULT' not in cmd
    source = CAND.read_text()
    uncommented = re.sub(r'/\*.*?\*/|//[^\n]*', '', source, flags=re.S)
    actual_writers = len(re.findall(r'\bdsp_a\s*<=', uncommented))
    assert actual_writers == inputs['literal_A_writer_count'] - 1
    counts = results['origin-result.json']['totals']
    report = f'''Private GPU v14: origin operand default

PASS: 12 native replay pairs and four adversarial boundary pairs match the
actual frozen v13 model in color/depth/fence bytes, total cycles, busy cycles
and batch counts. Quiet, nominal and contended memory schedules are covered;
boundary cases exercise both texture routes, row/column, affine/perspective,
blend, depth and combine modes. All inherited span, queue, request, clamp and
attribute ownership assertions pass.

The sole functional change is the default of dsp_a, from zero to dv_du, under
INCLUDE_SPAN_DEDICATED_MULT and without INCLUDE_SPAN_OPERAND_DEFAULT. The default
dsp_b remains zero. All explicit launches, reset assignments, dsp2 operands,
other macro configurations, FSM transitions and register widths are unchanged.
Only A is changed because the measured routed endpoint is dsp_a; changing the
second multiplier would introduce additional fanout without a measured need.

Source proof, covering every B writer rather than only the rendered corpus:
* The serial span setup task writes A and B together and is excluded by the
  selected dedicated-multiplier configuration. Its other configurations retain
  the original default.
* Reset writes both operands zero. Housekeeping sets B zero. Every later live
  launch explicitly writes A on the same edge as B: UV vertex products, area
  determinant, du/dv numerators, coarse/fine scale, and origin products.
* Optional legacy transform MAC, X/Y projection and ratio variants, normal dot
  and color products also pair every B assignment with an unconditional A
  assignment in the enclosing launch branch. These excluded features receive
  source review, not live-coverage claims from this selected configuration.
* Perspective tail S/T and Z, Newton reciprocal steps, final projection,
  constant-Z step, slope divide and correction all explicitly pair A and B.
* There is no functional reader of dsp_a except the signed 32x32-to-64 product
  register. Nonblocking source order and all client arbitration remain intact.

For each clock edge, either B takes its default zero, in which case both the
old and new products are exactly zero for every 32-bit A bit pattern, or a
client writes B and the identical explicit A. Last-writer priority is unchanged.
Holding/reset branches preserve equality. Consequently the registered product
and every functional consumer are identical for all two-state integer inputs,
including signed extrema. This is not a four-state X-propagation claim.

The simulation copies every literal A assignment to an independent old-A shadow
with identical branch and NBA priority, retaining zero at the one altered default.
The shadow never feeds functional outputs. There are {actual_writers} executable A
writers across all macro branches; the textual recipe also mirrors one commented
example, explaining its reported {inputs['literal_A_writer_count']} textual matches.
At every non-reset cycle it checks exact signed64 old/new product equality,
identical A whenever B is nonzero, and zero B whenever A differs.
  Every-cycle product comparisons: {counts['cycles']:,}
  Nonzero-B identical-A checks: {counts['nonzero_b']:,}
  Changed unused A with zero B: {counts['unused_a_changed']:,}
  Origin launches: {counts['origin_launches']:,}

The frozen candidate is distinct from its instrumented simulation copy; both
hashes and the precise instrumentation recipe are bound. An initial preparation
attempt placed the simulation shadow in the wrong generate scope and did not
build. It remains archived in gpu-origin-review-v14-prepare-attempt1. Moving only
the proof declaration to module scope fixed that fixture; candidate RTL never
changed. The successful build and all comparisons are bound here.

No functional flip-flops, FSM states or cycles are added. This proof authorizes
a subsequent resource/timing screen; no map, fitter, device or full-game FPS
claim is made. Production, sibling applications and SD files remain unchanged.
'''
    (OUT / 'REPORT.txt').write_text(report)
    shutil.copy2(Path(__file__), OUT / 'seal-recipe.py')
    evidence = [p for p in OUT.rglob('*') if p.is_file() and 'obj' not in p.parts]
    evidence += [OUT / 'model/obj/Vtb_gpu', OLD,
                 P / 'gpu-attribute-review-v13/manifest.json']
    evidence += list((P / 'gpu-origin-default-v14').glob('*'))
    helpers = Path(__file__).resolve().parent
    evidence += [helpers / name for name in [
        'phase10_gpu_origin_default.py', 'phase10_gpu_origin_acceptance.py',
        'phase10_gpu_origin_seal.py', 'phase10_gpu_attribute_acceptance.py',
        'phase10_gpu_paths_acceptance.py', 'phase10_gpu_request_acceptance.py',
        'phase10_gpu_queue_acceptance.py', 'phase10_span_resume_acceptance.py',
        'phase10_span_chunk_acceptance.py', 'phase10_span_acceptance.py']]
    write(OUT / 'manifest.json', dict(
        passed=True, passed_for_resource_screen=True, deployment_ready=False,
        candidate_sha256=sha(CAND), baseline_sha256=sha(OLD),
        exact_cycle_pixel_pairs=16, added_flip_flops=0, added_states=0,
        added_cycles=0, literal_product_checks=counts,
        evidence_sha256={str(p.relative_to(ROOT)): sha(p)
                         for p in sorted(set(evidence)) if p.is_file()}))
    print(sha(OUT / 'manifest.json'))


if __name__ == '__main__':
    main()
