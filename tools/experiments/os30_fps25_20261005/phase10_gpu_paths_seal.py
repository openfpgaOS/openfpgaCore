#!/usr/bin/env python3
"""Seal private GPU v11 critical-path cuts and differential acceptance."""
import hashlib,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3];HERE=Path(__file__).resolve().parent
P=ROOT/'docs/measurements/os30-fps25-20261005/phase10'
OUT=P/'gpu-paths-review-v11';CAND=P/'gpu-paths-v11/gpu_core.v'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,v):
 with p.open('x') as f:json.dump(v,f,indent=2);f.write('\n')
def main():
 assert sha(CAND)=='7e47d69ea809961251b675e860ede887458cde7b583202ad273165df301ed9a6'
 assert sha(OUT/'model/frozen/common/gpu_core.v')==sha(CAND)==sha(OUT/'ring/frozen/gpu_core.v')
 names=['result.json','chunk-result.json','resume-result.json','queue-result.json',
        'request-result.json','paths-result.json','boundaries-v3/result.json',
        'boundaries-v3/resume-result.json','ring/result.json']
 results={n:json.loads((OUT/n).read_text()) for n in names}
 assert all(x['passed'] for x in results.values())
 p=results['paths-result.json']['totals'];r=results['ring/result.json']
 assert p['xy_prepares']==p['xy_uses']
 report=f'''Private GPU v11: three zero-cycle critical-path cuts

Selected source: {CAND.relative_to(ROOT)}
SHA256 {sha(CAND)}
Parent GPU v10 SHA256
b67fb88a63870897cf5bae6c76a59a60d44edff2dfd15fc682dc981a9abe79ed

PASS: 12 native replay pairs and four synthetic boundary pairs have identical
color/depth bytes, total cycles, busy cycles and batch counts against frozen
gpu-request-review-v10d. All inherited span, chunk, resume, full-address write
queue and request-payload ownership assertions pass. This is functional and
cycle qualification of the selected os30 configuration; no mapping, fitting,
frequency, hardware FPS or deployment claim is made.

1. XY edge precompute uses the EXISTING dd1x/dd2x/dd1y/dd2y/dd_x0/dd_y0
registers in DRV_PROD_L0, after the last SORT_C permutation update. DELTA
then launches determinant DSP products directly from those registers. The
sorted coordinate selection and subtraction no longer feed DSP inputs in
the same cycle. No new state, register, arithmetic expression or cycle.
Static lifetime: SORT_C is the sole predecessor of PROD_L0. All paths to
DELTA pass through L0/L1/L2C0/C1/C2, with optional NORMALIZE affecting only
UV/reciprocal data. Raw triangle XY and dv_ord do not change in that interval.
The dd registers are not read before DELTA; later plane derivation and anchor
consumers are unchanged. At all {p['xy_uses']:,} actual DELTA launches, a
testbench recomputation from LIVE sorted XY equals every stored delta/anchor;
the following edge compares the four DSP inputs against the legacy expression.
This directly tests readiness and live-value equivalence, rather than only
the eventual framebuffer. Arithmetic remains signed17, including differences
that exceed the signed16 input range.

2. With INCLUDE_COMMAND_DMA=0, the ring BRAM data input is reg_wdata directly.
Full/overflow/CPU-mode/active ownership remains in the identical write-enable.
The DMA FSM is held IDLE when the parameter is zero, so dma_ring_wr_raw can
never own a write. With INCLUDE_COMMAND_DMA=1, the original CPU/DMA data mux
is verbatim the selected expression; no DMA behavior change is intended.
The separate actual dual-GPU test instantiates both parameter values. It
checks full4095 capacity, unpublished data, wrap, publication/drain, sticky
overflow and ring/soft reset; full writes never alter any owned word.
It checks {r['words_checked']:,} stored words and {r['overflow_rejections']}
rejections, with poisoned inactive DMA input. The DMA-enabled instance also
receives {r['dma_words']} exact response words under AR/R stalls; the excluded
instance ignores the same descriptor. No AXI/peripheral interface was changed.

3. An INVALID Z accumulator precaptures p2b's aligned word address every
cycle. The late window-fold path now only acquires validity/data; it does not
select the address register's write enable. An existing valid accumulator
can use the folded-write branch only on an exact accumulator-address hit,
which never changes its address. A window-hit folded write with a different
address requires !z_acc_valid in the existing fold predicate. Later readback
and pending-source assignments retain their original priority and operands.
All address consumers are ownership-gated: wc_z_read_miss, p2b_acc_hit, FBSS
hit/miss, prefetch/read barriers, pending-source flush and write publication
require z_acc_valid or an already accepted valid request. The request mux may
sample an invalid address but cannot publish it without the producer's valid
condition. Delayed snoops use accepted requests, not the unowned accumulator.
The observer reconstructs the legacy ordered address writers each clock and
compares only when the post-edge accumulator is owned. It also rejects every
invalid-to-valid transition lacking a legacy producer. Counts:
  owned-address checks {p['zowned']:,}
  window-fold acquisitions {p['zfold']:,}
  readback acquisitions {p['zread']:,}
  pending-source assignments {p['zpending']:,}
  allowed unowned capture cycles {p['zunowned']:,}.
Old invalid register values are deliberately outside the comparison contract.

Scope and reproducibility:
The main fixture inherits phase10_gpu_request_acceptance.py and its full
26-bit address/32-bit AXI AW monitors. That inherited request-inputs.json
contains a historical textual scope label 'v10 versus v8'; the actual selected
candidate, baseline manifest, command, paths-inputs.json and exact resume
comparison all bind v11 versus v10d. This report corrects that label without
rewriting inherited evidence. The ring test additionally exercises DMA=1;
the full raster corpus retains the selected DMA=0 os30 parameters. Optional
write-combine, alternate feature matrices and active-reset fabric semantics
are not newly qualified. No production, sibling, SDK or SD file was changed.
'''
 with (OUT/'REPORT.txt').open('x') as f:f.write(report)
 evidence=[p for p in OUT.rglob('*') if p.is_file() and 'obj' not in p.parts]
 evidence += [OUT/'model/obj/Vtb_gpu',OUT/'ring/obj/Vtb_ring']
 evidence += list((P/'gpu-paths-v11').glob('*'))
 evidence += [P/'gpu-request-review-v10d/manifest.json',P/'gpu-request-mux-v10/gpu_core.v',
              P/'gpu-request-mux-v10/divider-proof.json']
 evidence += [HERE/n for n in ['phase10_gpu_paths_v11.py','phase10_gpu_paths_acceptance.py',
  'phase10_gpu_paths_ring.py','phase10_gpu_paths_ring.cpp','phase10_gpu_paths_seal.py',
  'phase10_gpu_request_acceptance.py','phase10_gpu_queue_acceptance.py',
  'phase10_span_resume_acceptance.py','phase10_span_chunk_acceptance.py','phase10_span_acceptance.py']]
 save(OUT/'manifest.json',dict(passed=True,passed_for_map_only=True,deployment_ready=False,
  candidate_sha256=sha(CAND),baseline_sha256=sha(P/'gpu-request-mux-v10/gpu_core.v'),
  exact_cycle_pixel_pairs=16,added_fsm_cycles=0,added_registers_by_source=0,
  evidence_sha256={str(p.relative_to(ROOT)):sha(p) for p in sorted(set(evidence))}))
 print(OUT/'manifest.json');print(sha(OUT/'manifest.json'))
if __name__=='__main__':main()
