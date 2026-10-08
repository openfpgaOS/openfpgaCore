#!/usr/bin/env python3
"""Read-only synthesis of already sealed performance evidence; no experiments."""
import csv,hashlib,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
P=ROOT/'docs/measurements/os30-fps25-20261005/phase10'
OUT=P/'selected-performance-summary-v1'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def read(label,name='result.json'):return json.loads((P/label/name).read_text())
def main():
 base=read('gpu-critical-bound-v2');current=read('cache-write-lod5-coupled-v2')
 recycler=read('lod5-recycle-coupled-v1');warm=current['results']['warm'];cold=current['results']['cold']
 assert current['passed'] and warm['all8_actual_color_depth_exact'] and cold['all8_actual_color_depth_exact']
 assert sha(P/'cache-write-lod5-coupled-v2/manifest.json')=='c46755893729024837ec5958017e2b8a4f5ee80b0c10387c6146a202388415b3'
 d=P/'cache-write-lod5-coupled-v2/run-warm'
 c=json.loads((d/'coupled.json').read_text());assert c['clock_hz']==100000000 and c['frames']==8
 divisor=c['clock_hz']*c['frames']/1000
 def profile(name):return {r['function']:r for r in csv.DictReader((d/name).open(),delimiter='\t')}
 before=profile('profile-0360.tsv');after=profile('profile.tsv')
 funcs={n:{k:int(v[k])-int(before.get(n,{}).get(k,0)) for k in ['self_insns','self_cycles','icache_miss','dcache_miss','mispredict','calls']} for n,v in after.items()}
 ops=list(csv.DictReader((d/'geometry-operations.csv').open()))
 assert len(ops)==2608 and all(int(r['operation'])==1 for r in ops)
 hardware_ms=sum(int(r['hardware_finish'])-int(r['start']) for r in ops)/divisor
 observed_ms=sum(int(r['cycles']) for r in ops)/divisor
 initial=base['baseline']['modeled_frame_ms'];selected=warm['timing']['speculative_write']['frame_ms']
 saved=initial-selected;lower=100*saved/initial;throughput=100*(initial/selected-1)
 names=[('Indexed triangle preparation','gfx_cpu_indexed_run'),
  ('LOAD driver, including hardware wait','load_alias_vertex'),
  ('LOAD result conversion/copy','load_alias_copy.constprop.0'),
  ('Immutable source-pool lookup','gfx_gpu_cram_vertex_source'),
  ('Indexed batch packet submission','gfx_gpu_vtx_cache_batch'),
  ('Direct-record backend','gfx_gpu_draw_direct_records'),
  ('Generic triangle backend','gpu_draw_triangles'),
  ('Triangle dispatch/push','gfx_push_triangle.constprop.0.isra.0'),
  ('CPU polygon clipping','gfx_clip_triangle.constprop.0'),
  ('CPU fallback projection','gpu_project_clip'),
  ('Display-list dispatch','gfx_run_dl'),
  ('Vertex caller/fallback self work','gfx_sp_vertex')]
 budget={title:funcs[n]['self_cycles']/divisor for title,n in names}
 budget['All other modeled CPU execution/waits']=selected-sum(budget.values())
 table='\n'.join(f'  {name:43s} {ms:10.6f}' for name,ms in budget.items())
 refs=[('Retained F11 CPU/GPU limits','gpu-critical-bound-v2/REPORT.txt'),
 ('Span overlap comparison','span-nextchunk-coupled-v1/REPORT.txt'),
 ('Real LOD5 client, cold uploads and warm lookup','lod5-real-coupled-v2/REPORT.txt'),
 ('Unified worker performance','unified-lod5-coupled-v1/REPORT.txt'),
 ('Read and fill-hazard cache stages','cache-stage-lod5-coupled-v2/REPORT.txt'),
 ('Reviewed recycler cost and layout','lod5-recycle-coupled-v1/REPORT.txt'),
 ('Selected full-word cache write path','cache-write-lod5-coupled-v2/REPORT.txt'),
 ('Fine-grained indexed CPU work','lod5-indexed-profile-v1/REPORT.txt'),
 ('GPU v11 zero-cycle proof','gpu-paths-review-v11/REPORT.txt'),
 ('Application payload and reproducible build','lod5-recycle-delivery-v1/README.txt')]
 links='\n'.join(f'  {title}:\n    {P/path}' for title,path in refs)
 text=f'''Selected LOD5/SM64 performance evidence, before compact-v5 timing qualification

The selected bounded model reduces the busy-window CPU frame-progression time
from {initial:.8f} to {selected:.8f} ms/frame: {saved:.8f} ms saved,
{lower:.4f}% lower time, or {throughput:.4f}% greater modeled throughput.
This is not a Pocket FPS measurement or evidence of reaching25FPS. No clock,
successful timing closure, broad-world result or deployment is claimed here.
The summary reads existing sealed experiments only; it ran no new experiment.

Measurement contract

Warm window: eight consecutive Bob-omb Battlefield frames360..367. The CPU
executes the actual application in qsim with its instruction/dependency/cache
cost model. GPU, render cache, LOAD worker, SDRAM controller/model, and both
8MiB CRAM dies execute as actual RTL. Background CPU/scanout traffic and game
cadence remain modeled. The conversion uses the experiment's100MHz timebase;
that is not a claim that the eventual FPGA meets100MHz.

The primary denominator is modeled CPU frame progression, excluding the final
presentation/completion tail. GPU counters include that tail. CPU and GPU
times overlap and must not be added. Full-scene comparisons include368 warm
or242 cold scene/geometry/cull records; actual rendered color/depth comparisons
cover the selected eight frames in each window. Separately,721-frame command
stream/functional proofs are not721 actual RTL framebuffer renders.

Measured warm progression (ms/frame, same busy window)

  Retained F11 simple core/renderer                         37.82757375
  F11 plus GPU intra-/cross-chunk span lookahead             37.11836875
  Real LOD5 CRAM source client plus span lookahead           34.73734000
  Unified transform/lighting worker, same client            34.73315625
  Registered cache read and fill-hazard stages              34.78922125
  Reviewed120-frame source-pool recycler                    34.98837000
  Selected hot0 speculation + full-word W_WAIT shortcut     {selected:.8f}

These are measured cumulative controls, not independently additive speedups.
Span lookahead alone saves0.709205ms. Adding the real LOD5 client to that
control saves2.38102875ms. The unified worker is effectively performance-neutral.
The two cache read stages cost0.056065ms together. Recycler/layout overhead
costs0.19914875ms; its purpose is continued acceleration across source-pool
generations. Selected write speculation recovers its rejected predecessor's
~0.2ms penalty and is0.031275ms faster than the recycler control. The latter
small change is primarily a timing-path fix, not the main acceleration.

Selected warm time budget (exclusive CPU function self estimates, ms/frame)

{table}
  TOTAL                                                   {selected:.6f}

This budget is derived by subtracting the frame360 cumulative profile from
the frame368 profile of the SELECTED cache-write-v2 run. Child functions are
excluded from each self time. Submission helpers include ring-capacity polling
and I/O waits; their entire self time is not removable CPU preparation work.
Likewise, the LOAD driver's self time includes {observed_ms:.6f}ms/frame of
start-to-completion observation, overlapping the running GPU. Hardware busy
for those LOADs is {hardware_ms:.6f}ms/frame. Neither belongs on top of the CPU
table. There are2608 jobs/32496 vertices over eight frames (326 jobs and4062
vertices/frame). CPU conversion/copy and source-table lookup remain real work.

The selected GPU busy counter is {warm['timing']['speculative_write']['gpu_busy_ms']:.6f}ms/frame,
overlapping the CPU table. The ring still needs{warm['timing']['speculative_write']['rdptr_polls']:,}
read-pointer polls across eight frames. Submitted command words remain183171,
exactly matching the control; geometry/workload was not reduced to obtain the
gain. That changing mixture of CPU work and ring/memory waits explains why
faster isolated geometry hardware did not translate into its isolated speedup.

Remaining bottlenecks and honest bounds

CPU: indexed decode/eligibility/cull/material/preparation remains about5.3ms,
followed by real clipping/fallback projection and packet submission. The
independent unchanged-client PC audit attributes1.079ms to winding,
0.736ms to slot/clip access,0.596ms to decode,0.492ms to dirty jobs,0.355ms
to color,0.324ms to material,0.272ms to quantization, and0.155ms to UV.
Indexed D-cache penalty is only0.091534ms; a faster vertex source cannot
eliminate those calculations. The nearby recycler changes layout/timing,
so those detailed PC bins are the unchanged-client control, not falsely
relabelled as selected-run measurements. Selected total self time is above.

GPU: the detailed retained-F11 audit measures16.381ms in the fragment FSM,
including4.931ms depth-response waits and3.121ms depth-write-barrier holds.
Triangle derivation is2.052ms, clear writes1.826ms, span setup1.313ms and
projection0.724ms. Perspective issue starvation without simultaneous pixel
blocking is3.189ms; source-finished span drain is5.648ms and overlaps other
waits. Those are BASELINE state/dependency counts, not an additive selected
budget or independent achievable savings. The selected span scheduler hides
part of setup/next-span startup behind the previous span's tail.

The same-window baseline CPU-only estimate was35.746666ms; coupling adds
2.080908ms. Maximal command supply through the actual original GPU/memory
still takes23.517671ms. An ideal GPU SDRAM endpoint saves only2.011154ms of
whole-frame progression despite a much larger GPU-busy reduction. Thus the
remaining limit is the combined CPU preparation and GPU fragment/memory
dependency chain, with backpressure shifting where the CPU waits. Do not
reuse the different361-frame average30.36ms to invent7ms of removable wait.

Cold behavior and longevity

Cold BOB frames234..241 include initialization and actual immutable source
uploads. The selected mean is {cold['timing']['speculative_write']['frame_ms']:.8f}ms versus
{cold['timing']['baseline']['frame_ms']:.8f}ms for the recycler control. This is a different
window from the warm F11 comparison and must not be used as its denominator.
The earlier real-client cold comparison uploaded92400 extra source bytes;
its first frame became7.16499ms slower while following frames improved.
That first-frame difference is net overlap/startup behavior, not an isolated
upload-only timer. The final recycler keeps the top256KiB CRAM reservation,
preserves hits when full, and recycles on a capacity miss at most once per120
rendered frames after the first immediate recycle. Probe exhaustion falls
back; unsupported/mutable input and absent/failed hardware retain CPU fallback.
Normal warm/cold windows perform no recycle; forced-capacity multi-generation
and native/ASan/full-pool/cooldown/wrap proofs are separately bound by delivery.

Throughput changes versus fitting/timing changes

Actual acceleration: bounded exact vertex transform/reciprocal/one-light
work offloaded to LOD5; immutable source data reused from the independent
CRAM chip; completed results read through a cacheable BRAM view; and GPU span
lookahead overlaps next-span preparation with the old fragment tail. The
client still synchronously waits for LOAD and copies results, so this is not
unrestricted asynchronous CPU display-list execution or a new dual-issue CPU.

Fit/timing enablers: unified geometry scheduling/storage, narrowed/packed GPU
storage, queue/request mux factoring, exact divider borrow expression, early
existing-register XY deltas, direct DMA-disabled ring data and invalid-only
depth-address capture. Their selected proofs preserve arithmetic/owned state
and relevant cycle schedules; they do not earn separate FPS gains. Registered
cache lookup/hazard stages trade a measured small latency cost for shorter
paths. Hot0 speculation/full-word acceptance restores that write path without
a new RAM port. Source-pool recycling is a longevity feature with measured
cost, not a speed optimization. No cache-capacity cut or CPU-lighting fallback
variant is credited to the selected result.

Artifact and evidence scope

Application: lod5-recycle-driver-v1/app.elf
SHA256 bb41f2088130935dc637d55a4a1dbd0afddb68f94794440ddb5a67e504d4a2c5
Delivery: lod5-recycle-delivery-v1/payload/Assets/sm64/common/sm64.elf
The reviewed build integration and byte-identical clean rebuild are bundled
there. The device protocol is LOD5 ID0x4c4f4405. Selected cache source is
e42f06c03b8b2aa8cfc69d330c937f51fe7283bc15d9f1b034b657dbeed7afe0.
The actual coupled model uses span-v3/unified-v1; compact-v5 deployment depends
on the separate component/composed native equivalence bridges and its final
resource/timing result. This report deliberately does not supply a v5 timing
number. FPGA closure is not interchangeable with a Pocket performance test.

Exact reports (absolute local paths)

{links}
'''
 OUT.mkdir(exist_ok=False)
 (OUT/'REPORT.txt').write_text(text)
 summary=dict(read_only=True,baseline_ms=initial,selected_ms=selected,saved_ms=saved,
  modeled_time_reduction_percent=lower,modeled_throughput_increase_percent=throughput,
  selected_cpu_self_budget_ms=budget,hardware_load_ms=hardware_ms,observed_load_ms=observed_ms,
  clock_hz=c['clock_hz'],frames=c['frames'],source_profile_functions=funcs,
  no_pocket_fps_claim=True,no_fit_or_clock_claim=True)
 (OUT/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
 evidence=[P/path for _,path in refs]
 evidence += [P/'gpu-critical-bound-v2/result.json',P/'gpu-critical-bound-v2/final-manifest.json',
  P/'cache-write-lod5-coupled-v2/result.json',P/'cache-write-lod5-coupled-v2/manifest.json',
  P/'lod5-recycle-coupled-v1/manifest.json',P/'lod5-recycle-delivery-v1/manifest.json',
  P/'compact-lod5-core-v5/prepared.json',d/'profile.tsv',d/'profile-0360.tsv',
  d/'coupled.json',d/'geometry-operations.csv',OUT/'REPORT.txt',OUT/'summary.json',Path(__file__)]
 (OUT/'manifest.json').write_text(json.dumps(dict(read_only=True,summary_only=True,
  evidence_sha256={str(p.relative_to(ROOT)):sha(p) for p in evidence}),indent=2)+'\n')
 print(OUT/'REPORT.txt');print(sha(OUT/'manifest.json'));print(initial,selected,lower,throughput)
if __name__=='__main__':main()
