OS30 LEAN DUAL-ISSUE FEASIBILITY AND TIMING EXPERIMENT
2026-10-04 / 2026-10-05

Scope
-----
Private experiments targeting a fitted dual-issue os30 with estimated main-clock
Fmax above 90 MHz (latest user threshold; initially roughly 92 MHz).
The configured clock remains 100 MHz; estimated
Fmax near 92 does not constitute 100 MHz timing closure. Trials use saved seed 2,
with one explicitly recorded seed3 comparison on the qualified pointer/packer
source to measure placement variation. There is no full seed sweep, production
promotion, deployment or commit.

Every full-core candidate retains the complete ordinary os30 triangle GPU,
clip-fed vertices, combiner, render cache, texture-memory interface and audio.
The independent geometry worker is disabled. The current Quake II edge
corrections remain in the frozen source. This is not a game-level comparison
against the currently deployed geometry-worker build.

Authoritative results
---------------------
docs/measurements/os30-dual-20261004/REPORT.txt and results.json collect every
completed full-core trial. They distinguish fitter success from timing closure.
CPU_FITS.txt describes standalone CPU probes, whose virtual external ports
cannot qualify the complete system. TIMING_AUDIT.txt describes grouped full-core
paths, placement checks, and the rejected SDRAM placement experiment.

The original single-issue I32/D128 reference fits at 17,248 ALMs, 1,846 LABs,
306 M10Ks and 92.53 MHz main-clock Fmax. Setup at 100 MHz is -0.842 ns.
The device has 18,480 ALMs, 1,848 LABs and 308 M10Ks; physical LAB packing,
not just headline ALM percentage, limits the experiments.

The validated immediate-lane candidate with early FPU/integer match flags,
early cache-tag data selection, a GPU count predicate shadow, and shared
framebuffer queue address reads fits at 18,042 ALMs / 1,843 LABs / 308 M10Ks,
86.45 MHz. Its remaining walls are CPU decode control and forwarding; later
rows in REPORT.txt record the follow-up attempts and current best result.

The all-match preselection/ring-data candidate fits at 18,042 ALMs / 1,841 LABs
and 86.59 MHz, with positive hold/recovery/removal/pulse-width checks. Its FP and
integer match paths and GPU ring-data path are no longer the main limits. Later
experiments address the measured decode enable, RAW instruction normalization
and load-data paths. These transformations preserve pipeline cycles and are
checked against exact source cones and the fourteen-case CPU suite.

The two-bank frontend replaces the same skid/StageLink data storage with
ping-pong banks while preserving all four lane-mask registers and the existing
occupancy controls; only one pointer FF is added. Its 14 CPU cases retain parent
outputs/cycles, with bank/pointer/head-payload checks over 33.36 million cycles.
The complete fit reaches 87.50 MHz at 18,067 ALMs / 1,848 LABs / 308 M10Ks with
positive hold and other checks. This result was below the target; subsequent
trials are recorded in REPORT.txt. This candidate's worst paths moved to audio,
memory-write control and GPU queue control.

The next combined peripheral trial improves those three local timing cones,
but reaches only 86.33 MHz overall because a peripheral read mux and CPU
instruction-alignment control become the limits. A one-bit sampled D-cache
any-hit flag passes the fourteen-case CPU suite and has good local timing,
but its first two placements suffer routing congestion (59.96 MHz and about
76.12 MHz). The higher-effort trial also violates clk_74a setup; neither is
selected. Follow-ups directly classify instruction fetch faults from the
existing usable-slice masks and retain the existing SYSREG read-mux boundary.
They add no pipeline cycles or feature cuts. Every trial keeps its exact
frozen sources; see REPORT.txt for later results rather than assuming a local
path improvement guarantees better whole-device timing.

The pointer/packer/texture-offer candidate provided a qualified
dual result of 87.65 MHz, 18,084 ALMs / 1,847 LABs / 308 M10Ks, positive hold and
other timing checks. All fourteen CPU cases retain exact outputs and cycles.
The pointer now physically uses D with a constant enable, and the rounding
mode dependency is removed at the proved packed-result boundary. Both local
paths have positive margin. Integer RS1 and floating RS2 forwarding now limit
this placement. Subsequent forwarding, GPU and physical-optimization trials
are recorded below and in REPORT.txt.

The current best qualified full-core result adds coordinate staging to the
GPU, reusing existing scratch registers without adding cycles or storage.
At default placement effort and saved seed 2 it reaches 90.04 MHz warm /
90.11 MHz cold, 18,142 ALMs, 1,848 LABs and 308 M10Ks. Hold is +0.097 ns;
recovery, removal, pulse width and other-clock setup checks are positive.
All 14 CPU cases and 114 GPU comparisons are bound to this exact frozen fit in
coordinate-default-fit-integration-audit-v1/manifest.json. This exceeds the
requested 90 MHz estimate by only 0.04 MHz; setup at the unchanged configured
100 MHz remains -1.106 ns. It is not 100 MHz closure or a game-FPS result.
The same sources at placement effort 2 reach 88.95 MHz and at effort 3 reach
83.17 MHz; the default-effort result is selected. The worst selected-fit path
is CPU dispatch/hazard control to stage2 payload capture.

A second decoded CPU buffer passes all 14 architectural cases, but exceeds
capacity by one LAB in its closest placement. It is not cycle-identical:
the shift/branch test takes 1.161% more cycles while the vector/triangle
tests are nearly unchanged. Removing 13 optional RAW qualifier KEEP boundaries
and packing a GPU queue did not rescue its whole-device capacity.

Architecture and validation
---------------------------
The extra lane shares caches and implements either base integer ALU operations
or immediate ALU operations only. Memory, multiplication/division, floating
point, branch and shift execution remain on the primary lane. Full I32/D128
caches are retained in the current candidates; BTB256/GShare1KiB frees five
M10Ks compared with the initial dual profile. Deliberate capacity tests show
that halving either cache can cause large performance losses.

The follow-up frontend work separates compressed expansion from ISA decoding,
then adds a 278-bit skid buffer to break dispatch-to-fetch backpressure. The
buffer bypasses when empty and adds no unstalled forwarding cycle. A first
version failed interrupt/flush tests: invalid frames retained active lane masks.
That version is explicitly rejected even though its isolated timing looked good.
The corrected version cancels lane masks on flush and passes all fourteen CPU
program/traffic cases, including precise illegal traps and FP legality checks.

At equal clocks the corrected full-ALU skid improves the measured SM64 vector
kernel by about 9.9% and private captured-scene triangle setup by 10.7-11.3%
versus the single-issue reference. The immediate-only fallback retains the vector
gain but reduces triangle throughput by about 6.7-6.9% versus the full lane.
These are component tests, not whole-game FPS; vector inputs are synthetic and
the private software triangle setup is not the production renderer workload.

validation/README.txt and validation/REPORT.txt give exact per-profile coverage,
performance, source hashes, failure history and reproduction. Do not assume all
historical profiles were run against the expanded fourteen-case suite. The
original eight-case results remain in validation/summary-all.json; the new
validation-inventory.json enumerates the complete campaign.

Fit controls
------------
Area/combinational physical synthesis and existing synchronous RAM migrations
are explicit per-trial choices. WBUF_REVIEW.txt records 491 passing SDRAM write
buffer checks including poisoned read/write collisions; M10K mapping preserves
one-cycle OLD_DATA behavior. No false paths or relaxed timing exceptions are
introduced. No configured clock or SDRAM phase changes are made.

The first SDRAM receive placement proposal used braces that Quartus interpreted
literally in a directly written QSF. All 64 constraints were ignored, and that
trial is labeled accordingly. A canonical exact-name proposal passed direct
project-open checks but exceeded physical capacity. The unconstrained corrected
skid fit had ample SDRAM receive margin, so that placement is not selected.

Reproduction and artifacts
--------------------------
  generator/README.txt                  source patches and immutable recipes
  fit.py LABEL --cpu PRIVATE.v          frozen full-core single-placement build
  fit_cpu.py LABEL --cpu PRIVATE.v      isolated CPU diagnostic
  summarize.py                         completed full-core comparison
  audit_fit.py LABEL                    read-only grouped timing/placement audit

Optional fit.py flags:
  --placement-seed 3                   one explicit placement comparison
  --area --combo-area
  --optimization-mode 'HIGH PERFORMANCE EFFORT'  retain explicit area/physical settings
  --combo-speed --physical-effort EXTRA  optional physical timing optimization
  --register-duplication                optional physical register fanout reduction
  --write-buffer-bram --texture-queues-bram
  --gpu PRIVATE_GPU.v
  --render-cache PRIVATE_CACHE.sv
  --texture-cache PRIVATE_TEXTURE_CACHE.v
  --audio-mixer PRIVATE_AUDIO.v
  --cpu-system PRIVATE_CPU_SYSTEM.v
  --peripheral-slave PRIVATE_AXI_PERIPH.v
  --target-port-per PRIVATE_CRAM_PORT.v
  --cpu-speed                         CPU-only synthesis goal; verify mapped scope
  --register-packing MINIMIZE_AREA_WITH_CHAINS
  --decode-ready-fanout
  --decode-ready-fanout-limit 32        targeted skid control buffering (default 64)
  --stage2-ready-fanout-limit 32        buffer the measured stage2 capture enable
  --stage2-ready-global                alternative global route for that enable
  --placement-effort 2.0               unchanged seed and timing constraints
  --router-effort 2.0                  bounded Cyclone V routing comparison
  --register-retiming                  supported physical synthesis experiment
  --sdram-rx-labs                       experimental, not selected

Check the selected CPU memory budget before enabling RAM migrations. Existing
fit labels are refused. Frozen files, source hashes, complete reports and private
bitstreams are under docs/measurements/os30-dual-20261004/. Full-fit outputs carry
a NOT_DEPLOYABLE marker until separately qualified; fitter success alone does
not establish device behavior or performance.

production-input-check.json verifies the original 104 production inputs against
the initial frozen reference. Private experiment edits preserve the user's
pre-existing production working tree and sibling game folders.
