#!/usr/bin/env python3
"""Read-only schedule/port feasibility bound for streaming LOD5 input DMA."""
import csv,json,math,shutil
from collections import Counter
from pathlib import Path
from phase10_experiment import CAMP,ROOT,sha,write
P=CAMP/'phase10';OUT=P/'load-stream-audit-v1'
def main():
 OUT.mkdir(exist_ok=False)
 F=P/'compact-lod5-core-v5/frozen/src/fpga';R=P/'lod5-recycle-coupled-v1/run-warm'
 trace=R/'geometry-operations.csv';jobs=[{k:int(v)for k,v in r.items()}for r in csv.DictReader(trace.open())if r['operation']=='1']
 occ=json.loads((R/'cram-occupancy.json').read_text());geo=json.loads((R/'geometry.json').read_text())
 assert len(jobs)==2608 and sum(j['count']for j in jobs)==32496
 hist=Counter()
 for j in jobs:
  left=6*j['count']
  while left:b=min(16,left);hist[16+2*b]+=1;left-=b
 assert dict(hist)=={i:n for i,n in enumerate(occ['gd_response_histogram'])if n}
 assert sum(i*n for i,n in hist.items())==occ['gd_response_cycles']
 latency=P/'unified-load-latency-v3/latency.csv'
 rows=list(csv.DictReader(latency.open()))
 for r in rows:
  if r['lighting']=='1' and r['power_two_w']=='1':assert int(r['transform_cycles'])==17*int(r['count'])+50
 denom=800000
 useful=sum(j['count']-1 for j in jobs)/denom;handoff=len(jobs)/denom
 chunks=[]
 for k in (2,3,4,6,8,12,16):
  saving=0
  for j in jobs:
   n=j['count'];arrival=lambda v:12*v+16*math.ceil(6*v/16)
   end=0
   for lo in range(0,n,k):
    hi=min(n,lo+k);end=max(end,arrival(hi))+17*(hi-lo)+50
   saving+=arrival(n)+17*n+50-end
  chunks.append(dict(chunk_vertices=k,optimistic_transform_completion_delta_ms=saving/denom))
 result=dict(passed=True,read_only_audit=True,implementation_recommended=False,jobs=len(jobs),vertices=sum(j['count']for j in jobs),frames=8,
  current_source_response_ms=occ['gd_response_cycles']/denom,current_source_ar_wait_ms=geo['ar_wait']/denom,
  unchanged_order_moved_issue_ms=useful,unchanged_order_completion_ceiling_ms=useful+handoff,
  phase0_rowinner_moved_issue_ms=4*useful,phase0_rowinner_completion_ceiling_ms=4*useful+handoff,
  chunk_schedule_analytical_screen=chunks,
  first_full_vertex=dict(accepted_AR_to_sixth_R_inclusive_cycles=28,basis='Source-derived two cycles per 32-bit beat and response duration 16+2*burst_words; every observed burst-duration bucket matches exactly. Not an independently logged first-R timestamp.',additional_raw_store_cycles=1,first_AR_queue_latency_not_separately_recorded=True),
  limits=['Chunk screen is arithmetic scheduling analysis, not implemented RTL or a whole-frame prediction. It omits interburst dead time/queueing and treats the transform phase drain as 50 cycles per chunk; it leaves lighting/reciprocal overlap unchanged only as a simplifying assumption.',
   'An actual schedule would need numeric/error/reset qualification and could lose reciprocal overlap. No area, timing, or device FPS claim.',
   'All source response and queue time combined is only about0.8ms/frame; not all is hideable, and source timing can change if GPU/CRAM contention changes.',
   'This keeps lighting in hardware; it is distinct from the rejected LOD6 CPU-lighting overlap.'])
 write(OUT/'result.json',result)
 (OUT/'REPORT.txt').write_text('''Streaming LOAD input is physically possible with existing RAM ports, but the
simple availability gate has too little benefit to justify implementation.

The selected unified scheduler issues phase0 for every vertex in row0 before
row1. With full-vertex availability gating, it reaches each arriving vertex and
waits. At most N-1 useful row0 issues move before the final input word. For the
measured326jobs/4062vertices per frame this is0.03736ms. Allowing the existing
one-cycle fetch-done/start handoff yields a0.04062ms completion ceiling.
Interchanging only row/vertex loops within phase0 yields0.14944ms useful issue
overlap, at most0.15270ms including that handoff. Neither approaches0.75ms.

The existing physical trace has exactly600000 source-response cycles over eight
frames (.75ms/frame), plus39693 AR-wait cycles (.04961625ms/frame). All13128 burst
durations equal16+2*wordcount. The source-derived sixth-word arrival is therefore
28cycles inclusive after acceptedAR for an ordinary first16-word burst, followed
by one registered DMA-word/store cycle. First-AR arbitration is not separately
timestamped; do not present28cycles as a measured descriptor-to-first-vertex time.

Port feasibility and required changes:
* positions has one write and one read port. Its current !busy position-write
  gate must be relaxed during transform. A complete-vertex availability count
  ensures reads target fully stored positions and never collide with future DMA
  writes. Matrix writes remain blocked; coefficients use the separate context RAM.
* attributes uses one byte-enabled write port and one read port. DMA UV/RGBA writes
  can overlap transform. Lighting must remain deferred until all input vertices
  are available; then its RGB writes cannot collide with DMA. Position scratch
  writes from lighting likewise start only after all input position writes end.
* acc writes/results and inverse writes already have independent RAM ownership.
  Alias reads must remain blocked until both DMA and all arithmetic/clip writes
  finish. Busy/context/source ownership must span both engines, including errors.
* A mid-DMA error currently returns raw LOAD to idle immediately. An early-started
  compute engine would instead stall forever waiting for unavailable vertices or
  later corrupt a new job. It requires an explicit arithmetic drain/cancel that
  also clears all FMA/reciprocal tags after the external read burst is drained.
  No partial result may become visible. This is more than changing calculate.

A bounded chunk scheduler could retain existing storage and drain each phase,
using a chunk-end/base counter and delaying lighting until every chunk completes.
Current native timing proves17N+50 transform cycles. Repeating those50cycles
consumes most saved fetch time. The ideal regular-CRAM analytical screen gives
roughly0.20ms/frame for eight-vertex chunks; the exact table is in result.json.
This is not RTL or a performance measurement, and early-W/reciprocal timing could
reduce the benefit. Smaller chunks can be slower even before controller cost.

Larger overlap requires concurrent phases on different vertices: result tags must
carry phase because acc writes, W enqueue, lighting scratch, fixed conversion and
unsupported attribution currently depend on the single global phase. Read-after-
write spacing and completion/error draining also need a new schedule. This is a
substantial scheduler redesign, not a free availability counter; do not proceed
without a new benefit case. No implementation or fit was performed.
''')
 shutil.copy2(Path(__file__),OUT/'recipe.py')
 inputs=[trace,R/'cram-occupancy.json',R/'geometry.json',latency,P/'unified-load-components-v3/manifest.json',
  F/'common/geometry_transform_light.v',F/'common/geometry_raw_load.v',F/'common/phase10_raw_vtx_dma.v',
  F/'common/phase10_load_alias.v',F/'common/phase10_cram1_read_arbiter.v',F/'targets/pocket/gpu_cram1_tex_adapter.v',F/'targets/pocket/cram1_controller.v']
 evidence={str(p.relative_to(ROOT)):sha(p)for p in inputs+list(OUT.iterdir())if p.is_file()}
 write(OUT/'manifest.json',dict(passed=True,read_only=True,implementation_recommended=False,evidence_sha256=evidence));print(sha(OUT/'manifest.json'))
if __name__=='__main__':main()
