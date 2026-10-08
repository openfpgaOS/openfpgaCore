OS30: PARALLEL MESH PROCESSOR AND PREPARED-TRIANGLE RASTERIZER
2026-10-04

Decision
--------
Replace the current geometry worker, GPU vertex cache, triangle-plane solver
and edge-setup arithmetic with one processor that accepts mesh jobs. Keep the
pixel pipeline, texture/color/depth caches, truecolor combiner/blending, CRAM1
texture bus, main CPU/FPU and audio mixer. The immediate objective is to remove
CPU preparation of individual vertices/triangles, not merely accelerate the
existing matrix multiply again.

The rasterizer half of this split is implemented privately and tested here.
There is no completed replacement mesh processor or deployable game build.
The authoritative measurements and limitations are in:
  docs/measurements/os30-parallel-20261004/REPORT.txt
  docs/measurements/os30-parallel-20261004/results.json

Why the preceding worker did not deliver a large gain
----------------------------------------------------
It still requires the CPU to classify bounds, prepare matrix contexts, walk
faces, decide fallbacks, materialize selected attributes, and pack every upload.
The CPU constructs the work that the worker then accelerates. Geometry math
already overlaps much of rasterization, so making that math faster has little
effect while the CPU produces commands slowly. Enlarging the output FIFO does
not remove this work.

The P4 entrance-window profile has about 4.89 ms of geometry_prepare self time,
3.75 ms of gfx_sp_vertex, 3.07 ms of cached face selection, and 4.03 ms of batch
packing/submission. Fallback triangle emission/clipping adds substantial work.
These are estimated CPU timings, not Pocket measurements, and include dispatch
and bookkeeping rather than pure arithmetic.

Fresh coupled sensitivity runs execute every original instruction and all GPU
and SDRAM RTL, but discount selected CPU self-time. The control reproduces all
previous timestamps/counters and output bytes. With 75% of bounds/face/packing
cost removed, the entrance window improves only 11.2%. Including vertex and
fallback-triangle/clipping costs gives 26.7%. Discounting the broader renderer
by 90% gives 43.1%. The busier memory schedule reduces those latter two gains
to 23.1% and 38.1%. These are design sensitivities with hypothetical CPU costs;
they are NOT an implementation or predicted Pocket FPS. New processor and DMA
costs are absent; the original upload volume is still present.

The implication is concrete: tiny batches and triangles crossing clip planes
must be handled by the new processor, rather than remaining the ordinary slow
CPU path. Simply removing the CPU winding test already failed in the preceding
experiment because it increased attribute/upload work for rejected faces.

Concurrent ownership
--------------------
At steady state, different batches can occupy these stages:

  Main CPU                 Burst fetcher        Geometry processor       Rasterizer
  game + material jobs --> mesh/state N+1 --->   mesh N transform/clip --> triangle N-1 pixels
      descriptor ring      inactive RAM bank    ready packet queue       color/depth caches

N refers to batches, not independently reordered frames. Preserve submission
order, especially alpha blending. Rasterization consumes prepared triangles
without reading or waiting for the processor's live vertex cache. Two mesh
banks permit fetching the next job while processing the current one. A bounded
packet queue lets geometry advance during long pixel/memory work.

The CPU sends a mesh reference, immutable state snapshot, index range and flags.
Static SM64 VTX/TRI display-list runs should be compiled once into mesh records,
reusing the existing immutable-input/run cache validation. The CPU retains game
logic, scene/BSP traversal, material/texture residency and exceptional formats.
It should not build per-vertex output arrays or walk every face of an accepted
job on every frame. Compile structural runs, not mutable geometry results.

The processor handles transform, ambient/directional lighting, UV preparation,
conservative rejection, clipping, winding, projection, attribute planes and
edge presteps. It emits ready raster packets directly to on-chip storage. Do
not write geometry results to SDRAM and have the GPU fetch them again.

Pocket currently ties SDRAM arbiter M2 reads AND writes idle in core_top.v.
A bounded read-only mesh fetcher can use M2, preserving the CPU and mixer
channels and the arbiter's existing fairness mechanism. This avoids routing
geometry through the color/depth cache. It still shares physical SDRAM bandwidth;
M2 is not a second memory bus. Measure fairness, refill stalls and mixer deadlines
with the real reader enabled. Prefetch aligned bursts into local RAM instead of
issuing a random external load for each arithmetic instruction.

The app's present wait is already deferred until after the next frame's game
logic. Moving that same wait again does not create a new overlap opportunity.

Processor shape and budget (targets, not fitted implementation)
--------------------------------------------------------------
A small microcoded integer/DSP engine is preferable to a duplicate of the main
CPU. It needs local RAM, loops/branches, wide accumulation, reciprocal/divide
and saturated fixed-point operations. It does not need an OS, MMU, data cache,
branch predictor or a full duplicate FPU. Share the setup arithmetic rather
than synthesizing independent transform, clipping, edge and plane datapaths.

Initial combined processor + fetcher + queue target:
  <= 2,000 ALMs, <= 6 M10Ks, approximately 6 DSP blocks, same 100 MHz domain.
  These are acceptance budgets for the implementation, not synthesis results.

A possible six-block RAM allocation is two M10Ks for 512x32 microcode, two
256x32 mesh banks, one 256x32 phase-shared context/clipping/register scratchpad,
and one 256x32 packet FIFO. Four worst-size edge/plane packets plus their 0x4A
material records fit in that FIFO. Port scheduling and phase ownership must be
proved; a three-port register file cannot be assumed to cost one M10K.

Raw inputs, matrix/light state and indices fit a bank before expansion. Copy
live state/indices into scratch before overwriting that bank with transformed
vertices. The scratch layout can reuse matrix/light storage after transformation
for two clipping polygons. These lifetimes are a design proposal, not tested
allocator code. Start with whole-batch ownership; do not reuse a bank on merely
accepting a descriptor.

The prepared-raster backend uses 14,683/18,480 ALMs, 301/308 M10Ks, 40/66 DSPs
and 1,793/1,848 LABs at saved seed 2. Compared with the staged attribute-worker
fit this removes 3,520 ALMs, seven M10Ks and 54 occupied LABs. No CPU cache cut
was needed. Only 55 LABs are wholly unused, despite 3,797 nominal free ALMs:
placement of the actual processor, DMA routing and clock/reset loads remains
the deciding test. Do not claim the complete accelerator fits from subtraction.

The raster backend's minimum main-clock Fmax is 92.69 MHz; worst all-check
setup is -0.804 ns, including external SDRAM timing. Configured frequency is
still 100 MHz. It is not timing closed and is not a replacement release.

Interface and correctness
-------------------------
The private 0x60 experiment accepts three xy words plus all seven or ten plane
triples. It demonstrated that removing plane calculation alone gives only
about 3.2% entrance GPU replay improvement: serial edge setup then becomes
visible. The selected private 0x61 experiment replaces the xy words with nine
prepared-edge words and keeps the seven/ten plane triples (30/39 payload words).
It reduces entrance GPU replay cycles from 2,219,716 to 2,015,302, about 10.1%
more raster throughput with precomputed inputs. This is not a game speedup.

0x61 reuses 0x4A material/clip/blend state. Plane order is s/w, t/w, 1/w,
green, red, blue, independent depth, and optionally additive Dr/Dg/Db.
Each plane supplies origin, du and dv with the existing widths/rounding.
Edge words are documented in gpu_span_walker.v. The DDA preserves subpixel
center sampling, legacy corner sampling, edge ties, clipping and backpressure.

The corrected full 33-bit edge slopes MUST survive through fractional clipping
presteps. Only the final integer-scanline DDA increments may discard bit 32.
The new producer must implement a multiword correction or wider multiply for
those presteps; silently feeding a 33-bit slope to the old signed-32 multiplier
would recreate the Quake II edge-overflow bug.

The new frontend must preserve flat/provoking alpha, winding and vertex order
through clipping, perspective/depth conventions, the C+D combiner and texture
generation/multiple-light fallback semantics. Fixed-point lighting is not
arbitrarily float-bit-exact; the P4 precision limits remain applicable. New
clipping must be compared against actual game color/depth, not accepted merely
because its polygons look plausible.

State and raw data remain owned by the job until its completion fence. CPU
writes require explicit cache cleaning/ownership transfer; the system is not
coherent multicore. Preserve fences through queued fetches, processor work,
raster packets and physical color/depth writeback. Ring-space retirement is
not data-lifetime completion. Reset/abort must cancel both fetch and packet
state. A material change cannot alter an earlier in-flight packet.

Quake II applicability
----------------------
The local repository is ../Quake2 (capital Q). Its of_gl1.c already maintains
immutable BSP polygon caches, packed raw vertices, sampled lightmap colors,
bounds and raw/indexed submission paths. Those are natural mesh-job producers.
Keep BSP/PVS selection and lightmap-version ownership on the main CPU; share
the indexed geometry/raster contract with SM64 rather than putting an N64-only
display-list interpreter in the core.

Fractional animated vertices, water/particles, clipped polygons and alternate
depth ranges need an explicit format or ordered fallback. Do not assume the
current signed-16 integer SM64 input format handles every Quake II draw. This
review includes source inspection, not a Quake II frame-rate measurement.

Evidence and remaining implementation
-------------------------------------
Both prepared-raster interfaces pass 12 retained SM64 full-memory/fence replays.
The plane-only stream contains 10,937 packets; edge preparation rejects wholly
clipped triangles, leaving 10,341. The edge stream includes 682 C+D packets.
The prepared DDA matches the corrected full edge walker on 10,000 random and
boundary cases, 372,593 spans and 78 aborts, with independent consumer stalls.
All 64 headroom-run color/depth comparisons match; the zero-discount control
matches the earlier timestamps and counters exactly.

Next implementation units are the mesh-job ABI/client, microcoded arithmetic
and clipping program, M2 burst fetcher, ownership/fence integration and complete
os30 fit. They must be measured together with their real overheads. Use a >=30%
whole-frame throughput improvement as the substantial-gain acceptance target,
then verify on the Pocket in the same busy scene. From a 19.5 FPS observation,
30% faster is 25.35 FPS; reaching 30 FPS requires about 54% more throughput.
The model does not establish either Pocket result.

Scripts freeze their inputs and preserve each run. prepare_planes.py and
prepare_edges.py generate private RTL; build_planes.py/check_planes.py build
and replay it; check_span_walker.py checks the edge consumer; build_headroom.py
and run_headroom.py implement the timing sensitivity; fit_backend.py runs one
saved os30 seed per architecture; analyze.py audits retained evidence.

No production RTL, sibling app/SDK/runtime, deployment package or SD card was
changed. No commit or full seed sweep was performed. The backend SOFs/RBFs
deliberately lack the replacement geometry producer and must not be deployed.
