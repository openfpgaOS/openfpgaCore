SM64 geometry, visibility and depth-prefetch experiment (2026-10-03 UTC)

Selected result: docs/measurements/sm64-geometry-fast-20261003/results.json
(All campaign paths below are relative to the openfpgaCore repository.)

This extends the previously published geometry batching renderer. It converts
immutable model vertices to a compact input format once, caches normal lighting,
defers attributes until triangles survive backface culling, skips wholly
invisible vertex batches with recoverable state snapshots, and retains material
state across display-list operations that cannot change it. A bounded x/y guard
band leaves scissoring to the GPU; near/far clipping, fractional/dynamic inputs,
texture generation and unsupported materials retain CPU fallbacks. GPU command
uploads remain on the fitted CPU ring; the DMA experiment was rejected.

Selected hardware enables the existing span-start depth prefetch together with
geometry acceleration. One os30 fit used saved seed 2. The combined fit improved
setup/hold versus the previous geometry bitstream and costs 7 additional ALMs,
zero additional M10Ks and zero additional DSPs. Packing uses 1,847/1,848 LABs.
100 MHz timing is still open: Fmax 91.04 MHz, setup -0.984 ns, hold +0.112 ns.
No default production variant, seed file, other variant, or SD card was changed
by this campaign. Both hardware switches remain explicit build options:

    make -C src/fpga/targets/pocket build VARIANT=os30 SEED=2 \
        OS30_GEOMETRY=1 OS30_DEPTH_PREFETCH=1 JOB=os30-geometry-prefetch

Validated workload: 721 scripted intro/menu/Bob-omb Battlefield frames; sampled
GPU frames 40, 120, 200, 360, 400, 500, 600, 720. The route's player/camera/scene
trace exactly matches the original. Qsim estimates CPU work only, using an older
single-issue timing approximation with audio disabled and no GPU backpressure.
It does not reproduce reduced-accuracy FMA or real CPU/GPU overlap. RTL replay
uses the Pocket SDRAM path with quiet, scanout/audio, and added CPU-read traffic.
These measurements must not be reported as observed Pocket FPS.

CPU estimate at the entrance: original 44.01 ms; published batch 43.06 ms;
selected 36.42 ms. This is 17.3% less CPU work / 20.9% greater CPU throughput
versus the original. Across six equal 60-frame gameplay windows, aggregate
modeled CPU throughput improves 17.2% versus the original. It has not established
30% whole-game speedup. New hardware lowers selected-renderer GPU work by 5-8%
in gameplay scanout/audio captures; versus the previous published renderer and
hardware, combined GPU throughput improves roughly 5-7% in those captures.

Rendering checks: 24 framebuffer/depth pairs match the quantized CPU reference,
24 capability-disabled pairs match the published fallback, and 24 prefetch pairs
match the same software on the old geometry hardware. Traffic modes produce
identical buffers. This does NOT mean bit-identical output versus the original
renderer: delegating x/y clipping changes edge/texel interpolation. The saved
edge-comparison.png shows the entrance comparison. No detail level, lighting,
object update rate, world geometry, or gameplay feature was deliberately reduced.

Additional checks: 378 GPU acceptance assertions; sanitized batch/wire boundary
checks; 9,858 immutable cache input checks including table/pool exhaustion; and
144,000 moving-frustum comparisons to independent eight-corner tests, including
deferred vertex recovery after source, matrix, texture, lighting and fog changes.
The host cache test substitutes only the immutable ELF range with a fixture array.
The production linked ELF's immutable asset range is checked separately.

Reproduce final software validation (from repository root):
    python3 tools/experiments/sm64_geometry_fast_20261003/check_boundaries.py
    python3 tools/experiments/sm64_geometry_fast_20261003/check_visibility.py
    python3 tools/experiments/sm64_geometry_fast_20261003/analyze.py

Capture an independently built ELF under a NEW label:
    python3 tools/experiments/sm64_geometry_fast_20261003/capture.py LABEL --elf PATH
    python3 tools/experiments/sm64_geometry_fast_20261003/replay.py LABEL --modes 0,1,2

Prefetch replay adds:
    --hardware-label prefetch --exe \
    docs/measurements/sm64-geometry-fast-20261003/replay-prefetch-v1/obj/Vtb_gpu

Capture uses the private qsim build in the measurement tree, based on the first
level harness with capability and diagnostic counters. It does not rasterize
accelerated commands as an oracle; separate RTL replay is required. Each capture
records input hashes and copies its ELF. source-final-candidate freezes selected
renderer files. fit-prefetch-v1 and accept-prefetch-v1 freeze hardware inputs.

Discarded experiments remain evidence, not shipping code. In particular:
light-v1/light-gpucull/split-v1 had a wrong normal offset and are invalid renderers;
light-ceiling deliberately omitted lighting and is only an upper-bound probe;
DMA, temporal clip caches, whole-face/list caches, smaller batches, and a full
attribute cache did not provide a reliable net gain. None is in the final source.

stage_runtime.py verifies the new SOF/RBF, unchanged boot ROM/os.bin pairing, and
manifest. publish.py --apply updates only validated SM64 files after backing up
the previous renderer/runtime under .obj/geometry-fast-backup-20261003. Its
restore.py checks hashes before restoring; rebuild before copying restored files.
The final build and package are checked against the captured ELF and runtime.
No full seed sweep, SD-card copy, git commit, or push is part of this procedure.
