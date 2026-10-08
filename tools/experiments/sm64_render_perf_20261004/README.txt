SM64 P2 sampled renderer diagnostic, 2026-10-04 UTC

The Pocket P1 screenshot showed approximately 20.7 rendered/displayed FPS,
37.6ms in rendering, 9ms game logic, 1.3ms audio and <0.1ms frame acquisition.
P2 splits that renderer time before choosing another performance change.

Artifacts: docs/measurements/sm64-render-perf-20261004/ (local/ignored).
The starting app is P1 SHA256
5ba293e6c6afe9437e1eef424bb2f116a6ea7b25e5a82a00d1c012d69c8bf493.
before.json guards the actual sibling SM64 source, package, SDK and runtime.
sm64-render.patch is the final diff against that source. instrument.py applies
the probes to the guarded private copy only. No vendored SDK files change.

The standard Select+L+R shortcut displays the last completed two-second window.
Detailed buckets sample one rendered frame in eight, only while hidden.
Nested probes subtract their time from their parent: no double-counting.
GCC cleanup handles returns, break and goto; a bounded profiling stack drops
a bad sample without interrupting the game. Original P1 snapshot fields keep
their binary offsets. P2 appends version, sample count, sampled render time,
and the nine exclusive buckets. Profiling stays frozen while the panel shows.

Validation:
  python3 tools/experiments/sm64_render_perf_20261004/check_timing.py
  python3 tools/experiments/sm64_render_perf_20261004/check_disabled.py
  python3 tools/experiments/sm64_render_perf_20261004/analyze.py
  python3 tools/experiments/sm64_render_perf_20261004/publish.py

Build through the SDK Docker toolchain, using the explicit absolute app.elf
target; do not run make copy: the sibling runtime still carries the older
FPGA bitstream. Publication/deployment updates only the app and its source.
The worker FPGA/OS hashes on the Pocket are separately guarded and preserved.

The final hidden and panel captures each run 721 steps with functional audio.
Both match all scene records. Twelve saved frames replay on the actual worker
RTL test executable; ten hidden/before/after-panel color and depth comparisons
match the established baseline. Visible-panel frames are inspected for layout.
The 77-frame open-panel interval retains an identical saved snapshot.
PERF_DIAG=0 produces a loadable image identical to the original ordinary app
except eleven __assert_fail source-line arguments. No render probes remain.

The detail probes are diagnostic: they add roughly 3.3-4.3% modeled CPU cost
over P1 in the six first-level windows. Initial out-of-line/token and stack
versions are retained as capture-hidden and capture-light-hidden; the inline
trial is capture-inline-hidden. The final version removes redundant probes.
This is not a speed improvement. Hardware overhead needs device measurement.
qsim's synthetic clocks, simplified audio and eager GPU cannot validate actual
FPS, cadence or CPU/GPU overlap. Do not interpret its preview's numbers as
measurements from a Pocket.

Correction to earlier performance notes: this SM64 app allocates and publishes
textures in SDRAM. It does not call the SDK fast-texture/CRAM1 upload API.
PSRAM clock changes therefore cannot accelerate this app's current textures.

No FPGA changes, seed sweep, commit, push or public binary distribution.
