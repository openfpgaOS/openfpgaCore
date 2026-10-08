SM64 on-device performance snapshot, 2026-10-03

Purpose: the user reports that Bob-omb Battlefield still feels equally slow
after the geometry-fast trial. CPU-model improvements are not hardware FPS.
This experiment instruments actual app wall time and OS presentation counts.
It does not make another performance improvement claim.

Artifacts are under docs/measurements/sm64-device-perf-20261003/ (ignored).
The reviewed software baseline is the geometry-fast trial's final-candidate
ELF (83884ce3d5c9156738cc932f6fc32809a05fc6eebf06aedff640660650a0b7b9).
before.json records hashes of the actual sibling SM64 source/runtime.
sm64-device.patch records the seven diagnostic source changes and user notes.
qsim-device.patch applies to sm64-geometry-fast-20261003/qsim; the accompanying
qsim-device-inputs.json guards each changed simulator source file.

The qsim changes allow optional audio CPU code with functional voice lifetimes
and inject the snapshot chord. They retain the prior synthetic 30 Hz clock and
eager GPU. Audio mixing, kernel cost, IRQs, GPU backpressure and display cadence
are NOT modeled. Never interpret diagnostic.csv or panel-preview.png as FPS
measurements from a Pocket. The preview checks only panel layout and controls.

Validation scripts (run from the core repository):
  python3 tools/experiments/sm64_device_perf_20261003/check_timing.py
  python3 tools/experiments/sm64_device_perf_20261003/check_disabled.py
  python3 tools/experiments/sm64_device_perf_20261003/analyze.py
  python3 tools/experiments/sm64_device_perf_20261003/verify_publication.py

capture.py saves the ELF and input hashes before each 721-frame functional run.
Inputs for audio-current, diag-hidden, diag-panel and diag-disabled are saved
in their respective capture-*/inputs.json. The diag-panel capture requests GPU
vectors 360,600,660,720; other captures use the default eight sampled frames.
replay.py uses the current geometry+depth-prefetch RTL replay executable with
Pocket SDRAM and memory-traffic mode 1. Hidden/disabled renders match baseline
at eight frames each, with two additional before/after-panel comparisons.

The normal Makefile enables PERF_DIAG=1 and isolates objects in -perfdiag.
PERF_DIAG=0 removes all instrumentation; its loadable image differs only in
eleven assertion line-number arguments, and modeled cycles match every frame.
PROFILE=1 disables this snapshot to keep the reset-on-read counters exclusive.
The guarded publication updates only source and PERFORMANCE-TEST.txt. Runtime
hashes and all prior optimization changes are preserved. The backup has a
guarded restore.py; no SD-card copy, seed sweep, commit or push is performed.

Actual next measurement: copy the package, hold a slow first-level view for
five seconds with the panel hidden, then press Select+L+R and report the panel.
It shows the last completed two-second window and freezes while visible.
Individual LCD interval distributions would require further instrumentation;
this first snapshot separates dominant CPU, render and presentation costs.
