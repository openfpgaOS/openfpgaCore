OS25 CPU timing candidates — 2026-10-30
======================================

These candidates preserve I32/D128, the ISA, radix-4 division, prefetch policy,
pipeline stages and the configured 100 MHz clock. They change the physical
implementation of existing capture/forwarding logic, not instruction throughput.

Profiles
  baseline   Current os25 CPU configuration before these experiments.
  candidate  FETCH_READ_HOLD=1, ALIGNER_PC_HOLD=1, FETCH_READY_FACTOR=1.
  fpu-match  The candidate plus FPU_MATCH_REG=1: 27 registered register-ID
             comparisons beside the existing pipeline IDs. No stage is added.

The first candidate was previously tested on October 1, but was not retained
when a yellow-screen investigation switched to os20. That earlier investigation
does not establish an os25 failure. Fresh generation matches both earlier
implementations exactly except the two generator Git-provenance comments.
The FPU-match profile has separate functional and physical evidence.

Reproduce from the repository root with Python 3, Perl, Git, Verilator, Docker,
the installed openfpgaos-vexii/openfpgaos-firmware images and initialized generator
submodules. Choose a NEW output directory inside this checkout:

  python3 tools/experiments/os20_os25_remaining_20261030/os25_cpu/run.py prepare \
    --output build/os25-cpu-reproduction
  python3 tools/experiments/os20_os25_remaining_20261030/os25_cpu/run.py audit \
    --output build/os25-cpu-reproduction
  python3 tools/experiments/os20_os25_remaining_20261030/os25_cpu/run.py check \
    --output build/os25-cpu-reproduction

Preparation uses a private generator source tree, then applies the checked
postprocessors in the same order as generate_vexii.sh. It never edits production
configs or a shared generated CPU. Existing output is refused. The input pins
check generator/helper versions, submodule commits and each generated logic hash.
Only the two provenance-comment lines are omitted from logic hashing; all other
bytes are retained. The complete emitted netlists keep their actual comments.

Validation completed
  - Fresh full config-driven generation independently reproduced the fetch trio.
  - Baseline, fetch trio and FPU-match whole-CPU logs are byte-identical:
    1,669,299 cycles, 378 timer interrupts, 2,138 concurrent scanout bursts,
    zero read/write/scanout/protocol errors. Integer/FPU/atomic/trap/cache stress
    executes the same flat firmware binary in all three runs.
  - Fetch-bank/prefetch-PC and aligner-PC holds each passed 1,000,000 cycles,
    with 624,819 bank-zero stalls and 62,602 read/write collisions per run.
  - Exhaustive fetch-ready equivalence passed 16,384 inputs for both supported
    buffer widths, plus idempotence and rejection of malformed dependencies.
  - All 27 FPU match signals equal their original pipeline-ID comparisons for
    100,000 cycles with stalls; the transformation is idempotent.

functional-results.json records these bounded checks, artifact hashes and limits.
Raw logs, generated sources, exact config copies, compiler outputs and simulator
objects remain under:
  docs/measurements/os20-os25-remaining-20261030/os25-cpu/

Physical baseline/candidate fits are separate under the parent experiment's
physical/ directory. Functional success does not establish timing closure or FPS.
The older 94.88 MHz estimate is provenance, not a new result. Only the parent
experiment's final fitted selection may be promoted. Nothing here deploys.

Fresh seed-20 physical comparison in the current generic core:
  profile     restricted Fmax   worst hold   ALMs    M10Ks
  baseline        96.87 MHz      +0.078 ns   15490    296
  candidate       96.40 MHz      +0.006 ns   15577    296
  fpu-match       95.55 MHz      +0.086 ns   15572    296
All used crossing/input and non-setup checks pass. All three still fail main
clock setup at the configured 100 MHz. Both transforms are rejected for
promotion: they preserve cycles but reduce timing margin and increase area
against the fresh control. The older isolated improvement did not transfer.
The parent campaign ultimately retains os20 radix4 seed 35 and the os25 control
seed 20. Final metrics, rejected RAM/audio/clamp trials and exact proof hashes
are in ../physical-results.json. These were targeted fits, not a full sweep.
