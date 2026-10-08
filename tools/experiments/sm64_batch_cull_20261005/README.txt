Private SM64 software batch culling experiment, 2026-10-05

Final result: negative performance experiment. Nothing is selected for publication.

Root: docs/measurements/sm64-batch-cull-20261005/
Staged source: SM64/src/sm64/sm64/src/pc/gfx/
Original source: source-baseline/{gfx_geometry.h,gfx_vertex_cull.h,gfx_pc.c}
No sibling SM64, FPGA source, SD card, or production runtime is edited here.
The root agent owns build/publish decisions; a source candidate is not a release.

Scope and safety
- Geometry capability disabled (GEO0) can reject a wholly outside vertex batch.
  Accepted batches still run the ordinary CPU loader; GEO0 never accepts raw
  quantized GPU geometry, allocates its contexts, or changes GPU capability bits.
- A prospective bounding-box reject is confirmed against every original vertex
  using the actual CPU selected-coordinate/W expressions. A combined matrix
  plane is not itself sufficient: finite cancellation can make the old AABB
  plane negative while the CPU evaluates x == w. Exact cancellation regression
  and actual RISC-V MUL/FMA/ADD/aspect DAG audits are retained.
- Mutable vertices and matrix/light/UV/fog state are copied into the existing
  deferred snapshot; a later mixed triangle recovers load-time values. Recovery
  checks each index below 32 separately, permitting ordinary slots 32..63 in the
  same triangle. Common clip-AND rejection remains valid because each deferred
  clip bit is a subset of every normal CPU clip mask in its captured batch.
- GEO1 hardware preparation and quantization remain unchanged. The inherited
  GEO1 fixed-margin classification is not being claimed universally conservative.

Versions (each source/artifact is hash-bound; older ones are archived)
1. Move capability gate after confirmed coarse cull; fix mixed-index recovery.
   Correctness passed, but P3 first-world modeled cycles regressed 2.36%, normal
   regressed 3.12%. Not selected.
2. Separate noinline GEO0 path, bounds-only immutable cache and six frustum
   planes; skip materialization calls for slots with no pending snapshot.
   Additional RV32 BSS 32,944 bytes. P3 cycles regressed 1.06%. Not selected.
3. On a failed immutable-source cull attempt, skip the next seven attempts.
   Skipping always means full CPU vertex loading, never reusing a reject.
   Count/source collisions reset retry, mutable inputs always rescan, successful
   rejects always test current matrix/aspect and confirm CPU clip expressions.
   Cache entry remains 32 bytes. P3 cycles regressed 0.72%, normal 2.32%. Not selected.
4. Replace bounded count<=available/sizeof(Vtx) with count*sizeof(Vtx)<=available
   only in the software helper (caller admits 8..32, target Vtx 24 bytes, max 768).
   Skip the old hardware bitset/owner cleanup only when both masks are zero.
   P3 cycles regressed 0.56%, normal 2.10%. Not selected.
5. Fix demonstrated direct-map collisions using at most 16 wrapped probes in the
   same 1024-entry cache, replacing only the home slot on budget exhaustion.
   Observed 252 eligible immutable keys had 21,647 occupied-key collisions.
   Instructions improve about 0.4%, but timing is inconsistent: P3 modeled
   cycles improve 0.818%, normal cycles regress 0.493%. Not selected.
   The earlier 1024-probe draft is archived and was never built or selected.

Reproduce current source from the guarded baseline
  python3 tools/experiments/sm64_batch_cull_20261005/prepare_v5.py
This checks original hashes, emits only the two allowed source files, produces
exact baseline and parent patches, and records recipe hashes. It accepts only
the exact previous/current version in the staged source tree. The earlier
prepare scripts are explicit dependencies, not additional production actions.

Focused verification
  python3 tools/experiments/sm64_batch_cull_20261005/check_functions.py
  python3 tools/experiments/sm64_batch_cull_20261005/check_geo1.py
  python3 tools/experiments/sm64_batch_cull_20261005/check_replay.py

check_functions extracts the actual headers, gfx_sp_vertex and triangle recovery
block. The reference is the exact CPU loader with only geometry_prepare replaced
by 0. Controlled host immutable bounds and an unused light-direction stub are the
only platform substitutions. ASan/UBSan and application fast-math flags run:
- 12,007 batches, 240,126 loaded vertices compared to ordinary CPU results;
- 126 clip boundaries including negative/zero W, finite cancellation and special
 result bit checks; mutable source/state snapshots and mixed-index triangles;
- 1,181 cache/plane checks, deliberate collisions, full-range checks, 1,000 eligible
 visible loads; retry expiry/camera/aspect/count/collision behavior;
- 2,304 nonzero/zero hardware-owner cleanup transitions;
- 18 actually colliding source keys across index 1023, 16-probe exhaustion,
 home replacement, invalid-input preservation and per-key retry retention.
The original target plane-cache memcmp assertion was overstrict: the separate
software helper contracts W +/- aspect*M into FMA while the hardware helper
shares a rounded multiply with guard planes. The preserved failure is diagnosed
by actual target code and a split-product/fmaf oracle. The corrected test checks
software epoch/aspect invalidation exactly, while unchanged authoritative CPU
confirmation checks still protect every rejection.

The generated functions.c also builds as an actual RV32 SDK application; parent
builds it and validation agent executes it in the private qsim. The target counts
may differ from host due to actual FMA rounding; each checks its own ordinary CPU.

check_geo1 retains the previous actual-function regression on old/new headers:
144,000 moving frusta plus 9,858 bounds/cache checks with byte-identical reports.

Independent source/target arithmetic audits are under independent-audit*.
Controlled matching baseline/candidate normal/P3 application runs, first-world
frame costs and selected-dual GPU replay are under validation/. Host verification
and qsim modeled timing are distinct from physical Pocket FPS measurements.

Final target result: actual RV32 harness passes guest exit0 (12,007 batches,
240,126 vertices). It observes and proves the split bd48b700 / fused bd48b730
plane coefficient difference. All 32 paired selected-dual GPU replays pass.
focused-qualification-v5.json binds source, replay, host, target and renderer
evidence; selected_for_publication remains false. No source or SD publication.
