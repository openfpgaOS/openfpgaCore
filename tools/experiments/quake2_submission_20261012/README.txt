Quake2 CPU submission experiments — 2026-10-12

REJECTED: neither candidate provides a meaningful frame-time improvement.
Nothing was deployed. No RTL, bitstream, clock, firmware or memory mapping was
changed. This is a source-and-evidence archive, not an installable update.

                          Main demo       Sound demo      Live movement
World cached packets       -0.1248%         -0.1050%         -0.1724%
Opaque alias chunks        -0.0721%         +0.0233%         -0.0143%

Changes are modeled CPU cycles/frame relative to the freshly built control from the pinned current Quake2 source. Negative means fewer cycles. They are not measured Pocket FPS or
coupled Vexii/CPU/GPU/memory timings. Main covers 360 frames and sound/live
120, excluding 20 startup frames from performance. Sound and main cover
different windows; their difference is not an audio-cost comparison.

The world candidate caches complete existing fan packets, then combines them
under a 512-word bound. Actual eligible world-fan reservation counts fall by
71.40%, 73.91%, and 68.00% in the three workloads. This denominator is eligible
world-fan reservations, not all graphics work or hardware submissions. All
wire commands and word counts remain identical. Added rounded map hunk cost
is recorded per public-demo BSP in evidence/world/evidence/map-growth.json.
Those memory costs are not justified by the measured result.

The alias candidate batches CPU admission/bookkeeping within one opaque,
non-diagnostic model draw. It retains the original triangle math and payload
copy, with at most eight triangles per reservation/commit and no extra payload
buffer. Larger chunks use capacity already known to be available; any hardware
wait still requests only one real triangle. Clipped expansion stays in the
same opaque state. Blend/diagnostic draws retain the old path. The synthetic
65-triangle capacity test uses nine reserve/commit pairs instead of 65; this
is not a measured whole-game request reduction. Actual hardware publications,
read-pointer observations, commands and words are unchanged in all three
worker workloads. CPU-only main regresses 0.3249%; inline main changes -0.1143%.

Correctness and boundaries

Both candidates preserve all command bytes, order, frame assignment and tail,
all functional CPU framebuffer captures, and checked sound calls. Four main-demo
frames (20, 80, 180, 300) additionally replay on the unchanged actual GPU RTL:
complete RGB565 and compressed-depth buffers are exact for both candidates.
Those four samples do not establish arbitrary-level coverage or physical FPS.
The functional CPU observer does not itself rasterize 3D commands.

Alias passes the original strict CPU comparisons. The world's original strict
worker comparison fails solely because the gpu_barriers diagnostic counts
change with batching. The original failure receipts are retained. A separate,
explicit batch-semantics check validates all functional fields and command
ordering; this is not presented as an unchanged strict pass. Both candidates
also pass CPU-only and inline-geometry main comparisons.

World passes 15 ASan/UBSan groups plus packet/capability checks. Alias passes
14 ASan/UBSan groups including scope/clipping checks, plus 6,270 bounded
capacity/partial/refusal cases checking 20,484,660 output words. Final bound
receipts are evidence/validation/final-result.json and REPORT.txt. The package
keeps their source and measured ELF identities; selected ELF binaries are not
included. Passive count receipts preserve their explicit startup/full scope.

Source and reproducibility

baseline-source.tar.gz contains exactly the 715 files pinned by the source
snapshot, including the original SDK startup objects/libraries and upstream
auxiliary icon/crosshair/macOS bundle files. It contains no game PAKs, demo
recording data, large replay traces, or deployment binaries. Keeping these
baseline build inputs is distinct from packaging selected game executables.
Each variant carries exact changed-file preimages/postimages and a forward/
reverse-verifiable patch. Variants are independent; do not stack their patches.

Verify everything without changing source or contacting a service:
  python3 tools/experiments/quake2_submission_20261012/package.py verify

Stage one variant into a directory that MUST NOT EXIST:
  python3 tools/experiments/quake2_submission_20261012/package.py stage world /tmp/q2-world-review
  python3 tools/experiments/quake2_submission_20261012/package.py stage alias /tmp/q2-alias-review

The CLI verifies package hashes and every archive member before staging.
Extraction accepts only unique, pinned regular files with safe relative paths;
links and special files are rejected. Staging never overwrites an existing
checkout. Recheck relevant source pins later with:
  python3 tools/experiments/quake2_submission_20261012/package.py check-stage alias /tmp/q2-alias-review

Build from staged source using the measured native GCC 15.2 toolchain:
  make -C /tmp/q2-alias-review/src/quake2 USE_SDK_CONTAINER=0 GPU_PROFILE=0 OBJ_DIR=/tmp/q2-alias-review-obj /tmp/q2-alias-review-obj/app.elf -j8
Use analogous absolute paths for the world candidate. Do not invoke packaging,
copy or deployment targets. Different toolchains can produce different ELF
hashes or performance. Build-path effects must also be recorded when comparing
linked executable hashes; the receipts identify the actual measured files.

Portable host tests (host C compiler with ASan/UBSan required):
  python3 /tmp/q2-alias-review/tools/check_quake2_renderer.py
  python3 /tmp/q2-alias-review/tools/check_quake2_opaque_batch.py
  python3 /tmp/q2-world-review/tools/check_quake2_renderer.py
  python3 /tmp/q2-world-review/tools/check_quake2_geometry.py

The CPU/RTL benchmark recipes are preserved as provenance, not advertised as a
portable benchmark. They reference the retained local qsim/RTL model and public
demo fixture, none of whose PAKs, executable model or large traces are bundled.
Complete performance reproduction requires those separately pinned inputs.
No claim is made that these source tests alone reproduce the frame measurements.
