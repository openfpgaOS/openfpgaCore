#!/usr/bin/env python3
"""Seal private v17 source, literal state proof and exact GPU replays."""
import json,re,shutil
from pathlib import Path
from phase10_experiment import CAMP,ROOT,sha,write
P=CAMP/'phase10';OUT=P/'gpu-span-control-review-v17';SOURCE=P/'gpu-span-control-v17';OLD=P/'gpu-ring-equality-review-v16'
def main():
 assert not(OUT/'manifest.json').exists()and not(SOURCE/'manifest.json').exists()
 cand=SOURCE/'gpu_core.v';parent=P/'gpu-ring-equality-v16/gpu_core.v'
 assert sha(cand)=='7076c8e7c9d41361aa01bda2612089dcff924cc38885d958e2956a39ca0037a2'
 assert sha(parent)=='32de956cdfc6a85e627aed6772c805494c4475a92d9193ece5673d7650aff177'
 prepared=json.loads((SOURCE/'prepared.json').read_text());assert len(prepared['count_write_sites'])==10
 names=['result.json','chunk-result.json','resume-result.json','queue-result.json','request-result.json','paths-result.json','attribute-result.json','origin-result.json','ring-result.json','span-control-result.json','boundaries-v3/result.json','boundaries-v3/resume-result.json','zero-records/result.json']
 results={n:json.loads((OUT/n).read_text())for n in names};assert all(j['passed']for j in results.values())
 assert len(results['resume-result.json']['exact_cycle_and_output_pairs'])==12
 assert len(results['boundaries-v3/resume-result.json']['pairs'])==4
 literal=json.loads((SOURCE/'literal/result.json').read_text());assert literal['passed']
 assert(literal['terminal'],literal['counts'],literal['transitions'])==(16384,262144,4194304)
 inputs=json.loads((OUT/'span-control-inputs.json').read_text());assert inputs['mask_write_sites']==14
 assert sha(OUT/'model/frozen/common/gpu_core.v')==inputs['model_gpu_sha256']
 assert sha(cand)==inputs['candidate_sha256']and sha(OLD/'manifest.json')==inputs['parent_manifest_sha256']
 counts=results['span-control-result.json']['totals'];zero=results['zero-records/result.json']
 assert counts['selected_zero']==816 and counts['writes_zero']==852
 assert zero['pairs']==2 and zero['command_cases']==156
 for row in zero['runs']:
  assert row['zero_coverage']==dict(stale_zero=174,zero_lanes=15,nonzero_lanes=15)
  assert row['counts']['resets']==23
 # Recheck exact output/cycle pairs against the actual frozen parent, rather
 # than relying only on aggregate success booleans.
 for p in sorted((OUT/'runs').glob('candidate-*/run.log')):
  baseline=OLD/'runs'/p.parent.name/'run.log'
  stats=lambda path:re.search(r'RESULT cycles=(\d+) busy_cycles=(\d+) batches=(\d+)',path.read_text()).groups()
  assert stats(p)==stats(baseline)
  assert(p.parent/'output.bin').read_bytes()==(baseline.parent/'output.bin').read_bytes()
 report=f'''Private GPU span-control v17: exact control predicates

PASS for a resource/timing screen; not a production qualification.
Candidate SHA256: {sha(cand)}
Parent SHA256: {sha(parent)}

The existing four-bit count mask now means initialized AND nonzero. All ten
setters use exactly the payload written to their count RAM slot on the same
edge: four compact, four long-form, one serial walker and one lookahead
walker. Every whole-mask clear (four sites), state, address, write enable,
SELECT capture and lexical nonblocking-assignment priority stays unchanged.
The only mask reader snapshots the selected bit together with the count RAM.
If old_valid is false, both reads are zero. If it is true, the new bit is
(raw_count != 0), so masking a zero word still yields zero and masking a
nonzero word preserves it. The current nonzero test therefore needs only
current-valid AND current-count-valid; it no longer depends on RAM output.
Stale RAM after a clear or zero overwrite cannot become visible. The live
instrumented model mirrors all 14 original mask writers in their exact
conditions/order and asserts no count RAM read/write ownership collision.

The second change replaces only two comparisons of encoded next-step data
with its direct Boolean terminal predicate. The original next-step function
and its data/address uses remain unchanged. All 16 step encodings, including
unused/default 6, 7, 14 and 15, match for every ten-bit control combination.
No functional register, RAM, DSP, FSM state or cycle is added or removed.

Literal extracted RTL passes {literal['terminal']:,} terminal combinations,
{literal['counts']:,} complete count/valid combinations and a four-slot state
oracle covering {literal['transitions']:,} transitions. It tests every 16-bit
count, both clear/write priorities, invalid stale words, all slots, zero
writes and repeated slot use. The intentionally omitted default terminal
arms produce {literal['negative']:,} mismatches. These are two-state proofs;
no equivalence for uninitialized X values is asserted.

Twelve native replay pairs and four adversarial boundary pairs exactly match
actual GPU v16 color, depth, fence bytes, total cycles, busy cycles and batch
counts. This includes quiet/nominal/contended memory, both texture routes,
affine/perspective, row/column, combine, blend/depth and partial walker chunks.
All inherited arithmetic, queue, ownership and ring assertions pass.

The selected os30 build prunes compact/long-form command input. A separate
bounded protocol configuration enables those existing input decoders in both
GPUs, preserving their implementation. Its 156 commands per schedule cover
1..4 compact slots, long-form lengths 1/2/3/4/5/7/8/9/12, odd partial chunks,
zero/nonzero mixtures, slot reuse and three soft resets after idle drain.
Both quiet and contended schedules are cycle/output exact. Every physical
slot receives both zero and nonzero counts. There are 348 observed writes of
zero over stale nonzero RAM, and 816 selected zero records across the pair.
No in-flight reset cancellation claim is made.

Across 18 pairs the live original-mask/terminal comparisons total
{counts['checks']:,}; nonzero writes {counts['writes_nonzero']:,}, zero writes
{counts['writes_zero']:,}, still-valid slot replacements {counts['reused']:,},
mask clears {counts['clears']:,}, and reset observations {counts['resets']:,}.

The functional source is frozen separately from passive simulation shadows.
An initial monitor compile used an incorrect state label; an early protocol
coverage check expected still-valid reuse despite mandatory loader clears.
Those attempts are preserved. The final monitor checks actual stale RAM
replacement. A preliminary corpus omitted the perspective flag required for
depth records; it is retained separately and replaced by the valid final
corpus. The inherited resume counter parser was scoped to RESUME_AUDIT to
avoid matching the new comparison counter; original summaries are preserved.
None of these fixture corrections changed candidate RTL.

This targets the measured v12 count-RAM-to-drain and encoded-step-to-state
cones. No map, fitter, timing improvement or device FPS result is claimed by
this proof. Production and device files are unchanged.
'''
 (OUT/'REPORT.txt').write_text(report);shutil.copy2(__file__,OUT/'seal-recipe.py')
 evidence=[p for p in OUT.rglob('*')if p.is_file()and'obj'not in p.parts]
 evidence +=[p for p in SOURCE.rglob('*')if p.is_file()and'obj'not in p.parts]
 evidence +=[OUT/'model/obj/Vtb_gpu',OUT/'zero-records/candidate/obj/Vtb_gpu',OUT/'zero-records/baseline/obj/Vtb_gpu',SOURCE/'literal/obj/Vtb_span',OLD/'manifest.json',OLD/'model/obj/Vtb_gpu',parent,P/'gpu-ring-equality-v16/manifest.json',P/'compact-timing-diagnosis-v12/manifest.json']
 helpers=Path(__file__).resolve().parent
 evidence +=[helpers/n for n in ['phase10_gpu_span_control.py','phase10_gpu_span_control_acceptance.py','phase10_gpu_span_zero.py','phase10_gpu_span_control_seal.py','phase10_gpu_ring_acceptance.py','phase10_gpu_origin_acceptance.py','phase10_gpu_attribute_acceptance.py','phase10_gpu_paths_acceptance.py','phase10_gpu_request_acceptance.py','phase10_gpu_queue_acceptance.py','phase10_span_resume_acceptance.py','phase10_span_chunk_acceptance.py','phase10_span_acceptance.py']]
 assert all(p.is_file()for p in evidence)
 write(OUT/'manifest.json',dict(passed=True,passed_for_resource_screen=True,deployment_ready=False,candidate_sha256=sha(cand),parent_sha256=sha(parent),exact_cycle_pixel_pairs=16,extra_zero_record_pairs=2,live_counts=counts,added_flip_flops=0,added_states=0,added_cycles=0,evidence_sha256={str(p.relative_to(ROOT)):sha(p)for p in sorted(set(evidence))}))
 write(SOURCE/'result.json',dict(passed=True,candidate_sha256=sha(cand),parent_sha256=sha(parent),exact_cycle_pixel_pairs=16,extra_zero_record_pairs=2,exact_pixels_depth_fence_and_cycles=True,mask_sites=14,live_counts=counts,literal=literal,deployment_ready=False,proof_manifest=str((OUT/'manifest.json').relative_to(ROOT)),proof_manifest_sha256=sha(OUT/'manifest.json')))
 files=[p for p in SOURCE.rglob('*')if p.is_file()and'obj'not in p.parts]+[OUT/'manifest.json',helpers/'phase10_gpu_span_control_seal.py']
 write(SOURCE/'manifest.json',dict(passed=True,passed_for_resource_screen=True,deployment_ready=False,candidate_sha256=sha(cand),parent_sha256=sha(parent),evidence_sha256={str(p.relative_to(ROOT)):sha(p)for p in sorted(set(files))}))
 for manifest in [OUT/'manifest.json',SOURCE/'manifest.json']:
  j=json.loads(manifest.read_text())
  for path,digest in j['evidence_sha256'].items():assert sha(ROOT/path)==digest,path
 print(json.dumps(dict(proof_manifest_sha256=sha(OUT/'manifest.json'),component_manifest_sha256=sha(SOURCE/'manifest.json'),evidence_files=len(set(evidence)))))
if __name__=='__main__':main()
