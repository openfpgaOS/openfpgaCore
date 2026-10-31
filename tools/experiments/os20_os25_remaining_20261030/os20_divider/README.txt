OS20 radix4 divider experiment — 2026-10-30
==========================================

Functional, synthetic-performance and full-core physical gates pass. The
production OS20 config now enables DIV_RADIX=4; the retained radix4-only fit
is seed35. Caches, ISA, audio, Dock, firmware and runtime clock stay intact.
The isolated reproduction recipe does not modify production files or deploy.

Configuration
-------------
The experiment compares DIV_RADIX=2 against the now enabled DIV_RADIX=4.
Only that setting changes; dual issue, 32 KiB I-cache, 64 KiB D-cache, 4-slot /
32-op store buffer, full bypass and all existing OS20 timing transforms stay.

Fresh reference generation reproduced every functional byte of the qualified
311d16dd... CPU. The private checkout has no Git HEAD, so its two provenance
comments differ; baseline-fidelity.json records the exact difference.
The generated CPUs have the same 13 modules. Only VexiiRiscv (divider ports /
iteration count) and DivRadix change; structural-audit.json pins the other
11 modules. No approximate reciprocal or floating-point algorithm is used.

Measured results
----------------
- Generated divider: 200,397 exact integer/normalized-mantissa cases,
  response backpressure, and 4,096 flush/restart cases pass.
- Full CPU: 9,348 signed/unsigned DIV/REM checks pass on both CPUs, including
  divisor zero, INT_MIN / -1, mixed signs and random 32-bit operands.
- Same ELF on both CPUs, with quiet memory and concurrent scanout:
    warm loop                         radix2       radix4
    unsigned DIV + dependent add      37.204       21.204 cycles/op
    unsigned REM + dependent add      37.211       21.211
    signed DIV + dependent add        37.218       21.218
    signed REM + dependent add        37.211       21.211
    dependent fdiv.s                  32.103       19.103
    dependent fmul.s                   5.270        5.270
    dependent fsqrt.s                 29.103       29.103
  Loop/control/timer-read costs are included. Final architectural outputs
  match exactly. Quiet and scanout warm-loop measurements are identical.
- Mixed CPU stress with heavy scanout and timer interrupts:
    radix2: 1,630,748 cycles, 372 interrupts
    radix4: 1,540,793 cycles, 349 interrupts
  5.516% fewer cycles in this synthetic workload. Integer/FPU/atomics,
  trap returns, cache traffic and read/write/scanout scoreboards all pass.

These are not complete-game FPS measurements. Variable division does occur
in Diablo's GenerateRnd (engine/random.cpp:54-55) and animation calculations
(engine/animationinfo.cpp:75,80,174), and ScummVM's graphics paths
(engines/scumm/gfx.cpp:4730-4731; engines/agi/graphics.cpp:1310).
There is no measured dynamic divide share for the games in this campaign.
Constant divisions can already compile into shifts/multiplications.
No claim is made that a CPU-rendered game improves by 5.516%.

Physical costs
--------------
The retained radix4-only seed35 fit uses 16,468 ALMs and 228 M10Ks, with a
95.26 MHz worst-corner restricted main-clock estimate and +0.042 ns worst
hold slack. Nonsetup, nonmain setup and used crossing/input checks pass at
all four corners. The configured clock remains 100 MHz: main-clock setup
is -0.498 ns, so this is not timing closure at 100 MHz. The exact fitted ROM
is e505071d970e68bb7672be27db82ff304e37d9cb3a9d04180c0f9cf61992b031.

The matched radix2 seed35 control used 16,885 ALMs with a 92.65 MHz estimate
and -0.254 ns hold. These are measurements of the selected placements, not
a promise that every radix4 seed saves the same area or improves timing.
Optional audio/RAM changes were rejected after physical timing regressions;
they are not part of this retained fit. Radix4 needs no cache cuts or new RAM.

Reproduce
---------
Use Python3, git, sbt with the existing dependencies, Perl, Verilator, Docker
and the existing openfpgaos-firmware compiler image. From the Core root:

  python3 tools/experiments/os20_os25_remaining_20261030/os20_divider/run.py \
    --output build/os20-divider-validation

The output must be a new directory. The recipe copies the generator privately,
forces each divider setting only in that copy, verifies the reviewed generated
CPU body hashes, runs the existing focused-divider and Pocket IRQ regressions,
and builds/runs main.c with independently generated ISA corner-case vectors.
It never calls Quartus, writes active configs, publishes or deploys.

Raw initial logs and full generated CPUs remain in the ignored directory:
  docs/measurements/os20-os25-remaining-20261030/os20-divider/

results.json records their hashes and counts. The compact recipe was also
rerun end-to-end under os20-divider-reproduction. reproduction.json records
the successful run: both CPUs, the loaded program bytes, all result values,
stress cycles and every latency row reproduce exactly. Only a random temporary
assembler-object name in the ELF symbol table differs; division.bin is exact. Initial sandbox failures to lock SBT's cache
or access Docker occurred before the relevant test and are not RTL failures.
