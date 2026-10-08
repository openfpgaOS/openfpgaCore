SM64 geometry batching, 2026-10-03 UTC

Outcome: the existing accelerator trial now batches up to 64 consecutive raw
triangles per CPU submission. Immutable Vtx bounds are cached in 16 KiB of CPU
memory; dynamic vertices are rescanned. Clip planes are cached by matrix epoch
and aspect. Slot ownership is released once per context instead of per vertex.
Shared material helpers are inlined to retain the old fallback performance.

The geometry command ABI is unchanged: matrix 0x50, raw loads 0x59 in groups of
at most three, and indexed triangles 0x5B. One reservation covers each run
(maximum 1,184 of 4,095 staging words). Unique dirty vertices precede triangles,
which retain display-list order. Runs stop at every state/material change,
unsupported triangle, alpha change or capacity limit. TRI2 remains atomic when
falling back. Mixed matrix contexts retain their own snapshots.

Measured CPU work falls 7.7-8.8% versus the user-tested accelerator trial.
The gain versus the original pre-accelerator renderer is only 1.7-3.2%.
Entrance: 46.638 -> 43.062 ms at the model's 100 MHz. This is not measured FPS
and does not deliver the 30% performance target. Separate RTL GPU workloads
are within 0.4% of the previous trial in the scanout/audio schedule.

Validation:
- Both accelerator-on and capability-off runs complete the established
  721-frame intro/menu/Bob-omb Battlefield script, with identical scene state.
- 48 exact framebuffer/depth pairs (8 frames x 3 traffic modes x 2 paths).
- Accelerator output also matches the previous quantized CPU reference.
- All accelerated vertex/batch/triangle/materialization counters match the
  prior trial over all 721 frames.
- Actual extracted C functions pass AddressSanitizer/UndefinedBehaviorSanitizer
  checks for alpha boundaries, TRI2 atomicity, partial reloads, mutable bounds,
  multi-matrix uploads, maximum ring reservation, grouping and draw order.
  LeakSanitizer is disabled because this sandbox uses ptrace; these harnesses
  use static/stack storage and do not allocate.
- Linked read-only asset range checked using ELF symbols.
- The resulting app is checked byte-for-byte against the locally published
  SM64 app and package. Runtime bitstream hashes stay unchanged.

Artifacts: docs/measurements/sm64-geometry-batch-20261003/ in the core repo.
Authoritative files: results.json, boundary-checks.json, source-final/,
capture-final/, capture-fallback-final/, replay-results-final-fallback-final.json,
published.json, publication-verified.json. Failed or partial prototypes are
never inputs to analyze.py or publication.

Experiments retained for audit:
- checks-v1: cached visibility checks alone saved little CPU work.
- batch-v1: software batching, about 44.10 ms at the entrance.
- batch-v2: GPU-only culling saved little CPU time and increased GPU work;
  discarded. CPU conservative culling is retained.
- batch-v3: release vertex ownership by context, about 43.43 ms at entrance.
- batch-v4: batching remaining CPU-transformed triangles added more eligibility
  overhead than it saved; discarded.
- batch-v5: an invalid staged executable mixed old/new renderer objects after
  a timestamp-preserving source rollback. It faulted at frame 446. Both renderer
  objects were removed and rebuilt; only the complete final captures are used.
- final: batch-v3 plus inlined material helpers; 43.06 ms at entrance.

Reproduction from the core repository:
  make -C docs/measurements/sm64-geometry-batch-20261003/SM64/src/sm64 -j4
  python3 tools/experiments/sm64_geometry_batch_20261003/check_boundaries.py
  python3 tools/experiments/sm64_geometry_batch_20261003/capture.py NEW_LABEL \
    --elf docs/measurements/sm64-geometry-batch-20261003/SM64/.obj/sm64/app.elf
  python3 tools/experiments/sm64_geometry_batch_20261003/replay.py NEW_LABEL --modes 0,1,2
  python3 tools/experiments/sm64_geometry_batch_20261003/analyze.py

Capture labels are immutable. Replay refuses incomplete captures. The qsim
harness and fitted RTL replay binary are reused from the previous geometry
campaign; no synthesizable RTL changes or fitter runs are needed here.
Before rebuilding after a source rollback, remove both renderer .o/.d pairs
or update their timestamps, so an old interface cannot remain linked.

Model limits: approximate CPU pipeline/cache, no hardware reduced-accuracy FMA,
audio off in CPU capture, separate CPU/GPU workloads without overlap or ring
backpressure, one scripted route, eight sampled images. Other levels and actual
Pocket FPS remain unmeasured. The user reported similar performance for the
previous trial; no hardware measurement of this software update exists yet.

The os30 seed-2 bitstream remains at 17,911 ALMs, 307 M10Ks, 41 DSPs. It is
configured at 100/100/100 MHz but estimated Fmax remains 88.47 MHz with
-1.303 ns setup slack; this update does not change its timing.

The sibling SM64 folder contains the selected sources and a local package.
Rollback is .obj/geometry-batch-backup-20261003/restore.py in that folder.
No full seed sweep, SD-card copy, bitstream rebuild or commit was performed.
