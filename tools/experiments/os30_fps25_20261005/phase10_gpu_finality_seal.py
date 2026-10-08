#!/usr/bin/env python3
"""Seal queue finality equivalence without composing or fitting it."""
import json
import shutil
from pathlib import Path
from phase10_experiment import CAMP, ROOT, sha, write
P=CAMP/'phase10';OUT=P/'gpu-queue-finality-review-v15';SOURCE=P/'gpu-queue-finality-v15';OLD=P/'gpu-origin-review-v14'
def main():
 assert not(OUT/'manifest.json').exists()
 cand=SOURCE/'gpu_core.v'
 assert sha(cand)=='ce42d979d4f9d1925ca48bafa972621d2df73175c92fec25b5ac1d0568c8bae7'
 names=['result.json','chunk-result.json','resume-result.json','queue-result.json','request-result.json','paths-result.json','attribute-result.json','origin-result.json','finality-result.json','boundaries-v3/result.json','boundaries-v3/resume-result.json']
 results={n:json.loads((OUT/n).read_text())for n in names};assert all(j['passed']for j in results.values())
 assert len(results['resume-result.json']['exact_cycle_and_output_pairs'])==12
 assert len(results['boundaries-v3/resume-result.json']['pairs'])==4
 literal=json.loads((SOURCE/'literal/result.json').read_text());assert literal['passed']
 inputs=json.loads((OUT/'origin-inputs.json').read_text());assert sha(OUT/'model/frozen/common/gpu_core.v')==inputs['model_gpu_sha256']
 assert sha(cand)==inputs['candidate_sha256'] and sha(OLD/'manifest.json')==inputs['baseline_manifest_sha256']
 c=results['finality-result.json']['totals'];q=results['queue-result.json']['totals']
 report=f'''Private GPU v15 queue-finality factoring

PASS: exact candidate ce42d979 over frozen GPU v14 3c171d36. The only functional
change replaces the encoded-length-based head-chain-final predicate with a
direct OR of count thresholds and broken link bits. Actual burst-length/AWLEN
encoding, data/strobe writes, pointer updates, capacity, AW-ahead/gather control
consumers, reset and all other RTL remain byte-for-byte unchanged. No functional
flip-flop, FSM state or cycle is added.

Proof: for count 0 or 1 the original length is one and finality is false. For
count 2..7, the head prefix is shorter than count iff any link between existing
entries is broken; later breaks cannot change that truth. For count >= 8,
either the chain reaches eight (maximum burst) or an earlier break makes its
length less than count. Both are final. Thus the new expression is exact for
all 5-bit counts, all modulo-16 pointers and all 16 link bits. It does not rely
on reachable-link consistency. At count >= 8 no seventh-link test is needed.

The literal RTL unit extracts both complete original/candidate prefix encoders
and finality expressions from their actual sources. It checks predicate equality
and unchanged encoded length for {literal['all_combinations']:,} combinations:
all counts 0..31, all 16 read pointers and all 65,536 link patterns. This includes
{literal['legal_count_combinations']:,} cases with legal queue occupancy 0..16.

The actual GPU model passed 12 native replay pairs and four adversarial boundary
pairs against the sealed v14 simulator: exact color/depth/fence bytes, total and
busy cycles, and batch counts. Quiet, nominal and contended memory schedules;
both texture routes; row/column, affine/perspective, blend, depth and combine
cases are included. The inherited numerical/operand/span/request assertions
also remain enabled, including the v14 signed-product shadow proof.
  Live literal finality comparisons: {c['cycles']:,}
  Final-chain cycles: {c['final']:,}
  AW-ahead launches: {c['ahead']:,}
  Producer records enqueued and consumed: {q['pushes']:,} / {q['reads']:,}
  AW address records checked: {q['aw']:,}
  Partial-strobe W records: {q['partial']:,}
  W / AW stalled cycles: {q['w_stall']:,} / {q['aw_stall']:,}
  Reset-empty ownership observations: {q['resets']:,}
No write was reordered, overwritten, read unowned or changed while stalled.
These resets initialize empty queues; no new active-reset cancellation protocol
claim is made. The selected model has GPU_WRITE_GATHER=0, so live gather count
is zero; the exhaustive predicate proof covers its unchanged consumer's Boolean
input, not a separate enabled-gather rendering qualification.

The actual frozen candidate and instrumented model are intentionally different:
the latter adds only the inherited simulation old-DSP-A shadow, mirrored under
the same branch/NBA priority. Its source and derivation recipe are bound. The
model's additional monitor state never feeds the functional design.

No v7 source changed, and this candidate is not composed with the separately
proved SDRAM counter rewrite. No synthesis, map, fitter or device operation was
performed. The proof does not establish timing improvement or production
readiness. It is available only if the next routed measurement still needs it.
'''
 (OUT/'REPORT.txt').write_text(report);shutil.copy2(__file__,OUT/'seal-recipe.py')
 files=[p for p in OUT.rglob('*')if p.is_file()and'obj'not in p.parts]
 files += [p for p in SOURCE.rglob('*')if p.is_file()and'obj'not in p.parts]
 files += [OUT/'model/obj/Vtb_gpu',SOURCE/'literal/obj/Vtb_finality',OLD/'manifest.json',P/'gpu-origin-default-v14/gpu_core.v',P/'residual-timing-options-v6/manifest.json']
 h=Path(__file__).resolve().parent
 files += [h/n for n in ['phase10_gpu_queue_finality.py','phase10_gpu_finality_acceptance.py','phase10_gpu_finality_seal.py','phase10_gpu_origin_acceptance.py','phase10_gpu_attribute_acceptance.py','phase10_gpu_paths_acceptance.py','phase10_gpu_request_acceptance.py','phase10_gpu_queue_acceptance.py','phase10_span_resume_acceptance.py','phase10_span_chunk_acceptance.py','phase10_span_acceptance.py']]
 write(OUT/'manifest.json',dict(passed=True,passed_for_resource_screen=True,deployment_ready=False,composed=False,map_or_fit_run=False,candidate_sha256=sha(cand),parent_sha256=sha(P/'gpu-origin-default-v14/gpu_core.v'),exact_cycle_pixel_pairs=16,literal_combinations=literal['all_combinations'],live_finality_checks=c['cycles'],added_FF=0,added_states=0,added_cycles=0,evidence_sha256={str(p.relative_to(ROOT)):sha(p)for p in sorted(set(files))if p.is_file()}))
 print(sha(OUT/'manifest.json'))
if __name__=='__main__':main()
