#!/usr/bin/env python3
"""Seal combined v13 proof against frozen actual v11, without a fit claim."""
import json,shutil
from pathlib import Path
from phase10_experiment import CAMP,ROOT,sha,write
P=CAMP/'phase10';OUT=P/'gpu-attribute-review-v13';CAND=P/'gpu-attribute-v13/gpu_core.v';OLD=P/'gpu-paths-review-v11'
def main():
 assert not(OUT/'manifest.json').exists()
 assert sha(CAND)=='61f4776cada85fb3d209204b82444027054f18ced59eb46e970458c783110888'
 assert sha(OUT/'model/frozen/common/gpu_core.v')==sha(CAND)
 names=['result.json','chunk-result.json','resume-result.json','queue-result.json','request-result.json','paths-result.json','attribute-result.json','boundaries-v3/result.json','boundaries-v3/resume-result.json']
 results={n:json.loads((OUT/n).read_text())for n in names};assert all(r['passed']for r in results.values())
 assert len(results['resume-result.json']['exact_cycle_and_output_pairs'])==12
 assert len(results['boundaries-v3/resume-result.json']['pairs'])==4
 counts=results['attribute-result.json']['totals'];assert results['attribute-result.json']['attribute_seen_mask']==1023
 math=P/'gpu-clamp-review-v12';assert json.loads((math/'manifest.json').read_text())['passed']
 report=f'''Private GPU v13: exact clamp and attribute-path rewrites

PASS: 12 native replay pairs and four synthetic boundary pairs have identical
color/depth/fence output, total cycles, busy cycles and batch counts against
the actual frozen gpu-paths-review-v11 model and evidence. Native traffic covers
quiet, nominal and contended memory; boundaries include both texture routes,
affine/perspective, row/column spans, blending, depth modes and combine material.
All inherited span/chunk/resume/queue/request/XY/Z-address assertions pass.

Clamp v12: the stored threshold is floor(unsigned_abs(old_zi)/4)-1 modulo2^30.
The late comparison uses one's-complement new magnitude, strict comparison for
negative new_zi and inclusive comparison for nonnegative new_zi. Separate literal
RTL proof checks37,318,776 pairs, with an all-domain mathematical derivation for
all32-bit pairs including INT_MIN and threshold ties. The Q29 abs==0 replacement
is exact because two-complement has one zero. The selected os30 full-GPU fixture
has INCLUDE_PARAM_SPAN_Q29=0, so it cannot supply live Q29 coverage; the arithmetic
unit covers that predicate. Other absolute-value consumers remain unchanged.

Reset threshold representations differ while unowned: old reset stores T=0,
candidate reset L=0 would represent T=1. Source control-flow review proves every
PSS_ADV_CLAMP entrance comes through PSS_ADV, via either ADV_ISSUE or the tail
chain. Inactive perspective/reset goes to IDLE and anchor uses RECIP_NA without
the shrink threshold. Live checks reject any read before a fresh capture and
compare the captured threshold and final clamp decision against original live
unsigned-absolute expressions. No reset-source revision is required.
  captured thresholds: {counts['captures']:,}
  owned clamp decisions: {counts['clamps']:,}
  true/false shrink decisions: {counts['shrink_true']:,}/{counts['shrink_false']:,}
  negative advanced reciprocal cases: {counts['negative_new']:,}
Zero-threshold/other exceptional arithmetic cases come from the literal unit;
the live raster corpus does not claim exhaustive numeric input coverage.

Attribute v13: the only binary-counter writers are initialization at the final
DRV_RDET step and increment after DRV_ORG_FORM. The command's endpoint is3,6or9,
and remains stable throughout derivation. The10-bit shadow initializes to1 on
that same edge and shifts on that same increment. Every owned capture/control
use therefore has onehot==(1<<dv_attr) and dv_attr in0..9. The original case
mux, including signed/zero-extension widths and light/green at attribute3, is
recomputed literally in the testbench and compared before the sorted-slot mux.
All ten attribute values were exercised. Neither counter nor onehot needs a
reset value: DRV_DA_SEL requires the initializing predecessor. Values for binary
10..15 are unowned and outside this equivalence contract.
  owned onehot/mux cycles: {counts['attr_cycles']:,}
  sorted attribute captures: {counts['attr_captures']:,}
  owned origin-stage checks: {counts['attr_origins']:,}

Resource statement: attribute shadow adds10 flip-flops; clamp threshold removes2,
for net8 declared state bits. No FSM states or cycles were added. The candidate
prepared.json phrase 'No state' means no FSM state, not zero new storage. Actual
mapped/fitted resource and timing effects remain unmeasured by this proof.

Initial analyzer expected live Q29 events despite the selected parameter being0.
This coverage expectation was corrected after all16 simulations and comparisons
passed; candidate RTL, simulator and capture files were unchanged. Preparation
and analysis recipes are separately preserved. Inherited historical scope labels
inside older queue/request recipes do not override this proof's actual candidate,
parent manifest, simulator source and exact-cycle comparison bindings.

No production source, sibling application, device file or fitted design changed.
This is a source/protocol/numerical/cycle qualification for a map-only screen;
it does not establish frequency closure, Pocket FPS or deployment readiness.
'''
 (OUT/'REPORT.txt').write_text(report)
 shutil.copy2(Path(__file__),OUT/'seal-recipe.py')
 evidence=[p for p in OUT.rglob('*')if p.is_file()and'obj'not in p.parts]
 evidence+=[OUT/'model/obj/Vtb_gpu',OLD/'manifest.json',P/'gpu-paths-v11/gpu_core.v',math/'manifest.json',math/'result.json',math/'DERIVATION.txt']
 evidence+=list((P/'gpu-clamp-v12').glob('*'))+list((P/'gpu-attribute-v13').glob('*'))
 H=Path(__file__).resolve().parent
 evidence+=[H/n for n in ['phase10_gpu_attribute_acceptance.py','phase10_gpu_attribute_seal.py','phase10_gpu_clamp_math.py','phase10_gpu_paths_acceptance.py','phase10_gpu_request_acceptance.py','phase10_gpu_queue_acceptance.py','phase10_span_resume_acceptance.py','phase10_span_chunk_acceptance.py','phase10_span_acceptance.py']]
 write(OUT/'manifest.json',dict(passed=True,passed_for_map_only=True,deployment_ready=False,candidate_sha256=sha(CAND),baseline_sha256=sha(P/'gpu-paths-v11/gpu_core.v'),exact_cycle_pixel_pairs=16,added_FSM_states=0,added_cycles=0,added_flip_flops_by_source=8,clamp_math_manifest_sha256=sha(math/'manifest.json'),evidence_sha256={str(p.relative_to(ROOT)):sha(p)for p in sorted(set(evidence))if p.is_file()}))
 print(sha(OUT/'manifest.json'))
if __name__=='__main__':main()
