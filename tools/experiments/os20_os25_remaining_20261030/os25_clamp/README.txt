Exact os25 GPU clamp staging requalification, 2026-10-30

The existing INCLUDE_GPU_CLAMP_PIPE option moves the final clamp selection
from coordinate capture into the existing p0a-to-p0 boundary. It changes no
state transitions, queue capacities, fragment issue rate or public features.
It directly targets spanprod_clamp1_max -> p0a_t_y timing. The transform already
ships in os20 and os30; this experiment qualifies the full os25 parameter set.

Functional result: baseline/candidate acceptance output is byte-identical:
  quiet:   276 passed, 0 failed; 20,723,980 cycles
  delayed: 277 passed, 0 failed; 21,704,223 cycles
The production-derived acceptance includes translucency, affine clamp,
perspective/Q29 spans, chunk handoff, depth and command-drain coverage.
Column throughput and framebuffer hashes also match exactly:
  quiet:    9,826 / 12,312 cycles, ordinary / lit columns
  delayed: 29,433 / 41,747 cycles
This proves retained rendering/cycle behavior in these workloads. It is not
a measured frame-rate gain or physical timing qualification.

Final physical result: reject for promotion. With the unchanged baseline CPU,
clamp-only seed 20 estimates 95.17 MHz versus the control's 96.87 MHz and fails
hold at -0.105 ns. The failing fast-0 path is the used video x_count[9] duplicate
to scanout.lcd_x_count_prev[9], not an unused SDRAM endpoint. ALMs fall slightly
from 15,490 to 15,460 and M10Ks stay at 296, which does not justify a hold failure.
The audio-bypass plus clamp combination also loses timing margin (90.59 MHz).
Keep the os25 control seed 20: 96.87 MHz estimate, +0.078 ns worst hold. Its main
setup still fails at the configured 100 MHz. These were targeted fits, not a
full seed sweep or Pocket hardware test. See ../physical-results.json for exact
proof files/hashes and the final os20 radix4 / os25 control selection.

Reproduce from the repository root with Verilator and g++ installed:
  python3 tools/experiments/os20_os25_remaining_20261030/os25_clamp/run.py check --output /tmp/os25-clamp-recheck
Audit the existing raw output without rebuilding:
  python3 tools/experiments/os20_os25_remaining_20261030/os25_clamp/run.py audit --output docs/measurements/os20-os25-remaining-20261030/os25-clamp

The runner pins the original macro list even after promotion, checks source
hashes, and uses existing check_gpu_target_matrix.py pixel oracles. Throughput
uses the same compiled models with the existing GPU_PERF_POCKET driver.
All output is isolated; production config and HDL are not edited.

Original raw outputs (ignored build data):
  docs/measurements/os20-os25-remaining-20261030/os25-clamp/
  baseline/os25/{manifest.json,quiet.log,delayed.log,perf-*.log}
  candidate/os25/{manifest.json,quiet.log,delayed.log,perf-*.log}
  qualification.json, perf-build.json, reproduction-results.json
