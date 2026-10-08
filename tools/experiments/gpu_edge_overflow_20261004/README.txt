Shared GPU edge-range repair (2026-10-04)
========================================

Input: ../Quake2/build/gpu-edge-overflow-handoff.zip (the directory is
capitalized on this filesystem). Its reproducer matches the original
gpu_edge_walker.v SHA-256 exactly:
  dede05d5e1e4eb70514723227ede5d9010c6a96c369a693a49e440a91286272a

The reported triangle draws 57,873 pixels instead of 29,457 because a
13-bit divisor wraps Q4-Y differences at 8192 units / 512 pixels.
The repair retains the full 16-bit unsigned divisor and 17-bit trial
remainder in both shared and parallel divider configurations. Winding
and clip distances preserve 17-bit endpoint differences. Signed slopes
retain all 32 quotient bits plus a sign bit; fractional presteps no longer
clamp steep edges to +/-1024 pixels/line.

The first repair did not fit the current experimental os30 worker build.
The selected compact3 implementation shares setup subtractors, exploits
sorted nonnegative edge heights, takes comparisons from divider borrows,
and computes pixel-rounding carries before 16-bit increments. It loads
the shared divider directly, eliminating 33 temporary operand registers
while retaining its original preparation beat. No FSM states, divider
iterations or span-handshake cycles were added.

Artifacts live in docs/measurements/gpu-edge-overflow-20261004/:
  handoff/                 Original archive contents, unchanged
  baseline-repro/          Reproduced original overdraw
  range-before/            Expected failures in original RTL
  range-divisor-only/      Expected failures with only the handoff patch
  range-compact3/          Selected full-range tests, four configurations
  replay-compact3-parallel{0,1}/
                           Frozen full GPU + actual Pocket SDRAM replays
  routes-compact3/         Raw 0x4E, projected 0x5C/0x54, clip 0x56/0x54,
                           and worker geometry 0x59/0x5B, both windings
  sm64-compact3-results.json
                           12 retained frames: framebuffer, depth, cycles
  gpu-acceptance-final.*   Selected RTL: general + os30 acceptance and
                           independent shared-edge coverage tests
  fit-worker-compact3/     Saved-seed-2 fit of the selected full worker
  REPORT.txt              Final measurements and qualification limits

Quick self-contained regression (no game data or Docker needed):
  make -C src/fpga/test gpu-edge-range gpu-edge-coverage

The range test checks signed-16 endpoint limits, both Y formats, divisor
boundaries, winding, clipping, steep fractional presteps, vertex orders,
backpressure and abort. Its independent analytic oracle excludes only
exact boundary ties and a bound derived from the existing Q16.16 slope
quantization. The original 393,192-pixel subpixel coverage suite and its
watertight shared-edge meshes remain strict and unchanged.

Full GPU handoff replay, after extracting the input archive into handoff/:
  python3 tools/experiments/gpu_edge_overflow_20261004/replay.py --label NEW --parallel 0
  python3 tools/experiments/gpu_edge_overflow_20261004/replay.py --label NEW --parallel 1
  python3 tools/experiments/gpu_edge_overflow_20261004/routes.py --label NEW
  python3 tools/experiments/gpu_edge_overflow_20261004/compare_sm64.py --label NEW
Use a fresh label to preserve measured binaries and source manifests.
The replay build uses the existing worker-v6 frozen harness and replaces
only the edge walker. Those archived harness/capture files are local,
gitignored measurement inputs; the range regression is standalone.

One isolated fit, with unchanged clocks, CPU/cache configuration and seed:
  python3 tools/experiments/gpu_edge_overflow_20261004/fit.py NEW --rtl src/fpga/common/gpu_edge_walker.v
Requires the existing Quartus Docker environment. This selects os30 with
OS30_GEOMETRY_WORKER=1 and OS30_DEPTH_PREFETCH=1, saved seed only, and
neither publishes nor changes any seed. No full seed sweep is performed.

This fixes erroneous coverage and its associated overdraw. The retained
SM64 frames have identical GPU cycle counts: it is not evidence of a
whole-game FPS improvement. Physical Pocket confirmation remains needed.
Keep Quake2's software guard for compatibility with older bitstreams.
