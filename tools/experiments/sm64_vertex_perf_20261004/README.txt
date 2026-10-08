SM64 P3 vertex diagnostics

Private source and evidence:
  docs/measurements/sm64-vertex-perf-20261004/
  before.json, results.json, REPORT.txt, source-before/, SM64/

P3 subdivides only the existing VERT bucket. MATR covers matrix commands/MVP
and raw matrix/culling data preparation. PREP covers accelerated-path guards,
bounds/frustum work and slot bookkeeping. ATTR covers deferred attributes and
shared light-direction setup. CPU covers the entire ordinary vertex-load loop,
including that loop's lighting/UV/fog. REST retains remaining VERT overhead.
Late vertex recovery keeps its existing TRI attribution; it is counted by REC.

The P2 snapshot prefix remains 30 words. P3 appends five timing parts and four
route counts, for 39 words. Detailed timings and counts use the same one-in-eight
sample. Independently truncated parts can sum to 0.4ms less than VERT. RAW is
accepted at load time, not GPU utilization; REC is additional later work on
vertices already included in RAW or OUT. All-frame REND is a separate average.

instrument.py applies hash-guarded edits to the private baseline. It never edits
the SDK/runtime. Build through the existing SDK Docker toolchain, with an
explicit app.elf target to avoid assembling an older FPGA runtime into a package.
PERF_DIAG=0 compiles away the probes; check_disabled.py verifies identical
loadable code/data except 11 assertion source-line arguments, plus identical
segment/BSS layout.

check_timing.py uses the actual profiler logic/header with an injected clock.
It checks exclusive timing, route counts, ABI offsets, 1/8 sampling, early-exit
cleanup, wrap, catch-up steps, zero samples, frozen display and paused windows.
capture.py uses the existing scripted Bob-omb Battlefield route and audio
preparation. The private qsim patch only extends diagnostic.csv; a baseline
control proves all frame and geometry records remain identical.
replay.py validates GPU commands against the installed worker RTL with the
Pocket SDRAM contention model. analyze.py checks all final hashes and evidence.

P3 adds about 1.3% modeled CPU overhead over P2 CACHE. This is an instrumentation
build; on-device overhead and the actual vertex breakdown need a Pocket sample.
The simulator does not measure real FPS or CPU/GPU/display overlap.

publish.py defaults to dry run; --apply updates the sibling source/app/package
with guarded backups, --verify checks all hashes and unchanged SDK/runtime.
Backup: ../SM64/.obj/vertex-perf-backup-20261004/.
deploy.py defaults to verification; --apply backs up and atomically replaces
only the mounted card's sm64.elf. It checks UUID, previous app, worker and OS
hashes, syncs and verifies readback. Safely unmount separately after success.

No FPGA fit or seed sweep runs in this workflow. Show PERF P3 VERT with
Select+L+R after holding the same slow view for at least five seconds.
