#!/usr/bin/env python3
"""Seal independent request-mux/divider review without modifying frozen RTL."""
import hashlib,json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[3]
P=ROOT/'docs/measurements/os30-fps25-20261005/phase10'
OUT=P/'gpu-request-review-v10d'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,v):
 with p.open('x') as f:json.dump(v,f,indent=2);f.write('\n')

def main():
 candidate=P/'gpu-request-mux-v10/gpu_core.v'
 assert sha(candidate)=='b67fb88a63870897cf5bae6c76a59a60d44edff2dfd15fc682dc981a9abe79ed'
 assert sha(OUT/'model/frozen/common/gpu_core.v')==sha(candidate)
 results={n:json.loads((OUT/n).read_text()) for n in [
  'result.json','chunk-result.json','resume-result.json','queue-result.json','request-result.json',
  'boundaries-v3/result.json','boundaries-v3/resume-result.json']}
 assert all(r['passed'] for r in results.values())
 q=results['queue-result.json']['totals'];r=results['request-result.json']['totals']
 assert r['accepts']==q['pushes']==q['reads']
 divider=P/'gpu-request-mux-v10/divider-proof.json'
 assert json.loads(divider.read_text())['passed']
 report=f'''Independent review of GPU request mux v9 and restoring divider v10

Selected source: {candidate.relative_to(ROOT)}
SHA256: {sha(candidate)}
Reference: gpu-queue-pack-v8/gpu_core.v, SHA256
5bd0031d9c66083c72884926e836ce584b9f5a275b106c63934e63f3d3afdfad.

PASS: all 12 native replays (eight nominal frames, two quiet and two busy
memory schedules) and all four synthetic boundary combinations have exact
color/depth bytes, total cycles, busy cycles and batch counts versus v8.
The inherited span/chunk/resume invariants also pass. No GPU RTL was edited
by this reviewer, and no map, fit, clock or physical performance claim follows.

Independent request ownership checks observed {r['accepts']:,} accepted
requests, {r['stalls']:,} owned stall cycles and {r['unowned']:,} unowned
captures. Sources: Z-flush {r['zflush']:,}, Z-accumulator {r['zacc']:,},
color accumulator {r['fbacc']:,}, clear {r['clear']:,}. Each new request is
compared after its clock edge with the selected producer's PRE-edge address,
data and strobes. Clear strobes are calculated independently from lane/count.
Owned request payload remains stable when it cannot retire. An unaccepted
capture cannot acquire validity. Address width comes from $bits(actual port).

Independent queue records check all {q['reads']:,} W records and {q['aw']:,}
AW records, including {q['ahead']:,} AW-ahead issues and {q['partial']:,}
partial-strobe records. All 32 AW bits must equal the zero-extended full GPU
address. W/AW stalls ({q['w_stall']:,}/{q['aw_stall']:,} cycles) preserve valid
payload. Reset-start checks total {q['resets']:,}; this does not establish
new active-reset cancellation semantics.

Static request-consumer audit (line references are selected v10 source):
- 4695-4698 and 5093-5095: direct input is consumed only with req_valid;
  stage payload/link capture at 11374 is gated by in_valid and stage capacity.
- 5049-5065: unqualified link comparisons are harmless until the valid
  stage/swap ownership gates consume them. Swap 5084 requires both entries
  valid; array/tail/link writes 11347-11363 then consume the owned request.
- 5165: the optional selective-read scoreboard may sample an unowned tag,
  but srw_p_valid derives only from a valid swap/stage push; no unowned tag
  becomes a hazard owner.
- 4750-4860: optional WC readiness/hash combinational terms may vary while
  req_valid=0. Acceptance, RAM read, input/hash capture and bypass output all
  require accept=req_valid&&ready. Request-induced draining additionally
  requires req_valid. Independent flush/apply work does not consume invalid
  payload. This optional configuration is statically reviewed, not simulated
  by the selected os30 suite (WC=0, render-cache skid swap disabled).
- 11445-11477: delayed Z/blend snoops carry a pending bit from accepted push.
  On the next edge, they read the accepted old request before the nonblocking
  payload capture can replace it. Retirement plus replacement is therefore
  safe; no invalid capture can schedule a snoop.
- 6268/6539: reset clears request validity and payload as before. The only
  runtime new writes are under can_push: empty, retiring-to-stage/WC, or swap.
  req_valid and combine metadata gates remain unchanged.
- All nine original producer assignments map one-to-one to source selector
  0/1/2/3 (Z-flush/Z-accumulator/color-accumulator/clear). Producer operands
  retain pre-edge values because their state updates are nonblocking. The
  resolved temporary payload has no earlier consumer before its final mux.

Divider audit: operands are unsigned zero-extended to 37 bits. The full
trial-minus-divisor range is strictly (-2^35,2^36); bit36 thus exactly means
borrow. Low35 subtraction bits equal the old modulo-2^35 expression even
for unreachable divider states. Quotient append and FSM latency are unchanged.
Root's separately frozen actual-expression arithmetic proof is bound below;
the paired whole-GPU results also cover actual 33/39/45-step uses exercised.

Qualification limits and preserved attempts:
The original v8 queue observer stored only low24 address bits. Its old
manifest must not be interpreted as a full26-bit address proof. This new
selected fixture strengthens that check and compares full32 AW including
zero extension. The first two private monitor attempts repeated the same
24-bit truncation in request snapshots, falsely rejecting a valid clear
at address0x39da020 as0x9da020. No production/RTL failure occurred; attempts
remain intact. v10c widened to26 and passed all16; selected v10d additionally
derives widths and verifies all32 AW bits, as requested. No further repeats.
Selected test scope remains replay/protocol equivalence, not whole native
CPU, all possible feature combinations, timing closure or deployment.
'''
 with (OUT/'REPORT.txt').open('x') as f:f.write(report)
 attempts=[]
 for label,status in [('gpu-request-review-v10','monitor width error'),
                      ('gpu-request-review-v10b','same width error, diagnostic build'),
                      ('gpu-request-review-v10c','passed fixed26; superseded by parameter-derived full32 AW')]:
  d=P/label
  evidence=[d/'request-recipe.py',d/'model/frozen/tb_gpu.v']
  evidence+=list(d.glob('runs/candidate-*/run.log'))
  evidence+=list(d.glob('*result.json'))
  attempts.append(dict(path=str(d.relative_to(ROOT)),status=status,
                       evidence_sha256={str(p.relative_to(ROOT)):sha(p) for p in evidence}))
 save(OUT/'attempts.json',attempts)
 evidence=[]
 for p in OUT.rglob('*'):
  if not p.is_file() or 'obj' in p.parts:continue
  evidence.append(p)
 evidence.append(OUT/'model/obj/Vtb_gpu')
 evidence += [candidate,P/'gpu-request-mux-v9/gpu_core.v',
              P/'gpu-request-mux-v9/candidate.patch',P/'gpu-request-mux-v10/candidate.patch',
              P/'gpu-request-mux-v10/prepared.json',divider,
              P/'gpu-request-mux-v10/run.log',P/'gpu-queue-review-v8/manifest.json',
              Path(__file__),Path(__file__).with_name('phase10_gpu_request_acceptance.py')]
 save(OUT/'manifest.json',dict(passed=True,passed_for_map_only=True,deployment_ready=False,
  candidate_sha256=sha(candidate),exact_cycle_pixel_pairs=16,
  request_address_bits=26,axi_aw_compared_bits=32,
  evidence_sha256={str(p.relative_to(ROOT)):sha(p) for p in sorted(set(evidence))}))
 print(str(OUT/'manifest.json'));print(sha(OUT/'manifest.json'))

if __name__=='__main__':main()
