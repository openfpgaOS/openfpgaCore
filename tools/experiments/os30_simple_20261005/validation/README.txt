Private single-issue A software validation

All scripts run from repository root. No production, sibling or SD writes.
prepare.py reuses both exact published baseline captures, verifies their complete
result hashes, copies the byte-identical observed qsim, and extracts A's GPU
parameters/macros from its frozen full-fit source. A has GEO0 and MLAB texture
queues. Its feature word matches the retained qsim0x2b136753. The actual A CPU
33895e9a is the baseline128 CPU already exercised by the prior RTL fixtures.
The full-game capture uses the calibrated single-issue model, not that CPU RTL.

Run once:
  python3 tools/experiments/os30_simple_20261005/validation/prepare.py
  python3 tools/experiments/os30_simple_20261005/validation/build_replay.py
  python3 tools/experiments/os30_simple_20261005/validation/profile_breakdown.py

Each immutable candidate ELF uses a new label:
  python3 tools/experiments/os30_simple_20261005/validation/capture.py LABEL --elf PATH --geometry 0 --audio
  python3 tools/experiments/os30_simple_20261005/validation/analyze.py LABEL
  python3 tools/experiments/os30_simple_20261005/validation/replay.py LABEL
  python3 tools/experiments/os30_simple_20261005/validation/analyze.py LABEL --require-replay

Baseline rendering has already been captured under baseline-normal, with modes
1(scanout/audio) and2(additional CPU traffic). Do not overwrite or rerun existing
labels. Baseline-diagnostic is the990b P3 app; baseline-normal is0350. To compare a
diagnostic candidate, use analyze.py --baseline baseline-diagnostic and replay
that baseline once. GPU binaries capture320x240 color and depth independently;
ELF addresses/full-memory hashes can differ without changing rendered output.
Results include differing-pixel counts, not just a pass/fail hash. Guardband is
intentionally a behavior-changing trial and may fail exact pixels.

Performance comparisons use frames360–720 inclusive,361 gameplay frames, keeping
boot/predecode out. profile_breakdown.py uses cumulative snapshots0360→0720,
360 frames, and clearly labels that distinct boundary. Instructions are actual
guest counts. Estimated CPU cycles use the unchanged qsim assumptions; GPU
workload cycles are separate actual-RTL replay observations. Neither is hardware
FPS, real CPU/GPU overlap or a measured CPU/cache-stall breakdown.

The Pocket SDRAM controller/arbiter and A's GPU/cache source bytes are frozen.
The test texture arena is512KiB SDRAM-backed, unlike the hardware CRAM1 path.
Scanout/audio/CPU contenders are synthetic. The app model implements OS hooks;
firmware interrupts/mixing/CPU cache traffic do not execute through RTL. Eight
captured frames cannot establish every level/camera/scene. The older coupled
sm64_overlap_20261004 harness is GEO1 worker-v6, not this A, and needs an exact
GEO0 rebuild before reuse; its direct mode is an old GPU MAC, not A's ordinary
triangle path. No stale Sept22 depth-clear filtering is used here.
